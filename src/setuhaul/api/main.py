import sqlite3
import logging
import time

from fastapi import FastAPI, HTTPException, status, Query, Request

from setuhaul.api.deps import ChatDriverId, Conn, DriverId
from setuhaul.api.schemas import LoginIn, LoginOut, ChatIn, ChatOut, HistoryOut, MessageOut, ThreadOut, AuthConfigOut
from setuhaul.clock import SystemClock, get_clock
from setuhaul.services import chat
from setuhaul.services.identity import LoginFailed, login, InvalidSession, driver_for_session
from setuhaul.services.sanitize import InvalidMessage
from setuhaul.agent.runner import run_turn
from setuhaul.config import get_settings
from setuhaul.logging_setup import new_request_id, request_id_var, setup_logging


def api_docs_urls(app_env: str) -> dict:
    """/docs lists every route and schema: handy on a laptop, a free map for attackers when deployed."""
    if app_env == "dev":
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(title="SetuHaul Dock Agent", version="0.1.0", **api_docs_urls(get_settings().app_env))
wall_clock = SystemClock()

setup_logging(json_logs=get_settings().app_env != "dev")
log = logging.getLogger(__name__)


@app.middleware("http")
async def request_id_and_access_log(request: Request, call_next):
    """Tag everything logged during this request with one id; log one line per request."""
    rid = new_request_id(request.headers.get("x-request-id"))
    token = request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        log.info("%s %s -> %s in %.0f ms", request.method, request.url.path,
                 response.status_code, (time.perf_counter() - started) * 1000)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/login")
def login_route(body: LoginIn, conn: sqlite3.Connection = Conn) -> LoginOut:
    if get_settings().auth_mode != "local":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    try:
        driver, token = login(conn, body.email)
    except LoginFailed as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    thread_id = chat.start_thread(conn, driver.driver_id, wall_clock.now())
    return LoginOut(token=token, thread_id=thread_id, driver_name=driver.driver_name)


@app.post("/chat")
def chat_route(
    body: ChatIn, driver_id: str = ChatDriverId, conn: sqlite3.Connection = Conn
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


@app.post("/threads", status_code=status.HTTP_201_CREATED)
def start_thread_route(driver_id: str = DriverId, conn: sqlite3.Connection = Conn) -> ThreadOut:
    """Open a new chat for the logged-in driver (works in both AUTH_MODEs)."""
    try:
        driver = driver_for_session(conn, driver_id)
    except InvalidSession as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    thread_id = chat.start_thread(conn, driver.driver_id, wall_clock.now())
    return ThreadOut(thread_id=thread_id, driver_name=driver.driver_name)

@app.get("/auth/config")
def auth_config() -> AuthConfigOut:
    """Unauthenticated: the UI calls this before anyone has logged in."""
    s = get_settings()
    if s.auth_mode == "cognito":
        return AuthConfigOut(mode="cognito", region=s.cognito_region, client_id=s.cognito_client_id)
    return AuthConfigOut(mode="local")