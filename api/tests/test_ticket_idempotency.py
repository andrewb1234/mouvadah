"""Retry safety after lost responses, edits, invalid requests and races."""

from concurrent.futures import ThreadPoolExecutor
import asyncio

import pytest
from fastapi import Response
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from api.models.entities import Ticket, Project, Subproject, User
from api.routes.subprojects import create_ticket
from api.schemas import TicketCreate


def test_shared_project_retry_rechecks_collaborator_write_access(multi_user_client, engine):
    from api.models.entities import ProjectMembership
    client, users = multi_user_client
    sid = make_subproject(client)
    with Session(engine) as session:
        project_id = session.get(Subproject, sid).project_id
        membership = ProjectMembership(project_id=project_id, user_id=users["bob"].id,
            role="EDITOR", created_by_user_id=users["alice"].id)
        session.add(membership); session.commit()
    payload = {"title": "Shared creation", "client_ref": "persisted-intent"}
    url = f"/api/v1/subprojects/{sid}/tickets"
    first = client.post(url, json=payload, headers={"x-test-user": "bob"})
    assert first.status_code == 201
    retry = client.post(url, json=payload, headers={"x-test-user": "bob"})
    assert retry.status_code == 200 and retry.json()["id"] == first.json()["id"]
    with Session(engine) as session:
        membership = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == users["bob"].id)).one()
        membership.role = "VIEWER"; session.add(membership); session.commit()
    assert client.post(url, json=payload, headers={"x-test-user": "bob"}).status_code == 404


def make_subproject(client):
    p = client.post('/api/v1/projects', json={'name': 'Retries'}).json()
    return client.post(f"/api/v1/projects/{p['id']}/subprojects", json={'name': 'Slice'}).json()['id']


def test_replay_returns_existing_after_edits_without_duplicate_event(client, monkeypatch):
    sid = make_subproject(client)
    events = []
    async def publish(event):
        events.append(event)
    from api.events import get_broadcaster
    monkeypatch.setattr(get_broadcaster(), 'publish', publish)
    payload = {'title': 'Original', 'client_ref': 'planner:1'}
    first = client.post(f'/api/v1/subprojects/{sid}/tickets', json=payload)
    assert first.status_code == 201
    tid = first.json()['id']
    assert first.json()['client_ref'] == 'planner:1'
    client.patch(f'/api/v1/tickets/{tid}', json={'title': 'Human edit'})
    replay = client.post(f'/api/v1/subprojects/{sid}/tickets', json=payload)
    assert replay.status_code == 200
    assert replay.json()['id'] == tid
    assert replay.json()['title'] == 'Human edit'
    assert len([e for e in events if e.action.value == 'TICKET_CREATED']) == 1
    conflict = client.post(f'/api/v1/subprojects/{sid}/tickets', json={**payload, 'title': 'Different'})
    assert conflict.status_code == 409
    assert len(client.get(f'/api/v1/subprojects/{sid}').json()['tickets']) == 1


def test_ref_is_scoped_and_optional(client):
    s1, s2 = make_subproject(client), make_subproject(client)
    ids = []
    for sid, ref in [(s1, 'same'), (s2, 'same'), (s1, 'different'), (s1, None), (s1, None)]:
        r = client.post(f'/api/v1/subprojects/{sid}/tickets', json={'title': 'New', 'client_ref': ref})
        assert r.status_code == 201
        ids.append(r.json()['id'])
    assert len(set(ids)) == 5


def test_invalid_dependencies_do_not_consume_key(client):
    sid = make_subproject(client)
    url = f'/api/v1/subprojects/{sid}/tickets'
    bad = client.post(url, json={'title': 'New', 'client_ref': 'retry', 'depends_on': [99999]})
    assert bad.status_code == 422
    assert client.post(url, json={'title': 'New', 'client_ref': 'retry'}).status_code == 201


def test_dependency_order_is_not_a_conflict(client):
    sid = make_subproject(client)
    url = f'/api/v1/subprojects/{sid}/tickets'
    deps = [client.post(url, json={'title': t}).json()['id'] for t in ['a', 'b']]
    p = {'title': 'New', 'client_ref': 'retry', 'depends_on': deps}
    assert client.post(url, json=p).status_code == 201
    assert client.post(url, json={**p, 'depends_on': deps[::-1]}).status_code == 200


@pytest.mark.parametrize('ref', ['', 'x' * 129])
def test_invalid_ref(client, ref):
    sid = make_subproject(client)
    assert client.post(f'/api/v1/subprojects/{sid}/tickets', json={'title': 'a', 'client_ref': ref}).status_code == 422


def test_replay_does_not_bypass_authorization(multi_user_client):
    client, _ = multi_user_client
    client.headers['X-Test-User'] = 'alice'
    sid = make_subproject(client)
    url = f'/api/v1/subprojects/{sid}/tickets'
    p = {'title': 'Private', 'client_ref': 'shared'}
    assert client.post(url, json=p).status_code == 201
    client.headers['X-Test-User'] = 'bob'
    assert client.post(url, json=p).status_code == 404


def exercise_concurrent_creates(engine, monkeypatch):
    # Separate database connections, never concurrent sessions on StaticPool.
    with Session(engine) as s:
        user = User(google_id='concurrent', email='race@example.test', name='Race')
        project = Project(name='Race')
        s.add_all([user, project]); s.commit(); s.refresh(user); s.refresh(project)
        sub = Subproject(project_id=project.id, name='Race')
        s.add(sub); s.commit(); s.refresh(sub)
        sid, uid = sub.id, user.id
    from api.routes import subprojects
    from api.events import get_broadcaster
    monkeypatch.setattr(subprojects, 'require_subproject', lambda s, u, i, **kw: s.get(Subproject, i))
    monkeypatch.setattr(subprojects, 'workspace_id_for_project', lambda s, p: None)
    async def publish(event):
        pass
    monkeypatch.setattr(get_broadcaster(), 'publish', publish)
    def create(_):
        with Session(engine) as s:
            response = Response()
            result = asyncio.run(create_ticket(sid, TicketCreate(title='Race', client_ref='same'), response, s, s.get(User, uid)))
            return result.id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    with Session(engine) as s:
        assert len(s.exec(select(Ticket).where(Ticket.subproject_id == sid)).all()) == 1


def test_concurrent_creates_use_database_uniqueness(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "race.db"}', connect_args={'check_same_thread': False, 'timeout': 20})
    SQLModel.metadata.create_all(engine)
    try:
        exercise_concurrent_creates(engine, monkeypatch)
    finally:
        engine.dispose()


async def test_mcp_preserves_key_and_accepts_replay(monkeypatch):
    import httpx
    from api.hosted_mcp import bridge
    calls = []
    async def request(method, path, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200, json={'id': 42, 'title': 'Replay', 'status': 'TODO', 'assignee': 'AGENT', 'subproject_id': 7})
    monkeypatch.setattr(bridge, '_request', request)
    result = await bridge.create_ticket(7, 'Replay', '', 'AGENT', client_ref='stable')
    assert calls[0]['client_ref'] == 'stable'
    assert result.startswith('Existing ticket #42')
