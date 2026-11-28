"""Clean driver input before it reaches the LLM (spec R10-R11, plan §5.3).

Cleaning only removes things that carry no meaning; it never rewrites, corrects or translates.
"""

from __future__ import annotations

import unicodedata

MAX_CHARS = 1_000
_KEEP = {"\n", "\t"}


class InvalidMessage(ValueError):
    """The message was rejected. The text is safe to show to the driver."""


def clean_message(text: str) -> str:
    if not isinstance(text, str):
        raise InvalidMessage("Please type a message.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Control characters (Unicode category Cc) other than newline and tab.
    text = "".join(ch for ch in text if ch in _KEEP or unicodedata.category(ch) != "Cc")
    text = text.strip()
    if not text:
        raise InvalidMessage("Please type a message.")
    if len(text) > MAX_CHARS:
        raise InvalidMessage(f"That message is too long. Please keep it under {MAX_CHARS:,} characters.")
    return text
