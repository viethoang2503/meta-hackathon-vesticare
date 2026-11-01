"""The MVP 2 Telegram channel against a fake Telegram API (no network)."""

import asyncio
import json
import tempfile
import unittest

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import schemas  # noqa: F401
from app.services.spatial_runs import LiveNotReady, SpatialRunManager, session_snapshot
from app.services.telegram_channel import TelegramNotifier, handle_callback

TOKEN = "123456:SECRET-token-value"
CHATS = {"contact_1": "1001", "contact_2": "1002"}


class FakeTelegram:
    def __init__(self, fail_send=None, timeout_send=False):
        self.calls, self.fail_send, self.timeout_send, self.next_id = [], fail_send, timeout_send, 500

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        self.calls.append((method, body))
        if method == "sendMessage":
            if self.timeout_send:
                raise httpx.ReadTimeout("slow", request=request)
            if self.fail_send:
                return httpx.Response(400, json={"ok": False, "description": self.fail_send})
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def sent(self):
        return [b for m, b in self.calls if m == "sendMessage"]


def notifier(fake, chats=CHATS):
    return TelegramNotifier(TOKEN, chats, transport=httpx.MockTransport(fake))


def test_send_puts_the_acknowledge_button_on_alerts_only():
    fake = FakeTelegram()
    n = notifier(fake)
    assert n.send("contact_1", "Fall alert #4 ...", 4, "ack:4:9") == {"status": "sent", "message_id": 501, "chat_id": "1001"}
    n.send("contact_1", "Update for fall alert #4 ...", 4, None)
    first, update = fake.sent()
    assert first["reply_markup"]["inline_keyboard"][0][0] == {"text": "Acknowledge", "callback_data": "ack:4:9"}
    assert "reply_markup" not in update


def test_a_refused_send_is_failed_and_a_slow_one_is_unknown_and_neither_leaks_the_token():
    failed = notifier(FakeTelegram(fail_send="Bad Request: chat not found")).send("contact_1", "x", 1, "ack:1:1")
    slow = notifier(FakeTelegram(timeout_send=True)).send("contact_1", "x", 1, "ack:1:1")
    assert (failed["status"], slow["status"]) == ("failed", "unknown")
    assert TOKEN not in json.dumps([failed, slow])
    assert notifier(FakeTelegram(), {"contact_1": "1001"}).send("contact_2", "x", 1, "ack:1:1")["status"] == "failed"


class CallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        engine = create_engine(f"sqlite:///{self.tmp.name}/t.db", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def tearDown(self):
        self.tmp.cleanup()

    def alerted(self, taps):
        """S2 through the real policy with Telegram as the channel, then the given button taps."""
        fake = FakeTelegram()
        tg = notifier(fake)

        async def go():
            m = SpatialRunManager(session_factory=self.db, notifier=tg, realtime=False, run_timeout_seconds=60,
                                  wearer_cancel_seconds=0.3, caregiver_ack_seconds=5)
            sid = await m.start("S2")
            while not session_snapshot(sid, self.db)["incidents"] or not fake.sent():
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            snap = session_snapshot(sid, self.db)
            iid = snap["incidents"][0]["id"]
            action_id = next(a["id"] for a in snap["actions"] if a["kind"] == "notify")
            results = [await handle_callback(tg, m, tap(iid, action_id, 501)) for tap in taps]
            await m.stop(sid)
            await m.acknowledge(iid, "test_cleanup")
            await m.wait(sid)
            return iid, results, session_snapshot(sid, self.db)
        return (*asyncio.run(go()), fake)

    @staticmethod
    def tap(chat, sender, message_id_shift=0, data=None):
        return lambda iid, action_id, mid: {"update_id": 1, "callback_query": {
            "id": "cb1", "from": {"id": int(sender)}, "data": data or f"ack:{iid}:{action_id}",
            "message": {"message_id": mid + message_id_shift, "chat": {"id": int(chat)}}}}

    def test_contact_1_tapping_the_alert_acknowledges_and_the_button_goes(self):
        iid, (result,), snap, fake = self.alerted([self.tap("1001", "1001")])
        self.assertEqual((result["contact"], result["result"]["applied"]), ("contact_1", True))
        acks = [a["target"] for a in snap["actions"] if a["kind"] == "ack"]
        self.assertEqual(acks[0], "telegram:contact_1")
        answers = [b for m, b in fake.calls if m == "answerCallbackQuery"]
        self.assertEqual(answers[0]["text"], f"Acknowledgement recorded for fall alert #{iid}.")
        self.assertIn(("editMessageReplyMarkup", {"chat_id": 1001, "message_id": 501, "reply_markup": {"inline_keyboard": []}}),
                      fake.calls)

    def test_a_stranger_or_another_message_cannot_acknowledge(self):
        iid, results, snap, fake = self.alerted([
            self.tap("999", "999"),                       # someone who found the bot
            self.tap("1001", "999"),                      # forwarded into contact 1's chat by someone else
            self.tap("1001", "1001", message_id_shift=7), # a different message
        ])
        self.assertEqual([r["result"] for r in results[:2]], [None, None])
        self.assertEqual(results[2]["result"]["reason"], "not_this_message")
        self.assertEqual([a["target"] for a in snap["actions"] if a["kind"] == "ack"], ["test_cleanup"])
        self.assertTrue(all("text" not in b for m, b in fake.calls if m == "answerCallbackQuery"))


def test_a_live_session_that_sends_for_real_needs_both_contacts():
    async def go():
        m = SpatialRunManager()
        m.use_telegram(notifier(FakeTelegram(), {"contact_1": "1001"}))
        try:
            await m.start_live(1, "esp32-01")
        except LiveNotReady as err:
            return str(err)
    assert asyncio.run(go()) == "contact setup"


class AckAfterTheFactTest(unittest.TestCase):
    setUp, tearDown, tap = CallbackTest.setUp, CallbackTest.tearDown, staticmethod(CallbackTest.tap)
    """Review findings 2 and 3 (2026-11-01)."""

    def run_to_escalation(self, fake, stop_first):
        tg = notifier(fake)

        async def go():
            m = SpatialRunManager(session_factory=self.db, notifier=tg, realtime=False, run_timeout_seconds=60,
                                  wearer_cancel_seconds=0.3, caregiver_ack_seconds=0.3, late_help_grace_seconds=0.2)
            sid = await m.start("S2")
            while not session_snapshot(sid, self.db)["incidents"]:
                await asyncio.sleep(0.02)
            if stop_first:
                await m.stop(sid)
            await m.wait(sid)                       # escalation sent, every task done, registry released
            snap = session_snapshot(sid, self.db)
            iid = snap["incidents"][0]["id"]
            escalation = next(a for a in snap["actions"] if a["kind"] == "escalate")
            # A lost reply leaves no message id; then any message carrying this button's data counts.
            tap = self.tap("1002", "1002")(iid, escalation["id"], escalation["detail"].get("message_id", 777))
            result = await handle_callback(tg, m, tap)
            return result, session_snapshot(sid, self.db), sid in m._locks
        return asyncio.run(go())

    def test_a_tap_counts_when_the_send_reply_was_lost(self):
        result, snap, _ = self.run_to_escalation(FakeTelegram(timeout_send=True), stop_first=False)
        escalation = next(a for a in snap["actions"] if a["kind"] == "escalate")
        self.assertEqual(escalation["detail"]["status"], "unknown")
        self.assertEqual((result["result"]["applied"], result["result"]["reason"]), (True, None))

    def test_contact_2_can_acknowledge_after_stop_once_the_run_is_gone(self):
        result, snap, registry_alive = self.run_to_escalation(FakeTelegram(), stop_first=True)
        self.assertFalse(registry_alive)
        self.assertEqual(snap["status"], "Stopped")
        self.assertEqual((result["result"]["applied"], result["result"]["reason"]), (True, None))
        self.assertIn("telegram:contact_2", [a["target"] for a in snap["actions"] if a["kind"] == "ack"])


def test_a_failed_callback_is_retried_from_the_same_offset_not_skipped():
    """Review finding 4: an error handling update 7 must not move the offset to 8."""
    from app.services import telegram_channel

    class Bot:
        def __init__(self):
            self.offsets, self.answered = [], []

        def get_updates(self, offset):
            self.offsets.append(offset)
            if len(self.offsets) > 4:
                raise asyncio.CancelledError
            pending = [u for u in (7, 8) if offset is None or u >= offset]
            return [{"update_id": u, "callback_query": {"id": str(u)}} for u in pending]

    handled, failures = [], {"7": 1}

    async def flaky_handle(notifier, manager, update):
        key = update["callback_query"]["id"]
        if failures.get(key):
            failures[key] -= 1
            raise RuntimeError("database busy")
        handled.append(int(key))

    bot = Bot()
    original = telegram_channel.handle_callback
    telegram_channel.handle_callback = flaky_handle
    try:
        try:
            asyncio.run(telegram_channel.poll_forever(bot, None, retry_seconds=0))
        except asyncio.CancelledError:
            pass
    finally:
        telegram_channel.handle_callback = original
    assert handled[:2] == [7, 8]
    assert bot.offsets[:3] == [None, None, 9]
