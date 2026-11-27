"""Decision notifications: decide what is due, then hand the driver their new messages.

Polling drives this today. A poll does two things:
1. Resolves any of this driver's slot requests whose window has closed. The decision
   would otherwise wait for someone to ask (resolve_slot is lazy).
2. Returns this driver's new outbox rows and copies them into the chat thread as
   SYSTEM messages, so they survive a page refresh and the agent sees them next turn.

Both are idempotent: polling twice decides nothing twice and stores nothing twice.
At scale, step 1 moves to a background sweeper and the outbox also feeds SMS/push.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from setuhaul.db import queries, writes
from setuhaul.db.connection import immediate_transaction
from setuhaul.models.domain import Notification, SlotView
from setuhaul.services.chat import NotYourThread
from setuhaul.services.contention import resolve_slot

MAX_BATCH = 20


@dataclass(frozen=True)
class Delivered:
    notification: Notification
    offered_slot: SlotView | None    # shown to the driver as a tappable option


def _external_id(notification_id: int) -> str:
    # ':' is not allowed in client_msg_id (ChatIn pattern), so a client can never
    # pre-claim this id and silently suppress a notification.
    return f"notif:{notification_id}"


def poll(
    conn: sqlite3.Connection,
    driver_id: str,
    thread_id: str,
    after_id: int,
    *,
    business_now: datetime,
    wall_now: datetime,
) -> list[Delivered]:
    opened_at = queries.thread_opened_at(conn, thread_id, driver_id)
    if opened_at is None:
        raise NotYourThread("Chat not found")

    for slot_id in queries.due_request_slots_for_driver(conn, driver_id, wall_now):
        resolve_slot(conn, slot_id, business_now=business_now, wall_now=wall_now)

    # Only decisions made while this thread was open: a fresh login doesn't replay old news.
    new = queries.notifications_for_driver(conn, driver_id, after_id, opened_at, MAX_BATCH)
    if new:
        with immediate_transaction(conn):
            for n in new:
                writes.insert_chat_message(
                    conn, thread_id, "SYSTEM", n.message_text, wall_now,
                    external_id=_external_id(n.notification_id),
                )
    return [
        Delivered(n, queries.get_slot(conn, n.offered_slot_id) if n.offered_slot_id else None)
        for n in new
    ]
