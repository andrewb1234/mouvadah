"""PostgreSQL-only migration regressions that SQLite cannot represent."""

from __future__ import annotations

import asyncio
import base64
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from api.authorization import require_project, require_workspace
from api.backup import (
    create_backup,
    reset_restore_drill_target,
    restore_backup,
)
from api.config import get_settings
from api.migrations.runtime import (
    assert_database_current,
    assert_schema_matches_metadata,
    upgrade_database,
)
from api.events import Event, EventBroadcaster
from api.models.entities import (
    AuditLog,
    Project,
    Subproject,
    Ticket,
    User,
    Workspace,
    WorkspaceMembership,
)
from api.models.enums import (
    ActorRole,
    AuditAction,
    SSEAction,
    WorkspaceRole,
)
from api.routes.tickets import _claim_ticket_atomic
from api.routes.workspaces import create_workspace_invitation
from api.schemas import WorkspaceInvitationCreate
from api.utils.time import utcnow


@pytest.fixture
def postgres_engine():
    raw_url = os.environ.get("POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("POSTGRES_TEST_URL is not configured")
    parsed = make_url(raw_url)
    if (
        parsed.get_backend_name() != "postgresql"
        or parsed.database != "taskable_test"
        or parsed.host not in {"localhost", "127.0.0.1"}
    ):
        pytest.fail(
            "PostgreSQL integration tests require the dedicated loopback "
            "database named taskable_test."
        )

    engine = create_engine(raw_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    try:
        yield engine
    finally:
        engine.dispose()


def test_legacy_audit_enum_is_repaired_before_claim_audit(
    postgres_engine,
) -> None:
    upgrade_database(
        postgres_engine,
        "0004_session_key_security",
    )

    # Reproduce the exact pre-Alembic production shape: the native enum lacks
    # the two coordination actions even though the table/revision is current.
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE auditlog ALTER COLUMN action TYPE text "
                "USING action::text"
            )
        )
        connection.execute(text("DROP TYPE auditaction"))
        connection.execute(
            text(
                "CREATE TYPE auditaction AS ENUM "
                "('STATUS_UPDATE', 'CONTENT_UPDATE', 'MR_LINKED')"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE auditlog ALTER COLUMN action TYPE auditaction "
                "USING action::auditaction"
            )
        )

    with pytest.raises(RuntimeError, match="auditaction"):
        assert_schema_matches_metadata(postgres_engine)

    upgrade_database(postgres_engine, backup_confirmed=True)
    assert_database_current(postgres_engine)
    assert_schema_matches_metadata(postgres_engine)

    with postgres_engine.connect() as connection:
        labels = list(
            connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum "
                    "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                    "WHERE pg_type.typname = 'auditaction' "
                    "ORDER BY enumsortorder"
                )
            ).scalars()
        )
    assert labels == [
        "STATUS_UPDATE",
        "CONTENT_UPDATE",
        "MR_LINKED",
        "TICKET_CLAIMED",
        "TICKET_REQUEUED",
    ]

    with Session(postgres_engine) as session:
        project = Project(name="PostgreSQL claim")
        session.add(project)
        session.flush()
        subproject = Subproject(
            project_id=project.id,  # type: ignore[arg-type]
            name="Enum regression",
        )
        session.add(subproject)
        session.flush()
        ticket = Ticket(
            subproject_id=subproject.id,  # type: ignore[arg-type]
            title="Claim without a 500",
        )
        session.add(ticket)
        session.commit()
        session.refresh(ticket)

        assert _claim_ticket_atomic(
            session,
            ticket.id,  # type: ignore[arg-type]
            "postgres-worker",
            utcnow(),
        )
        session.add(
            AuditLog(
                ticket_id=ticket.id,  # type: ignore[arg-type]
                action=AuditAction.TICKET_CLAIMED,
                actor=ActorRole.AGENT,
            )
        )
        session.add(
            AuditLog(
                ticket_id=ticket.id,  # type: ignore[arg-type]
                action=AuditAction.TICKET_REQUEUED,
                actor=ActorRole.AGENT,
            )
        )
        session.commit()

        session.refresh(ticket)
        assert ticket.claimed_by == "postgres-worker"
        assert ticket.status.value == "IN_PROGRESS"


