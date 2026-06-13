"""iMessage connector — reads directly from the macOS Messages SQLite database.

No API calls, no OAuth.  The connector opens ``~/Library/Messages/chat.db``
in read-only mode and yields one :class:`Document` per message that has
non-NULL text.

Requires **Full Disk Access** granted to the terminal / app in
System Settings → Privacy & Security → Full Disk Access.

Timestamp notes
---------------
The iMessage database stores timestamps as nanoseconds since the Apple
epoch of 2001-01-01 00:00:00 UTC.  Conversion formula::

    dt = datetime(2001, 1, 1, tzinfo=utc) + timedelta(seconds=apple_ns / 1_000_000_000)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Apple epoch: 2001-01-01 00:00:00 UTC
_APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# AddressBook database paths
_ADDRESSBOOK_DIR = Path.home() / "Library" / "Application Support" / "AddressBook"
_ADDRESSBOOK_DB = "AddressBook-v22.abcddb"


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def _apple_ts_to_datetime(apple_ns: int) -> datetime:
    """Convert an Apple nanosecond timestamp to a UTC :class:`datetime`.

    Parameters
    ----------
    apple_ns:
        Nanoseconds since 2001-01-01 00:00:00 UTC.

    Returns
    -------
    datetime
        UTC-aware datetime.
    """
    seconds = apple_ns / 1_000_000_000
    return _APPLE_EPOCH + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Contact name resolution from Apple Contacts
# ---------------------------------------------------------------------------

_DIGITS_ONLY = re.compile(r"[^\d+]")


def _normalize_phone(raw: str) -> str:
    """Strip a phone number to digits + optional leading '+' for matching."""
    return _DIGITS_ONLY.sub("", raw)


def _build_contact_lookup() -> Dict[str, str]:
    """Build a mapping of phone/email → contact display name.

    Reads the macOS Apple Contacts database(s) and returns a dict where
    keys are normalized phone numbers and lowercase email addresses, and
    values are the contact's display name.
    """
    lookup: Dict[str, str] = {}

    db_paths: List[Path] = []
    main_db = _ADDRESSBOOK_DIR / _ADDRESSBOOK_DB
    if main_db.exists():
        db_paths.append(main_db)
    sources_dir = _ADDRESSBOOK_DIR / "Sources"
    try:
        if sources_dir.is_dir():
            for child in sorted(sources_dir.iterdir()):
                candidate = child / _ADDRESSBOOK_DB
                if candidate.exists():
                    db_paths.append(candidate)
    except PermissionError:
        pass

    if not db_paths:
        logger.debug("No AddressBook databases found for contact name resolution")
        return lookup

    for db_path in db_paths:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            continue

        try:
            rows = conn.execute(
                "SELECT Z_PK, ZFIRSTNAME, ZMIDDLENAME, ZLASTNAME, ZORGANIZATION "
                "FROM ZABCDRECORD "
                "WHERE ZFIRSTNAME IS NOT NULL OR ZLASTNAME IS NOT NULL "
                "   OR ZORGANIZATION IS NOT NULL"
            ).fetchall()

            for pk, first, middle, last, org in rows:
                first = first or ""
                middle = middle or ""
                last = last or ""
                org = org or ""
                parts = [p for p in (first, middle, last) if p]
                name = " ".join(parts) or org
                if not name:
                    continue

                # Map phone numbers → name
                phones = conn.execute(
                    "SELECT ZFULLNUMBER FROM ZABCDPHONENUMBER WHERE ZOWNER = ?",
                    (pk,),
                ).fetchall()
                for (number,) in phones:
                    if number:
                        lookup[_normalize_phone(number)] = name

                # Map email addresses → name
                emails = conn.execute(
                    "SELECT ZADDRESS FROM ZABCDEMAILADDRESS WHERE ZOWNER = ?",
                    (pk,),
                ).fetchall()
                for (addr,) in emails:
                    if addr:
                        lookup[addr.lower()] = name
        except sqlite3.OperationalError:
            logger.debug("Could not read AddressBook at %s", db_path)
        finally:
            conn.close()

    logger.info("Built contact lookup with %d entries", len(lookup))
    return lookup


def _resolve_handle(identifier: str, contact_lookup: Dict[str, str]) -> str:
    """Resolve a raw iMessage handle (phone/email) to a contact name.

    Falls back to the raw identifier if no match is found.
    """
    # Try email match (case-insensitive)
    if "@" in identifier:
        name = contact_lookup.get(identifier.lower())
        if name:
            return name
        return identifier

    # Try phone match (normalized digits)
    normalized = _normalize_phone(identifier)
    name = contact_lookup.get(normalized)
    if name:
        return name

    # Try without country code (strip leading +1 for US numbers)
    if normalized.startswith("+1") and len(normalized) > 5:
        name = contact_lookup.get(normalized[2:])
        if name:
            return name
    elif not normalized.startswith("+") and len(normalized) == 10:
        # Try adding +1 prefix
        name = contact_lookup.get(f"+1{normalized}")
        if name:
            return name

    return identifier


# ---------------------------------------------------------------------------
# IMessageConnector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("imessage")
class IMessageConnector(BaseConnector):
    """Connector that reads messages from the macOS Messages SQLite database.

    Parameters
    ----------
    db_path:
        Path to ``chat.db``.  Defaults to
        ``~/Library/Messages/chat.db``.
    """

    connector_id = "imessage"
    display_name = "iMessage"
    auth_type = "local"

    def __init__(self, db_path: str = "") -> None:
        self._db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._connected: bool = False
        self._items_synced: int = 0
        self._items_total: int = 0
        self._last_sync: Optional[datetime] = None

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return ``True`` if chat.db exists and is readable.

        If the file exists but cannot be opened, this almost certainly means
        Full Disk Access has not been granted.  The method logs a clear
        diagnostic so the user knows what to fix.
        """
        if not self._db_path.exists():
            return False
        try:
            conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro", uri=True
            )
            conn.execute("SELECT 1 FROM message LIMIT 1")
            conn.close()
            return True
        except sqlite3.OperationalError:
            logger.warning(
                "iMessage database exists at %s but cannot be read. "
                "Grant Full Disk Access to this process in "
                "System Settings → Privacy & Security → Full Disk Access.",
                self._db_path,
            )
            return False

    def disconnect(self) -> None:
        """Mark the connector as disconnected."""
        self._connected = False

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,  # noqa: ARG002
    ) -> Iterator[Document]:
        """Read messages from chat.db and yield one :class:`Document` each.

        Parameters
        ----------
        since:
            If provided, skip messages whose timestamp is before this
            datetime.
        cursor:
            Not used for this local connector (included for API
            compatibility).

        Yields
        ------
        Document
            One document per message with non-NULL text.
        """
        db_path = str(self._db_path)

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.OperationalError:
            logger.warning(
                "Cannot open iMessage database at %s — "
                "Full Disk Access is likely not granted. "
                "Go to System Settings → Privacy & Security → Full Disk Access "
                "and add this application.",
                db_path,
            )
            return

        try:
            # ------------------------------------------------------------------
            # 1. Build handle_id → identifier map
            # ------------------------------------------------------------------
            handle_map: Dict[int, str] = {}
            for row in conn.execute("SELECT ROWID, id FROM handle"):
                handle_map[row[0]] = row[1]

            # ------------------------------------------------------------------
            # 1b. Build contact name lookup from Apple Contacts
            # ------------------------------------------------------------------
            contact_lookup = _build_contact_lookup()

            # ------------------------------------------------------------------
            # 2. Build message_id → chat_id map
            # ------------------------------------------------------------------
            msg_to_chat: Dict[int, int] = {}
            for row in conn.execute(
                "SELECT message_id, chat_id FROM chat_message_join"
            ):
                msg_to_chat[row[0]] = row[1]

            # ------------------------------------------------------------------
            # 3. Build chat_id → {identifier, display_name} map
            # ------------------------------------------------------------------
            chat_map: Dict[int, Tuple[str, str]] = {}
            for row in conn.execute(
                "SELECT ROWID, chat_identifier, display_name FROM chat"
            ):
                chat_id: int = row[0]
                chat_identifier: str = row[1] or ""
                display_name: str = row[2] or chat_identifier
                chat_map[chat_id] = (chat_identifier, display_name)

            # ------------------------------------------------------------------
            # 4. Query messages with non-NULL text
            # ------------------------------------------------------------------
            rows = conn.execute(
                "SELECT ROWID, text, handle_id, date, is_from_me "
                "FROM message "
                "WHERE text IS NOT NULL "
                "ORDER BY date ASC"
            ).fetchall()

            self._items_total = len(rows)
            synced = 0

            for row in rows:
                rowid: int = row[0]
                text: str = row[1]
                handle_id: int = row[2] or 0
                apple_ts: int = row[3] or 0
                is_from_me: int = row[4] or 0

                # Convert timestamp
                timestamp = _apple_ts_to_datetime(apple_ts)

                # Apply since filter
                if since is not None:
                    since_utc = since
                    if since_utc.tzinfo is None:
                        since_utc = since_utc.replace(tzinfo=timezone.utc)
                    if timestamp < since_utc:
                        continue

                # Determine author — resolve phone/email to contact name
                if is_from_me:
                    author = "me"
                else:
                    raw_handle = handle_map.get(handle_id, "unknown")
                    author = _resolve_handle(raw_handle, contact_lookup)

                # Determine chat name / title
                chat_id = msg_to_chat.get(rowid)
                if chat_id is not None and chat_id in chat_map:
                    _chat_identifier, chat_name = chat_map[chat_id]
                    # Resolve chat name if it's still a phone number
                    if chat_name and (chat_name.startswith("+") or chat_name.replace("-", "").replace(" ", "").isdigit()):
                        chat_name = _resolve_handle(chat_name, contact_lookup)
                else:
                    # Fall back to resolved handle name
                    raw_handle = handle_map.get(handle_id, "")
                    chat_name = _resolve_handle(raw_handle, contact_lookup) if raw_handle else ""

                doc = Document(
                    doc_id=f"imessage:{rowid}",
                    source="imessage",
                    doc_type="message",
                    content=text,
                    title=chat_name,
                    author=author,
                    timestamp=timestamp,
                )
                synced += 1
                yield doc

            self._items_synced = synced
            self._last_sync = datetime.now(tz=timezone.utc)

        finally:
            conn.close()

    def sync_status(self) -> SyncStatus:
        """Return sync progress from the most recent :meth:`sync` call."""
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            items_total=self._items_total,
            last_sync=self._last_sync,
        )

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose two MCP tool specs for real-time iMessage queries."""
        return [
            ToolSpec(
                name="imessage_search_messages",
                description=(
                    "Search iMessage messages by keyword or contact. "
                    "Returns matching messages with sender and timestamp."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query string",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of messages to return",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
                category="knowledge",
            ),
            ToolSpec(
                name="imessage_get_conversation",
                description=(
                    "Retrieve the full message history for a specific iMessage "
                    "conversation by contact phone number or email address."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "contact": {
                            "type": "string",
                            "description": (
                                "Phone number or email address of the contact "
                                "(e.g. '+15550100' or 'alice@icloud.com')"
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of messages to return",
                            "default": 50,
                        },
                    },
                    "required": ["contact"],
                },
                category="knowledge",
            ),
        ]
