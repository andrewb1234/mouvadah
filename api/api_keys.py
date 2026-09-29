"""Shared API-key issuance helpers.

API keys are high-entropy bearer credentials. The full value is returned only
at issuance; the database stores a deterministic SHA-256 digest for lookup.
Unlike a human password, the generated token has 256 bits of entropy, so a
password KDF is neither necessary nor useful here.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from collections.abc import Iterable

from sqlmodel import Session, select

from api.models.entities import (
    ApiKey,
    ApiKeyProject,
    Project,
    WorkspaceMembership,
    User,
    Workspace,
)
from api.security import (
    READ_SCOPE,
    VALID_API_KEY_SCOPES,
    WRITE_SCOPE,
)

KEY_PREFIX = "mouvadah_"
KEY_RANDOM_LENGTH = 32  # bytes of entropy -> ~43 URL-safe base64 characters


def hash_api_key(raw_key: str) -> str:
    """Return the stable SHA-256 lookup digest for a random API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a namespaced API key backed by 256 random bits."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(KEY_RANDOM_LENGTH)}"


def issue_api_key(
    session: Session,
    *,
    user_id: int,
    workspace_id: int,
    name: str,
    scopes: Iterable[str] = (READ_SCOPE, WRITE_SCOPE),
    project_ids: Iterable[int] = (),
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    """Create an API-key record and return ``(record, full_key_once)``."""
    normalized_scopes = sorted(set(scopes))
    normalized_project_ids = sorted(set(project_ids))
    if not normalized_scopes or not set(normalized_scopes).issubset(
        VALID_API_KEY_SCOPES
    ):
        raise ValueError("At least one supported API-key scope is required.")
    from api.authorization import require_project, require_workspace
    from fastapi import HTTPException

    user = session.get(User, user_id)
    if user is None:
        raise ValueError("API-key owner not found.")
    try:
        if normalized_project_ids:
            for project_id in normalized_project_ids:
                project = require_project(
                    session,
                    user,
                    project_id,
                    write=bool({"write", "delete"} & set(normalized_scopes)),
                    lock=True,
                )
                if project.workspace_id != workspace_id:
                    raise ValueError(
                        "Every API-key project must belong to the selected workspace."
                    )
        else:
            require_workspace(
                session,
                user,
                workspace_id,
                write=bool({"write", "delete"} & set(normalized_scopes)),
                lock=True,
            )
    except HTTPException as exc:
        raise ValueError("API-key owner lacks the selected access.") from exc

    raw_key = generate_api_key()
    expires_at = None
    if expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key = ApiKey(
        user_id=user_id,
        workspace_id=workspace_id,
        resource_mode="PROJECTS" if normalized_project_ids else "WORKSPACE",
        name=name,
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
        scopes=normalized_scopes,
        expires_at=expires_at,
    )
    session.add(api_key)
    session.flush()
    for project_id in normalized_project_ids:
        session.add(
            ApiKeyProject(
                api_key_id=api_key.id,  # type: ignore[arg-type]
                project_id=project_id,
            )
        )
    session.commit()
    session.refresh(api_key)
    return api_key, raw_key


def key_has_current_access(session: Session, key: ApiKey) -> bool:
    """Validate live resource grants without consulting ambient credentials."""
    from api.authorization import project_access, _WRITE_ROLES
    from api.models.enums import WorkspaceRole

    user = session.get(User, key.user_id)
    workspace = session.get(Workspace, key.workspace_id) if key.workspace_id else None
    if not user or not workspace or workspace.deletion_requested_at is not None:
        return False
    ids = session.exec(
        select(ApiKeyProject.project_id).where(ApiKeyProject.api_key_id == key.id)
    ).all()
    if key.resource_mode == "PROJECTS" or ids:
        if not ids:
            return False
        for project_id in ids:
            project = session.get(Project, project_id)
            if not project or project.workspace_id != key.workspace_id:
                return False
            access = project_access(session, user, project)
            if access is None or (
                {"write", "delete"} & set(key.scopes) and not access.can_edit
            ):
                return False
        return True
    if key.resource_mode != "WORKSPACE":
        return False
    membership = session.exec(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == key.workspace_id,
            WorkspaceMembership.user_id == key.user_id,
        )
    ).first()
    return membership is not None and (
        not ({"write", "delete"} & set(key.scopes))
        or WorkspaceRole(membership.role) in _WRITE_ROLES
    )
