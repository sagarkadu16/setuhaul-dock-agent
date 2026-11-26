"""HTTP request/response shapes. Validated by FastAPI before our code runs."""

from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(_In):
    email: str = Field(min_length=3, max_length=254)


class LoginOut(BaseModel):
    token: str
    thread_id: str
    driver_name: str


class ChatIn(_In):
    thread_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4_000)
    client_msg_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatOut(BaseModel):
    reply: str


class MessageOut(BaseModel):
    sender: str          # 'DRIVER' | 'AGENT' | 'SYSTEM'
    text: str
    sent_at: datetime


class HistoryOut(BaseModel):
    thread_id: str
    messages: list[MessageOut]