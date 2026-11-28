"""T3.5: input cleaning (AC6, R10-R11)."""

import pytest

from app.input_clean import MAX_CHARS, InvalidMessage, clean_message


@pytest.mark.parametrize("text", ["", "   ", "\n\t  \n", "\x00\x07", None])
def test_ac6_empty_rejected(text):
    with pytest.raises(InvalidMessage, match="Please type a message"):
        clean_message(text)


def test_ac6_too_long_rejected():
    with pytest.raises(InvalidMessage, match="too long"):
        clean_message("a" * 1_200)


def test_exactly_max_length_is_allowed():
    assert len(clean_message("a" * MAX_CHARS)) == MAX_CHARS


def test_length_is_checked_after_cleaning():
    assert clean_message("   " + "a" * MAX_CHARS + "   ") == "a" * MAX_CHARS


def test_surrounding_whitespace_removed():
    assert clean_message("  reaching 11:20  \n") == "reaching 11:20"


def test_control_characters_removed():
    assert clean_message("reach\x00ing 11:20\x1b") == "reaching 11:20"


def test_newlines_and_tabs_inside_are_kept():
    assert clean_message("line one\r\nline two\tend") == "line one\nline two\tend"


@pytest.mark.parametrize("text", [
    "Reaching 11:20 , tyre burst!!",
    "LATE BY 1 HR",
    "reachng arnd 11.20 sir 🙏",
    "मैं लेट हूँ",                     # not translated (R11, A5)
])
def test_meaning_is_never_changed(text):
    assert clean_message(text) == text
