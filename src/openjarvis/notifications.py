"""Centralized Telegram notification sender.

All outbound notifications (reminders, alerts, check-in, social publish, etc.)
go through this module. Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from
environment (loaded from ~/.openjarvis/cloud-keys.env at startup).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def send_telegram(
    message: str,
    *,
    chat_id: Optional[str] = None,
) -> bool:
    """Send a Telegram message via the Bot API.

    Args:
        message: Text body to send.
        chat_id: Override the default TELEGRAM_CHAT_ID from env.

    Returns:
        True if the message was sent successfully.
    """
    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — cannot send notification")
        return False

    target = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not target:
        logger.warning("TELEGRAM_CHAT_ID not set — cannot send notification")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": target, "text": message},
            timeout=10.0,
        )
        if resp.status_code >= 300:
            logger.warning(
                "Telegram API error %d: %s", resp.status_code, resp.text
            )
            return False
        return True
    except Exception:
        logger.exception("Telegram send failed")
        return False
