"""Facebook connector — page posts and info via Meta Graph API.

Uses a page access token stored at ~/.openjarvis/connectors/facebook.json.
All API calls are in module-level functions for easy mocking in tests.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

_log = logging.getLogger(__name__)

_GRAPH_API_BASE = "https://graph.facebook.com/v21.0"
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "facebook.json")


# ---------------------------------------------------------------------------
# Module-level API helpers
# ---------------------------------------------------------------------------


def _fb_api_get(
    token: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Call a Meta Graph API endpoint for Facebook."""
    merged = {"access_token": token, **(params or {})}
    resp = httpx.get(
        f"{_GRAPH_API_BASE}/{endpoint}",
        params=merged,
        timeout=30.0,
    )
    if resp.status_code >= 400:
        body = resp.text
        # Code 190 = expired/invalid token — the only truly fatal error.
        if '"code":190' in body:
            raise PermissionError(
                "Facebook access token expired or invalid. "
                "Refresh it at https://developers.facebook.com/tools/explorer/"
            )
        # Other errors (field mismatches, missing scopes) bubble up normally.
        resp.raise_for_status()
    return resp.json()


def _fb_fetch_page_info(token: str, page_id: str = "me") -> Dict[str, Any]:
    """Fetch basic info about a Facebook page or user profile.

    Tries page-specific fields first; falls back to user-compatible
    fields if the token is a user access token.
    """
    page_fields = "id,name,about,category,fan_count,followers_count,link,website"
    try:
        return _fb_api_get(token, page_id, params={"fields": page_fields})
    except httpx.HTTPStatusError:
        # Page fields failed — try user-compatible fields.
        user_fields = "id,name,link"
        return _fb_api_get(token, page_id, params={"fields": user_fields})


def _fb_create_post(
    token: str, page_id: str, message: str, link: Optional[str] = None
) -> Dict[str, Any]:
    """Publish a post to a Facebook page."""
    payload: Dict[str, Any] = {"message": message, "access_token": token}
    if link:
        payload["link"] = link
    resp = httpx.post(
        f"{_GRAPH_API_BASE}/{page_id}/feed",
        data=payload,
        timeout=30.0,
    )
    if resp.status_code >= 400:
        if '"code":190' in resp.text:
            raise PermissionError(
                "Facebook access token expired or invalid. "
                "Refresh it at https://developers.facebook.com/tools/explorer/"
            )
        resp.raise_for_status()
    return resp.json()