def test_postgres_membership_migration_enforces_single_owner(
    postgres_engine,
) -> None:
    upgrade_database(postgres_engine)
    assert_database_current(postgres_engine)
    assert_schema_matches_metadata(postgres_engine)

    with Session(postgres_engine) as session:
        first = User(
            google_id="postgres-owner-one",
            email="postgres-owner-one@example.com",
            name="Owner One",
        )
        second = User(
            google_id="postgres-owner-two",
            email="postgres-owner-two@example.com",
            name="Owner Two",
        )
        workspace = Workspace(
            name="Single owner",
            slug="postgres-single-owner",
        )
        session.add_all([first, second, workspace])
        session.flush()
        session.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=first.id,
                role=WorkspaceRole.OWNER,
            )
        )
        session.commit()
        session.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=second.id,
                role=WorkspaceRole.OWNER,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_cross_tenant_authorization_fails_before_workspace_lock(
    postgres_engine,
) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session:
        owner = User(
            google_id="lock-preflight-owner",
            email="lock-preflight-owner@example.com",
            name="Lock Preflight Owner",
        )
        outsider = User(
            google_id="lock-preflight-outsider",
            email="lock-preflight-outsider@example.com",
            name="Lock Preflight Outsider",
        )
        workspace = Workspace(
            name="Lock preflight",
            slug="lock-preflight",
        )
        session.add_all([owner, outsider, workspace])
        session.flush()
        session.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=owner.id,
                role=WorkspaceRole.OWNER,
            )
        )
        project = Project(
            workspace_id=workspace.id,
            name="Lock preflight project",
        )
        session.add(project)
        session.commit()
        workspace_id = workspace.id
        project_id = project.id
        outsider_id = outsider.id

    raw_url = make_url(os.environ["POSTGRES_TEST_URL"])
    child_engine = create_engine(
        raw_url.update_query_dict(
            {
                "application_name": "cross-tenant-lock-preflight",
                "options": "-c statement_timeout=500",
            }
        )
    )
    try:
        with Session(postgres_engine) as blocker:
            blocker.exec(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            ).one()
            with Session(child_engine) as child_session:
                child_user = child_session.get(User, outsider_id)
                assert child_user is not None
                with pytest.raises(HTTPException) as workspace_error:
                    require_workspace(
                        child_session,
                        child_user,
                        workspace_id,  # type: ignore[arg-type]
                        write=True,
                    )
                assert workspace_error.value.status_code == 404

                with pytest.raises(HTTPException) as project_error:
                    require_project(
                        child_session,
                        child_user,
                        project_id,  # type: ignore[arg-type]
                        write=True,
                    )
                assert project_error.value.status_code == 404
    finally:
        child_engine.dispose()


def test_owner_authorization_is_rechecked_after_workspace_lock(
    postgres_engine,
) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session:
        old_owner = User(
            google_id="queued-old-owner",
            email="queued-old-owner@example.com",
            name="Queued Old Owner",
        )
        new_owner = User(
            google_id="queued-new-owner",
            email="queued-new-owner@example.com",
            name="Queued New Owner",
        )
        workspace = Workspace(
            name="Queued authorization",
            slug="queued-authorization",
        )
        session.add_all([old_owner, new_owner, workspace])
        session.flush()
        session.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=old_owner.id,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=new_owner.id,
                    role=WorkspaceRole.ADMIN,
                ),
            ]
        )
        session.commit()
        old_owner_id = old_owner.id
        new_owner_id = new_owner.id
        workspace_id = workspace.id

    raw_url = make_url(os.environ["POSTGRES_TEST_URL"])
    child_engine = create_engine(
        raw_url.update_query_dict(
            {"application_name": "membership-lock-regression"}
        )
    )

    def queued_invitation() -> int:
        with Session(child_engine) as child_session:
            child_user = child_session.get(User, old_owner_id)
            assert child_user is not None
            try:
                create_workspace_invitation(
                    workspace_id,  # type: ignore[arg-type]
                    WorkspaceInvitationCreate(
                        email="queued@example.com",
                        role=WorkspaceRole.MEMBER,
                    ),
                    child_session,
                    child_user,
                )
            except HTTPException as exc:
                return exc.status_code
            return 201

    try:
        with Session(postgres_engine) as blocker:
            locked_workspace = blocker.exec(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            ).one()
            assert locked_workspace.id == workspace_id
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(queued_invitation)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with postgres_engine.connect() as connection:
                        wait_event = connection.execute(
                            text(
                                "SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE application_name = "
                                "'membership-lock-regression' "
                                "AND state = 'active'"
                            )
                        ).scalar_one_or_none()
                    if wait_event == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail(
                        "Queued owner request did not reach the workspace lock."
                    )

                old_membership = blocker.exec(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id == old_owner_id,
                    )
                ).one()
                new_membership = blocker.exec(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id == new_owner_id,
                    )
                ).one()
                old_membership.role = WorkspaceRole.ADMIN
                blocker.add(old_membership)
                blocker.flush()
                new_membership.role = WorkspaceRole.OWNER
                blocker.add(new_membership)
                blocker.commit()

                assert future.result(timeout=5) == 404
    finally:
        child_engine.dispose()


