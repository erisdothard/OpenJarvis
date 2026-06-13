"""Outlook / Microsoft 365 connector — reads email via Microsoft Graph API.

Uses OAuth 2.0 tokens stored at ~/.openjarvis/connectors/outlook.json.
All API calls are in module-level functions for easy mocking in tests.

Setup: register an Azure AD app at https://portal.azure.com/
→ App registrations → New → Redirect URI: http://127.0.0.1:8789/callback
→ API permissions → Mail.Read → Certificates & secrets → New client secret
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.connectors.oauth import (
    delete_tokens,
    load_tokens,
    refresh_microsoft_token,
    save_tokens,
)
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

_log = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_CREDENTIALS_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "outlook.json")


# ---------------------------------------------------------------------------
# HTML → text (lightweight, stdlib-only)
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return readable text."""

    _SKIP = {"script", "style", "head", "title", "meta", "link"}
    _BLOCK = {"p", "div", "br", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip: int = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK and self._skip == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data: str) -> None:
        if self._skip == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n[ \t]*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    ext = _HTMLTextExtractor()
    try:
        ext.feed(html)
        ext.close()
    except Exception:
        pass
    return ext.get_text()


# ---------------------------------------------------------------------------
# Module-level API functions
# ---------------------------------------------------------------------------


def _call_with_refresh(fn: Any, credentials_path: str, *args: Any, **kwargs: Any) -> Any:
    """Call *fn(token, ...)* with auto-refresh on 401."""
    tokens = load_tokens(credentials_path)
    if not tokens:
        raise RuntimeError("Outlook not authenticated")
    token = tokens.get("access_token") or tokens.get("token") or ""

    try:
        return fn(token, *args, **kwargs)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise
        new_token = refresh_microsoft_token(credentials_path)
        if not new_token:
            raise
        return fn(new_token, *args, **kwargs)


