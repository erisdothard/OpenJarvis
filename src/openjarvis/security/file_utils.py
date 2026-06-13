"""Secure file and directory creation helpers.

All OpenJarvis data files under ``~/.openjarvis/`` should be created
through these helpers to ensure consistent, restrictive permissions.
"""

from __future__ import annotations

import os
from pathlib import Path


def secure_mkdir(path: Path, mode: int = 0o700) -> Path:
    """Create a directory with restrictive permissions.

    Creates parent directories as needed, then sets *mode* on the
    target directory (even if it already exists).
    """
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    return path


def secure_create(path: Path, mode: int = 0o600) -> Path:
    """Ensure a file exists with restrictive permissions.

    Creates the parent directory with ``0o700`` if needed, touches the
    file if it doesn't exist, and sets *mode* on it.
    """
    secure_mkdir(path.parent, mode=0o700)
    if not path.exists():
        path.touch()
    os.chmod(path, mode)
    return path


def audit_config_permissions(config_dir: Path) -> list[str]:
    """Scan *config_dir* for sensitive files with excessive permissions.

    Checks every ``*.db``, ``*.env``, and ``cloud-keys.env`` file found
    (recursively) under *config_dir*.  Any file whose group or other read/
    write bits are set is silently chmoded to ``0o600``.

    Returns a list of paths that were corrected (as strings), so callers
    can log them if desired.
    """
    corrected: list[str] = []
    patterns = ("*.db", "*.env")

    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(config_dir.rglob(pattern))
    # Also pick up cloud-keys.env regardless of extension pattern coverage
    cloud_keys = config_dir / "cloud-keys.env"
    if cloud_keys.exists() and cloud_keys not in candidates:
        candidates.append(cloud_keys)

    for filepath in candidates:
        if not filepath.is_file():
            continue
        try:
            current = filepath.stat().st_mode & 0o777
            # Group or other has any access bits
            if current & 0o077:
                os.chmod(filepath, 0o600)
                corrected.append(str(filepath))
        except OSError:
            pass

    return corrected
