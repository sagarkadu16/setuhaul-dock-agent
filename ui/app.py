"""Driver chat UI. Talks to the API over HTTP only: never imports setuhaul internals."""

import os
import uuid
from datetime import datetime

import httpx
import streamlit as st

from cognito_login import CognitoLogin, LoginError

API_URL = os.getenv("SETUHAUL_API_URL", "http://localhost:8000")
TIMEOUT_S = 60   # an agent turn can take several seconds
POLL_EVERY = "3s"   # how often the chat asks for slot decisions (no LLM cost)
AVATARS = {"DRIVER": "🚚", "AGENT": "🏭", "SYSTEM": "🔔"}

st.set_page_config(page_title="SetuHaul Driver Chat", page_icon="🚚")


def api_post(path: str, payload: dict, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.post(f"{API_URL}{path}", json=payload, headers=headers, timeout=TIMEOUT_S)

def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state.token}"}


def logout() -> None:
    refresh_token = st.session_state.get("refresh_token")
    if refresh_token:                           # Cognito login: kill the session at Cognito too
        try:
            cfg = auth_config()
            cognito(cfg["region"], cfg["client_id"]).revoke(refresh_token)
        except httpx.HTTPError:
            pass                                # API unreachable: still log out locally
    st.session_state.clear()
    st.rerun()

def load_history() -> list[dict]:
    r = authed("GET", "/history", params={"thread_id": st.session_state.thread_id})
    r.raise_for_status()
    return r.json()["messages"]

def send(text: str) -> tuple[str, list[dict]]:
    """One chat turn. Returns (reply, slots to offer as buttons)."""
    payload = {"thread_id": st.session_state.thread_id, "text": text,
               "client_msg_id": uuid.uuid4().hex}          # one id per message, reused on retry
    for attempt in range(2):
        try:
            r = authed("POST", "/chat", json=payload)
            break
        except httpx.TransportError:
            if attempt == 1:
                return "Network problem. Please try again.", []
    if r.status_code != 200:
        return r.json().get("detail", "Something went wrong."), []
    body = r.json()
    return body["reply"], body.get("slots", [])


def hhmm(iso: str) -> str:
    """'2026-08-04T12:00:00+05:30' -> '12:00'. Keeps the facility's offset (no tz shift)."""
    return datetime.fromisoformat(iso).strftime("%H:%M")


def slot_label(slot: dict) -> str:
    return f"Dock {slot['dock']} · {hhmm(slot['start'])}–{hhmm(slot['end'])}"


def render_message(m: dict) -> None:
    sender = m["sender"]
    with st.chat_message("user" if sender == "DRIVER" else "assistant",
                         avatar=AVATARS.get(sender, "🏭")):
        if sender == "DRIVER":
            st.text(m["text"])                  # driver text is never rendered as markdown
        else:
            st.markdown(m["text"])


def slot_buttons(slots: list[dict]) -> None:
    """Tappable slots under the latest message. A tap sends a normal chat message, so a
    booking still goes through the agent and Python's checks (one path, not two)."""
    st.caption("Tap a slot to choose it")
    for col, slot in zip(st.columns(len(slots)), slots):
        if col.button(slot_label(slot), key=f"slot-{slot['slot_id']}", icon="🕒", width="stretch"):
            st.session_state.queued_prompt = f"I'll take {slot_label(slot)} ({slot['slot_id']})."


@st.fragment(run_every=POLL_EVERY)
def notification_poller() -> None:
    """Every few seconds, ask the API for slot decisions. Renders nothing itself:
    new messages go into session_state, then one full rerun shows them."""
    try:
        r = authed("GET", "/notifications", params={
            "thread_id": st.session_state.thread_id,
            "after": st.session_state.get("notif_cursor", 0),
        })
    except httpx.HTTPError:
        return                                  # API blip: just try again next tick
    if r.status_code != 200:
        return
    new = r.json()["notifications"]
    if not new:
        return
    for n in new:
        st.session_state.messages.append({"sender": "SYSTEM", "text": n["text"], "slots": n["slots"]})
        st.session_state.setdefault("toasts", []).append(n["text"])
    st.session_state.notif_cursor = new[-1]["id"]
    st.rerun(scope="app")


