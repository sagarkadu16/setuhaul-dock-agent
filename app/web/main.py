"""HTTP API and the chat page (plan §8, task T5.1).

Run with `make run`, then open http://127.0.0.1:8000.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app.agent.graph import Agent, open_checkpointer
from app.agent.model import ModelNotConfigured, build_chat_model
from app.chat import handle_driver_message
from app.config import settings
from app.conversations import (
    Conversation,
    Message,
    NotFound,
    create_conversation,
    get_messages,
    list_conversations,
)
from app.db import connect
from app.identity import IdentityError, NotLinked, User, UnknownUser, get_user, link_phone, login
from app.input_clean import InvalidMessage

STATIC_DIR = Path(__file__).parent / "static"
SESSION_DAYS = 7


class EmailIn(BaseModel):
    email: str


class PhoneIn(BaseModel):
    phone: str


class TextIn(BaseModel):
    text: str


def _user_out(user: User) -> dict:
    return {"email": user.email, "linked": user.linked, "driver_name": user.driver_name}


def _conversation_out(c: Conversation) -> dict:
    return {"id": c.thread_id, "opened_at": c.opened_at.isoformat(), "preview": c.preview}


def _message_out(m: Message) -> dict:
    return {"id": m.message_id, "sender": m.sender, "text": m.text, "sent_at": m.sent_at.isoformat()}


def create_app(
    *,
    db_path: Path | str | None = None,
    checkpoint_path: Path | str | None = None,
    model_factory: Callable[[], BaseChatModel] = build_chat_model,
) -> FastAPI:
    """Tests pass their own database copy and a scripted model; `make run` uses the defaults."""
    db_path = Path(db_path or settings.app_db_path)
    checkpoint_path = Path(checkpoint_path or settings.checkpoint_db_path)

    app = FastAPI(title="SetuHaul driver chat", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        max_age=SESSION_DAYS * 24 * 3600,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # The agent is built on first use, so the app starts (and the login pages work) without a key.
    agent_lock = threading.Lock()
    agent_holder: dict[str, Agent] = {}

    def get_agent() -> Agent:
        with agent_lock:
            if "agent" not in agent_holder:
                try:
                    model = model_factory()
                except ModelNotConfigured as exc:
                    raise HTTPException(503, f"The chat assistant is not set up: {exc}") from None
                agent_holder["agent"] = Agent(model, open_checkpointer(checkpoint_path), db_path=db_path)
            return agent_holder["agent"]

    # --- dependencies ---

    def db() -> Iterator[sqlite3.Connection]:
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def current_user(request: Request, conn: sqlite3.Connection = Depends(db)) -> User:
        user_id = request.session.get("user_id")
        if not user_id:
            raise HTTPException(401, "Please log in.")
        try:
            return get_user(conn, user_id)
        except UnknownUser:
            request.session.clear()
            raise HTTPException(401, "Please log in again.") from None

    def current_driver(user: User = Depends(current_user)) -> str:
        if not user.linked:
            raise HTTPException(403, "Please link your phone number first.")
        return user.driver_id

    # --- errors with messages that are safe to show ---

    @app.exception_handler(IdentityError)
    async def _identity_error(_: Request, exc: IdentityError):
        return JSONResponse({"detail": str(exc)}, status_code=403 if isinstance(exc, NotLinked) else 400)

    @app.exception_handler(InvalidMessage)
    async def _invalid_message(_: Request, exc: InvalidMessage):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    # --- routes ---

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return {"user": _user_out(user)}

    @app.post("/api/login")
    def do_login(body: EmailIn, request: Request, conn: sqlite3.Connection = Depends(db)):
        user = login(conn, body.email)
        request.session["user_id"] = user.user_id
        return {"user": _user_out(user)}

    @app.post("/api/link")
    def do_link(body: PhoneIn, user: User = Depends(current_user), conn: sqlite3.Connection = Depends(db)):
        return {"user": _user_out(link_phone(conn, user.user_id, body.phone))}

    @app.post("/api/logout")
    def do_logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/conversations")
    def conversations(driver_id: str = Depends(current_driver), conn: sqlite3.Connection = Depends(db)):
        return {"conversations": [_conversation_out(c) for c in list_conversations(conn, driver_id)]}

    @app.post("/api/conversations")
    def new_conversation(driver_id: str = Depends(current_driver), conn: sqlite3.Connection = Depends(db)):
        return {"conversation": _conversation_out(create_conversation(conn, driver_id))}

    @app.get("/api/conversations/{thread_id}")
    def open_conversation(thread_id: str, driver_id: str = Depends(current_driver),
                          conn: sqlite3.Connection = Depends(db)):
        return {"id": thread_id, "messages": [_message_out(m) for m in get_messages(conn, thread_id, driver_id)]}

    @app.post("/api/conversations/{thread_id}/messages")
    def send_message(thread_id: str, body: TextIn, driver_id: str = Depends(current_driver),
                     conn: sqlite3.Connection = Depends(db)):
        agent = get_agent()   # fail before storing anything if the model isn't set up
        turn = handle_driver_message(conn, agent, thread_id, driver_id, body.text)
        return {"driver_message": _message_out(turn.driver_message),
                "agent_message": _message_out(turn.agent_message)}

    return app


app = create_app()
