"""Apple Calendar connector — reads directly from the macOS Calendar SQLite database.

No API calls, no OAuth, no AppleScript.  The connector opens
``~/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb``
in read-only mode and yields one :class:`Document` per event.

Requires **Full Disk Access** granted to the process in
System Settings → Privacy & Security → Full Disk Access.

Timestamp notes
---------------
The Calendar database stores timestamps as seconds since the Apple
epoch of 2001-01-01 00:00:00 UTC.
"""

from __future__ import annotations

import logging
import subprocess
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = (
    Path.home()
    / "Library"
    / "Group Containers"
    / "group.com.apple.calendar"
    / "Calendar.sqlitedb"
)

# Apple epoch: 2001-01-01 00:00:00 UTC
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _apple_ts_to_datetime(apple_seconds: float) -> datetime:
    """Convert an Apple seconds timestamp to a UTC datetime."""
    return _APPLE_EPOCH + timedelta(seconds=apple_seconds)


def _datetime_to_apple_ts(dt: datetime) -> float:
    """Convert a UTC datetime to Apple seconds timestamp."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - _APPLE_EPOCH).total_seconds()


# ---------------------------------------------------------------------------
# Helpers (used by tools in connector_tools.py)
# ---------------------------------------------------------------------------


def _run_applescript(script: str, *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Run an AppleScript via a temp file to avoid subprocess -e timeouts."""
    import tempfile
    import os

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


def _applescript_get_events(
    start_dt: datetime, end_dt: datetime
) -> List[Dict[str, Any]]:
    """Read calendar events between two datetimes using the SQLite connector.

    Falls back to the fast SQLite approach rather than slow AppleScript
    iteration over all calendars (which times out on holiday calendars).

    Returns a list of dicts with keys: summary, start, end, calendar,
    location, all_day, notes.
    """
    connector = AppleCalendarConnector()
    if not connector.is_connected():
        logger.warning("Apple Calendar SQLite not accessible")
        return []

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    events: List[Dict[str, Any]] = []
    for doc in connector.sync(since=start_dt):
        if doc.timestamp and doc.timestamp > end_dt:
            continue
        meta = doc.metadata or {}
        events.append({
            "summary": doc.title or "",
            "start": meta.get("start", ""),
            "end": meta.get("end", ""),
            "calendar": meta.get("calendar", ""),
            "location": meta.get("location", ""),
            "all_day": meta.get("all_day", False),
            "notes": (doc.content or "").split("Notes: ")[-1] if "Notes: " in (doc.content or "") else "",
        })
    return events


def _applescript_create_event(
    *,
    summary: str,
    start_dt: datetime,
    end_dt: datetime,
    calendar_name: str = "Home",
    location: str = "",
    notes: str = "",
    all_day: bool = False,
) -> Dict[str, Any]:
    """Create a calendar event via AppleScript.

    Returns dict with uid on success, or error info on failure.
    """
    escaped_summary = summary.replace("\\", "\\\\").replace('"', '\\"')
    start_str = start_dt.strftime("%B %d, %Y at %I:%M:%S %p")
    end_str = end_dt.strftime("%B %d, %Y at %I:%M:%S %p")

    props = f'summary:"{escaped_summary}", start date:(date "{start_str}"), end date:(date "{end_str}")'
    if all_day:
        props += ", allday event:true"
    if location:
        escaped_loc = location.replace("\\", "\\\\").replace('"', '\\"')
        props += f', location:"{escaped_loc}"'
    if notes:
        escaped_notes = notes.replace("\\", "\\\\").replace('"', '\\"')
        props += f', description:"{escaped_notes}"'

    escaped_cal = calendar_name.replace("\\", "\\\\").replace('"', '\\"')

    # Ensure Calendar.app is running — AppleScript 'launch' / 'activate'
    # fails with -600 on macOS Sequoia+; 'open -a' is reliable.
    subprocess.run(["open", "-a", "Calendar"], check=False, timeout=5)
    import time as _time
    _time.sleep(1)

    script = (
        'tell application "Calendar"\n'
        f'  tell calendar "{escaped_cal}"\n'
        f"    set newEvent to make new event with properties {{{props}}}\n"
        "    return uid of newEvent\n"
        "  end tell\n"
        "end tell"
    )

    try:
        result = _run_applescript(script)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"success": False, "error": "osascript not available"}

    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip()}

    return {
        "success": True,
        "uid": result.stdout.strip(),
        "summary": summary,
        "start": start_str,
        "end": end_str,
        "calendar": calendar_name,
    }


