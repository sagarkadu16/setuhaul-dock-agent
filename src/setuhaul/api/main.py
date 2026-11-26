import sqlite3

from fastapi import FastAPI, HTTPException, status, Query

from setuhaul.api.deps import Conn, DriverId
from setuhaul.api.schemas import LoginIn, LoginOut, ChatIn, ChatOut, HistoryOut, MessageOut
from setuhaul.clock import SystemClock, get_clock
from setuhaul.services import chat
from setuhaul.services.identity import LoginFailed, login
from setuhaul.services.sanitize import InvalidMessage
from setuhaul.agent.runner import run_turn


app = FastAPI(title="SetuHaul Dock Agent", version="0.1.0")
wall_clock = SystemClock()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/login")
def login_route(body: LoginIn, conn: sqlite3.Connection = Conn) -> LoginOut:
    try:
        driver, token = login(conn, body.email)
    except LoginFailed as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    thread_id = chat.start_thread(conn, driver.driver_id, wall_clock.now())
    return LoginOut(token=token, thread_id=thread_id, driver_name=driver.driver_name)


@app.post("/chat")
def chat_route(
    body: ChatIn, driver_id: str = DriverId, conn: sqlite3.Connection = Conn
) -> ChatOut:
    try:
        reply = run_turn(
            conn, driver_id, body.thread_id, body.text, body.client_msg_id,
            business_clock=get_clock(), wall_clock=wall_clock,
        )
    except InvalidMessage as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    except chat.NotYourThread as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return ChatOut(reply=reply)


@app.get("/history")
def history_route(
    thread_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    driver_id: str = DriverId,
    conn: sqlite3.Connection = Conn,
) -> HistoryOut:
    try:
        msgs = chat.history(conn, driver_id, thread_id, limit)
    except chat.NotYourThread as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return HistoryOut(
        thread_id=thread_id,
        messages=[
            MessageOut(sender=m.sender_type, text=m.message_text, sent_at=m.message_ts)
            for m in msgs
        ],
    )