def test_project_write_role_is_rechecked_after_workspace_lock(
    postgres_engine,
) -> None:
    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session:
        owner = User(
            google_id="project-lock-owner",
            email="project-lock-owner@example.com",
            name="Project Lock Owner",
        )
        writer = User(
            google_id="project-lock-writer",
            email="project-lock-writer@example.com",
            name="Project Lock Writer",
        )
        workspace = Workspace(
            name="Project lock",
            slug="project-lock",
        )
        session.add_all([owner, writer, workspace])
        session.flush()
        session.add_all(
            [
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=owner.id,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMembership(
                    workspace_id=workspace.id,
                    user_id=writer.id,
                    role=WorkspaceRole.MEMBER,
                ),
            ]
        )
        project = Project(
            workspace_id=workspace.id,
            name="Queued project write",
        )
        session.add(project)
        session.commit()
        workspace_id = workspace.id
        writer_id = writer.id
        project_id = project.id

    raw_url = make_url(os.environ["POSTGRES_TEST_URL"])
    child_engine = create_engine(
        raw_url.update_query_dict(
            {"application_name": "project-lock-regression"}
        )
    )

    def queued_project_write() -> int:
        with Session(child_engine) as child_session:
            child_user = child_session.get(User, writer_id)
            assert child_user is not None
            try:
                require_project(
                    child_session,
                    child_user,
                    project_id,  # type: ignore[arg-type]
                    write=True,
                )
            except HTTPException as exc:
                return exc.status_code
            return 200

    try:
        with Session(postgres_engine) as blocker:
            blocker.exec(
                select(Workspace)
                .where(Workspace.id == workspace_id)
                .with_for_update()
            ).one()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(queued_project_write)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with postgres_engine.connect() as connection:
                        wait_event = connection.execute(
                            text(
                                "SELECT wait_event_type FROM pg_stat_activity "
                                "WHERE application_name = "
                                "'project-lock-regression' "
                                "AND state = 'active'"
                            )
                        ).scalar_one_or_none()
                    if wait_event == "Lock":
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail(
                        "Queued project write did not reach the workspace lock."
                    )

                membership = blocker.exec(
                    select(WorkspaceMembership).where(
                        WorkspaceMembership.workspace_id == workspace_id,
                        WorkspaceMembership.user_id == writer_id,
                    )
                ).one()
                membership.role = WorkspaceRole.VIEWER
                blocker.add(membership)
                blocker.commit()

                assert future.result(timeout=5) == 404
    finally:
        child_engine.dispose()


