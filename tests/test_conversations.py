"""T3.3-T3.4: conversations (AC5, R5-R9)."""

import pytest

from app.conversations import (
    NotFound,
    append_message,
    create_conversation,
    get_messages,
    list_conversations,
    require_owner,
    set_shipment,
)
from app.db import connect

# DRV002 has no seed conversations, so the list holds only what the test creates.
DRIVER = "DRV002"


def test_ac5_conversations_survive_restart_newest_first(db_path):
    conn = connect(db_path)
    first = create_conversation(conn, DRIVER)
    append_message(conn, first.thread_id, DRIVER, "DRIVER", "stuck in traffic")
    append_message(conn, first.thread_id, DRIVER, "AGENT", "When will you reach?")
    second = create_conversation(conn, DRIVER)
    append_message(conn, second.thread_id, DRIVER, "DRIVER", "late by one hour")
    conn.close()

    # "Restart": a brand-new connection to the same database file.
    conn = connect(db_path)
    conversations = list_conversations(conn, DRIVER)
    assert [c.thread_id for c in conversations] == [second.thread_id, first.thread_id]
    assert conversations[0].preview == "late by one hour"
    assert conversations[1].preview == "When will you reach?"

    messages = get_messages(conn, first.thread_id, DRIVER)
    assert [(m.sender, m.text) for m in messages] == [
        ("DRIVER", "stuck in traffic"),
        ("AGENT", "When will you reach?"),
    ]
    conn.close()


def test_new_conversation_is_empty(db):
    conversation = create_conversation(db, DRIVER)
    assert get_messages(db, conversation.thread_id, DRIVER) == []
    assert list_conversations(db, DRIVER)[0].preview is None


def test_seed_history_shows_for_its_driver(db):
    # DRV006 has seed threads THR001 and THR009 (decision D1).
    ids = [c.thread_id for c in list_conversations(db, "DRV006")]
    assert ids == ["THR009", "THR001"]
    new = create_conversation(db, "DRV006")
    assert list_conversations(db, "DRV006")[0].thread_id == new.thread_id


def test_messages_sent_quickly_keep_their_order(db):
    conversation = create_conversation(db, DRIVER)
    texts = [f"message {i}" for i in range(20)]
    for i, text in enumerate(texts):
        append_message(db, conversation.thread_id, DRIVER, "DRIVER" if i % 2 == 0 else "AGENT", text)
    assert [m.text for m in get_messages(db, conversation.thread_id, DRIVER)] == texts


def test_r5_cannot_open_another_drivers_conversation(db):
    conversation = create_conversation(db, "DRV006")
    with pytest.raises(NotFound):
        get_messages(db, conversation.thread_id, DRIVER)
    with pytest.raises(NotFound):
        append_message(db, conversation.thread_id, DRIVER, "DRIVER", "hello")
    with pytest.raises(NotFound):
        require_owner(db, "THR001", DRIVER)         # a seed thread of DRV006
    assert list_conversations(db, DRIVER) == []     # and it isn't in DRV002's list


def test_missing_conversation_is_not_found(db):
    with pytest.raises(NotFound):
        get_messages(db, "THR-nope", DRIVER)


def test_only_driver_and_agent_messages_can_be_added(db):
    conversation = create_conversation(db, DRIVER)
    with pytest.raises(ValueError):
        append_message(db, conversation.thread_id, DRIVER, "OPERATIONS", "hi")


def test_set_shipment(db):
    conversation = create_conversation(db, DRIVER)
    set_shipment(db, conversation.thread_id, DRIVER, "SHP1002")
    assert list_conversations(db, DRIVER)[0].shipment_id == "SHP1002"
