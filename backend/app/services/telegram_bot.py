"""Telegram alert bot — Phase 5.

Sends FALL detection notifications to a configured Telegram chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment (.env).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


def send_fall_alert(
    *,
    alert_id: int,
    patient_id: int,
    confidence: float,
    peak_svm: float,
    timestamp: datetime,
    alert_type: str = "FALL",
) -> tuple[bool, str | None]:
    """Send an alert notification to the configured Telegram chat.

    Supports alert_type: "FALL" and "VESTIBULAR_INSTABILITY".

    Returns (sent, error) where:
      - (True, None)               — message delivered successfully
      - (False, "not_configured")  — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID absent
      - (False, "<error text>")    — HTTP or network failure

    Never raises — callers must not crash if Telegram is unavailable.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug(
            "Telegram not configured — skipping notification for alert_id=%d", alert_id
        )
        return False, "not_configured"

    _VN_TZ = timezone(timedelta(hours=7))
    ts_str = timestamp.astimezone(_VN_TZ).strftime("%Y-%m-%d %H:%M:%S (ICT)")
    if alert_type == "VESTIBULAR_INSTABILITY":
        emoji, title = "\u26a0\ufe0f", "VESTIBULAR INSTABILITY"
    else:
        emoji, title = "\U0001f6a8", "FALL DETECTED"

    text = (
        f"{emoji} *{title}*\n"
        f"Alert ID: `{alert_id}`\n"
        f"Patient: `{patient_id}`\n"
        f"Confidence: `{confidence * 100:.1f}%`\n"
        f"Peak SVM: `{peak_svm:.3f} g`\n"
        f"Time: `{ts_str}`"
    )

    url = f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5.0,
        )
        resp.raise_for_status()
        logger.info("Telegram notification sent for alert_id=%d", alert_id)
        return True, None
    except Exception as exc:
        # str(exc) of an httpx error includes the request URL, which carries the bot token.
        logger.warning(
            "Telegram notification failed for alert_id=%d: %s", alert_id, type(exc).__name__
        )
        return False, type(exc).__name__
