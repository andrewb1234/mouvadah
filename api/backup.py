"""Authenticated encrypted database backup, verification, and restore CLI.

PostgreSQL archives use the server-matched ``pg_dump``/``pg_restore`` tools.
SQLite uses its online backup API. Both formats are encrypted before leaving
the process with AES-256-GCM and an authenticated, non-secret manifest.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from api.config import get_settings
from api.migrations.runtime import (
    assert_database_current,
    assert_schema_matches_metadata,
    current_revision,
)
from api.observability import configure_runtime, flush_telemetry, observe_job

BACKUP_FORMAT = "mouvadah.encrypted-database-backup.v1"
MAGIC = b"MOUVADAH-BACKUP\x01"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
REQUIRED_POSTGRES_TABLES = {
    "projectmembership", "projectinvitation", "projectaccessevent",
    "agentsession",
    "alembic_version",
    "apikey",
    "apikeyproject",
    "auditlog",
    "browsersession",
    "comment",
    "knowledgenode",
    "knowledgeproposal",
    "project",
    "subproject",
    "ticket",
    "ticketdependency",
    "user",
    "workspace",
    "workspaceinvitation",
    "workspacelifecycleevent",
    "workspacemembership",
    "workspacemembershipevent",
}
RESTORE_DRILL_DATABASE_PREFIX = "mouvadah_restore_drill"


class BackupError(RuntimeError):
    """Raised when backup safety, integrity, or tooling checks fail."""


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.manifest.json")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _load_key(value: str | None = None) -> bytes:
    encoded = (value or os.environ.get("BACKUP_ENCRYPTION_KEY", "")).strip()
    if not encoded:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY must be a base64-encoded 32-byte key."
        )
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY is not valid base64."
        ) from exc
    if len(key) != 32:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes."
        )
    return key


def _database_identity(url: str | URL) -> dict[str, str | int | None]:
    parsed = make_url(url) if isinstance(url, str) else url
    return {
        "backend": parsed.get_backend_name(),
        "host": parsed.host,
        "port": parsed.port,
        "database": parsed.database,
    }


def _database_fingerprint(url: str | URL) -> str:
    return hashlib.sha256(
        _canonical_json(_database_identity(url))
    ).hexdigest()


def _database_name(url: str | URL) -> str:
    parsed = make_url(url) if isinstance(url, str) else url
    if not parsed.database:
        raise BackupError("Database URL must include a database name.")
    if parsed.get_backend_name() == "sqlite":
        return Path(parsed.database).name
    return parsed.database


def _safe_postgres_url(
    raw_url: str,
    *,
    host_override: str | None = None,
) -> tuple[str, dict[str, str]]:
    parsed = make_url(raw_url)
    if parsed.get_backend_name() != "postgresql":
        raise BackupError("PostgreSQL tooling requires a PostgreSQL URL.")
    password = parsed.password
    safe_url = URL.create(
        drivername="postgresql",
        username=parsed.username,
        host=host_override or parsed.host,
        port=parsed.port,
        database=parsed.database,
        query=dict(parsed.query),
    )
    safe = safe_url.render_as_string(hide_password=False)
    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password
    return safe, env


def _command(env_name: str, default: str) -> list[str]:
    value = os.environ.get(env_name, default)
    command = shlex.split(value)
    if not command:
        raise BackupError(f"{env_name} cannot be empty.")
    return command


def _run_archive_command(
    command: list[str],
    args: list[str],
    *,
    env: dict[str, str],
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | int = subprocess.PIPE,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            [*command, *args],
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BackupError(
            f"Required database tool {command[0]!r} is not installed."
        ) from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise BackupError(
            f"Database archive command failed with exit code "
            f"{result.returncode}: {message}"
        )
    return result


def _postgres_tool_version(
    command: list[str],
    *,
    env: dict[str, str],
) -> str:
    result = _run_archive_command(
        command,
        ["--version"],
        env=env,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _create_plaintext_archive(
    database_url: str,
    destination: Path,
    *,
    postgres_host_override: str | None = None,
) -> tuple[str, str]:
    parsed = make_url(database_url)
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        if not parsed.database or parsed.database == ":memory:":
            raise BackupError(
                "SQLite backups require an existing file-backed database."
            )
        source_path = Path(parsed.database).expanduser().resolve()
        if not source_path.exists():
            raise BackupError(f"SQLite database does not exist: {source_path}")
        with (
            sqlite3.connect(
                f"file:{source_path}?mode=ro",
                uri=True,
            ) as source,
            sqlite3.connect(destination) as target,
        ):
            source.backup(target)
        return "sqlite", sqlite3.sqlite_version
    if backend != "postgresql":
        raise BackupError(f"Unsupported database backend {backend!r}.")

    safe_url, env = _safe_postgres_url(
        database_url,
        host_override=postgres_host_override,
    )
    command = _command("PG_DUMP_COMMAND", "pg_dump")
    with destination.open("wb") as output:
        _run_archive_command(
            command,
            [
                "--format=custom",
                "--compress=9",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                safe_url,
            ],
            env=env,
            stdout=output,
        )
    return "postgresql-custom", _postgres_tool_version(command, env=env)


def _encrypt_file(
    plaintext: Path,
    destination: Path,
    *,
    key: bytes,
    aad: bytes,
) -> None:
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(nonce),
    ).encryptor()
    encryptor.authenticate_additional_data(aad)
    with plaintext.open("rb") as source, destination.open("wb") as target:
        target.write(MAGIC)
        target.write(nonce)
        while chunk := source.read(CHUNK_BYTES):
            target.write(encryptor.update(chunk))
        target.write(encryptor.finalize())
        target.write(encryptor.tag)


def _decrypt_file(
    backup_path: Path,
    destination: Path,
    *,
    key: bytes,
    aad: bytes,
) -> None:
    size = backup_path.stat().st_size
    minimum = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if size < minimum:
        raise BackupError("Encrypted backup is truncated.")
    with backup_path.open("rb") as source:
        if source.read(len(MAGIC)) != MAGIC:
            raise BackupError("Encrypted backup has an unknown format.")
        nonce = source.read(NONCE_BYTES)
        source.seek(-TAG_BYTES, os.SEEK_END)
        tag = source.read(TAG_BYTES)
        ciphertext_bytes = size - minimum
        source.seek(len(MAGIC) + NONCE_BYTES)
        decryptor = Cipher(
            algorithms.AES(key),
            modes.GCM(nonce, tag),
        ).decryptor()
        decryptor.authenticate_additional_data(aad)
        try:
            with destination.open("wb") as target:
                remaining = ciphertext_bytes
                while remaining:
                    chunk = source.read(min(CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BackupError("Encrypted backup is truncated.")
                    remaining -= len(chunk)
                    target.write(decryptor.update(chunk))
                target.write(decryptor.finalize())
        except InvalidTag as exc:
            destination.unlink(missing_ok=True)
            raise BackupError(
                "Backup authentication failed: wrong key or modified "
                "backup/manifest."
            ) from exc


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(_canonical_json(value) + b"\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_backup(
    database_url: str,
    output_path: str | Path,
    *,
    encryption_key: str | None = None,
    key_id: str = "primary",
    overwrite: bool = False,
    postgres_host_override: str | None = None,
) -> dict:
    """Create an authenticated encrypted database archive and manifest."""
    key = _load_key(encryption_key)
    output = Path(output_path).expanduser().resolve()
    manifest_path = _manifest_path(output)
    if not key_id.strip() or len(key_id) > 100:
        raise BackupError("key_id must contain 1-100 characters.")
    if not overwrite and (output.exists() or manifest_path.exists()):
        raise BackupError(
            "Refusing to overwrite an existing backup or manifest."
        )
    output.parent.mkdir(parents=True, exist_ok=True)

    target_engine = create_engine(database_url)
    try:
        assert_database_current(target_engine)
        assert_schema_matches_metadata(target_engine)
        revision = current_revision(target_engine)
    finally:
        target_engine.dispose()
    if revision is None:  # pragma: no cover - guarded above
        raise BackupError("Database is not at a known migration revision.")

    plaintext_handle = tempfile.NamedTemporaryFile(
        prefix=".mouvadah-plaintext-",
        dir=output.parent,
        delete=False,
    )
    plaintext_path = Path(plaintext_handle.name)
    plaintext_handle.close()
    os.chmod(plaintext_path, 0o600)
    encrypted_tmp = output.with_name(
        f".{output.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        archive_format, tool_version = _create_plaintext_archive(
            database_url,
            plaintext_path,
            postgres_host_override=postgres_host_override,
        )
        authenticated = {
            "format": BACKUP_FORMAT,
            "backup_id": str(uuid.uuid4()),
            "created_at": _utc_iso(),
            "archive_format": archive_format,
            "database_backend": make_url(database_url).get_backend_name(),
            "database_name": _database_name(database_url),
            "source_fingerprint": _database_fingerprint(database_url),
            "migration_revision": revision,
            "plaintext_sha256": _sha256_file(plaintext_path),
            "plaintext_bytes": plaintext_path.stat().st_size,
            "key_id": key_id.strip(),
            "database_tool_version": tool_version,
        }
        _encrypt_file(
            plaintext_path,
            encrypted_tmp,
            key=key,
            aad=_canonical_json(authenticated),
        )
        os.chmod(encrypted_tmp, 0o600)
        manifest = {
            "authenticated": authenticated,
            "ciphertext_sha256": _sha256_file(encrypted_tmp),
            "ciphertext_bytes": encrypted_tmp.stat().st_size,
        }
        encrypted_tmp.replace(output)
        _write_json_atomic(manifest_path, manifest)
        return manifest
    finally:
        plaintext_path.unlink(missing_ok=True)
        encrypted_tmp.unlink(missing_ok=True)


def _load_manifest(
    backup_path: Path,
    manifest_path: str | Path | None = None,
) -> dict:
    path = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path
        else _manifest_path(backup_path)
    )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Cannot read backup manifest: {path}") from exc
    authenticated = manifest.get("authenticated")
    if not isinstance(authenticated, dict):
        raise BackupError("Backup manifest lacks authenticated metadata.")
    if authenticated.get("format") != BACKUP_FORMAT:
        raise BackupError("Backup manifest has an unsupported format.")
    return manifest


def _decrypt_verified(
    backup_path: Path,
    *,
    manifest: dict,
    key: bytes,
) -> Path:
    expected_size = manifest.get("ciphertext_bytes")
    expected_sha = manifest.get("ciphertext_sha256")
    if backup_path.stat().st_size != expected_size:
        raise BackupError("Encrypted backup size does not match its manifest.")
    if _sha256_file(backup_path) != expected_sha:
        raise BackupError("Encrypted backup hash does not match its manifest.")
    handle = tempfile.NamedTemporaryFile(
        prefix=".mouvadah-restore-",
        delete=False,
    )
    plaintext_path = Path(handle.name)
    handle.close()
    os.chmod(plaintext_path, 0o600)
    try:
        _decrypt_file(
            backup_path,
            plaintext_path,
            key=key,
            aad=_canonical_json(manifest["authenticated"]),
        )
        if (
            _sha256_file(plaintext_path)
            != manifest["authenticated"].get("plaintext_sha256")
        ):
            raise BackupError(
                "Decrypted archive hash does not match its manifest."
            )
        return plaintext_path
    except Exception:
        plaintext_path.unlink(missing_ok=True)
        raise


def _verify_plaintext_archive(
    archive_path: Path,
    *,
    manifest: dict,
) -> None:
    authenticated = manifest["authenticated"]
    archive_format = authenticated["archive_format"]
    if archive_format == "sqlite":
        with sqlite3.connect(
            f"file:{archive_path}?mode=ro",
            uri=True,
        ) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise BackupError(f"SQLite integrity check failed: {result}")
        return
    if archive_format != "postgresql-custom":
        raise BackupError(f"Unsupported archive format {archive_format!r}.")
    command = _command("PG_RESTORE_COMMAND", "pg_restore")
    with archive_path.open("rb") as source:
        result = _run_archive_command(
            command,
            ["--list"],
            env=dict(os.environ),
            stdin=source,
        )
    listing = result.stdout.decode("utf-8", errors="replace")
    archived_tables: set[str] = set()
    for line in listing.splitlines():
        fields = line.split()
        if (
            line.startswith(";")
            or len(fields) < 7
            or fields[3] != "TABLE"
            or fields[4] != "public"
        ):
            continue
        archived_tables.add(fields[5])
    missing = sorted(REQUIRED_POSTGRES_TABLES - archived_tables)
    if missing:
        raise BackupError(
            "PostgreSQL archive is missing required tables: "
            + ", ".join(missing)
        )


def verify_backup(
    backup_path: str | Path,
    *,
    encryption_key: str | None = None,
    manifest_path: str | Path | None = None,
) -> dict:
    backup = Path(backup_path).expanduser().resolve()
    manifest = _load_manifest(backup, manifest_path)
    plaintext = _decrypt_verified(
        backup,
        manifest=manifest,
        key=_load_key(encryption_key),
    )
    try:
        _verify_plaintext_archive(plaintext, manifest=manifest)
    finally:
        plaintext.unlink(missing_ok=True)
    return manifest


def _guard_restore_target(
    target_url: str,
    *,
    confirm_database: str,
    allow_configured_target: bool,
    backup_evidence: str,
    protected_database_fingerprint: str | None = None,
) -> None:
    database_name = _database_name(target_url)
    if confirm_database != database_name:
        raise BackupError(
            "Restore confirmation must exactly match the target database name."
        )
    configured_url = get_settings().database_url
    target_fingerprint = _database_fingerprint(target_url)
    protected_fingerprint = (
        protected_database_fingerprint.strip().lower()
        if protected_database_fingerprint
        else None
    )
    if protected_fingerprint is not None:
        if len(protected_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in protected_fingerprint
        ):
            raise BackupError(
                "Protected database fingerprint must be 64 lowercase "
                "hexadecimal characters."
            )
        if target_fingerprint == protected_fingerprint:
            raise BackupError(
                "Refusing to restore over the protected application database."
            )
    if target_fingerprint == _database_fingerprint(configured_url):
        if not allow_configured_target:
            raise BackupError(
                "Refusing to restore over the configured application database "
                "without --allow-configured-target."
            )
        if len(backup_evidence.strip()) < 8:
            raise BackupError(
                "Production restore requires separate backup evidence."
            )


def restore_backup(
    backup_path: str | Path,
    target_url: str,
    *,
    confirm_database: str,
    encryption_key: str | None = None,
    manifest_path: str | Path | None = None,
    allow_replace: bool = False,
    allow_configured_target: bool = False,
    backup_evidence: str = "",
    postgres_host_override: str | None = None,
    protected_database_fingerprint: str | None = None,
) -> dict:
    """Restore a verified archive into an explicitly confirmed target."""
    _guard_restore_target(
        target_url,
        confirm_database=confirm_database,
        allow_configured_target=allow_configured_target,
        backup_evidence=backup_evidence,
        protected_database_fingerprint=protected_database_fingerprint,
    )
    backup = Path(backup_path).expanduser().resolve()
    manifest = _load_manifest(backup, manifest_path)
    source_backend = manifest["authenticated"]["database_backend"]
    target_backend = make_url(target_url).get_backend_name()
    if source_backend != target_backend:
        raise BackupError(
            "Backup and restore target database backends must match."
        )
    plaintext = _decrypt_verified(
        backup,
        manifest=manifest,
        key=_load_key(encryption_key),
    )
    try:
        _verify_plaintext_archive(plaintext, manifest=manifest)
        if target_backend == "sqlite":
            parsed = make_url(target_url)
            if not parsed.database or parsed.database == ":memory:":
                raise BackupError(
                    "SQLite restore requires a file-backed target."
                )
            target = Path(parsed.database).expanduser().resolve()
            if target.exists() and target.stat().st_size > 0 and not allow_replace:
                raise BackupError(
                    "Refusing to replace a non-empty SQLite target without "
                    "--allow-replace."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(
                f".{target.name}.{uuid.uuid4().hex}.restore"
            )
            try:
                shutil.copyfile(plaintext, temporary)
                os.chmod(temporary, 0o600)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        elif target_backend == "postgresql":
            safe_url, env = _safe_postgres_url(
                target_url,
                host_override=postgres_host_override,
            )
            args = [
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
            ]
            if allow_replace:
                args.extend(["--clean", "--if-exists"])
            args.extend(["--dbname", safe_url])
            with plaintext.open("rb") as source:
                _run_archive_command(
                    _command("PG_RESTORE_COMMAND", "pg_restore"),
                    args,
                    env=env,
                    stdin=source,
                )
        else:  # pragma: no cover - guarded by backup creation
            raise BackupError(
                f"Unsupported restore backend {target_backend!r}."
            )
    finally:
        plaintext.unlink(missing_ok=True)

    restored_engine = create_engine(target_url)
    try:
        assert_database_current(restored_engine)
        assert_schema_matches_metadata(restored_engine)
    finally:
        restored_engine.dispose()
    return manifest


def reset_restore_drill_target(
    target_url: str,
    *,
    confirm_database: str,
    protected_database_fingerprint: str | None = None,
) -> dict[str, str]:
    """Remove restored data from an explicitly named disposable database.

    The configured application database is always rejected.  Requiring a
    dedicated database-name prefix makes this destructive operation fail
    closed even when an operator supplies a valid but unintended URL.
    """
    _guard_restore_target(
        target_url,
        confirm_database=confirm_database,
        allow_configured_target=False,
        backup_evidence="",
        protected_database_fingerprint=protected_database_fingerprint,
    )
    parsed = make_url(target_url)
    database_name = parsed.database or ""
    if parsed.get_backend_name() != "postgresql":
        raise BackupError(
            "Restore-drill cleanup requires a PostgreSQL target."
        )
    if not database_name.startswith(RESTORE_DRILL_DATABASE_PREFIX):
        raise BackupError(
            "Restore-drill database names must start with "
            f"{RESTORE_DRILL_DATABASE_PREFIX!r}."
        )

    target_engine = create_engine(
        target_url,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with target_engine.connect() as connection:
            schema_names = list(
                connection.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name NOT IN "
                        "('information_schema', 'pg_catalog', 'pg_toast') "
                        "AND schema_name NOT LIKE 'pg_temp_%' "
                        "AND schema_name NOT LIKE 'pg_toast_temp_%' "
                        "AND (schema_owner = current_user "
                        "OR schema_name = 'public')"
                    )
                ).scalars()
            )
            preparer = connection.dialect.identifier_preparer
            for schema_name in schema_names:
                connection.execute(
                    text(
                        "DROP SCHEMA IF EXISTS "
                        f"{preparer.quote(schema_name)} CASCADE"
                    )
                )
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        target_engine.dispose()
    return {
        "database_name": database_name,
        "status": "reset",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mouvadah encrypted database backup and restore."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup")
    backup.add_argument("--output", required=True)
    backup.add_argument("--database-url")
    backup.add_argument("--key-id", default="primary")
    backup.add_argument("--overwrite", action="store_true")
    backup.add_argument("--postgres-host-override")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--input", required=True)
    verify.add_argument("--manifest")

    restore = subparsers.add_parser("restore")
    restore.add_argument("--input", required=True)
    restore.add_argument("--manifest")
    restore.add_argument("--target-url", required=True)
    restore.add_argument("--confirm-database", required=True)
    restore.add_argument("--allow-replace", action="store_true")
    restore.add_argument("--allow-configured-target", action="store_true")
    restore.add_argument("--backup-evidence", default="")
    restore.add_argument("--postgres-host-override")
    restore.add_argument("--protected-database-fingerprint")
    reset_drill = subparsers.add_parser("reset-drill-target")
    reset_drill.add_argument("--target-url", required=True)
    reset_drill.add_argument("--confirm-database", required=True)
    reset_drill.add_argument("--protected-database-fingerprint")
    fingerprint = subparsers.add_parser("fingerprint")
    fingerprint.add_argument("--database-url", required=True)
    return parser


def _safe_summary(manifest: dict) -> dict:
    authenticated = manifest["authenticated"]
    return {
        "backup_id": authenticated["backup_id"],
        "created_at": authenticated["created_at"],
        "archive_format": authenticated["archive_format"],
        "database_backend": authenticated["database_backend"],
        "database_name": authenticated["database_name"],
        "migration_revision": authenticated["migration_revision"],
        "key_id": authenticated["key_id"],
        "ciphertext_sha256": manifest["ciphertext_sha256"],
        "ciphertext_bytes": manifest["ciphertext_bytes"],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_runtime(settings)
    try:
        with observe_job(f"database_{args.command.replace('-', '_')}"):
            if args.command == "backup":
                manifest = create_backup(
                    args.database_url or settings.database_url,
                    args.output,
                    key_id=args.key_id,
                    overwrite=args.overwrite,
                    postgres_host_override=args.postgres_host_override,
                )
            elif args.command == "verify":
                manifest = verify_backup(
                    args.input,
                    manifest_path=args.manifest,
                )
            elif args.command == "restore":
                manifest = restore_backup(
                    args.input,
                    args.target_url,
                    confirm_database=args.confirm_database,
                    manifest_path=args.manifest,
                    allow_replace=args.allow_replace,
                    allow_configured_target=args.allow_configured_target,
                    backup_evidence=args.backup_evidence,
                    postgres_host_override=args.postgres_host_override,
                    protected_database_fingerprint=(
                        args.protected_database_fingerprint
                    ),
                )
            elif args.command == "reset-drill-target":
                summary = reset_restore_drill_target(
                    args.target_url,
                    confirm_database=args.confirm_database,
                    protected_database_fingerprint=(
                        args.protected_database_fingerprint
                    ),
                )
            elif args.command == "fingerprint":
                summary = {
                    "database_fingerprint": _database_fingerprint(
                        args.database_url
                    ),
                    "database_identity": _database_identity(
                        args.database_url
                    ),
                }
            else:  # pragma: no cover - argparse invariant
                raise BackupError(
                    f"Unsupported command {args.command!r}."
                )
    finally:
        flush_telemetry()
    if args.command not in {"reset-drill-target", "fingerprint"}:
        summary = _safe_summary(manifest)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