def _outlook_api_list_messages(
    token: str,
    *,
    top: int = 50,
    skip: int = 0,
    filter_query: str = "",
) -> Dict[str, Any]:
    """Call Microsoft Graph /me/messages endpoint."""
    params: Dict[str, str] = {
        "$top": str(top),
        "$skip": str(skip),
        "$orderby": "receivedDateTime desc",
        "$select": (
            "id,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,isRead,hasAttachments,"
            "body,conversationId,webLink,isDraft"
        ),
    }
    if filter_query:
        params["$filter"] = filter_query

    resp = httpx.get(
        f"{_GRAPH_BASE}/me/messages",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _outlook_api_get_message(token: str, msg_id: str) -> Dict[str, Any]:
    """Fetch a single message by ID."""
    resp = httpx.get(
        f"{_GRAPH_BASE}/me/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "$select": (
                "id,subject,from,toRecipients,ccRecipients,"
                "receivedDateTime,isRead,hasAttachments,"
                "body,conversationId,webLink,isDraft"
            )
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _extract_address(recipient: Dict[str, Any]) -> str:
    """Extract 'Name <email>' from a Graph API recipient object."""
    ea = recipient.get("emailAddress", {})
    name = ea.get("name", "")
    addr = ea.get("address", "")
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name


def _parse_graph_date(date_str: str) -> datetime:
    """Parse a Graph API datetime string."""
    if not date_str:
        return datetime.now()
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now()


def _extract_body(msg: Dict[str, Any]) -> str:
    """Extract plain text from message body."""
    body = msg.get("body", {})
    content = body.get("content", "")
    content_type = body.get("contentType", "text")

    if content_type == "html":
        return _html_to_text(content)
    return content.strip()


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("outlook")
class OutlookConnector(BaseConnector):
    """Outlook connector using Microsoft Graph API with OAuth 2.0."""

    connector_id = "outlook"
    display_name = "Outlook / Microsoft 365"
    auth_type = "oauth"

    def __init__(self, credentials_path: str = "") -> None:
        self._credentials_path = credentials_path or _DEFAULT_CREDENTIALS_PATH
        self._items_synced: int = 0
        self._last_sync: Optional[datetime] = None

    def is_connected(self) -> bool:
        tokens = load_tokens(self._credentials_path)
        if tokens is None:
            return False
        return bool(tokens.get("access_token") or tokens.get("token"))

    def disconnect(self) -> None:
        delete_tokens(self._credentials_path)

    def auth_url(self) -> str:
        return "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"

    def handle_callback(self, code: str) -> None:
        save_tokens(self._credentials_path, {"token": code})

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(
        self,
        *,
        since: Optional[datetime] = None,
        cursor: Optional[str] = None,
        query_extra: str = "",
    ) -> Iterator[Document]:
        """Yield Document objects for Outlook messages via Graph API."""
        if not self.is_connected():
            return

        # Build OData filter
        filter_parts: List[str] = []
        if since is not None:
            # Graph API uses ISO 8601 format for datetime filters
            since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
            filter_parts.append(f"receivedDateTime ge {since_str}")
        # Exclude drafts
        filter_parts.append("isDraft eq false")
        if query_extra:
            filter_parts.append(query_extra)
        filter_query = " and ".join(filter_parts)

        skip = 0
        synced = 0
        page_size = 50

        while True:
            try:
                resp = _call_with_refresh(
                    _outlook_api_list_messages,
                    self._credentials_path,
                    top=page_size,
                    skip=skip,
                    filter_query=filter_query,
                )
            except (httpx.HTTPStatusError, RuntimeError) as exc:
                _log.warning("Failed to list Outlook messages: %s", exc)
                break

            messages = resp.get("value", [])
            if not messages:
                break

            for msg in messages:
                msg_id = msg.get("id", "")
                if not msg_id:
                    continue

                subject = msg.get("subject", "")
                from_field = msg.get("from", {})
                from_str = _extract_address(from_field)
                to_recipients = [
                    _extract_address(r) for r in msg.get("toRecipients", [])
                ]
                cc_recipients = [
                    _extract_address(r) for r in msg.get("ccRecipients", [])
                ]
                timestamp = _parse_graph_date(msg.get("receivedDateTime", ""))
                body = _extract_body(msg)
                is_read = msg.get("isRead", False)
                conversation_id = msg.get("conversationId")
                web_link = msg.get("webLink", "")

                # Build participants list
                all_addresses: List[str] = []
                from_addr = from_field.get("emailAddress", {}).get("address", "")
                if from_addr:
                    all_addresses.append(from_addr.lower())
                for r in msg.get("toRecipients", []):
                    addr = r.get("emailAddress", {}).get("address", "")
                    if addr:
                        all_addresses.append(addr.lower())

                # Determine channel (sent vs inbox)
                from_email = from_addr.lower()
                tokens = load_tokens(self._credentials_path)
                # Check if the sender is the authenticated user
                channel = "INBOX"
                labels = []
                if not is_read:
                    labels.append("UNREAD")

                yield Document(
                    doc_id=f"outlook:{msg_id}",
                    source="outlook",
                    source_id=msg_id,
                    doc_type="email",
                    content=body,
                    title=subject,
                    author=from_str,
                    participants=all_addresses,
                    timestamp=timestamp,
                    thread_id=conversation_id,
                    channel=channel,
                    url=web_link,
                    metadata={
                        "message_id": msg_id,
                        "is_read": is_read,
                        "has_attachments": msg.get("hasAttachments", False),
                        "labels": labels,
                        "to": to_recipients,
                        "cc": cc_recipients,
                    },
                )
                synced += 1

            # Check for next page
            next_link = resp.get("@odata.nextLink")
            if not next_link:
                break
            skip += page_size

        self._items_synced = synced
        self._last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return SyncStatus(
            state="idle",
            items_synced=self._items_synced,
            last_sync=self._last_sync,
        )

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="outlook_search_emails",
                description=(
                    "Search Outlook emails using Microsoft Graph API filters. "
                    "Examples: \"from/emailAddress/address eq 'alice@example.com'\""
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "OData filter query for messages",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of emails to return",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
                category="communication",
            ),
            ToolSpec(
                name="outlook_list_unread",
                description="List unread Outlook emails.",
                parameters={
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum number of messages to return",
                            "default": 20,
                        },
                    },
                    "required": [],
                },
                category="communication",
            ),
        ]

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        if tool_name == "outlook_search_emails":
            query = params.get("query", "")
            max_results = params.get("max_results", 20)
            resp = _call_with_refresh(
                _outlook_api_list_messages,
                self._credentials_path,
                top=max_results,
                filter_query=query,
            )
            return resp.get("value", [])
        elif tool_name == "outlook_list_unread":
            max_results = params.get("max_results", 20)
            resp = _call_with_refresh(
                _outlook_api_list_messages,
                self._credentials_path,
                top=max_results,
                filter_query="isRead eq false",
            )
            return resp.get("value", [])
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
