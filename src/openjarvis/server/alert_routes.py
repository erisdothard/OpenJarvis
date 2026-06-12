"""Alert webhook — receives structured alert payloads and sends iMessage.

POST /api/alert accepts a JSON payload with event type and details,
formats a clean text message, and sends it via iMessage. No LLM, no
approval store, no polling — just receive, format, send.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

alert_router = APIRouter(prefix="/api", tags=["alerts"])

DEFAULT_PHONE = "+16152439891"


class AlertPayload(BaseModel):
    type: Literal["calendar", "reminder", "email"]
    title: str
    details: Dict[str, Any] = {}


def _format_alert(payload: AlertPayload) -> str:
    """Format an alert payload into clean iMessage text."""
    lines: list[str] = []

    if payload.type == "calendar":
        lines.append("JARVIS — Calendar")
        lines.append("")
        lines.append(payload.title)
        if payload.details.get("minutes_until"):
            lines.append(f"Starts in {payload.details['minutes_until']} min")
        if payload.details.get("start"):
            lines.append(f"At {payload.details['start']}")
        if payload.details.get("calendar"):
            lines.append(f"Calendar: {payload.details['calendar']}")
        if payload.details.get("location"):
            lines.append(f"Location: {payload.details['location']}")

    elif payload.type == "reminder":
        lines.append("JARVIS — Reminder")
        lines.append("")
        lines.append(payload.title)
        if payload.details.get("due"):
            lines.append(f"Due: {payload.details['due']}")
        if payload.details.get("list"):
            lines.append(f"List: {payload.details['list']}")

    elif payload.type == "email":
        lines.append("JARVIS — Email")
        lines.append("")
        lines.append(payload.title)
        if payload.details.get("sender"):
            lines.append(f"From: {payload.details['sender']}")
        if payload.details.get("snippet"):
            lines.append(f"Preview: {payload.details['snippet'][:120]}")

    return "\n".join(lines)


def _send(phone: str, message: str) -> bool:
    """Send an iMessage. Thin wrapper around the daemon helper."""
    try:
        from openjarvis.channels.imessage_daemon import send_imessage

        return send_imessage(phone, message)
    except ImportError:
        logger.error("imessage_daemon not available")
        return False


@alert_router.post("/alert")
async def receive_alert(
    payload: AlertPayload, request: Request
) -> Dict[str, Any]:
    """Receive an alert and send it via iMessage."""
    phone = DEFAULT_PHONE
    try:
        phone = request.app.state.config.alerts.phone or DEFAULT_PHONE
    except AttributeError:
        pass

    message = _format_alert(payload)
    loop = asyncio.get_event_loop()
    sent = await loop.run_in_executor(None, _send, phone, message)

    if sent:
        logger.info("Alert sent to %s: %s", phone, payload.title)
    else:
        logger.error("Failed to send alert to %s: %s", phone, payload.title)

    return {"sent": sent, "message": message}
