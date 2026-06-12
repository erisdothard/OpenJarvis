"""Glance endpoint — at-a-glance status data for the dashboard.

Returns: today's events, overdue reminders, unread email count,
weather, disk space, Ollama status, and server uptime.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter

logger = logging.getLogger(__name__)

glance_router = APIRouter(prefix="/api", tags=["glance"])

_SERVER_START_TIME = time.monotonic()


def _get_today_events() -> List[Dict[str, Any]]:
    """Get today's calendar events via the SQLite connector."""
    try:
        from openjarvis.connectors.apple_calendar import _applescript_get_events

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + timedelta(days=1)
        events = _applescript_get_events(today, tomorrow)
        return [
            {
                "summary": e.get("summary", ""),
                "start": e.get("start", ""),
                "end": e.get("end", ""),
                "calendar": e.get("calendar", ""),
                "all_day": e.get("all_day", False),
            }
            for e in events
        ]
    except Exception as exc:
        logger.debug("Failed to fetch calendar events: %s", exc)
        return []


def _run_applescript(script: str, *, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run AppleScript via temp .scpt file."""
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


def _get_overdue_reminders() -> List[Dict[str, Any]]:
    """Get overdue and upcoming reminders via AppleScript."""
    script = '''tell application "Reminders"
    set output to ""
    set rList to every reminder of list "Reminders" whose completed is false
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
            is_overdue = False
            if due_str and due_str != "none":
                try:
                    # macOS date format: "Thursday, April 16, 2026 at 12:00:00 PM"
                    # Remove narrow no-break space (\u202f) that macOS inserts
                    clean_due = due_str.replace("\u202f", " ")
                    due_dt = datetime.strptime(clean_due, "%A, %B %d, %Y at %I:%M:%S %p")
                    is_overdue = due_dt < now
                except ValueError:
                    pass
            reminders.append({
                "name": name,
                "due_date": due_str if due_str != "none" else None,
                "overdue": is_overdue,
            })
        return reminders
    except Exception as exc:
        logger.debug("Failed to fetch reminders: %s", exc)
        return []


def _get_unread_email_count() -> int | None:
    """Get unread email count via IMAP SEARCH (fast, no full sync)."""
    import imaplib
    import socket

    try:
        from openjarvis.core.registry import ConnectorRegistry

        connector_cls = ConnectorRegistry.get("gmail")
        if connector_cls is None:
            return None
        connector = connector_cls()
        if not connector.is_connected():
            return None

        # Open a fresh IMAP connection with a strict timeout
        # (reusing the connector's connection can hang if it's stale)
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com", timeout=5)
            # Get credentials from the connector
            email_addr = getattr(connector, "_email", None)
            password = getattr(connector, "_password", None) or getattr(connector, "_app_password", None)
            if not email_addr or not password:
                return None
            imap.login(email_addr, password)
            imap.select("INBOX", readonly=True)
            _status, data = imap.search(None, "UNSEEN")
            ids = data[0].split() if data and data[0] else []
            count = len(ids)
            try:
                imap.logout()
            except Exception:
                pass
            return count
        except (socket.timeout, imaplib.IMAP4.error, OSError):
            return None

    except Exception as exc:
        logger.debug("Failed to fetch unread emails: %s", exc)
        return None


async def _get_weather() -> Dict[str, Any] | None:
    """Get weather from wttr.in (no API key needed)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://wttr.in/Nashville?format=j1",
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            return {
                "temp_f": current.get("temp_F", ""),
                "condition": current.get("weatherDesc", [{}])[0].get("value", ""),
                "humidity": current.get("humidity", ""),
                "feels_like_f": current.get("FeelsLikeF", ""),
                "location": "Nashville, TN",
            }
    except Exception as exc:
        logger.debug("Failed to fetch weather: %s", exc)
        return None


def _get_disk_usage() -> Dict[str, Any]:
    """Get disk usage for the main volume."""
    usage = shutil.disk_usage("/")
    total_gb = usage.total / (1024**3)
    free_gb = usage.free / (1024**3)
    used_gb = usage.used / (1024**3)
    pct_used = (usage.used / usage.total) * 100
    return {
        "total_gb": round(total_gb, 1),
        "used_gb": round(used_gb, 1),
        "free_gb": round(free_gb, 1),
        "percent_used": round(pct_used, 1),
    }


async def _get_ollama_status() -> Dict[str, Any]:
    """Check Ollama status and loaded models."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://localhost:11434/api/ps")
            if resp.status_code != 200:
                return {"running": False, "models": []}
            data = resp.json()
            models = [
                {
                    "name": m.get("name", ""),
                    "size_gb": round(m.get("size", 0) / (1024**3), 1),
                }
                for m in data.get("models", [])
            ]
            return {"running": True, "models": models}
    except Exception:
        return {"running": False, "models": []}


def _get_uptime() -> Dict[str, Any]:
    """Server uptime."""
    elapsed = time.monotonic() - _SERVER_START_TIME
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    return {
        "seconds": round(elapsed),
        "display": f"{hours}h {minutes}m" if hours else f"{minutes}m",
    }


async def _run_with_timeout(func, fallback, timeout_s: float = 10.0):
    """Run a sync function in a thread with a timeout."""
    import asyncio

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(func), timeout=timeout_s
        )
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("Glance sub-task %s failed: %s", func.__name__, exc)
        return fallback


@glance_router.get("/glance")
async def glance():
    """Return at-a-glance status for the dashboard."""
    import asyncio

    # Run all data sources concurrently with timeouts
    (calendar, reminders, unread, weather, ollama) = await asyncio.gather(
        _run_with_timeout(_get_today_events, [], timeout_s=10.0),
        _run_with_timeout(_get_overdue_reminders, [], timeout_s=10.0),
        _run_with_timeout(_get_unread_email_count, None, timeout_s=8.0),
        _get_weather(),
        _get_ollama_status(),
    )

    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "calendar": calendar,
        "reminders": reminders,
        "unread_emails": unread,
        "weather": weather,
        "disk": _get_disk_usage(),
        "ollama": ollama,
        "uptime": _get_uptime(),
    }


__all__ = ["glance_router"]