def chat_view() -> None:
    with st.sidebar:
        st.subheader(f"🚚 {st.session_state.driver_name}")
        st.caption("SetuHaul dock assistant")
        if st.button("Log out", width="stretch"):
            logout()

    st.title("Dock assistant")
    st.caption("Running late or need a new dock slot? Tell me here.")

    if "messages" not in st.session_state:
        st.session_state.messages = load_history()
    for text in st.session_state.pop("toasts", []):   # shown after the rerun, so they persist
        st.toast(text, icon="🔔")

    for m in st.session_state.messages:
        render_message(m)
    last = st.session_state.messages[-1] if st.session_state.messages else None
    if last and last.get("slots"):              # only the latest options are still valid
        slot_buttons(last["slots"])

    notification_poller()

    typed = st.chat_input("Type your message", max_chars=1000)
    prompt = typed or st.session_state.pop("queued_prompt", None)
    if prompt:
        render_message({"sender": "DRIVER", "text": prompt})
        with st.chat_message("assistant", avatar=AVATARS["AGENT"]), st.spinner("Checking docks..."):
            reply, slots = send(prompt)
        st.session_state.messages += [
            {"sender": "DRIVER", "text": prompt},
            {"sender": "AGENT", "text": reply, "slots": slots},
        ]
        st.rerun()                              # one render path: the loop above draws it all


def login_view() -> None:
    st.title("SetuHaul driver login")
    with st.form("login"):
        email = st.text_input("Email")
        submitted = st.form_submit_button("Log in")
    if not submitted:
        return
    try:
        r = api_post("/login", {"email": email})
    except httpx.HTTPError:
        st.error("Can't reach the server. Is the API running?")
        return
    if r.status_code != 200:
        st.error("Unknown email")
        return
    st.session_state.update(r.json())   # token, thread_id, driver_name
    st.rerun()


@st.cache_data(ttl=300, show_spinner=False)
def auth_config() -> dict:
    """Which login to show. Asked from the API so UI and API can never disagree."""
    r = httpx.get(f"{API_URL}/auth/config", timeout=10)
    r.raise_for_status()
    return r.json()


@st.cache_resource
def cognito(region: str, client_id: str) -> CognitoLogin:
    """One boto3 client for the whole app, shared by every browser tab."""
    return CognitoLogin(region, client_id)

def start_chat(access_token: str, refresh_token: str) -> None:
    """After a Cognito login: open a chat thread with our API, then enter the chat."""
    try:
        r = api_post("/threads", {}, access_token)
    except httpx.HTTPError:
        st.error("Can't reach the server. Please try again.")
        return
    if r.status_code == 403:
        st.error("This account is not linked to an active driver. Please contact SetuHaul ops.")
        return
    if r.status_code != 201:
        st.error("Logged in, but the chat could not be started. Please try again.")
        return
    st.session_state.update(token=access_token, refresh_token=refresh_token, **r.json())
    st.rerun()


def cognito_login_view(cfg: dict) -> None:
    st.title("SetuHaul driver login")
    login = cognito(cfg["region"], cfg["client_id"])
    challenge = st.session_state.get("otp")

    if challenge is None:                       # screen 1: email
        with st.form("email"):
            email = st.text_input("Email")
            sent = st.form_submit_button("Send me a code")
        if sent and email.strip():
            try:
                st.session_state.otp = login.start(email.strip())
            except LoginError as e:
                st.error(str(e))
                return
            st.rerun()
        return

    st.info(f"If {challenge.email} is registered, we've emailed it a sign-in code.")
    with st.form("code"):                       # screen 2: code
        code = st.text_input("Code", max_chars=10)
        submitted = st.form_submit_button("Log in")
    if st.button("Start again (new code or different email)"):
        del st.session_state.otp
        st.rerun()
    if not submitted or not code.strip():
        return
    try:
        tokens = login.verify(challenge, code)
    except LoginError as e:
        st.error(str(e))
        return
    del st.session_state.otp                    # a code works once; never keep the session around
    start_chat(tokens.access_token, tokens.refresh_token)

def try_refresh() -> bool:
    """Swap the refresh token for a new access token. False = can't, log in again."""
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:                       # AUTH_MODE=local has no refresh token
        return False
    cfg = auth_config()
    try:
        st.session_state.token = cognito(cfg["region"], cfg["client_id"]).refresh(refresh_token)
    except LoginError:
        return False
    return True


def authed(method: str, path: str, **kwargs) -> httpx.Response:
    """Call our API as the logged-in driver. On 401: refresh once and retry, else log out."""
    r = httpx.request(method, f"{API_URL}{path}", headers=_auth(), timeout=TIMEOUT_S, **kwargs)
    if r.status_code == 401:
        if not try_refresh():
            logout()                            # st.rerun() inside: stops this run here
        r = httpx.request(method, f"{API_URL}{path}", headers=_auth(), timeout=TIMEOUT_S, **kwargs)
        if r.status_code == 401:
            logout()
    return r



if "token" in st.session_state:
    chat_view()
else:
    try:
        cfg = auth_config()
    except httpx.HTTPError:
        st.error("Can't reach the server. Is the API running?")
        st.stop()
    if cfg["mode"] == "cognito":
        cognito_login_view(cfg)
    else:
        login_view()
    