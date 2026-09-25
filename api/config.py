"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# Default SQLite location: a hidden folder in the user's home directory so the
# DB survives `git clean`, is trivial to back up / inspect with desktop tools,
# and stays consistent across bare-metal and docker-bind-mount deployments.
# Override with the DATABASE_URL env var when you need something else (eg.
# `:memory:` in tests, a Postgres URL in production).
_DEFAULT_DB_DIR = Path.home() / ".taskable"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "taskable.db"
_DEFAULT_DATABASE_URL = f"sqlite:///{_DEFAULT_DB_PATH}"


class Settings(BaseSettings):
    """Typed, cached runtime settings for the FastAPI process."""

    github_pat: str | None = None
    database_url: str = _DEFAULT_DATABASE_URL
    realtime_database_url: str | None = None
    migration_mode: Literal["upgrade", "check"] = "upgrade"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    # Auth
    google_client_id: str | None = None
    google_client_secret: str | None = None
    local_auth_enabled: bool = False
    hosted_mcp_enabled: bool = True
    jwt_secret: str = "dev-jwt-secret-change-me"
    frontend_url: str = "http://localhost:5173"
    auth_rate_limit: int = 10
    auth_rate_window_seconds: int = 300
    action_rate_limit: int = 180
    action_rate_window_seconds: int = 60
    max_request_body_bytes: int = 1_048_576
    deletion_recovery_days: int = 30
    # Required to adopt pre-tenancy projects in a production database. Local
    # development may safely adopt them when exactly one user exists.
    legacy_owner_email: str | None = None

    # Observability. Export/scrape integrations remain fail-closed until their
    # dedicated credentials or endpoints are explicitly configured.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    deployment_environment: str | None = None
    metrics_bearer_token: SecretStr | None = None
    sentry_dsn: SecretStr | None = None
    otel_exporter_otlp_endpoint: str | None = None
    otel_trace_sample_ratio: float = 0.1

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def is_production(self) -> bool:
        """Return whether trusted configuration describes an HTTPS deployment."""
        return self.frontend_url.startswith("https://")

    def observability_environment(self) -> str:
        if self.deployment_environment:
            return self.deployment_environment
        return "production" if self.is_production() else "local"

    def metrics_bearer_token_value(self) -> str | None:
        if self.metrics_bearer_token is None:
            return None
        return self.metrics_bearer_token.get_secret_value()

    def sentry_dsn_value(self) -> str | None:
        if self.sentry_dsn is None:
            return None
        return self.sentry_dsn.get_secret_value()

    def public_origin(self) -> str:
        """Return the configured frontend origin without a path or query."""
        parsed = urlsplit(self.frontend_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("FRONTEND_URL must be an absolute HTTP(S) URL.")
        if parsed.username or parsed.password:
            raise RuntimeError("FRONTEND_URL must not contain credentials.")
        return f"{parsed.scheme}://{parsed.netloc}"

    def effective_realtime_database_url(self) -> str:
        """Return the direct database URL used by PostgreSQL LISTEN/NOTIFY.

        Neon uses ``-pooler`` hostnames for transaction-pooled connections,
        where session-level LISTEN state is unsupported. When no explicit
        override is supplied, convert that documented Neon hostname shape to
        its direct endpoint while retaining the same credential and database.
        Other providers can set ``REALTIME_DATABASE_URL`` explicitly.
        """
        application = make_url(self.database_url)
        if application.get_backend_name() != "postgresql":
            if self.realtime_database_url:
                raise RuntimeError(
                    "REALTIME_DATABASE_URL is only valid with PostgreSQL."
                )
            return self.database_url

        realtime = make_url(
            self.realtime_database_url or self.database_url
        )
        if realtime.get_backend_name() != "postgresql":
            raise RuntimeError(
                "REALTIME_DATABASE_URL must use PostgreSQL."
            )
        if realtime.database != application.database:
            raise RuntimeError(
                "REALTIME_DATABASE_URL must target the application database."
            )
        if realtime.username != application.username:
            raise RuntimeError(
                "REALTIME_DATABASE_URL must use the application database user."
            )
        if (
            self.realtime_database_url is None
            and realtime.host
            and "-pooler." in realtime.host
        ):
            realtime = realtime.set(
                host=realtime.host.replace("-pooler.", ".", 1)
            )
        return realtime.render_as_string(hide_password=False)

    def validate_production(self) -> None:
        """Raise if security-sensitive defaults are still set in production."""
        parsed = urlsplit(self.public_origin())
        self.effective_realtime_database_url()
        rate_values = {
            "AUTH_RATE_LIMIT": self.auth_rate_limit,
            "AUTH_RATE_WINDOW_SECONDS": self.auth_rate_window_seconds,
            "ACTION_RATE_LIMIT": self.action_rate_limit,
            "ACTION_RATE_WINDOW_SECONDS": self.action_rate_window_seconds,
        }
        invalid_rate_values = [
            name for name, value in rate_values.items() if value <= 0
        ]
        if invalid_rate_values:
            raise RuntimeError(
                "Rate-limit settings must be positive: "
                + ", ".join(invalid_rate_values)
            )
        if not 16_384 <= self.max_request_body_bytes <= 10_485_760:
            raise RuntimeError(
                "MAX_REQUEST_BODY_BYTES must be between 16384 and 10485760."
            )
        if not 7 <= self.deletion_recovery_days <= 90:
            raise RuntimeError(
                "DELETION_RECOVERY_DAYS must be between 7 and 90."
            )
        if not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}",
            self.observability_environment(),
        ):
            raise RuntimeError(
                "DEPLOYMENT_ENVIRONMENT must be a simple 1-63 character label."
            )
        metrics_token = self.metrics_bearer_token_value()
        if metrics_token is not None and len(metrics_token) < 32:
            raise RuntimeError(
                "METRICS_BEARER_TOKEN must contain at least 32 characters."
            )
        if not 0 <= self.otel_trace_sample_ratio <= 1:
            raise RuntimeError(
                "OTEL_TRACE_SAMPLE_RATIO must be between 0 and 1."
            )
        for name, raw_url in {
            "OTEL_EXPORTER_OTLP_ENDPOINT": self.otel_exporter_otlp_endpoint,
            "SENTRY_DSN": self.sentry_dsn_value(),
        }.items():
            if not raw_url:
                continue
            observability_url = urlsplit(raw_url)
            if (
                observability_url.scheme not in {"http", "https"}
                or not observability_url.netloc
            ):
                raise RuntimeError(
                    f"{name} must be an absolute HTTP(S) URL."
                )
            if name == "OTEL_EXPORTER_OTLP_ENDPOINT" and (
                observability_url.username or observability_url.password
            ):
                raise RuntimeError(
                    "OTEL_EXPORTER_OTLP_ENDPOINT must not contain credentials; "
                    "use OTEL_EXPORTER_OTLP_HEADERS."
                )
        if self.local_auth_enabled and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise RuntimeError(
                "LOCAL_AUTH_ENABLED is restricted to loopback FRONTEND_URL "
                "origins. Use a configured identity provider for hosted use."
            )
        if not self.is_production():
            return
        for name, raw_url in {
            "OTEL_EXPORTER_OTLP_ENDPOINT": self.otel_exporter_otlp_endpoint,
            "SENTRY_DSN": self.sentry_dsn_value(),
        }.items():
            if raw_url and urlsplit(raw_url).scheme != "https":
                raise RuntimeError(f"{name} must use HTTPS in production.")
        if self.local_auth_enabled:
            raise RuntimeError(
                "LOCAL_AUTH_ENABLED must be false in production."
            )
        if (
            self.jwt_secret == "dev-jwt-secret-change-me"
            or len(self.jwt_secret) < 32
        ):
            raise RuntimeError(
                "JWT_SECRET must contain at least 32 characters and must not "
                "use the development default in production."
            )
        if not self.google_client_id or not self.google_client_secret:
            raise RuntimeError(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be configured "
                "in production."
            )
        if self.migration_mode != "check":
            raise RuntimeError(
                "MIGRATION_MODE must be 'check' in production. Run a single "
                "`python -m api.migrations upgrade --backup-confirmed` job "
                "before starting application instances."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