def test_postgres_encrypted_backup_restores_into_fresh_database(
    postgres_engine,
    tmp_path,
    monkeypatch,
) -> None:
    raw_url = os.environ["POSTGRES_TEST_URL"]
    parsed = make_url(raw_url)
    restore_database = "mouvadah_restore_drill_test"
    restore_url = parsed.set(database=restore_database).render_as_string(
        hide_password=False
    )
    admin_url = parsed.set(database="postgres").render_as_string(
        hide_password=False
    )
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(
            text(
                "DROP DATABASE IF EXISTS mouvadah_restore_drill_test "
                "WITH (FORCE)"
            )
        )
        connection.execute(
            text("CREATE DATABASE mouvadah_restore_drill_test")
        )

    upgrade_database(postgres_engine)
    with Session(postgres_engine) as session:
        user = User(
            google_id="postgres-recovery-user",
            email="postgres-recovery@example.com",
            name="PostgreSQL Recovery",
        )
        workspace = Workspace(
            name="PostgreSQL recovery",
            slug="postgresql-recovery",
        )
        session.add_all([user, workspace])
        session.flush()
        session.add(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user.id,
                role=WorkspaceRole.OWNER,
            )
        )
        session.add(
            Project(
                workspace_id=workspace.id,
                name="Restored PostgreSQL project",
            )
        )
        session.commit()

    backup_path = tmp_path / "postgres.mouvadah-backup"
    encryption_key = base64.b64encode(b"p" * 32).decode("ascii")
    host_override = os.environ.get("POSTGRES_TOOL_HOST_OVERRIDE")
    try:
        manifest = create_backup(
            raw_url,
            backup_path,
            encryption_key=encryption_key,
            postgres_host_override=host_override,
        )
        assert manifest["authenticated"]["archive_format"] == (
            "postgresql-custom"
        )

        restore_backup(
            backup_path,
            restore_url,
            confirm_database=restore_database,
            encryption_key=encryption_key,
            postgres_host_override=host_override,
        )
        restored_engine = create_engine(restore_url)
        try:
            assert_database_current(restored_engine)
            assert_schema_matches_metadata(restored_engine)
            with Session(restored_engine) as session:
                names = list(
                    session.exec(
                        select(Project.name).order_by(Project.id)
                    ).all()
                )
            assert names == ["Restored PostgreSQL project"]
        finally:
            restored_engine.dispose()

        monkeypatch.setenv("DATABASE_URL", raw_url)
        get_settings.cache_clear()
        restored_engine = create_engine(restore_url)
        try:
            with restored_engine.begin() as connection:
                connection.execute(
                    text("CREATE SCHEMA restored_customer_data")
                )
                connection.execute(
                    text(
                        "CREATE TABLE restored_customer_data.private_rows "
                        "(id integer primary key, value text)"
                    )
                )
        finally:
            restored_engine.dispose()
        reset_restore_drill_target(
            restore_url,
            confirm_database=restore_database,
        )
        scrubbed_engine = create_engine(restore_url)
        try:
            assert inspect(scrubbed_engine).get_table_names() == []
            assert "restored_customer_data" not in (
                inspect(scrubbed_engine).get_schema_names()
            )
        finally:
            scrubbed_engine.dispose()
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "DROP DATABASE IF EXISTS mouvadah_restore_drill_test "
                    "WITH (FORCE)"
                )
            )
        admin_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_realtime_fans_out_across_process_boundaries(
    postgres_engine,
) -> None:
    raw_url = os.environ["POSTGRES_TEST_URL"]
    first = EventBroadcaster()
    second = EventBroadcaster()
    event = Event(
        action=SSEAction.TICKET_UPDATED,
        entity="ticket",
        entity_id=73,
        parent_id=11,
        workspace_id=5,
    )
    await first.start(raw_url)
    await second.start(raw_url)
    try:
        async with second.subscribe() as queue:
            await first.publish(event)
            received = await asyncio.wait_for(queue.get(), timeout=3.0)
        assert received == event
        assert first.status() == "healthy"
        assert second.status() == "healthy"
    finally:
        await first.stop()
        await second.stop()


def test_hosted_mcp_oauth_grants_and_refresh_race(postgres_engine, monkeypatch):
    """Prove the OAuth flow and single-use refresh transaction on PostgreSQL."""
    from fastapi.testclient import TestClient
    from api import database, events
    from api.main import app
    from api.tests.test_hosted_mcp import ORIGIN, connect, rpc

    upgrade_database(postgres_engine)
    assert_schema_matches_metadata(postgres_engine)
    monkeypatch.setenv("FRONTEND_URL", ORIGIN)
    get_settings.cache_clear()
    original_engine = database.engine
    database._set_engine(postgres_engine)
    events.reset_broadcaster()
    try:
        with Session(postgres_engine) as session:
            user = User(google_id="postgres-mcp-user", email="pg-mcp@example.invalid", name="PG MCP")
            session.add(user); session.commit(); session.refresh(user)
        with TestClient(app) as client:
            registered, tokens, _ = connect(client, postgres_engine, user)
            assert rpc(client, tokens["access_token"], "tools/list").status_code == 200
            form = {"grant_type": "refresh_token", "client_id": registered["client_id"],
                    "refresh_token": tokens["refresh_token"], "resource": ORIGIN + "/mcp"}
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: client.post("/oauth/token", data=form), range(2)))
            assert sorted(r.status_code for r in responses) == [200, 400]
            issued = next(r.json() for r in responses if r.status_code == 200)
            assert rpc(client, issued["access_token"], "tools/list").status_code == 401
    finally:
        database._set_engine(original_engine)
