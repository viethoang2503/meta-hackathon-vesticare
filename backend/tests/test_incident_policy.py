import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.services.incident_policy import ALERTED, AWAITING, CANCELLED, decide_deadline, decide_reply, late_ok_targets
from app.services.notify_templates import TEMPLATES, render

D = datetime(2026, 11, 1, 10, 0, 30)
G = timedelta(seconds=15)
before, after = D - timedelta(seconds=5), D + timedelta(seconds=5)


def test_only_a_clear_ok_before_the_deadline_cancels():
    assert decide_reply(AWAITING, "ok", D, G, before, before).cancel
    assert decide_reply(AWAITING, "unclear", D, G, before, before).reason == "unclear"
    assert decide_reply(AWAITING, "help", D, G, before, before).alert == "wearer_help"


def test_an_ok_processed_at_or_after_the_deadline_alerts_first_then_counts_as_late():
    d = decide_reply(AWAITING, "ok", D, G, D, before)   # captured in time, decided too late
    assert (d.alert, d.cancel, d.late_ok) == ("timeout", False, True)


def test_help_after_a_cancel_raises_the_alert_only_if_captured_in_time_and_decided_within_grace():
    assert decide_reply(CANCELLED, "help", D, G, after, before).alert == "wearer_help"
    assert decide_reply(CANCELLED, "help", D, G, D + G + timedelta(seconds=1), before).reason == "processed_after_grace"
    assert decide_reply(CANCELLED, "help", D, G, after, after).reason == "captured_after_deadline"
    assert decide_reply(CANCELLED, "help", D, G, after, None).reason == "capture_time_unknown"
    assert decide_reply(CANCELLED, "ok", D, G, before, before).reason == "already_cancelled"


def test_after_the_alert_ok_is_a_late_update_and_help_changes_nothing():
    assert decide_reply(ALERTED, "ok", D, G, after, after).late_ok
    assert decide_reply(ALERTED, "help", D, G, after, after).reason == "already_alerted"


def test_the_deadline_alerts_only_while_waiting():
    assert [decide_deadline(s) for s in (AWAITING, CANCELLED, ALERTED)] == ["timeout", None, None]


def test_late_ok_goes_to_contacts_whose_alert_went_out_or_may_have():
    sends = {"contact_1": "unknown", "contact_2": "failed"}
    assert late_ok_targets(sends, set()) == ["contact_1"]
    assert late_ok_targets({"contact_1": "sent", "contact_2": "scheduled"}, {"contact_1"}) == []


def test_templates_match_the_spec_table_word_for_word():
    spec = (Path(__file__).resolve().parents[2] / "docs" / "spatial" / "MVP2.md").read_text()
    rows = dict(re.findall(r"^\| `(\w+)` \| (.+?) \|", spec, flags=re.M))
    for template_id, text in TEMPLATES.items():
        assert rows[template_id] == text, template_id


def test_render_refuses_values_the_spec_does_not_list():
    ok = dict(incident_id=3, event_time="2026-11-01 10:00:30 +07:00", response_status="No clear response",
              wearer_channel_status="Unknown", location_label="Unknown")
    assert render("notify_initial", **ok).startswith("Fall alert #3 at 2026-11-01 10:00:30 +07:00.")
    with pytest.raises(ValueError):
        render("notify_initial", **{**ok, "response_status": "Help is coming"})
    with pytest.raises(ValueError):
        render("notify_initial", **{**ok, "location_label": "Kitchen, probably"})
    with pytest.raises(ValueError):
        render("notify_initial", incident_id=3)
