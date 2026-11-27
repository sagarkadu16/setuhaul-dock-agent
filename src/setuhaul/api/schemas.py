"""HTTP request/response shapes. Validated by FastAPI before our code runs."""

from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Literal


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(_In):
    email: str = Field(min_length=3, max_length=254)


class LoginOut(BaseModel):
    token: str
    thread_id: str
    driver_name: str

class ThreadOut(BaseModel):
    thread_id: str
    driver_name: str

class AuthConfigOut(BaseModel):
    """What the UI needs to show the right login. Public values only, never secrets."""
    mode: Literal["local", "cognito"]
    region: str | None = None
    client_id: str | None = None


class ChatIn(_In):
    thread_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4_000)
    client_msg_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class SlotChip(BaseModel):
    """A slot the UI may show as a button. Always from tool/DB data, never from LLM text."""
    slot_id: str
    dock: str
    start: datetime      # facility-local offset kept (+05:30), so the UI can show HH:MM as-is
    end: datetime


class ChatOut(BaseModel):
    reply: str
    slots: list[SlotChip] = []


class NotificationOut(BaseModel):
    id: int
    kind: Literal["SLOT_CONFIRMED", "SLOT_NOT_BOOKED"]
    text: str
    created_at: datetime
    slots: list[SlotChip] = []     # the offered slot, when the driver did not get theirs


class NotificationsOut(BaseModel):
    notifications: list[NotificationOut]


class MessageOut(BaseModel):
    sender: str          # 'DRIVER' | 'AGENT' | 'SYSTEM'
    text: str
    sent_at: datetime


class HistoryOut(BaseModel):
    thread_id: str
    messages: list[MessageOut]

