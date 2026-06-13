"""SQLite backup utilities for OpenJarvis.

All backups use ``sqlite3.backup()`` (the official online-backup API) so
they are safe to run while other connections are active — no exclusive lock
is taken on the source database.

Backup files are written with 0o600 permissions (owner read/write only).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Databases that must be included in every backup run.
CRITICAL_DBS: List[str] = ["knowledge.db", "agents.db", "approvals.db"]


def backup_db(source_path: Path, backup_dir: Path) -> Path:
    """Create an online backup of *source_path* into *backup_dir*.

    Uses ``sqlite3.backup()`` which is safe even while other connections hold
    the database open.  The backup file is named::

        <stem>-<unix_timestamp_int>.db

    and its permissions are set to 0o600.

    Parameters
    ----------
    source_path:
        Absolute path to the source ``.db`` file.
    backup_dir:
        Directory where the backup will be written.  Created if it does not
        exist.

    Returns the ``Path`` of the created backup file.

    Raises ``FileNotFoundError`` if *source_path* does not exist.
    """
    if not source_path.exists():
        raise FileNotFoundError(f"Source database not found: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    backup_path = backup_dir / f"{source_path.stem}-{timestamp}.db"

    src_conn = sqlite3.connect(str(source_path))
    dst_conn = sqlite3.connect(str(backup_path))
    try:
        src_conn.backup(dst_conn)
        dst_conn.close()
    finally:
        src_conn.close()

    # Restrict permissions to owner-only.
    backup_path.chmod(0o600)

    logger.info("Backed up %s -> %s", source_path.name, backup_path.name)
    return backup_path


def backup_critical(
    config_dir: Path,
    backup_dir: Optional[Path] = None,
) -> List[Path]:
    """Backup all critical databases found under *config_dir*.

    Iterates over ``CRITICAL_DBS`` and calls ``backup_db()`` for each file
    that exists.  Non-existent databases are skipped with a debug log entry
    (it is normal for some stores to not yet be initialised).

    Parameters
    ----------
    config_dir:
        Directory containing the OpenJarvis runtime databases (typically
        ``~/.openjarvis``).
    backup_dir:
        Destination directory for backup files.  Defaults to
        ``config_dir / "backups"``.

    Returns a list of ``Path`` objects for every backup file created.
    """
    if backup_dir is None:
        backup_dir = config_dir / "backups"

    created: List[Path] = []
    for db_name in CRITICAL_DBS:
        source = config_dir / db_name
        if not source.exists():
            logger.debug("Skipping backup for %s — file not found", db_name)
            continue
        try:
            path = backup_db(source, backup_dir)
            created.append(path)
        except Exception as exc:
            logger.warning("Failed to backup %s: %s", db_name, exc)

    return created


def rotate_backups(backup_dir: Path, keep: int = 7) -> int:
    """Remove old backup files, keeping the *keep* most recent per database.

    Backup files are expected to follow the naming convention produced by
    ``backup_db()``: ``<stem>-<unix_timestamp_int>.db``.  Files that do not
    match the pattern (no ``-<digits>`` suffix) are left untouched.

    Parameters
    ----------
    backup_dir:
        Directory containing backup files.
    keep:
        Number of most-recent backups to retain per database stem.

    Returns the total number of backup files removed.
    """
    if not backup_dir.exists():
        return 0

    # Group backup files by their stem (everything before the last '-<digits>')
    from collections import defaultdict
    import re

    pattern = re.compile(r"^(.+)-(\d+)\.db$")
    groups: dict[str, list[Path]] = defaultdict(list)

    for f in backup_dir.iterdir():
        if not f.is_file():
            continue
        m = pattern.match(f.name)
        if m:
            groups[m.group(1)].append(f)

    removed = 0
    for stem, files in groups.items():
        # Sort descending by embedded timestamp (most recent first).
        files.sort(
            key=lambda p: int(pattern.match(p.name).group(2)),  # type: ignore[union-attr]
            reverse=True,
        )
        to_remove = files[keep:]
        for old_file in to_remove:
            try:
                old_file.unlink()
                removed += 1
                logger.debug("Removed old backup: %s", old_file.name)
            except OSError as exc:
                logger.warning("Could not remove backup %s: %s", old_file.name, exc)

    return removed


__all__ = ["CRITICAL_DBS", "backup_critical", "backup_db", "rotate_backups"]
