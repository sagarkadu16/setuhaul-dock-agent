"""T3.1-T3.3: login, phone linking and ownership (AC1-AC4a, R4b, R5)."""

import pytest

from app.identity import (
    AlreadyLinked,
    DriverAlreadyLinked,
    InvalidEmail,
    NoSuchDriver,
    NotLinked,
    get_user,
    link_phone,
    login,
    require_driver,
)


def count_users(db):
    return db.execute("SELECT COUNT(*) FROM app_users").fetchone()[0]


# --- T3.1 login ---

def test_ac1_same_email_twice_is_one_user(db):
    first = login(db, " Manoj@Test.com ")
    second = login(db, " Manoj@Test.com ")
    assert first == second
    assert first.email == "manoj@test.com"
    assert count_users(db) == 1


def test_email_is_case_insensitive(db):
    assert login(db, "MANOJ@TEST.COM") == login(db, "manoj@test.com")


@pytest.mark.parametrize("bad", ["manoj@", "manoj", "", "   ", "@test.com", "manoj@test"])
def test_ac2_invalid_email_rejected(db, bad):
    with pytest.raises(InvalidEmail, match="doesn't look like an email"):
        login(db, bad)
    assert count_users(db) == 0


def test_new_user_is_not_linked(db):
    user = login(db, "new@test.com")
    assert not user.linked
    with pytest.raises(NotLinked):
        require_driver(user)


# --- T3.2 link phone ---

@pytest.mark.parametrize("phone", [
    "+91-9000010006",      # AC3
    "9000010006",          # AC3a
    "+91 90000 10006",
    "919000010006",
])
def test_ac3_link_by_phone(db, phone):
    user = login(db, "manoj@test.com")
    linked = link_phone(db, user.user_id, phone)
    assert linked.driver_id == "DRV006"
    assert linked.driver_name == "Manoj Sharma"
    assert require_driver(linked) == "DRV006"


@pytest.mark.parametrize("phone", ["+91-0000000000", "12345", "", "abc"])
def test_ac4_unknown_phone_rejected(db, phone):
    user = login(db, "someone@test.com")
    with pytest.raises(NoSuchDriver):
        link_phone(db, user.user_id, phone)
    assert not get_user(db, user.user_id).linked


def test_ac4a_driver_can_have_only_one_email(db):
    a = login(db, "a@test.com")
    b = login(db, "b@test.com")
    link_phone(db, a.user_id, "+91-9000010006")
    with pytest.raises(DriverAlreadyLinked):
        link_phone(db, b.user_id, "+91-9000010006")
    assert not get_user(db, b.user_id).linked
    assert get_user(db, a.user_id).driver_id == "DRV006"


def test_r4b_link_cannot_be_changed(db):
    user = login(db, "manoj@test.com")
    link_phone(db, user.user_id, "+91-9000010006")
    with pytest.raises(AlreadyLinked):
        link_phone(db, user.user_id, "+91-9000010007")
    assert get_user(db, user.user_id).driver_id == "DRV006"


def test_link_survives_new_login(db):
    user = login(db, "manoj@test.com")
    link_phone(db, user.user_id, "9000010006")
    assert login(db, "Manoj@Test.com").driver_id == "DRV006"
