"""Daily database maintenance tasks for OpenJarvis.

Provides blocking helpers for VACUUM, FTS5 optimisation, and row purging.
All public functions are safe to run from a thread-pool executor — they
open their own short-lived SQLite connections and close them on exit.

Usage (from async context):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_daily_maintenance, config_dir)
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def vacuum_db(db_path: Path) -> bool:
    """Run VACUUM on *db_path*.

    VACUUM rewrites the database file in-place, recovering free pages and
    defragmenting storage.  On large files this can take several seconds;
    always run in an executor.

    Returns True on success, False on any error.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("VACUUM")
        conn.close()
        logger.info("VACUUM completed: %s", db_path.name)
        return True
    except sqlite3.Error as exc:
        logger.warning("VACUUM failed for %s: %s", db_path.name, exc)
        return False


def optimize_fts(db_path: Path, fts_table: str = "knowledge_fts") -> bool:
    """Run the FTS5 'optimize' command on *fts_table* inside *db_path*.

    The optimize command merges all b-tree segments into a single segment,
    which speeds up subsequent queries.  Safe to run periodically; has no
    effect if the table is already fully optimised.

    Returns True on success, False on any error.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            f"INSERT INTO {fts_table}({fts_table}) VALUES('optimize')"
        )
        conn.commit()
        conn.close()
        logger.info("FTS optimize completed: %s / %s", db_path.name, fts_table)
        return True
    except sqlite3.Error as exc:
        logger.warning(
            "FTS optimize failed for %s / %s: %s", db_path.name, fts_table, exc
        )
        return False


def purge_old_rows(
    db_path: Path,
    table: str,
    timestamp_col: str,
    max_age_days: int,
) -> int:
    """Delete rows from *table* whose *timestamp_col* is older than *max_age_days*.

    Handles two timestamp formats automatically:
    - **Epoch float** (REAL column): compared directly against ``time.time()``.
    - **ISO string** (TEXT column): compared against an ISO cutoff string.

    The format is detected by sampling the first non-NULL value from the
    column; if the table is empty the function returns 0 immediately.

    Returns the number of rows deleted, or 0 on any error.
    """
    try:
        conn = sqlite3.connect(str(db_path))

        # Sample one value to detect storage format.
        sample_row = conn.execute(
            f"SELECT {timestamp_col} FROM {table} "  # noqa: S608
            f"WHERE {timestamp_col} IS NOT NULL LIMIT 1"
        ).fetchone()

        if sample_row is None:
            conn.close()
            return 0

        sample_value = sample_row[0]
        cutoff_epoch = time.time() - max_age_days * 86400

        if isinstance(sample_value, (int, float)):
            # Epoch float storage
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {timestamp_col} < ?",  # noqa: S608
                (cutoff_epoch,),
            )
        else:
            # ISO string storage — build a comparable ISO cutoff string
            cutoff_dt = datetime.fromtimestamp(cutoff_epoch, tz=timezone.utc)
            cutoff_str = cutoff_dt.isoformat()
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {timestamp_col} < ?",  # noqa: S608
                (cutoff_str,),
            )

        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted:
            logger.info(
                "Purged %d row(s) older than %d days from %s.%s",
                deleted,
                max_age_days,
                db_path.name,
                table,
            )
        return deleted

    except sqlite3.Error as exc:
        logger.warning(
            "Purge failed for %s.%s: %s", db_path.name, table, exc
        )
        return 0


# ---------------------------------------------------------------------------
# Daily maintenance orchestrator
# ---------------------------------------------------------------------------


def run_daily_maintenance(config_dir: Path) -> None:
    """Run all daily maintenance tasks for every OpenJarvis database.

    Designed to be called from a thread-pool executor once per day.
    All errors are caught and logged; this function never raises.

    Tasks performed:
    - FTS5 optimize on knowledge.db (daily)
    - VACUUM on knowledge.db (Mondays only — file can be hundreds of MB)
    - Purge telemetry rows older than 30 days
    - Purge trace rows older than 30 days
    - Purge digest rows older than 14 days
    """
    config_dir = Path(config_dir)

    # Determine today's weekday (0 = Monday, 6 = Sunday)
    today_weekday = datetime.now(tz=timezone.utc).weekday()
    is_monday = today_weekday == 0

    # -----------------------------------------------------------------------
    # knowledge.db — FTS optimize (daily) + VACUUM (weekly on Monday)
    # -----------------------------------------------------------------------
    knowledge_db = config_dir / "knowledge.db"
    if knowledge_db.exists():
        try:
            optimize_fts(knowledge_db, fts_table="knowledge_fts")
        except Exception as exc:
            logger.warning("FTS optimize skipped: %s", exc)

        if is_monday:
            try:
                vacuum_db(knowledge_db)
            except Exception as exc:
                logger.warning("VACUUM skipped for knowledge.db: %s", exc)

    # -----------------------------------------------------------------------
    # telemetry.db — purge rows older than 30 days
    # timestamp column is REAL (epoch float)
    # -----------------------------------------------------------------------
    telemetry_db = config_dir / "telemetry.db"
    if telemetry_db.exists():
        try:
            purge_old_rows(
                telemetry_db,
                table="telemetry",
                timestamp_col="timestamp",
                max_age_days=30,
            )
        except Exception as exc:
            logger.warning("Telemetry purge skipped: %s", exc)

    # -----------------------------------------------------------------------
    # traces.db — purge rows older than 30 days
    # started_at column is REAL (epoch float)
    # -----------------------------------------------------------------------
    traces_db = config_dir / "traces.db"
    if traces_db.exists():
        try:
            purge_old_rows(
                traces_db,
                table="traces",
                timestamp_col="started_at",
                max_age_days=30,
            )
        except Exception as exc:
            logger.warning("Traces purge skipped: %s", exc)

    # -----------------------------------------------------------------------
    # digest.db — purge rows older than 14 days
    # generated_at column is TEXT (ISO string)
    # -----------------------------------------------------------------------
    digest_db = config_dir / "digest.db"
    if digest_db.exists():
        try:
            purge_old_rows(
                digest_db,
                table="digests",
                timestamp_col="generated_at",
                max_age_days=14,
            )
        except Exception as exc:
            logger.warning("Digest purge skipped: %s", exc)

    logger.info("Daily maintenance complete")


__all__ = [
    "optimize_fts",
    "purge_old_rows",
    "run_daily_maintenance",
    "vacuum_db",
]