def _fb_fetch_page_posts(
    token: str, page_id: str = "me", limit: int = 25
) -> List[Dict[str, Any]]:
    """Fetch recent posts from a Facebook page or user feed."""
    fields = (
        "id,message,created_time,permalink_url,full_picture,"
        "likes.summary(true),comments.summary(true),shares"
    )
    try:
        data = _fb_api_get(
            token,
            f"{page_id}/posts",
            params={"fields": fields, "limit": str(limit)},
        )
    except httpx.HTTPStatusError:
        # Engagement fields may require additional scopes; retry with basics.
        data = _fb_api_get(
            token,
            f"{page_id}/posts",
            params={
                "fields": "id,message,created_time,permalink_url",
                "limit": str(limit),
            },
        )
    return data.get("data", [])


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("facebook")
class FacebookConnector(BaseConnector):
    """Sync page posts and info from Facebook via Meta Graph API."""

    connector_id = "facebook"
    display_name = "Facebook"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        return json.loads(self._token_path.read_text(encoding="utf-8"))

    def _get_access_token(self) -> str:
        return os.environ.get(
            "OPENJARVIS_FACEBOOK_ACCESS_TOKEN",
            self._load_config().get("access_token", ""),
        )

    def _get_page_id(self) -> str:
        """Return the configured page ID, defaulting to 'me'."""
        return os.environ.get(
            "OPENJARVIS_FACEBOOK_PAGE_ID",
            self._load_config().get("page_id", "me"),
        )

    def _get_app_secret(self) -> str:
        return os.environ.get(
            "OPENJARVIS_FACEBOOK_APP_SECRET",
            self._load_config().get("app_secret", ""),
        )

    def is_connected(self) -> bool:
        if not self._token_path.exists():
            return False
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
            return bool(data.get("access_token"))
        except (json.JSONDecodeError, OSError):
            return False

    def disconnect(self) -> None:
        if self._token_path.exists():
            self._token_path.unlink()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield Documents for Facebook page info and recent posts."""
        token = self._get_access_token()
        page_id = self._get_page_id()

        # Sync page/user info as a single document
        try:
            page_info = _fb_fetch_page_info(token, page_id)
            yield Document(
                doc_id=f"facebook-page-{page_info.get('id', page_id)}",
                source="facebook",
                doc_type="page_info",
                content=json.dumps(page_info),
                title=page_info.get("name", "Facebook Profile"),
                timestamp=datetime.now(),
                url=page_info.get("link"),
                metadata={
                    "page_id": page_info.get("id", ""),
                    "category": page_info.get("category", ""),
                    "fan_count": page_info.get("fan_count", 0),
                    "followers_count": page_info.get("followers_count", 0),
                },
            )
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch page info for %s: %s", page_id, exc)

        # Sync posts
        try:
            posts = _fb_fetch_page_posts(token, page_id)
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch posts for %s: %s", page_id, exc)
            posts = []

        for post in posts:
            created_str = post.get("created_time", "")
            ts = (
                datetime.fromisoformat(created_str.replace("+0000", "+00:00"))
                if created_str
                else datetime.now()
            )

            if since and ts < since:
                continue

            post_id = post.get("id", "")
            message = post.get("message", "")
            likes_count = (
                post.get("likes", {}).get("summary", {}).get("total_count", 0)
            )
            comments_count = (
                post.get("comments", {}).get("summary", {}).get("total_count", 0)
            )
            shares_count = post.get("shares", {}).get("count", 0)

            yield Document(
                doc_id=f"facebook-post-{post_id}",
                source="facebook",
                doc_type="post",
                content=message,
                title=message[:80] if message else "Facebook Post",
                timestamp=ts,
                url=post.get("permalink_url"),
                metadata={
                    "post_id": post_id,
                    "full_picture": post.get("full_picture", ""),
                    "like_count": likes_count,
                    "comments_count": comments_count,
                    "shares_count": shares_count,
                },
            )

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose MCP tool specs for real-time Facebook queries."""
        return [
            ToolSpec(
                name="facebook_list_posts",
                description=(
                    "List recent Facebook page posts with messages, timestamps, "
                    "and engagement counts (likes, comments, shares)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of posts to return",
                            "default": 10,
                        },
                        "page_id": {
                            "type": "string",
                            "description": (
                                "Facebook page ID. Defaults to the configured page."
                            ),
                        },
                    },
                    "required": [],
                },
                category="social",
            ),
            ToolSpec(
                name="facebook_get_page_info",
                description=(
                    "Get info about a Facebook page including name, category, "
                    "fan count, follower count, and website."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "page_id": {
                            "type": "string",
                            "description": (
                                "Facebook page ID. Defaults to the configured page."
                            ),
                        },
                    },
                    "required": [],
                },
                category="social",
            ),
            ToolSpec(
                name="facebook_create_post",
                description=(
                    "Publish a post to the Syntra AI Facebook page. "
                    "Optionally include a link to share."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The text content of the post.",
                        },
                        "link": {
                            "type": "string",
                            "description": (
                                "Optional URL to include in the post."
                            ),
                        },
                    },
                    "required": ["message"],
                },
                category="social",
            ),
        ]

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute a Facebook MCP tool."""
        token = self._get_access_token()
        page_id = self._get_page_id()

        if tool_name == "facebook_list_posts":
            limit = params.get("limit", 10)
            pid = params.get("page_id", page_id)
            return _fb_fetch_page_posts(token, pid, limit=limit)
        elif tool_name == "facebook_get_page_info":
            pid = params.get("page_id", page_id)
            return _fb_fetch_page_info(token, pid)
        elif tool_name == "facebook_create_post":
            message = params["message"]
            link = params.get("link")
            result = _fb_create_post(token, page_id, message, link=link)
            return result
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
