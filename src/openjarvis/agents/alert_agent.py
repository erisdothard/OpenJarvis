"""AlertAgent — DEPRECATED.

Superseded by the event-driven alert system:
  - POST /api/alert endpoint (alert_routes.py)
  - scripts/event_watcher.py (launchd-driven EventKit watcher)
  - gmail_push.py (Gmail Pub/Sub real-time listener)

This module remains for backward compatibility but is no longer
auto-started by serve.py. It will be removed in a future version.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_PHONE = "+16152439891"
DEFAULT_INTERVAL_SECONDS = 600  # 10 minutes
CALENDAR_LOOKAHEAD_MINUTES = 15
ALERT_COOLDOWN_HOURS = 12  # don't re-alert the same item for 12h


# ── Data collectors ─────────────────────────────────────────────────────────

def _run_applescript(script: str, *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    fd, path = tempfile.mkstemp(suffix=".scpt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        return subprocess.run(
            ["osascript", path],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _collect_upcoming_events(lookahead_minutes: int = CALENDAR_LOOKAHEAD_MINUTES) -> List[Dict[str, Any]]:
    """Calendar events starting within the next N minutes."""
    try:
        from openjarvis.connectors.apple_calendar import _applescript_get_events

        now = datetime.now()
        window_end = now + timedelta(minutes=lookahead_minutes)
        events = _applescript_get_events(now, window_end)
        results = []
        for e in events:
            # Skip all-day events (not urgent)
            if e.get("all_day"):
                continue
            results.append({
                "type": "calendar",
                "summary": e.get("summary", "Unknown event"),
                "start": e.get("start", ""),
                "calendar": e.get("calendar", ""),
            })
        return results
    except Exception as exc:
        logger.debug("Alert: failed to fetch calendar events: %s", exc)
        return []


def _collect_overdue_reminders() -> List[Dict[str, Any]]:
    """Overdue Apple Reminders."""
    script = '''tell application "Reminders"
    set output to ""
    set rList to every reminder whose completed is false
    repeat with r in rList
        set rName to name of r
        set rDue to "none"
        try
            set rDue to due date of r as string
        end try
        set output to output & rName & "||" & rDue & return
    end repeat
    return output
end tell'''

    try:
        result = _run_applescript(script)
        if result.returncode != 0:
            return []

        reminders: List[Dict[str, Any]] = []
        now = datetime.now()
        for line in result.stdout.strip().split("\n"):
            parts = line.split("||")
            if len(parts) < 2:
                continue
            name = parts[0].strip()
            due_str = parts[1].strip()
            if not due_str or due_str == "none":
                continue
            try:
                clean_due = due_str.replace("\u202f", " ")
                due_dt = datetime.strptime(clean_due, "%A, %B %d, %Y at %I:%M:%S %p")
                if due_dt < now:
                    reminders.append({
                        "type": "reminder",
                        "summary": name,
                        "due": due_str,
                    })
            except ValueError:
                continue
        return reminders
    except Exception as exc:
        logger.debug("Alert: failed to fetch reminders: %s", exc)
        return []


def _collect_important_emails(max_count: int = 5) -> List[Dict[str, Any]]:
    """Recent important unread emails (starred/flagged + unread)."""
    import imaplib
    import socket

    try:
        from openjarvis.core.registry import ConnectorRegistry

        connector_cls = ConnectorRegistry.get("gmail")
        if connector_cls is None:
            return []
        connector = connector_cls()
        if not connector.is_connected():
            return []

        email_addr = getattr(connector, "_email", None)
        password = getattr(connector, "_password", None) or getattr(connector, "_app_password", None)
        if not email_addr or not password:
            return []

        results: List[Dict[str, Any]] = []
        imap = None
        try:
            import email as email_mod
            from email.header import decode_header

            imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=5)
            imap.login(email_addr, password)
            imap.select("INBOX", readonly=True)
            _status, data = imap.search(None, "FLAGGED", "UNSEEN")
            ids = data[0].split() if data and data[0] else []

            for msg_id in ids[-max_count:]:
                _status, msg_data = imap.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                msg = email_mod.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())
                subject = msg.get("Subject", "(no subject)")
                decoded_parts = decode_header(subject)
                subject = " ".join(
                    part.decode(enc or "utf-8") if isinstance(part, bytes) else part
                    for part, enc in decoded_parts
                )
                sender = msg.get("From", "unknown")
                results.append({
                    "type": "email",
                    "summary": subject,
                    "from": sender,
                })
        except (socket.timeout, imaplib.IMAP4.error, OSError) as exc:
            logger.debug("Alert: IMAP search failed: %s", exc)
        finally:
            if imap:
                try:
                    imap.logout()
                except Exception:
                    pass

        return results
    except Exception as exc:
        logger.debug("Alert: failed to fetch emails: %s", exc)
        return []


# ── Alert deduplication ─────────────────────────────────────────────────────

class _AlertDedup:
    """In-memory dedup with time-based expiry."""

    def __init__(self, cooldown_hours: float = ALERT_COOLDOWN_HOURS):
        self._sent: Dict[str, float] = {}
        self._cooldown = cooldown_hours * 3600

    def _key(self, item: Dict[str, Any]) -> str:
        raw = f"{item.get('type', '')}:{item.get('summary', '')}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def should_send(self, item: Dict[str, Any]) -> bool:
        key = self._key(item)
        now = time.time()
        # Evict expired entries
        self._sent = {k: v for k, v in self._sent.items() if now - v < self._cooldown}
        return key not in self._sent

    def mark_sent(self, item: Dict[str, Any]) -> None:
        self._sent[self._key(item)] = time.time()


# ── Message formatting ──────────────────────────────────────────────────────

def _format_alert_message(items: List[Dict[str, Any]]) -> str:
    """Build a single consolidated alert message."""
    lines = ["JARVIS ALERT"]
    lines.append("")

    calendar = [i for i in items if i["type"] == "calendar"]
    reminders = [i for i in items if i["type"] == "reminder"]
    emails = [i for i in items if i["type"] == "email"]

    if calendar:
        lines.append("UPCOMING:")
        for e in calendar:
            start = e.get("start", "")
            try:
                dt = datetime.fromisoformat(start)
                t = dt.strftime("%-I:%M %p")
            except (ValueError, TypeError):
                t = start
            lines.append(f"  {t} — {e['summary']}")
        lines.append("")

    if reminders:
        lines.append("OVERDUE REMINDERS:")
        for r in reminders:
            lines.append(f"  • {r['summary']}")
        lines.append("")

    if emails:
        lines.append("FLAGGED EMAILS:")
        for e in emails:
            sender = e.get("from", "").split("<")[0].strip().strip('"')
            lines.append(f"  • {e['summary']} (from {sender})")

    return "\n".join(lines)


# ── iMessage sender ─────────────────────────────────────────────────────────

def _send_text(phone: str, message: str) -> bool:
    """Send a notification via Telegram."""
    from openjarvis.notifications import send_telegram

    return send_telegram(message)


# ── Main loop ───────────────────────────────────────────────────────────────

class AlertAgent:
    """Background thread that checks sources and sends SMS alerts."""

    def __init__(
        self,
        phone: str = DEFAULT_PHONE,
        interval: int = DEFAULT_INTERVAL_SECONDS,
        lookahead_minutes: int = CALENDAR_LOOKAHEAD_MINUTES,
    ):
        self.phone = phone
        self.interval = interval
        self.lookahead = lookahead_minutes
        self._dedup = _AlertDedup()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alert-agent")
        self._thread.start()
        logger.info("AlertAgent started (phone=%s, interval=%ds)", self.phone, self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("AlertAgent stopped")

    def check_and_alert(self) -> int:
        """Run one check cycle. Returns number of alerts sent."""
        items: List[Dict[str, Any]] = []

        # Collect from all sources
        try:
            items.extend(_collect_upcoming_events(self.lookahead))
        except Exception as exc:
            logger.debug("Alert calendar check failed: %s", exc)

        try:
            items.extend(_collect_overdue_reminders())
        except Exception as exc:
            logger.debug("Alert reminder check failed: %s", exc)

        try:
            items.extend(_collect_important_emails())
        except Exception as exc:
            logger.debug("Alert email check failed: %s", exc)

        if not items:
            return 0

        # Filter to unsent alerts
        new_items = [i for i in items if self._dedup.should_send(i)]
        if not new_items:
            return 0

        # Build and send consolidated message
        message = _format_alert_message(new_items)
        success = _send_text(self.phone, message)

        if success:
            for item in new_items:
                self._dedup.mark_sent(item)
            logger.info("AlertAgent sent %d alerts to %s", len(new_items), self.phone)
        else:
            logger.error("AlertAgent failed to send iMessage to %s", self.phone)

        return len(new_items) if success else 0

    def _loop(self) -> None:
        # Initial delay — let the server finish starting
        self._stop.wait(30)

        while not self._stop.is_set():
            try:
                self.check_and_alert()
            except Exception as exc:
                logger.error("AlertAgent tick failed: %s", exc)
            self._stop.wait(self.interval)


# ── Convenience for serve.py integration ────────────────────────────────────

_instance: Optional[AlertAgent] = None


def start_alert_agent(
    phone: str = DEFAULT_PHONE,
    interval: int = DEFAULT_INTERVAL_SECONDS,
) -> AlertAgent:
    """Start the global alert agent singleton."""
    global _instance
    if _instance is not None:
        _instance.stop()
    _instance = AlertAgent(phone=phone, interval=interval)
    _instance.start()
    return _instance


def stop_alert_agent() -> None:
    """Stop the global alert agent if running."""
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None
