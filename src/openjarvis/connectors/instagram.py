"""Instagram connector — posts and insights via Meta Graph API.

Uses a long-lived access token stored at ~/.openjarvis/connectors/instagram.json.
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
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "instagram.json")


# ---------------------------------------------------------------------------
# Module-level API helpers
# ---------------------------------------------------------------------------


def _ig_api_get(
    token: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Call a Meta Graph API endpoint for Instagram."""
    merged = {"access_token": token, **(params or {})}
    resp = httpx.get(
        f"{_GRAPH_API_BASE}/{endpoint}",
        params=merged,
        timeout=30.0,
    )
    if resp.status_code >= 400:
        if '"code":190' in resp.text:
            raise PermissionError(
                "Instagram access token expired or invalid. "
                "Refresh it at https://developers.facebook.com/tools/explorer/"
            )
        resp.raise_for_status()
    return resp.json()


def _ig_fetch_posts(
    token: str, ig_user_id: str = "me", limit: int = 25
) -> List[Dict[str, Any]]:
    """Fetch recent media from the Instagram business/creator account."""
    fields = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count"
    data = _ig_api_get(
        token,
        f"{ig_user_id}/media",
        params={"fields": fields, "limit": str(limit)},
    )
    return data.get("data", [])


def _ig_create_post(
    token: str, ig_user_id: str, image_url: str, caption: str = ""
) -> Dict[str, Any]:
    """Publish a photo post to Instagram via the Content Publishing API.

    Two-step process: create a media container, then publish it.
    """
    # Step 1: Create media container
    container_resp = httpx.post(
        f"{_GRAPH_API_BASE}/{ig_user_id}/media",
        data={
            "image_url": image_url,
            "caption": caption,
            "access_token": token,
        },
        timeout=30.0,
    )
    if container_resp.status_code >= 400:
        if '"code":190' in container_resp.text:
            raise PermissionError("Instagram access token expired or invalid.")
        container_resp.raise_for_status()
    container_id = container_resp.json()["id"]

    # Step 2: Publish
    publish_resp = httpx.post(
        f"{_GRAPH_API_BASE}/{ig_user_id}/media_publish",
        data={
            "creation_id": container_id,
            "access_token": token,
        },
        timeout=30.0,
    )
    if publish_resp.status_code >= 400:
        publish_resp.raise_for_status()
    return publish_resp.json()


def _ig_fetch_comments(
    token: str, media_id: str, limit: int = 25
) -> List[Dict[str, Any]]:
    """Fetch comments on a specific Instagram media object."""
    fields = "id,text,username,timestamp"
    data = _ig_api_get(
        token,
        f"{media_id}/comments",
        params={"fields": fields, "limit": str(limit)},
    )
    return data.get("data", [])


def _ig_fetch_insights(
    token: str, media_id: str
) -> List[Dict[str, Any]]:
    """Fetch insights for a specific Instagram media object.

    Only available for business/creator accounts. Returns an empty list
    if the account type does not support insights.
    """
    try:
        data = _ig_api_get(
            token,
            f"{media_id}/insights",
            params={"metric": "impressions,reach,engagement"},
        )
        return data.get("data", [])
    except httpx.HTTPStatusError:
        return []


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("instagram")
class InstagramConnector(BaseConnector):
    """Sync posts, comments, and insights from Instagram via Meta Graph API."""

    connector_id = "instagram"
    display_name = "Instagram"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        return json.loads(self._token_path.read_text(encoding="utf-8"))

    def _get_access_token(self) -> str:
        return os.environ.get(
            "OPENJARVIS_INSTAGRAM_ACCESS_TOKEN",
            self._load_config().get("access_token", ""),
        )

    def _get_ig_user_id(self) -> str:
        """Return the configured IG business account ID, defaulting to 'me'."""
        return os.environ.get(
            "OPENJARVIS_INSTAGRAM_USER_ID",
            self._load_config().get("ig_user_id", "me"),
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
        """Yield Documents for recent Instagram posts and their comments."""
        token = self._get_access_token()
        ig_user_id = self._get_ig_user_id()
        posts = _ig_fetch_posts(token, ig_user_id)

        for post in posts:
            post_ts_str = post.get("timestamp", "")
            ts = (
                datetime.fromisoformat(post_ts_str.replace("Z", "+00:00"))
                if post_ts_str
                else datetime.now()
            )

            if since and ts < since:
                continue

            caption = post.get("caption", "")
            media_id = post.get("id", "")
            likes = post.get("like_count", 0)
            comments_count = post.get("comments_count", 0)

            yield Document(
                doc_id=f"instagram-post-{media_id}",
                source="instagram",
                doc_type="post",
                content=caption,
                title=caption[:80] if caption else "Instagram Post",
                timestamp=ts,
                url=post.get("permalink"),
                metadata={
                    "media_id": media_id,
                    "media_type": post.get("media_type", ""),
                    "media_url": post.get("media_url", ""),
                    "like_count": likes,
                    "comments_count": comments_count,
                },
            )

            # Sync comments on each post
            if comments_count > 0:
                try:
                    comments = _ig_fetch_comments(token, media_id)
                except httpx.HTTPStatusError:
                    _log.warning("Failed to fetch comments for post %s", media_id)
                    comments = []

                for comment in comments:
                    comment_ts_str = comment.get("timestamp", "")
                    comment_ts = (
                        datetime.fromisoformat(
                            comment_ts_str.replace("Z", "+00:00")
                        )
                        if comment_ts_str
                        else datetime.now()
                    )
                    yield Document(
                        doc_id=f"instagram-comment-{comment.get('id', '')}",
                        source="instagram",
                        doc_type="comment",
                        content=comment.get("text", ""),
                        author=comment.get("username", ""),
                        timestamp=comment_ts,
                        thread_id=media_id,
                        metadata={
                            "comment_id": comment.get("id", ""),
                            "parent_post_id": media_id,
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
        """Expose MCP tool specs for real-time Instagram queries."""
        return [
            ToolSpec(
                name="instagram_list_posts",
                description=(
                    "List recent Instagram posts with captions, media URLs, "
                    "timestamps, and engagement counts (likes/comments)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of posts to return",
                            "default": 10,
                        },
                    },
                    "required": [],
                },
                category="social",
            ),
            ToolSpec(
                name="instagram_get_insights",
                description=(
                    "Get engagement insights (impressions, reach, engagement) "
                    "for a specific Instagram post by its media ID. "
                    "Requires a business or creator account."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "description": "The Instagram media ID to fetch insights for",
                        },
                    },
                    "required": ["media_id"],
                },
                category="social",
            ),
            ToolSpec(
                name="instagram_create_post",
                description=(
                    "Publish a photo post to the Syntra AI Instagram account. "
                    "Requires a publicly accessible image URL."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "image_url": {
                            "type": "string",
                            "description": "Public URL of the image to post.",
                        },
                        "caption": {
                            "type": "string",
                            "description": "Caption text for the post.",
                        },
                    },
                    "required": ["image_url"],
                },
                category="social",
            ),
        ]

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute an Instagram MCP tool."""
        token = self._get_access_token()
        ig_user_id = self._get_ig_user_id()

        if tool_name == "instagram_list_posts":
            limit = params.get("limit", 10)
            return _ig_fetch_posts(token, ig_user_id, limit=limit)
        elif tool_name == "instagram_get_insights":
            return _ig_fetch_insights(token, params["media_id"])
        elif tool_name == "instagram_create_post":
            image_url = params["image_url"]
            caption = params.get("caption", "")
            return _ig_create_post(token, ig_user_id, image_url, caption)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
