#!/usr/bin/env python3
"""Phase 2: Watch for check-in replies and route them.

Runs every 2 minutes via launchd. Checks if a check-in is active,
looks for new iMessages, classifies updates, executes tools, confirms.
Each run takes seconds. No long-lived process.

Flow:
  1. Read state file — if no active check-in or expired, exit.
  2. Poll chat.db for new messages since baseline ROWID.
  3. If no messages, exit (will check again in 2 min).
  4. Classify reply via LLM.
  5. Execute each update (calendar, reminder, note).
  6. Send confirmation iMessage.
  7. Clear state file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("checkin.watcher")

STATE_PATH = Path.home() / ".openjarvis" / "checkin_state.json"
DB_PATH = str(Path.home() / "Library" / "Messages" / "chat.db")


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


def _read_state() -> Optional[Dict[str, Any]]:
    if not STATE_PATH.exists():
        return None
    try:
        state = json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not state.get("active"):
        return None
    # Check expiry
    expires = datetime.fromisoformat(state["expires_at"])
    if datetime.now() > expires:
        logger.info("Check-in expired at %s. Clearing.", state["expires_at"])
        _clear_state()
        return None
    return state


def _clear_state() -> None:
    if STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps({"active": False}))


def _poll_messages(baseline_rowid: int, phone: str) -> List[Dict[str, Any]]:
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT m.ROWID as rowid, m.text "
            "FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
            "JOIN chat c ON c.ROWID = cmj.chat_id "
            "WHERE m.ROWID > ? AND m.is_from_me = 0 "
            "AND m.text IS NOT NULL "
            "AND c.chat_identifier = ? "
            "ORDER BY m.ROWID ASC",
            (baseline_rowid, phone),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> None:
    state = _read_state()
    if not state:
        return  # No active check-in — silent exit

    phone = state["phone"]
    baseline = state["baseline_rowid"]

    messages = _poll_messages(baseline, phone)
    if not messages:
        return  # No reply yet — will check again next run

    reply_text = "\n".join(m["text"] for m in messages if m.get("text"))
    if not reply_text.strip():
        return

    logger.info("Reply received (%d chars). Processing...", len(reply_text))

    # Clear state immediately so we don't double-process
    _clear_state()

    # Load engine and classify
    _load_keys()

    from openjarvis.agents.checkin_agent import CheckinAgent
    from openjarvis.core.config import load_config
    from openjarvis.engine.cloud import CloudEngine

    cfg = load_config()
    engine = CloudEngine()
    if not engine.health():
        logger.error("CloudEngine not healthy. Cannot process reply.")
        return

    agent = CheckinAgent(engine, cfg.intelligence.default_model)

    # Classify
    classified = agent._classify_updates(reply_text)
    if not classified:
        from openjarvis.notifications import send_telegram

        send_telegram("Got it \u2014 all clear. Let me know if anything comes up.")
        logger.info("No actionable updates in reply.")
        return

    # Execute
    results: List[Dict[str, Any]] = []
    clarifications: List[Dict[str, Any]] = []

    for item in classified:
        if item.get("confidence") == "low":
            clarifications.append(item)
            continue
        result = agent._execute_update(item)
        results.append(result)
        status = "OK" if result.get("success") else "FAIL"
        logger.info("  %s: [%s] %s", status, item["type"], item["summary"])

    # Confirm
    confirmation = CheckinAgent._build_confirmation(results, clarifications)

    from openjarvis.notifications import send_telegram

    sent = send_telegram(confirmation)
    if sent:
        logger.info("Confirmation sent.")
    else:
        logger.error("Failed to send confirmation via Telegram.")

    logger.info(
        "Done: %d processed, %d need clarification.",
        len(results),
        len(clarifications),
    )


if __name__ == "__main__":
    main()
