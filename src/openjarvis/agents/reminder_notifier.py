"""ReminderNotifier — polls Apple Reminders + Calendar, texts you when items are due.

Runs as a background thread (started from serve.py). Every poll cycle:
1. Queries Apple Reminders via AppleScript for items due within the window
2. Queries Apple Calendar via SQLite for events starting within the window
3. Sends an iMessage for each unnotified match (prefixed with "Jarvis:")
4. Tracks notified items in ~/.openjarvis/notified.json to prevent duplicates

Dedup uses a normalized name key (lowercase, stripped) so the same item
appearing across multiple Reminders lists or mirrored into Calendar
only triggers one notification.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_NOTIFIED_PATH = Path.home() / ".openjarvis" / "notified.json"
_POLL_INTERVAL = 120  # seconds between polls
_WINDOW_MINUTES = 4  # ±2 min from now

# Calendar names that mirror Reminders — skip these to avoid double-notify
_REMINDER_CALENDAR_NAMES = frozenset({
    "scheduled reminders",
    "reminders",
    "siri suggestions",
})


# ---------------------------------------------------------------------------
# Apple Reminders (AppleScript — no SQLite path available)
# ---------------------------------------------------------------------------


def _get_due_reminders(window_minutes: int = _WINDOW_MINUTES) -> List[Dict[str, str]]:
    """Query Apple Reminders for incomplete items due within the time window.

    Returns list of dicts: {name, list_name, body}.
    AppleScript handles the date comparison natively — no locale parsing needed.
    """
    half = (window_minutes * 60) // 2  # ±half the window in seconds
    script = f"""\
tell application "Reminders"
    set output to ""
    set now to current date
    set windowStart to now - {half}
    set windowEnd to now + {half}

    repeat with rList in every list
        set rems to every reminder of rList whose completed is false
        repeat with r in rems
            try
                set rDue to due date of r
                if rDue >= windowStart and rDue <= windowEnd then
                    set rName to name of r
                    set rListName to name of rList
                    set rBody to ""
                    try
                        set rBody to body of r
                    end try
                    set output to output & rName & "||" & rListName & "||" & rBody & return
                end if
            on error
                -- No due date set, skip
            end try
        end repeat
    end repeat
    return output
