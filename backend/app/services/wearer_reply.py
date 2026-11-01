"""
wearer_reply.py

What the wearer said, from the STT text (docs/spatial/MVP2.md, "Hợp đồng phân loại P0").
The phrase lists are copied from that table and a test keeps them equal to it. HELP is
checked first and anywhere in the text; OK only when every sentence is exactly an OK
phrase. Everything else is UNCLEAR, which never cancels.
"""

from __future__ import annotations

import re

OK_PHRASES = ("i am ok", "i am okay", "i am all right", "i am alright", "i am fine")
HELP_PHRASES = (
    "help", "call", "hurt", "hurts", "pain", "ambulance", "emergency", "fell", "fallen",
    "not ok", "not okay", "not fine", "not all right", "not alright",
    "cannot get up", "cannot stand up", "cannot move",
    "painful", "hurting", "injured", "bleeding", "dizzy", "cannot breathe", "broke", "broken",
)
_EXPAND = ((r"\bi'm\b|\bim\b", "i am"), (r"\bcan't\b|\bcan not\b", "cannot"), (r"\bdon't\b", "do not"))


def normalize(text: str) -> str:
    """The spec's order: lower case, straight apostrophes, expand the listed tokens, then
    turn the remaining punctuation into spaces and collapse them."""
    text = text.lower().replace("’", "'").replace("‘", "'").replace("`", "'")
    for pattern, replacement in _EXPAND:
        text = re.sub(pattern, replacement, text)
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _has_phrase(normalized: str, phrases) -> bool:
    padded = f" {normalized} "
    return any(f" {p} " in padded for p in phrases)


def contains_trigger(text: str) -> bool:
    """True when a spoken line would match an OK or HELP phrase anywhere in it. A prompt the
    wearer might hear echoed back must not."""
    return _has_phrase(normalize(text), OK_PHRASES + HELP_PHRASES)


def classify_reply(transcript: str | None) -> str:
    text = transcript or ""
    if _has_phrase(normalize(text), HELP_PHRASES):
        return "help"
    sentences = [normalize(s) for s in re.split(r"[.!?;]+", text)]
    sentences = [s for s in sentences if s]
    if sentences and all(s in OK_PHRASES for s in sentences):
        return "ok"
    return "unclear"
