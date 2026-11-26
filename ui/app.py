"""Driver chat UI. Talks to the API over HTTP only: never imports setuhaul internals."""

import os

import httpx
import streamlit as st
import uuid

API_URL = os.getenv("SETUHAUL_API_URL", "http://localhost:8000")
TIMEOUT_S = 60   # an agent turn can take several seconds

st.set_page_config(page_title="SetuHaul Driver Chat", page_icon="🚚")


def api_post(path: str, payload: dict, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.post(f"{API_URL}{path}", json=payload, headers=headers, timeout=TIMEOUT_S)

def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state.token}"}


def logout() -> None:
    st.session_state.clear()
    st.rerun()


def load_history() -> list[dict]:
    r = httpx.get(f"{API_URL}/history", params={"thread_id": st.session_state.thread_id},
                  headers=_auth(), timeout=TIMEOUT_S)
    if r.status_code == 401:
        logout()
    r.raise_for_status()
    return r.json()["messages"]


def send(text: str) -> str:
    payload = {"thread_id": st.session_state.thread_id, "text": text,
               "client_msg_id": uuid.uuid4().hex}          # one id per message, reused on retry
    for attempt in range(2):
        try:
            r = api_post("/chat", payload, st.session_state.token)
            break
        except httpx.TransportError:
            if attempt == 1:
                return "Network problem. Please try again."
    if r.status_code == 401:
        logout()
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


if "token" not in st.session_state:
    login_view()
else:
    chat_view()