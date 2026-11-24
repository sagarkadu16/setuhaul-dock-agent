"""Driver input hygiene. Runs on every chat message before it is stored or sent to the LLM.

Not the security boundary (session identity + tool ownership checks are).
This removes junk that confuses models or hides text, and enforces size limits.
"""

import re
import unicodedata

MAX_MESSAGE_CHARS = 1_000

# Invisible characters: zero-width spaces/joiners, bidi overrides, BOM. Used to hide or reorder text.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
# Control characters, except tab and newline.
_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")

_INJECTION_HINTS = re.compile(
    r"ignore (all |any )?(previous|prior|above|your) (instructions|rules)"
    r"|system prompt|developer mode|you are now|\bjailbreak\b",
    re.IGNORECASE,
)


class InvalidMessage(ValueError):
    """str(error) is safe to show to the driver."""


def clean_message(text: str) -> str:
    if len(text) > MAX_MESSAGE_CHARS * 4:  # cheap guard before doing any work
        raise InvalidMessage(f"Message is too long (max {MAX_MESSAGE_CHARS} characters)")

    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    text = _MANY_BLANK_LINES.sub("\n\n", text).strip()

    if not text:
        raise InvalidMessage("Message is empty")
    if len(text) > MAX_MESSAGE_CHARS:
        raise InvalidMessage(f"Message is too long (max {MAX_MESSAGE_CHARS} characters)")
    return text


def needs_review(text: str) -> bool:
    """Heuristic flag for ops review. Never used to block: false positives are expected."""
    return bool(_INJECTION_HINTS.search(text))
