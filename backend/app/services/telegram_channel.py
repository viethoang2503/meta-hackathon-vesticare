"""
telegram_channel.py

The MVP 2 caregiver channel (docs/spatial/MVP2_ARCHITECTURE.md, "Telegram và quyền quyết
định"): messages with an Acknowledge button, and a long-polling loop that turns a tap into
an acknowledgement through the same policy as every other ack.

Error texts never carry the request URL: it contains the bot token.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from app.services.notify_templates import render

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
ACK_DATA = re.compile(r"^ack:(\d+)$")
POLL_SECONDS = 25


class TelegramNotifier:
    channel = "telegram"

    def __init__(self, token: str, chat_ids: dict[str, str], timeout_seconds: float = 5.0,
                 transport: httpx.BaseTransport | None = None):
        self._base = f"{API_BASE}/bot{token}"
        self._chat_ids = {contact: str(chat) for contact, chat in chat_ids.items() if chat}
        self._timeout = timeout_seconds
        self._transport = transport

    def configured_contacts(self) -> set[str]:
        return set(self._chat_ids)

    def contact_for_chat(self, chat_id) -> str | None:
        return next((c for c, chat in self._chat_ids.items() if chat == str(chat_id)), None)

    def _call(self, method: str, payload: dict, timeout: float | None = None) -> dict:
        with httpx.Client(transport=self._transport, timeout=timeout or self._timeout) as client:
            return client.post(f"{self._base}/{method}", json=payload).json()

    def send(self, contact: str, text: str, incident_id: int, ack_button: bool) -> dict:
        chat_id = self._chat_ids.get(contact)
        if chat_id is None:
            return {"status": "failed", "error": f"{contact} has no chat id"}
        payload = {"chat_id": chat_id, "text": text}
        if ack_button:
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "Acknowledge", "callback_data": f"ack:{incident_id}"}]]}
        try:
            body = self._call("sendMessage", payload)
        except httpx.TimeoutException:
            return {"status": "unknown", "error": "Telegram did not answer in time"}
        except httpx.HTTPError as err:
            return {"status": "unknown", "error": type(err).__name__}
        except ValueError:
            return {"status": "unknown", "error": "Telegram answered with something that is not JSON"}
        if body.get("ok"):
            return {"status": "sent", "message_id": body["result"]["message_id"], "chat_id": chat_id}
        return {"status": "failed", "error": str(body.get("description", "rejected"))[:200]}

    def answer_callback(self, callback_id: str, text: str | None) -> None:
        payload = {"callback_query_id": callback_id, **({"text": text} if text else {})}
        try:
            self._call("answerCallbackQuery", payload)
        except (httpx.HTTPError, ValueError):
            logger.warning("answerCallbackQuery failed")

    def remove_button(self, chat_id, message_id: int) -> None:
        try:
            self._call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                  "reply_markup": {"inline_keyboard": []}})
        except (httpx.HTTPError, ValueError):
            logger.warning("could not remove the Acknowledge button")

    def get_updates(self, offset: int | None) -> list[dict]:
        payload = {"timeout": POLL_SECONDS, "allowed_updates": ["callback_query"],
                   **({"offset": offset} if offset is not None else {})}
        body = self._call("getUpdates", payload, timeout=POLL_SECONDS + 10)
        if not body.get("ok"):
            raise RuntimeError(str(body.get("description", "getUpdates rejected"))[:200])
        return body["result"]


async def handle_callback(notifier: TelegramNotifier, manager, update: dict) -> dict:
    """One button tap. Only a configured contact, on the very message that alerted them, acknowledges."""
    query = update["callback_query"]
    message = query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    contact = notifier.contact_for_chat(chat_id)
    sender_ok = contact is not None and notifier.contact_for_chat(query.get("from", {}).get("id")) == contact
    match = ACK_DATA.match(query.get("data") or "")
    result = None
    if sender_ok and match:
        result = await manager.telegram_ack(int(match.group(1)), contact, message.get("message_id"))
    if result and (result["applied"] or result["reason"] == "already_recorded"):
        incident_id = int(match.group(1))
        await asyncio.to_thread(notifier.answer_callback, query["id"], render("ack_received", incident_id=incident_id))
        await asyncio.to_thread(notifier.remove_button, chat_id, message.get("message_id"))
    else:
        await asyncio.to_thread(notifier.answer_callback, query["id"], None)
    return {"contact": contact, "sender_ok": sender_ok, "result": result}


async def poll_forever(notifier: TelegramNotifier, manager) -> None:
    """getUpdates long polling; the offset only moves past updates that were handled."""
    offset = None
    while True:
        try:
            updates = await asyncio.to_thread(notifier.get_updates, offset)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.warning("Telegram polling failed (%s); retrying in 5 s", type(err).__name__)
            await asyncio.sleep(5)
            continue
        for update in updates:
            if "callback_query" in update:
                try:
                    await handle_callback(notifier, manager, update)
                except Exception:
                    logger.exception("Telegram callback %s failed", update.get("update_id"))
            offset = update["update_id"] + 1
