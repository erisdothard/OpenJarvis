"""Shared SQLite connection factory with production-grade tuning.

Every SQLite store in OpenJarvis should create connections via ``open_db()``
rather than calling ``sqlite3.connect()`` directly.  This ensures consistent
WAL mode, cache sizing, busy timeouts, and fsync behavior across all stores.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


def open_db(
    path: Union[str, Path],
    *,
    row_factory: bool = False,
    foreign_keys: bool = False,
    busy_timeout_ms: int = 5000,
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
    busy_timeout_ms:
        Milliseconds to wait on lock contention before raising an error.
        Defaults to 5000 (5 s).  Pass a higher value (e.g. 10000) for
        large, heavily-written databases such as the knowledge store.

    Applied PRAGMAs
    ---------------
    - ``journal_mode=WAL`` — concurrent readers, non-blocking writes
    - ``synchronous=NORMAL`` — safe with WAL, ~2x faster than FULL
    - ``busy_timeout`` — configurable; defaults to 5 s
    - ``cache_size=-8000`` — 8 MB page cache (default is 2 MB)
    - ``mmap_size=268435456`` — 256 MB memory-mapped I/O for fast reads
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)

    # Enforce owner-only permissions on every on-disk database.
    if str(path) != ":memory:":
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass

    if row_factory:
        conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
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


def checkpoint_truncate(conn: sqlite3.Connection) -> None:
    """Run a TRUNCATE WAL checkpoint on a single connection.

    TRUNCATE mode blocks until all readers have finished, then truncates
    the WAL file to zero bytes.  Use this at shutdown when you want a
    clean, minimal WAL on disk.  Do NOT call this from hot paths — it
    will block writers until the truncation completes.
    """
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:
        logger.debug("WAL TRUNCATE checkpoint failed: %s", exc)


def checkpoint_all_truncate(db_dir: Union[str, Path]) -> int:
    """Run a TRUNCATE WAL checkpoint on every ``.db`` file under *db_dir*.

    Intended for use at server shutdown.  Opens a fresh connection to
    each database, runs ``PRAGMA wal_checkpoint(TRUNCATE)``, then closes
    the connection.  Returns the number of databases successfully
    checkpointed.
    """
    db_dir = Path(db_dir)
    count = 0
    for db_file in db_dir.rglob("*.db"):
        try:
            conn = sqlite3.connect(str(db_file))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            count += 1
        except sqlite3.Error as exc:
            logger.debug("TRUNCATE checkpoint %s failed: %s", db_file.name, exc)
    return count


def check_integrity(path: Union[str, Path]) -> bool:
    """Run ``PRAGMA integrity_check`` on *path*.

    Opens a short-lived connection, runs the integrity check, then closes
    the connection.  Returns ``True`` if the database reports ``ok``,
    ``False`` on any structural issue or if the file cannot be opened.

    Safe to call on a live database — it only acquires a shared read lock.
    """
    try:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        # SQLite returns a single row with "ok" when the database is healthy.
        return len(rows) == 1 and rows[0][0].lower() == "ok"
    except sqlite3.Error as exc:
        logger.warning("integrity_check failed for %s: %s", path, exc)
        return False


def recover_from_wal(path: Union[str, Path]) -> bool:
    """Attempt WAL recovery for a potentially corrupt database.

    Strategy
    --------
    1. Try a normal ``integrity_check`` — if it passes, return ``True``
       immediately (nothing to do).
    2. Rename the WAL file aside (``<path>-wal`` → ``<path>-wal.corrupt``).
       Renaming — rather than deleting — preserves evidence for debugging.
    3. Also rename the shared-memory file (``<path>-shm``) aside if it
       exists, since a stale SHM paired with a missing WAL can confuse
       SQLite.
    4. Re-run ``integrity_check`` on the base database without the WAL.
       Return ``True`` if the base DB is healthy, ``False`` otherwise.

    Returns ``True`` if the database is usable after the procedure,
    ``False`` if it is corrupt beyond WAL recovery.
    """
    path = Path(path)
    wal_path = path.with_suffix(path.suffix + "-wal")
    shm_path = path.with_suffix(path.suffix + "-shm")

    # Step 1: check current state.
    if check_integrity(path):
        return True

    logger.warning(
        "Database %s failed integrity check — attempting WAL recovery", path.name
    )

    # Step 2: move WAL aside.
    if wal_path.exists():
        corrupt_wal = wal_path.with_name(wal_path.name + ".corrupt")
        try:
            wal_path.rename(corrupt_wal)
            logger.info("Moved corrupt WAL: %s -> %s", wal_path.name, corrupt_wal.name)
        except OSError as exc:
            logger.warning("Could not rename WAL file: %s", exc)

    # Step 3: move SHM aside.
    if shm_path.exists():
        corrupt_shm = shm_path.with_name(shm_path.name + ".corrupt")
        try:
            shm_path.rename(corrupt_shm)
            logger.info("Moved SHM file: %s -> %s", shm_path.name, corrupt_shm.name)
        except OSError as exc:
            logger.warning("Could not rename SHM file: %s", exc)

    # Step 4: re-check base database.
    ok = check_integrity(path)
    if ok:
        logger.info("WAL recovery succeeded for %s", path.name)
    else:
        logger.error(
            "WAL recovery failed — base database %s is corrupt", path.name
        )
    return ok


__all__ = [
    "check_integrity",
    "checkpoint_all",
    "checkpoint_all_truncate",
    "checkpoint_truncate",
    "open_db",
    "recover_from_wal",
    "wal_checkpoint",
]
