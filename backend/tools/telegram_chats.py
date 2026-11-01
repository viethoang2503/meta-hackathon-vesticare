"""Lists who has messaged the bot, so their chat ids can go into backend/.env.

    .verify/venv/bin/python backend/tools/telegram_chats.py

Reads TELEGRAM_BOT_TOKEN from backend/.env and prints chat id, name and last text.
The token itself is never printed. Stop the backend first: Telegram serves updates to
one reader at a time, and the backend's own polling would take them.
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import TELEGRAM_BOT_TOKEN  # noqa: E402


def main() -> int:
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN is not set in backend/.env")
        return 1
    try:
        body = httpx.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                          json={"allowed_updates": ["message"]}, timeout=10).json()
    except httpx.HTTPError as err:
        print(f"Telegram request failed: {type(err).__name__}")
        return 1
    if not body.get("ok"):
        print(f"Telegram refused: {body.get('description')}")
        return 1
    chats = {}
    for update in body["result"]:
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat:
            chats[chat["id"]] = (chat.get("first_name") or chat.get("title") or "?", msg.get("text", ""))
    if not chats:
        print("Nobody has messaged the bot yet. Send it \"hi\" from each contact's phone, then run this again.")
    for chat_id, (name, text) in chats.items():
        print(f"chat id {chat_id}  name {name!r}  last message {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
