"""Project-only collaboration across independent workspaces."""

from datetime import timedelta
import pytest
from sqlmodel import Session, select

from api.api_keys import issue_api_key
from api.auth import _verify_api_key_record, verify_api_key
from api.events import Event
from api.models.entities import (
    ApiKey,
    ApiKeyProject,
    ProjectMembership,
    ProjectInvitation,
    WorkspaceMembership,
    Project,
    BrowserSession,
)
from api.models.enums import SSEAction, WorkspaceRole
from api.routes.events import StreamAuthorization, can_receive_event
from api.mcp_oauth import active_key
from api.utils.time import utcnow
from api.tests.test_tenancy import _create_graph, _headers


def share(client, project_id, role="EDITOR"):
    response = client.post(
        f"/api/v1/projects/{project_id}/invitations",
        headers=_headers("alice"),
        json={"email": "bob@example.com", "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


def accept(client, invitation, identity="bob"):
    return client.post(
        "/api/v1/project-invitations/accept",
        headers=_headers(identity),
        json={"token": invitation["token"]},
    )


def test_direct_membership_isolates_both_workspaces_and_all_descendants(
    multi_user_client, engine
):
    client, users = multi_user_client
    shared = _create_graph(client, "alice")
    private = _create_graph(client, "alice")
    personal = _create_graph(client, "bob")
    invitation = share(client, shared["project"])
    assert accept(client, invitation).status_code == 200
    rows = client.get("/api/v1/projects", headers=_headers("bob")).json()
    assert {row["id"] for row in rows} == {shared["project"], personal["project"]}
    shared_row = next(row for row in rows if row["id"] == shared["project"])
    assert shared_row["can_edit"] and shared_row["can_leave"]
    assert not shared_row["can_manage_access"] and not shared_row["can_delete_project"]
    assert shared_row["access_source"] == "project"
    assert (
        client.get(
            f'/api/v1/projects/{private["project"]}', headers=_headers("bob")
        ).status_code
        == 404
    )
    assert (
        client.get(
            f'/api/v1/projects/{personal["project"]}', headers=_headers("alice")
        ).status_code
        == 404
    )
    for path in [
        f'projects/{shared["project"]}/control-room',
        f'subprojects/{shared["subproject"]}',
        f'tickets/{shared["ticket"]}',
        f'knowledge/{shared["node"]}',
        f'projects/{shared["project"]}/knowledge/proposals',
        f'agent/sessions/{shared["session"]}',
        f'agent/context/{shared["subproject"]}',
    ]:
        assert (
            client.get("/api/v1/" + path, headers=_headers("bob")).status_code == 200
        ), path
    assert (
        client.patch(
            f'/api/v1/tickets/{shared["ticket"]}',
            headers=_headers("bob"),
            json={"title": "Shared edit"},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f'/api/v1/projects/{shared["project"]}', headers=_headers("bob")
        ).status_code
        == 404
    )
    assert (
        client.get(
            f'/api/v1/projects/{shared["project"]}/invitations', headers=_headers("bob")
        ).status_code
        == 404
    )
    with Session(engine) as session:
        assert (
            session.exec(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == shared["workspace"],
                    WorkspaceMembership.user_id == users["bob"].id,
                )
            ).first()
            is None
        )
        auth = StreamAuthorization(users["bob"].id, None)
        assert can_receive_event(
            session,
            auth,
            Event(
                SSEAction.TICKET_UPDATED,
                "ticket",
                shared["ticket"],
                workspace_id=shared["workspace"],
                project_id=shared["project"],
            ),
        )
        assert not can_receive_event(
            session,
            auth,
            Event(
                SSEAction.TICKET_UPDATED,
                "ticket",
                private["ticket"],
                workspace_id=private["workspace"],
                project_id=private["project"],
            ),
        )


def test_invitation_is_bound_single_use_expiring_and_revocable(
    multi_user_client, engine
):
    client, _ = multi_user_client
    graph = _create_graph(client, "alice")
    invitation = share(client, graph["project"])
    assert accept(client, invitation, "alice").status_code == 404
    with Session(engine) as session:
        row = session.get(ProjectInvitation, invitation["id"])
        assert row.token_hash != invitation["token"] and len(row.token_hash) == 64
    preview = client.post(
        "/api/v1/project-invitations/preview",
        headers=_headers("bob"),
        json={"token": invitation["token"]},
    )
    assert preview.json()["project_id"] == graph["project"]
    assert accept(client, invitation).status_code == 200
    assert accept(client, invitation).status_code == 404
    graph2 = _create_graph(client, "alice")
    expired = share(client, graph2["project"])
    with Session(engine) as session:
        row = session.get(ProjectInvitation, expired["id"])
        row.created_at = utcnow() - timedelta(days=10)
        row.expires_at = utcnow() - timedelta(days=1)
        session.add(row)
        session.commit()
    assert accept(client, expired).status_code == 404
    revoked = share(client, graph2["project"])
    assert (
        client.delete(
            f'/api/v1/projects/{graph2["project"]}/invitations/{revoked["id"]}',
            headers=_headers("alice"),
        ).status_code
        == 204
    )
    assert accept(client, revoked).status_code == 404


def test_viewer_and_revocation_preserve_personal_access_and_sessions(
    multi_user_client, engine
):
    client, users = multi_user_client
    shared = _create_graph(client, "alice")
    personal = _create_graph(client, "bob")
    assert accept(client, share(client, shared["project"], "VIEWER")).status_code == 200
    assert (
        client.patch(
            f'/api/v1/tickets/{shared["ticket"]}',
            headers=_headers("bob"),
            json={"title": "Denied"},
        ).status_code
        == 404
    )
    with Session(engine) as session:
        session.add(
            BrowserSession(
                id="bob-session",
                user_id=users["bob"].id,
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        session.commit()
    assert (
        client.delete(
            f'/api/v1/projects/{shared["project"]}/membership', headers=_headers("bob")
        ).status_code
        == 204
    )
    assert (
        client.get(
            f'/api/v1/projects/{shared["project"]}', headers=_headers("bob")
        ).status_code
        == 404
    )
    assert (
        client.get(
            f'/api/v1/projects/{personal["project"]}', headers=_headers("bob")
        ).status_code
        == 200
    )
    with Session(engine) as session:
        assert session.get(BrowserSession, "bob-session").revoked_at is None


def test_guest_credentials_fail_closed_and_revoke_with_membership(
    multi_user_client, engine
):
    client, users = multi_user_client
    shared = _create_graph(client, "alice")
    personal = _create_graph(client, "bob")
    accept(client, share(client, shared["project"]))
    with Session(engine) as session:
        key, raw = issue_api_key(
            session,
            user_id=users["bob"].id,
            workspace_id=shared["workspace"],
            name="Shared agent",
            project_ids=[shared["project"]],
        )
        key_id = key.id
        own, own_raw = issue_api_key(
            session,
            user_id=users["bob"].id,
            workspace_id=personal["workspace"],
            name="Personal agent",
        )
        assert key.resource_mode == "PROJECTS"
        assert _verify_api_key_record(raw, session)
        assert active_key(session, key.id)
        assert verify_api_key(raw, session) is None
        with pytest.raises(ValueError):
            issue_api_key(
                session,
                user_id=users["bob"].id,
                workspace_id=shared["workspace"],
                name="Too broad",
            )
    assert (
        client.patch(
            f'/api/v1/projects/{shared["project"]}/members/{users["bob"].id}',
            headers=_headers("alice"),
            json={"role": "VIEWER"},
        ).status_code
        == 200
    )
    with Session(engine) as session:
        assert _verify_api_key_record(raw, session) is None
        assert active_key(session, key_id) is None
        assert _verify_api_key_record(own_raw, session)
        read_key, read_raw = issue_api_key(
            session,
            user_id=users["bob"].id,
            workspace_id=shared["workspace"],
            name="Read shared",
            project_ids=[shared["project"]],
            scopes=["read"],
        )
        restriction = session.get(ApiKeyProject, (read_key.id, shared["project"]))
        session.delete(restriction)
        session.commit()
        assert _verify_api_key_record(read_raw, session) is None


def test_inherited_access_is_deduplicated_and_survives_direct_removal(
    multi_user_client, engine
):
    client, users = multi_user_client
    graph = _create_graph(client, "alice")
    accept(client, share(client, graph["project"], "VIEWER"))
    with Session(engine) as session:
        session.add(
            WorkspaceMembership(
                workspace_id=graph["workspace"],
                user_id=users["bob"].id,
                role=WorkspaceRole.MEMBER,
            )
        )
        session.commit()
    rows = client.get("/api/v1/projects", headers=_headers("bob")).json()
    assert (
        len(rows) == 1
        and rows[0]["can_edit"]
        and rows[0]["access_source"] == "workspace"
    )
    assert (
        client.delete(
            f'/api/v1/projects/{graph["project"]}/membership', headers=_headers("bob")
        ).status_code
        == 204
    )
    assert (
        client.get(
            f'/api/v1/projects/{graph["project"]}', headers=_headers("bob")
        ).status_code
        == 200
    )


def test_attribution_and_project_deletion_cleanup(multi_user_client, engine):
    client, users = multi_user_client
    graph = _create_graph(client, "alice")
    accept(client, share(client, graph["project"]))
    row = client.post(
        f'/api/v1/tickets/{graph["ticket"]}/comments',
        headers=_headers("bob"),
        json={"author": "AGENT", "content": "From Bob"},
    ).json()
    assert (
        row["author"] == "HUMAN"
        and row["actor_user_id"] == users["bob"].id
        and row["actor_name"] == "Bob"
    )
    assert (
        client.delete(
            f'/api/v1/projects/{graph["project"]}', headers=_headers("alice")
        ).status_code
        == 204
    )
    with Session(engine) as session:
        assert not session.exec(select(ProjectMembership)).all()
        assert not session.exec(select(ProjectInvitation)).all()
