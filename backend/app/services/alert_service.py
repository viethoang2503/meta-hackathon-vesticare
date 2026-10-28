"""Alert service — Phase 5.

Alert creation and cooldown logic lives in fall_detection.py and sensor.py.
Telegram notifications are dispatched via telegram_bot.send_fall_alert().
Feedback (valid / false alert) is stored on AlertEvent.feedback via
POST /api/alerts/feedback/{id}.
"""
