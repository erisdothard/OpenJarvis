#!/usr/bin/env python3
"""EventKit watcher — queries Calendar + Reminders, POSTs alerts.

Designed to run via launchd every 5 minutes. Tracks seen event IDs
in ~/.openjarvis/alert_seen.json to avoid duplicate alerts.

Requires: pyobjc-framework-EventKit, httpx
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

SEEN_PATH = Path.home() / ".openjarvis" / "alert_seen.json"
ALERT_URL = "http://localhost:8000/api/alert"
LOOKAHEAD_MINUTES = 10
SEEN_TTL_HOURS = 24


def _load_seen() -> dict[str, str]:
    """Load seen IDs, pruning entries older than SEEN_TTL_HOURS."""
    if not SEEN_PATH.exists():
        return {}
    try:
        data: dict[str, str] = json.loads(SEEN_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    cutoff = datetime.now() - timedelta(hours=SEEN_TTL_HOURS)
    return {
        k: v
        for k, v in data.items()
        if datetime.fromisoformat(v) > cutoff
    }


def _save_seen(seen: dict[str, str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2))


def _check_calendar(seen: dict[str, str]) -> list[dict]:
    """Query EventKit for events starting within LOOKAHEAD_MINUTES."""
    try:
        import EventKit  # type: ignore[import-untyped]
        from Foundation import NSDate  # type: ignore[import-untyped]
    except ImportError:
        print("pyobjc-framework-EventKit not installed", file=sys.stderr)
        return []

    store = EventKit.EKEventStore.alloc().init()
    now = NSDate.date()
    future = NSDate.dateWithTimeIntervalSinceNow_(LOOKAHEAD_MINUTES * 60)
    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        now, future, None
    )
    events = store.eventsMatchingPredicate_(predicate) or []

    alerts: list[dict] = []
    for ev in events:
        if ev.isAllDay():
            continue
        eid = ev.calendarItemIdentifier()
        if eid in seen:
            continue

        start_dt = ev.startDate()
        # NSDate → Python datetime
        from Foundation import NSTimeZone  # type: ignore[import-untyped]

        epoch = start_dt.timeIntervalSince1970()
        start_py = datetime.fromtimestamp(epoch)
        minutes_until = max(0, int((start_py - datetime.now()).total_seconds() / 60))

        alerts.append({
            "id": eid,
            "payload": {
                "type": "calendar",
                "title": ev.title() or "Untitled Event",
                "details": {
                    "start": start_py.strftime("%-I:%M %p"),
                    "minutes_until": minutes_until,
                    "calendar": ev.calendar().title() if ev.calendar() else "",
                    "location": ev.location() or "",
                },
            },
        })
    return alerts


def _check_reminders(seen: dict[str, str]) -> list[dict]:
    """Query EventKit for overdue incomplete reminders."""
    try:
        import EventKit  # type: ignore[import-untyped]
        from Foundation import NSDate  # type: ignore[import-untyped]
    except ImportError:
        return []

    store = EventKit.EKEventStore.alloc().init()
    # Fetch incomplete reminders due before now
    now = NSDate.date()
    predicate = store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(
        None, now, None
    )

    # fetchRemindersMatchingPredicate is async with a completion handler.
    # Use a simple semaphore to block until done.
    import threading

    result_holder: list[list] = [[]]
    sem = threading.Semaphore(0)

    def callback(reminders: list) -> None:
        result_holder[0] = list(reminders) if reminders else []
        sem.release()

    store.fetchRemindersMatchingPredicate_completion_(predicate, callback)
    sem.acquire(timeout=10)

    alerts: list[dict] = []
    for rem in result_holder[0]:
        rid = rem.calendarItemIdentifier()
        if rid in seen:
            continue

        due_date = rem.dueDateComponents()
        due_str = ""
        if due_date and due_date.date():
            from Foundation import NSCalendar  # type: ignore[import-untyped]

            cal = NSCalendar.currentCalendar()
            ns_date = cal.dateFromComponents_(due_date)
            if ns_date:
                epoch = ns_date.timeIntervalSince1970()
                due_py = datetime.fromtimestamp(epoch)
                due_str = due_py.strftime("%b %d, %-I:%M %p")

        alerts.append({
            "id": rid,
            "payload": {
                "type": "reminder",
                "title": rem.title() or "Untitled Reminder",
                "details": {
                    "due": due_str,
                    "list": rem.calendar().title() if rem.calendar() else "",
                },
            },
        })
    return alerts


def _post_alert(payload: dict) -> bool:
    """POST alert to the local Jarvis server."""
    try:
        import httpx

        resp = httpx.post(ALERT_URL, json=payload, timeout=5)
        return resp.status_code < 300
    except Exception as exc:
        print(f"Failed to POST alert: {exc}", file=sys.stderr)
        return False


def main() -> None:
    seen = _load_seen()
    alerts = _check_calendar(seen) + _check_reminders(seen)

    if not alerts:
        return

    for alert in alerts:
        if _post_alert(alert["payload"]):
            seen[alert["id"]] = datetime.now().isoformat()

    _save_seen(seen)


if __name__ == "__main__":
    main()