end tell"""

    try:
        result = _run_applescript(script)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.error("AppleScript not available for reminder poll")
        return []

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if "(-600)" not in stderr:
            logger.warning("Reminder poll AppleScript failed: %s", stderr)
        return []

    items: List[Dict[str, str]] = []
    seen_names: set[str] = set()
    for line in (result.stdout or "").strip().split("\n"):
        parts = line.split("||")
        if len(parts) >= 2 and parts[0].strip():
            name = parts[0].strip()
            # Deduplicate across lists within the same poll
            norm = _normalize(name)
            if norm in seen_names:
                continue
            seen_names.add(norm)
            body = parts[2].strip() if len(parts) > 2 else ""
            # AppleScript returns literal "missing value" for empty fields
            if body.lower() == "missing value":
                body = ""
            items.append({
                "name": name,
                "list_name": parts[1].strip(),
                "body": body,
            })
    return items


# ---------------------------------------------------------------------------
# Apple Calendar (SQLite — fast, no AppleScript)
# ---------------------------------------------------------------------------

_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_CAL_DB_PATH = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.com.apple.calendar"
    / "Calendar.sqlitedb"
)


def _get_due_events(
    window_minutes: int = _WINDOW_MINUTES,
    skip_names: frozenset[str] = frozenset(),
) -> List[Dict[str, str]]:
    """Query Apple Calendar SQLite for events starting within the time window.

    ``skip_names`` is a set of normalized event names to skip (already notified
    via the Reminders path).
    """
    import sqlite3

    if not _CAL_DB_PATH.exists():
        return []

    now_utc = datetime.now(tz=timezone.utc)
    half = timedelta(minutes=window_minutes / 2)
    start_ts = (now_utc - half - _APPLE_EPOCH).total_seconds()
    end_ts = (now_utc + half - _APPLE_EPOCH).total_seconds()

    try:
        conn = sqlite3.connect(f"file:{_CAL_DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []

    try:
        cal_map: Dict[int, str] = {}
        for row in conn.execute("SELECT ROWID, title FROM Calendar"):
            cal_map[row[0]] = row[1] or ""

        loc_map: Dict[int, str] = {}
        try:
            for row in conn.execute("SELECT ROWID, title FROM Location"):
                loc_map[row[0]] = row[1] or ""
        except sqlite3.OperationalError:
            pass

        rows = conn.execute(
            "SELECT summary, start_date, calendar_id, location_id "
            "FROM CalendarItem "
            "WHERE start_date >= ? AND start_date <= ? "
            "AND summary IS NOT NULL "
            "ORDER BY start_date ASC",
            (start_ts, end_ts),
        ).fetchall()

        events: List[Dict[str, str]] = []
        for row in rows:
            summary = row[0] or ""
            start_apple = row[1] or 0
            cal_id = row[2] or 0
            loc_id = row[3] or 0

            cal_name = cal_map.get(cal_id, "")

            # Skip calendars that mirror Apple Reminders
            if cal_name.lower().strip() in _REMINDER_CALENDAR_NAMES:
                continue

            # Skip events already notified via the Reminders path
            if _normalize(summary) in skip_names:
                continue

            start_dt = _APPLE_EPOCH + timedelta(seconds=start_apple)
            events.append({
                "name": summary,
                "calendar": cal_name,
                "location": loc_map.get(loc_id, ""),
                "start": start_dt.strftime("%I:%M %p"),
            })
        return events
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Notification dedup
# ---------------------------------------------------------------------------

_NORM_RE = re.compile(r"[^a-z0-9 ]")


def _normalize(name: str) -> str:
    """Normalize a name for dedup: lowercase, strip punctuation/whitespace."""
    return _NORM_RE.sub("", name.lower()).strip()


def _load_notified() -> Dict[str, float]:
    """Load the notified items map: {composite_key: timestamp}."""
    if not _NOTIFIED_PATH.exists():
        return {}
    try:
        return json.loads(_NOTIFIED_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_notified(data: Dict[str, float]) -> None:
    _NOTIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    _NOTIFIED_PATH.write_text(json.dumps(data), encoding="utf-8")


def _prune_old(data: Dict[str, float], max_age_hours: int = 24) -> Dict[str, float]:
    """Remove entries older than max_age_hours."""
    cutoff = time.time() - max_age_hours * 3600
    return {k: v for k, v in data.items() if v > cutoff}


def _make_key(name: str) -> str:
    """Create a dedup key from a normalized name only — no list/calendar distinction."""
    return _normalize(name)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

_JARVIS_PREFIX = "Jarvis:"


def _format_reminder_msg(item: Dict[str, str]) -> str:
    msg = f"{_JARVIS_PREFIX} Reminder — {item['name']}"
    if item.get("body"):
        msg += f"\n{item['body']}"
    return msg


def _format_event_msg(item: Dict[str, str]) -> str:
    if item.get("start"):
        msg = f"{_JARVIS_PREFIX} {item['name']} starts at {item['start']}"
    else:
        msg = f"{_JARVIS_PREFIX} {item['name']} — starting now"
    if item.get("location"):
        msg += f"\nLocation: {item['location']}"
    return msg


# ---------------------------------------------------------------------------
# AppleScript runner (shared with connector_tools)
# ---------------------------------------------------------------------------


def _run_applescript(script: str, *, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Run an AppleScript via a temp file to avoid -e timeout issues."""
    import os
    import tempfile

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


# ---------------------------------------------------------------------------
# Main poll + notify loop
# ---------------------------------------------------------------------------


def _poll_and_notify(phone: str) -> int:
    """Run one poll cycle. Returns number of notifications sent."""
    from openjarvis.channels.imessage_daemon import send_imessage

    notified = _prune_old(_load_notified())
    sent = 0
    notified_names: set[str] = set()  # track names sent this cycle

    # --- Reminders first (primary source) ---
    try:
        reminders = _get_due_reminders()
    except Exception:
        logger.exception("Reminder poll failed")
        reminders = []

    for item in reminders:
        key = _make_key(item["name"])
        if key in notified:
            notified_names.add(key)
            continue
        msg = _format_reminder_msg(item)
        if send_imessage(phone, msg):
            notified[key] = time.time()
            notified_names.add(key)
            sent += 1
            logger.info("Sent reminder notification: %s", item["name"])

    # --- Calendar events (skip anything already notified via Reminders) ---
    try:
        events = _get_due_events(skip_names=frozenset(notified_names) | frozenset(notified.keys()))
    except Exception:
        logger.exception("Calendar poll failed")
        events = []

    for item in events:
        key = _make_key(item["name"])
        if key in notified:
            continue
        msg = _format_event_msg(item)
        if send_imessage(phone, msg):
            notified[key] = time.time()
            sent += 1
            logger.info("Sent event notification: %s", item["name"])

    _save_notified(notified)
    return sent


def start_notifier(
    phone: str,
    *,
    poll_interval: float = _POLL_INTERVAL,
) -> threading.Thread:
    """Start the reminder/calendar notifier in a background thread.

    Returns the thread handle (daemon, so it dies with the process).
    """
    def _loop() -> None:
        logger.info(
            "ReminderNotifier started — polling every %ds, texting %s",
            poll_interval, phone,
        )
        while True:
            try:
                count = _poll_and_notify(phone)
                if count:
                    logger.info("ReminderNotifier sent %d notification(s)", count)
            except Exception:
                logger.exception("ReminderNotifier poll error")
            time.sleep(poll_interval)

    thread = threading.Thread(
        target=_loop, daemon=True, name="reminder-notifier",
    )
    thread.start()
    return thread