@ConnectorRegistry.register("apple_calendar")
class AppleCalendarConnector(BaseConnector):
    """Read-only Apple Calendar connector using direct SQLite access."""

    connector_id = "apple_calendar"
    display_name = "Apple Calendar"
    auth_type = "local"

    def __init__(self, db_path: str = "") -> None:
        self._db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._items_synced: int = 0
        self._items_total: int = 0
        self._last_sync: Optional[datetime] = None

    def is_connected(self) -> bool:
        """Return True if Calendar.sqlitedb exists and is readable."""
        if not self._db_path.exists():
            return False
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True
            )
            conn.execute("SELECT 1 FROM CalendarItem LIMIT 1")
            conn.close()
            return True
        except sqlite3.OperationalError:
            logger.warning(
                "Apple Calendar database exists at %s but cannot be read. "
                "Grant Full Disk Access to this process in "
                "System Settings → Privacy & Security → Full Disk Access.",
                self._db_path,
            )
            return False

    def sync(
        self,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,
        **kwargs: Any,
    ) -> Iterator[Document]:
        """Read events from Calendar.sqlitedb and yield one Document each."""
        db_path = str(self._db_path)

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            logger.warning(
                "Cannot open Apple Calendar database at %s — "
                "Full Disk Access is likely not granted.",
                db_path,
            )
            return

        try:
            # Build calendar_id → title map
            cal_map: Dict[int, str] = {}
            for row in conn.execute("SELECT ROWID, title FROM Calendar"):
                cal_map[row[0]] = row[1] or ""

            # Build location_id → title map
            loc_map: Dict[int, str] = {}
            try:
                for row in conn.execute("SELECT ROWID, title FROM Location"):
                    loc_map[row[0]] = row[1] or ""
            except sqlite3.OperationalError:
                pass  # Location table may not exist

            # Query events — filter by date if since is provided
            if since is not None:
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
                since_ts = _datetime_to_apple_ts(since)
                rows = conn.execute(
                    "SELECT ROWID, summary, description, start_date, end_date, "
                    "  all_day, calendar_id, location_id, UUID "
                    "FROM CalendarItem "
                    "WHERE start_date >= ? AND summary IS NOT NULL "
                    "ORDER BY start_date ASC",
                    (since_ts,),
                ).fetchall()
            else:
                # Default: last 30 days through 30 days ahead
                now = datetime.now(tz=timezone.utc)
                start_ts = _datetime_to_apple_ts(now - timedelta(days=30))
                end_ts = _datetime_to_apple_ts(now + timedelta(days=30))
                rows = conn.execute(
                    "SELECT ROWID, summary, description, start_date, end_date, "
                    "  all_day, calendar_id, location_id, UUID "
                    "FROM CalendarItem "
                    "WHERE start_date >= ? AND start_date <= ? "
                    "  AND summary IS NOT NULL "
                    "ORDER BY start_date ASC",
                    (start_ts, end_ts),
                ).fetchall()

            self._items_total = len(rows)
            synced = 0

            for row in rows:
                rowid = row[0]
                summary = row[1] or ""
                description = row[2] or ""
                start_date = row[3] or 0.0
                end_date = row[4] or 0.0
                all_day = row[5] or 0
                calendar_id = row[6] or 0
                location_id = row[7] or 0
                uuid = row[8] or str(rowid)

                start_dt = _apple_ts_to_datetime(start_date)
                end_dt = _apple_ts_to_datetime(end_date)
                cal_name = cal_map.get(calendar_id, "")
                location = loc_map.get(location_id, "")

                content_parts = [f"{summary}"]
                content_parts.append(
                    f"Start: {start_dt.strftime('%Y-%m-%d %H:%M')}"
                )
                if end_date:
                    content_parts.append(
                        f"End: {end_dt.strftime('%Y-%m-%d %H:%M')}"
                    )
                if all_day:
                    content_parts.append("All day event")
                if location:
                    content_parts.append(f"Location: {location}")
                if cal_name:
                    content_parts.append(f"Calendar: {cal_name}")
                if description:
                    content_parts.append(f"Notes: {description}")

                doc = Document(
                    doc_id=f"apple_calendar:{uuid}",
                    source="apple_calendar",
                    doc_type="event",
                    content="\n".join(content_parts),
                    title=summary,
                    timestamp=start_dt,
                    metadata={
                        "start": start_dt.isoformat(),
                        "end": end_dt.isoformat(),
                        "all_day": bool(all_day),
                        "location": location,
                        "calendar": cal_name,
                    },
                )
                synced += 1
                yield doc

            self._items_synced = synced
            self._last_sync = datetime.now(tz=timezone.utc)

        finally:
            conn.close()

    def sync_status(self) -> SyncStatus:
        """Return sync progress from the most recent sync call."""
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            items_total=self._items_total,
            last_sync=self._last_sync,
        )

    def disconnect(self) -> None:
        """No-op — local connector, nothing to disconnect."""
        pass

    def mcp_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="apple_calendar_today",
                description="Get today's events from Apple Calendar.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                category="productivity",
            ),
            ToolSpec(
                name="apple_calendar_upcoming",
                description="Get upcoming events from Apple Calendar for the next N days.",
                parameters={
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Number of days to look ahead",
                            "default": 7,
                        },
                    },
                    "required": [],
                },
                category="productivity",
            ),
        ]
