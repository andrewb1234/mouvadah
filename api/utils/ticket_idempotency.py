"""Stable create-request identity, independent of subsequent ticket edits."""

import hashlib
import json

from fastapi import HTTPException
from sqlmodel import Session, select

from api.models.entities import Ticket
from api.schemas import TicketCreate


def creation_fingerprint(payload: TicketCreate) -> str:
    values = payload.model_dump(mode="json", exclude={"client_ref"})
    values["depends_on"] = sorted(set(values["depends_on"]))
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def find_replay(
    session: Session, subproject_id: int, client_ref: str | None, fingerprint: str
) -> Ticket | None:
    if client_ref is None:
        return None
    ticket = session.exec(
        select(Ticket).where(
            Ticket.subproject_id == subproject_id, Ticket.client_ref == client_ref
        )
    ).first()
    if ticket is not None and ticket.creation_fingerprint != fingerprint:
        raise HTTPException(409, "client_ref was already used for a different create request.")
    return ticket
