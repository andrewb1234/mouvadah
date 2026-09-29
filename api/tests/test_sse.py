"""SSE broadcaster — intercept internal events during state mutations."""

from __future__ import annotations

import asyncio
import json

import pytest

from api.events import (
    Event,
    EventBroadcaster,
    ResyncSignal,
    get_broadcaster,
    reset_broadcaster,
)
from api.models.enums import SSEAction
from api.routes.events import stream_events


@pytest.mark.asyncio
async def test_broadcaster_does_not_resolve_application_database(monkeypatch):
    from api import database
    from sqlmodel import create_engine

    empty_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(database, "engine", empty_engine)
    broadcaster = EventBroadcaster()
    event = Event(
        action=SSEAction.TICKET_UPDATED,
        entity="ticket",
        entity_id=73,
        parent_id=11,
        workspace_id=5,
    )
    try:
        async with broadcaster.subscribe() as queue:
            await broadcaster.publish(event)
            assert queue.get_nowait() == event
    finally:
        empty_engine.dispose()


@pytest.mark.asyncio
async def test_broadcaster_delivers_to_subscriber():
    reset_broadcaster()
    broadcaster = get_broadcaster()

    received: list[Event] = []

    async def listener():
        async with broadcaster.subscribe() as queue:
            received.append(await asyncio.wait_for(queue.get(), timeout=1.0))

    listener_task = asyncio.create_task(listener())
    await asyncio.sleep(0)  # let listener register
    await broadcaster.publish(
        Event(action=SSEAction.TICKET_UPDATED, entity="ticket", entity_id=1)
    )
    await listener_task

    assert received[0].action is SSEAction.TICKET_UPDATED
    assert received[0].entity_id == 1


@pytest.mark.asyncio
async def test_slow_subscriber_gets_resync_instead_of_stale_deltas():
    broadcaster = EventBroadcaster(_queue_maxsize=1)
    first = Event(
        action=SSEAction.TICKET_UPDATED,
        entity="ticket",
        entity_id=1,
        workspace_id=7,
    )
    second = Event(
        action=SSEAction.TICKET_UPDATED,
        entity="ticket",
        entity_id=2,
        workspace_id=7,
    )

    async with broadcaster.subscribe() as queue:
        await broadcaster.publish(first)
        await broadcaster.publish(second)
        item = queue.get_nowait()

    assert isinstance(item, ResyncSignal)
    assert item.reason == "subscriber_overflow"
    payload = json.loads(item.to_json())
    assert payload["action"] == "SYNC_REQUIRED"
    assert payload["workspace_id"] is None


@pytest.mark.asyncio
async def test_sqlite_broadcaster_stays_local_and_reports_status():
    broadcaster = EventBroadcaster()

    await broadcaster.start("sqlite:///:memory:")
    assert broadcaster.status() == "local"
    await broadcaster.stop()
    assert broadcaster.status() == "not_started"


def test_event_transport_roundtrip_rejects_invalid_payloads():
    event = Event(
        action=SSEAction.KNOWLEDGE_NODE_UPDATED,
        entity="knowledge_node",
        entity_id=11,
        parent_id=3,
        workspace_id=2,
    )

    assert Event.from_dict(event.to_dict()) == event
    with pytest.raises(ValueError, match="entity_id"):
        Event.from_dict(
            {
                **event.to_dict(),
                "entity_id": "11",
            }
        )


@pytest.mark.asyncio
async def test_stream_starts_with_explicit_resync(test_user):
    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    response = await stream_events(ConnectedRequest(), test_user)
    iterator = response.body_iterator
    first = await anext(iterator)
    await iterator.aclose()

    assert first["event"] == "ready"
    payload = json.loads(first["data"])
    assert payload["action"] == "SYNC_REQUIRED"
    assert payload["reason"] == "connected"


def test_ticket_mutation_publishes_event(client, session):
    """State mutations must push an SSEAction.TICKET_UPDATED event."""
    captured: list[Event] = []
    broadcaster = get_broadcaster()
    original_publish = broadcaster.publish

    async def capturing_publish(event: Event) -> None:
        captured.append(event)
        await original_publish(event)

    broadcaster.publish = capturing_publish  # type: ignore[assignment]
    try:
        project = client.post("/api/v1/projects", json={"name": "P"}).json()
        sp = client.post(
            f"/api/v1/projects/{project['id']}/subprojects",
            json={"name": "S"},
        ).json()
        ticket = client.post(
            f"/api/v1/subprojects/{sp['id']}/tickets", json={"title": "T"}
        ).json()
        client.patch(
            f"/api/v1/tickets/{ticket['id']}", json={"status": "IN_PROGRESS"}
        )
        client.post(
            f"/api/v1/tickets/{ticket['id']}/comments",
            json={"content": "Shared update", "author": "HUMAN"},
        )
        client.delete(f"/api/v1/tickets/{ticket['id']}")
    finally:
        broadcaster.publish = original_publish  # type: ignore[assignment]

    actions = [e.action for e in captured]
    assert SSEAction.PROJECT_CREATED in actions
    assert SSEAction.SUBPROJECT_CREATED in actions
    assert SSEAction.TICKET_CREATED in actions
    assert SSEAction.TICKET_UPDATED in actions
    assert SSEAction.COMMENT_CREATED in actions
    assert SSEAction.TICKET_DELETED in actions
    assert all(event.project_id == project["id"] for event in captured)
