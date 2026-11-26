"""Driver chat UI. Talks to the API over HTTP only: never imports setuhaul internals."""

import os

import httpx
import streamlit as st
import uuid

from cognito_login import CognitoLogin, LoginError

API_URL = os.getenv("SETUHAUL_API_URL", "http://localhost:8000")
TIMEOUT_S = 60   # an agent turn can take several seconds

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

def send(text: str) -> str:
    payload = {"thread_id": st.session_state.thread_id, "text": text,
               "client_msg_id": uuid.uuid4().hex}          # one id per message, reused on retry
    for attempt in range(2):
        try:
            r = authed("POST", "/chat", json=payload)
            break
        except httpx.TransportError:
            if attempt == 1:
                return "Network problem. Please try again."
    if r.status_code != 200:
        return r.json().get("detail", "Something went wrong.")
    return r.json()["reply"]


def chat_view() -> None:
    st.title(f"Hi {st.session_state.driver_name}")
    if st.button("Log out"):
        logout()

    if "messages" not in st.session_state:
        st.session_state.messages = load_history()

    for m in st.session_state.messages:
        is_driver = m["sender"] == "DRIVER"
        with st.chat_message("user" if is_driver else "assistant"):
            if is_driver:   
                st.text(m["text"])
            else:
                st.markdown(m["text"])

    if prompt := st.chat_input("Type your message", max_chars=1000):
        with st.chat_message("user"):
            st.text(prompt)
        with st.chat_message("assistant"), st.spinner("Thinking..."):
            reply = send(prompt)
            st.markdown(reply)
        st.session_state.messages += [
            {"sender": "DRIVER", "text": prompt},
            {"sender": "AGENT", "text": reply},
        ]


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
    