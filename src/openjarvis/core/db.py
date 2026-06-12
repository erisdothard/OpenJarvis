"""Shared SQLite connection factory with production-grade tuning.

Every SQLite store in OpenJarvis should create connections via ``open_db()``
rather than calling ``sqlite3.connect()`` directly.  This ensures consistent
WAL mode, cache sizing, busy timeouts, and fsync behavior across all stores.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


def open_db(
    path: Union[str, Path],
    *,
    row_factory: bool = False,
    foreign_keys: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with production-grade tuning.

    Parameters
    ----------
    path:
        Path to the ``.db`` file, or ``":memory:"`` for in-memory databases.
    row_factory:
        If True, set ``conn.row_factory = sqlite3.Row``.
    foreign_keys:
        If True, enable ``PRAGMA foreign_keys=ON``.

    Applied PRAGMAs
    ---------------
    - ``journal_mode=WAL`` — concurrent readers, non-blocking writes
    - ``synchronous=NORMAL`` — safe with WAL, ~2x faster than FULL
    - ``busy_timeout=5000`` — wait 5 s on lock contention instead of failing
    - ``cache_size=-8000`` — 8 MB page cache (default is 2 MB)
    - ``mmap_size=268435456`` — 256 MB memory-mapped I/O for fast reads
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)

    if row_factory:
        conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA mmap_size=268435456")

    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")

    return conn


def wal_checkpoint(conn: sqlite3.Connection) -> None:
    """Run a passive WAL checkpoint (non-blocking).

    Safe to call periodically from a background task.  Uses PASSIVE mode
    so it never blocks readers or writers.
    """
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error as exc:
        logger.debug("WAL checkpoint failed: %s", exc)


def checkpoint_all(db_dir: Union[str, Path]) -> int:
    """Checkpoint every ``.db`` file under *db_dir* (recursive).

    Opens a temporary connection to each database, runs a passive WAL
    checkpoint, then closes the connection.  Returns the number of
    databases checkpointed.
    """
    db_dir = Path(db_dir)
    count = 0
    for db_file in db_dir.rglob("*.db"):
        try:
            conn = sqlite3.connect(str(db_file))
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            conn.close()
            count += 1
        except sqlite3.Error as exc:
            logger.debug("Checkpoint %s failed: %s", db_file.name, exc)
    return count


__all__ = ["checkpoint_all", "open_db", "wal_checkpoint"]
