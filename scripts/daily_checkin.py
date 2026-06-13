#!/usr/bin/env python3
"""Phase 1: Send the daily check-in iMessage.

Fires at 4 PM via launchd. Sends ONE iMessage, writes a state file, exits.
Takes ~5 seconds. No polling, no blocking.

State file (~/.openjarvis/checkin_state.json) tells the reply watcher
that a check-in is active and replies should be routed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("checkin.send")

STATE_PATH = Path.home() / ".openjarvis" / "checkin_state.json"
DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

GREETING = (
    "Good afternoon, sir \u2014 any updates to log? "
    "Meetings, tasks, reminders, anything at all."
)


def _load_keys() -> None:
    import os

    keys_file = Path.home() / ".openjarvis" / "cloud-keys.env"
    if keys_file.exists():
        for line in keys_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if v and not os.environ.get(k):
                    os.environ[k] = v


def _get_max_rowid() -> int:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = conn.execute("SELECT MAX(ROWID) FROM message").fetchone()
        conn.close()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def _write_state(phone: str, baseline_rowid: int, expires_at: datetime) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "active": True,
                "phone": phone,
                "baseline_rowid": baseline_rowid,
                "sent_at": datetime.now().isoformat(),
                "expires_at": expires_at.isoformat(),
            },
            indent=2,
        )
    )


def main() -> None:
    _load_keys()

    from openjarvis.core.config import load_config

    cfg = load_config()
    if not cfg.checkin.enabled:
        logger.info("Check-in disabled. Exiting.")
        return

    phone = cfg.checkin.phone
    if not phone:
        logger.error("No phone configured. Exiting.")
        return

    # Capture baseline before sending
    baseline = _get_max_rowid()

    # Send the greeting
    from openjarvis.channels.imessage_daemon import send_imessage

    sent = send_imessage(phone, GREETING)
    if not sent:
        logger.error("Failed to send iMessage to %s", phone)
        return

    # Write state for the reply watcher
    expires = datetime.now() + timedelta(minutes=cfg.checkin.reply_timeout_minutes)
    _write_state(phone, baseline, expires)

    logger.info("Check-in sent to %s (baseline ROWID=%d, expires=%s)", phone, baseline, expires.isoformat())


if __name__ == "__main__":
    main()
