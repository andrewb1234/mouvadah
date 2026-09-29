"""Workspace-scoped realtime invalidation fan-out.

SQLite/local deployments use an in-process queue per subscriber. PostgreSQL
deployments additionally use LISTEN/NOTIFY over a direct connection so every
API process receives the same invalidation hints. Notifications are not a
durable business-event log: clients perform a full authorized resync on every
connect/reconnect and whenever a slow-subscriber queue overflows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeAlias

import psycopg2
from psycopg2.extensions import connection as PsycopgConnection
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from api.models.enums import SSEAction
from api.observability import (
    add_realtime_subscriber,
    record_realtime_event,
    record_realtime_reconnect,
    record_realtime_resync,
    set_realtime_health,
)

logger = logging.getLogger(__name__)

_CHANNEL = "mouvadah_realtime_v1"
_PROTOCOL_VERSION = 1
_MAX_NOTIFY_BYTES = 7_500


def _optional_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer or null.")
    return value


@dataclass(frozen=True)
class Event:
    """Content-free invalidation payload broadcast over the SSE stream."""

    action: SSEAction
    entity: str
    entity_id: int
    parent_id: int | None = None
    workspace_id: int | None = None
    project_id: int | None = None
    recipient_user_id: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "entity": self.entity,
            "entity_id": self.entity_id,
            "parent_id": self.parent_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "recipient_user_id": self.recipient_user_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: object) -> Event:
        if not isinstance(value, dict):
            raise ValueError("Realtime event must be a JSON object.")
        entity = value.get("entity")
        if not isinstance(entity, str) or not entity or len(entity) > 100:
            raise ValueError("Realtime event entity is invalid.")
        entity_id = _optional_int(
            value.get("entity_id"),
            field_name="entity_id",
        )
        if entity_id is None:
            raise ValueError("entity_id cannot be null.")
        return cls(
            action=SSEAction(value.get("action")),
            entity=entity,
            entity_id=entity_id,
            parent_id=_optional_int(
                value.get("parent_id"),
                field_name="parent_id",
            ),
            project_id=_optional_int(value.get("project_id"), field_name="project_id"),
            recipient_user_id=_optional_int(
                value.get("recipient_user_id"), field_name="recipient_user_id"
            ),
            workspace_id=_optional_int(
                value.get("workspace_id"),
                field_name="workspace_id",
            ),
        )


@dataclass(frozen=True)
class ResyncSignal:
    """Content-free instruction for a client to refetch authorized state."""

    reason: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "action": SSEAction.SYNC_REQUIRED.value,
                "entity": "system",
                "entity_id": 0,
                "parent_id": None,
                "workspace_id": None,
                "reason": self.reason,
            },
            separators=(",", ":"),
        )


QueueItem: TypeAlias = Event | ResyncSignal


def _listener_kwargs(database_url: str) -> dict[str, Any]:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("Realtime transport requires PostgreSQL.")
    values: dict[str, Any] = {
        "dbname": parsed.database,
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.host,
        "port": parsed.port,
        "application_name": "mouvadah-realtime-listener",
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    values.update(dict(parsed.query))
    return {key: value for key, value in values.items() if value is not None}


class _PostgresTransport:
    """Direct PostgreSQL listener plus a small publishing connection pool."""

    def __init__(
        self,
        database_url: str,
        *,
        origin_id: str,
        on_event: Callable[[Event], Awaitable[None]],
        on_resync: Callable[[str], Awaitable[None]],
    ) -> None:
        self._database_url = database_url
        self._origin_id = origin_id
        self._on_event = on_event
        self._on_resync = on_resync
        self._engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connection: PsycopgConnection | None = None
        self._reader_fd: int | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._publish_healthy = True

    @property
    def healthy(self) -> bool:
        return (
            self._connection is not None
            and not self._connection.closed
            and self._publish_healthy
        )

    def _open_listener(self) -> PsycopgConnection:
        connection = psycopg2.connect(**_listener_kwargs(self._database_url))
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f"LISTEN {_CHANNEL}")
        return connection

    def _attach(self, connection: PsycopgConnection) -> None:
        if self._loop is None:  # pragma: no cover - start invariant
            connection.close()
            raise RuntimeError("Realtime event loop is not initialized.")
        self._connection = connection
        self._reader_fd = connection.fileno()
        self._loop.add_reader(self._reader_fd, self._on_readable)

    async def start(self) -> None:
        if self._connection is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        self._stop_event.clear()
        connection = await asyncio.to_thread(self._open_listener)
        self._attach(connection)
        set_realtime_health(True)

    def _detach(self) -> None:
        if self._loop is not None and self._reader_fd is not None:
            self._loop.remove_reader(self._reader_fd)
        self._reader_fd = None
        connection = self._connection
        self._connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:  # pragma: no cover - driver defensive path
                logger.exception("Failed to close realtime listener.")

    def _schedule_reconnect(self) -> None:
        self._detach()
        if self._stopping or self._loop is None:
            return
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self._loop.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        delay = 1.0
        try:
            while not self._stopping:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=delay,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    connection = await asyncio.to_thread(self._open_listener)
                except Exception:
                    record_realtime_reconnect("failed")
                    set_realtime_health(False)
                    logger.exception("Realtime listener reconnect failed; retrying.")
                    delay = min(delay * 2, 30.0)
                    continue
                if self._stopping:
                    connection.close()
                    return
                self._attach(connection)
                record_realtime_reconnect("succeeded")
                set_realtime_health(True)
                await self._on_resync("transport_reconnected")
                logger.info("Realtime PostgreSQL listener reconnected.")
                return
        finally:
            self._reconnect_task = None

    def _decode_notification(self, payload: str) -> Event | None:
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("Realtime envelope must be an object.")
        if value.get("version") != _PROTOCOL_VERSION:
            raise ValueError("Unsupported realtime envelope version.")
        origin = value.get("origin")
        if not isinstance(origin, str) or not origin:
            raise ValueError("Realtime envelope origin is invalid.")
        if origin == self._origin_id:
            return None
        return Event.from_dict(value.get("event"))

    def _on_readable(self) -> None:
        connection = self._connection
        if connection is None or self._loop is None:
            return
        try:
            connection.poll()
            while connection.notifies:
                notification = connection.notifies.pop(0)
                try:
                    event = self._decode_notification(notification.payload)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    record_realtime_event("postgresql", "invalid")
                    logger.warning("Ignored an invalid realtime notification.")
                    continue
                if event is not None:
                    self._loop.create_task(self._on_event(event))
        except Exception:
            set_realtime_health(False)
            logger.exception("Realtime listener disconnected; scheduling reconnect.")
            self._schedule_reconnect()

    def _notify(self, payload: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": _CHANNEL, "payload": payload},
            )

    async def publish(self, event: Event) -> bool:
        payload = json.dumps(
            {
                "version": _PROTOCOL_VERSION,
                "origin": self._origin_id,
                "event": event.to_dict(),
            },
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > _MAX_NOTIFY_BYTES:
            record_realtime_event("postgresql", "oversize")
            logger.error("Realtime notification exceeded safe payload size.")
            self._publish_healthy = False
            return False
        try:
            await asyncio.to_thread(self._notify, payload)
        except Exception:
            record_realtime_event("postgresql", "failed")
            set_realtime_health(False)
            logger.exception(
                "Shared realtime publish failed after the application "
                "transaction committed."
            )
            self._publish_healthy = False
            return False
        self._publish_healthy = True
        record_realtime_event("postgresql", "published")
        set_realtime_health(True)
        return True

    async def stop(self) -> None:
        self._stopping = True
        self._stop_event.set()
        reconnect = self._reconnect_task
        self._reconnect_task = None
        if reconnect is not None:
            await reconnect
        self._detach()
        await asyncio.to_thread(self._engine.dispose)
        set_realtime_health(False)
        self._loop = None


@dataclass
class EventBroadcaster:
    """Fan out invalidations locally and, for PostgreSQL, across processes."""

    _subscribers: set[asyncio.Queue[QueueItem]] = field(default_factory=set)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _queue_maxsize: int = 128
    _origin_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _transport: _PostgresTransport | None = None
    _started: bool = False

    async def start(self, database_url: str) -> None:
        """Start shared fan-out for PostgreSQL; SQLite remains process-local."""
        if self._started:
            return
        backend = make_url(database_url).get_backend_name()
        if backend == "postgresql":
            transport = _PostgresTransport(
                database_url,
                origin_id=self._origin_id,
                on_event=self._deliver,
                on_resync=self.resync_all,
            )
            try:
                await transport.start()
            except Exception:
                await transport.stop()
                raise
            self._transport = transport
        elif backend != "sqlite":
            raise RuntimeError(f"Unsupported realtime database backend {backend!r}.")
        else:
            set_realtime_health(True)
        self._started = True

    async def stop(self) -> None:
        transport = self._transport
        self._transport = None
        self._started = False
        if transport is not None:
            await transport.stop()
        else:
            set_realtime_health(False)

    def status(self) -> str:
        if self._transport is None:
            return "local" if self._started else "not_started"
        return "healthy" if self._transport.healthy else "degraded"

    async def _deliver_item(self, item: QueueItem) -> None:
        async with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                record_realtime_resync("subscriber_overflow")
                while True:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                queue.put_nowait(ResyncSignal(reason="subscriber_overflow"))

    async def _deliver(self, event: Event) -> None:
        await self._deliver_item(event)

    async def publish(self, event: Event) -> None:
        """Deliver locally, then emit a shared PostgreSQL invalidation."""
        # Stamp project identity before transport; parent IDs survive individual
        # child deletion. Whole-project deletion uses its own entity ID.
        if event.project_id is None:
            from dataclasses import replace
            from sqlmodel import Session
            from api import database
            from api.models.entities import Subproject, Ticket

            project_id = None
            if event.entity == "project":
                project_id = event.entity_id
            elif event.entity in {
                "subproject",
                "knowledge_node",
                "knowledge_proposal",
                "agent_session",
            }:
                project_id = event.parent_id
            elif event.entity in {"ticket", "comment"}:
                with Session(database.engine) as session:
                    parent_id = event.parent_id
                    if event.entity == "comment":
                        ticket = session.get(Ticket, parent_id)
                        parent_id = ticket.subproject_id if ticket else None
                    subproject = (
                        session.get(Subproject, parent_id) if parent_id else None
                    )
                    project_id = subproject.project_id if subproject else None
            event = replace(event, project_id=project_id)
        await self._deliver(event)
        record_realtime_event("local", "delivered")
        if self._transport is not None:
            await self._transport.publish(event)

    async def resync_all(self, reason: str) -> None:
        record_realtime_resync(reason)
        await self._deliver_item(ResyncSignal(reason=reason))

    @asynccontextmanager
    async def subscribe(
        self,
    ) -> AsyncIterator[asyncio.Queue[QueueItem]]:
        queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._subscribers.add(queue)
        add_realtime_subscriber(1)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)
            add_realtime_subscriber(-1)

    def subscriber_count(self) -> int:
        return len(self._subscribers)


_broadcaster: EventBroadcaster | None = None


def get_broadcaster() -> EventBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = EventBroadcaster()
    return _broadcaster


def reset_broadcaster() -> None:  # pragma: no cover - test-only helper
    global _broadcaster
    _broadcaster = EventBroadcaster()
