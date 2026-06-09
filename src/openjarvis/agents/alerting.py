"""Desktop alerting for agent failures via macOS notifications."""

from __future__ import annotations

import logging
import subprocess

from openjarvis.core.events import EventBus, EventType

logger = logging.getLogger(__name__)


class AlertSubscriber:
    """Subscribe to agent error events and send macOS desktop notifications."""

    def __init__(self, bus: EventBus) -> None:
        bus.subscribe(EventType.AGENT_TICK_ERROR, self._on_error)

    def _on_error(self, event) -> None:
        data = event.data if hasattr(event, "data") else {}
        agent_name = data.get("agent_name", data.get("agent_id", "Unknown"))
        error_msg = str(data.get("error", ""))[:150]
        self._send_notification(
            title=f"Agent Failed: {agent_name}",
            body=error_msg or "Check logs for details",
        )

    @staticmethod
    def _send_notification(title: str, body: str) -> None:
        try:
            body_escaped = body.replace('"', '\\"').replace("\n", " ")
            title_escaped = title.replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{body_escaped}" with title "{title_escaped}"',
                ],
                capture_output=True,
                timeout=5,
            )
        except Exception as exc:
            logger.debug("Failed to send desktop notification: %s", exc)
