import re
from pathlib import Path

import pytest

from app.services.notify_templates import SPOKEN
from app.services.wearer_reply import HELP_PHRASES, OK_PHRASES, classify_reply, contains_trigger, normalize

SPEC = (Path(__file__).resolve().parents[2] / "docs" / "spatial" / "MVP2.md").read_text()


def spec_list(label):
    row = re.search(rf"^\| {label} \| (.+?) \|", SPEC, flags=re.M).group(1)
    return tuple(re.findall(r"`([^`]+)`", row))


def test_phrase_lists_equal_the_spec_table():
    assert OK_PHRASES == spec_list("OK")
    assert HELP_PHRASES == spec_list("HELP")


def test_spoken_lines_equal_the_spec_table():
    rows = dict(re.findall(r"^\| `(\w+)` \| (.+?) \|", SPEC, flags=re.M))
    for template_id, (text, _) in SPOKEN.items():
        assert rows[template_id] == text, template_id


def test_lines_spoken_while_listening_hold_no_trigger_phrase():
    listening = {"awaiting_wearer", "cancelled_by_wearer"}
    for template_id, (text, states) in SPOKEN.items():
        if states & listening:
            assert not contains_trigger(text), template_id
    assert contains_trigger(SPOKEN["alert_unconfirmed"][0])  # allowed: only after the alert, never listened to


@pytest.mark.parametrize("said, expected", [
    # The spec's own examples
    ("I'm all right", "ok"), ("I'm fine", "ok"), ("Yes", "unclear"), ("I'm not sure I'm OK", "unclear"),
    ("I'm OK, call my son", "help"), ("don't call", "help"),
    # U0: Whisper repeated a single answer
    ("I'm fine. I'm fine. I'm fine.", "ok"),
    # The earlier reply.js cancelled on all of these
    ("I don't think I'm OK", "unclear"), ("maybe I'm okay", "unclear"), ("I'm OK I guess, my leg", "unclear"),
    # Injuries the first list missed (review of 2026-10-31)
    ("It's painful", "help"), ("My back is hurting", "help"), ("I can't breathe", "help"),
    ("I can not get up", "help"), ("I think I broke my wrist", "help"),
    # Not a trigger inside another word, curly apostrophe, empty, Whisper filler
    ("helpful", "unclear"), ("I’m okay.", "ok"), ("", "unclear"), (None, "unclear"),
    ("Thank you.", "unclear"), ("ok", "unclear"), ("I'm OK!", "ok"), ("Help.", "help"),
])
def test_classify_reply(said, expected):
    assert classify_reply(said) == expected


def test_normalize_follows_the_spec_order():
    assert normalize("I’M OK, can't  stand-up!") == "i am ok cannot stand up"
