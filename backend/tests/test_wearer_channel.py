from datetime import datetime, timedelta

from app.services.wearer_channel import Listen, WearerChannel

T0 = datetime(2026, 11, 1, 10, 0, 0)
D = T0 + timedelta(seconds=30)


def test_only_the_newest_reminder_waits_and_old_lines_drop_before_they_play():
    c = WearerChannel()
    c.say("ask_ok", 1, T0)
    c.say("ask_ok_10", 1, T0 + timedelta(seconds=10))
    c.say("ask_ok_20", 1, T0 + timedelta(seconds=20))
    waiting = c.pending(0, lambda i: "awaiting_wearer", lambda i: D, T0 + timedelta(seconds=21))
    assert [u.template_id for u in waiting] == ["ask_ok", "ask_ok_20"]
    assert c.utterances[1].reason == "newer_reminder"
    assert c.pending(0, lambda i: "alerted", lambda i: D, T0 + timedelta(seconds=22)) == []
    assert c.utterances[0].reason == "incident is alerted"


def test_a_prompt_past_the_deadline_is_not_played():
    c = WearerChannel()
    c.say("ask_ok_20", 1, T0)
    assert c.pending(0, lambda i: "awaiting_wearer", lambda i: D, D) == []


def test_listening_ends_at_the_deadline_and_is_never_extended():
    c = WearerChannel()
    c.listen = Listen(1, "help_only", D)
    assert c.listening_for(D - timedelta(seconds=1)).mode == "help_only"
    assert c.listening_for(D) is None


def test_channel_status_follows_the_spec_order():
    c = WearerChannel()
    assert c.status(T0) == "Unknown"
    c.last_seen = T0
    assert c.status(T0 + timedelta(seconds=1)) == "Ready"
    c.speech_dropped(T0)
    assert c.status(T0 + timedelta(seconds=1)) == "Speech degraded"
    c.stt_failed = True
    assert c.status(T0 + timedelta(seconds=1)) == "STT failed"
    c.microphone_error = "NotAllowedError"
    assert c.status(T0 + timedelta(seconds=1)) == "Microphone unavailable"
    assert c.status(T0 + timedelta(seconds=6)) == "Microphone unavailable"   # recording a 4 s turn is not offline
    assert c.status(T0 + timedelta(seconds=8)) == "Phone offline"
