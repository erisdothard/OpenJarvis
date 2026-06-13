"""Twitter/X connector — posts and timeline via X API v2.

Uses OAuth 2.0 Bearer Token or API key/secret stored at
~/.openjarvis/connectors/twitter.json.
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

_X_API_BASE = "https://api.x.com/2"
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "twitter.json")


# ---------------------------------------------------------------------------
# Module-level API helpers
# ---------------------------------------------------------------------------


def _x_api_get(
    token: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Call an X API v2 endpoint."""
    resp = httpx.get(
        f"{_X_API_BASE}/{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
        },
        params=params or {},
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise PermissionError(
            "X/Twitter access token expired or invalid. "
            "Re-run the OAuth flow to get a new token."
        )
    if resp.status_code == 429:
        raise RuntimeError("X API rate limit exceeded. Try again later.")
    resp.raise_for_status()
    return resp.json()


def _x_create_tweet(
    token: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Post a tweet via X API v2."""
    payload: Dict[str, Any] = {"text": text}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}

    resp = httpx.post(
        f"{_X_API_BASE}/tweets",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise PermissionError("X/Twitter access token expired or invalid.")
    if resp.status_code == 403:
        raise PermissionError(
            "X API returned 403. Check that your app has write permissions "
            "and your access token includes tweet.write scope."
        )
    if resp.status_code == 429:
        raise RuntimeError("X API rate limit exceeded. Try again later.")
    resp.raise_for_status()
    return resp.json()


def _x_fetch_user_tweets(
    token: str, user_id: str, max_results: int = 10
) -> List[Dict[str, Any]]:
    """Fetch recent tweets from a user."""
    params = {
        "max_results": str(min(max_results, 100)),
        "tweet.fields": "created_at,public_metrics,text",
    }
    data = _x_api_get(token, f"users/{user_id}/tweets", params=params)
    return data.get("data", [])


def _x_fetch_me(token: str) -> Dict[str, Any]:
    """Fetch the authenticated user's profile."""
    data = _x_api_get(
        token,
        "users/me",
        params={"user.fields": "id,name,username,description,public_metrics,profile_image_url"},
    )
    return data.get("data", {})


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("twitter")
class TwitterConnector(BaseConnector):
    """Sync tweets and profile from Twitter/X via API v2."""

    connector_id = "twitter"
    display_name = "Twitter / X"
    auth_type = "token"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        if not self._token_path.exists():
            return {}
        return json.loads(self._token_path.read_text(encoding="utf-8"))

    def _get_access_token(self) -> str:
        return os.environ.get(
            "OPENJARVIS_TWITTER_ACCESS_TOKEN",
            self._load_config().get("access_token", ""),
        )

    def _get_user_id(self) -> str:
        return os.environ.get(
            "OPENJARVIS_TWITTER_USER_ID",
            self._load_config().get("user_id", ""),
        )

    def is_connected(self) -> bool:
        if os.environ.get("OPENJARVIS_TWITTER_ACCESS_TOKEN"):
            return True
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
        """Yield Documents for the user's profile and recent tweets."""
        token = self._get_access_token()
        if not token:
            return

        # Sync profile
        try:
            profile = _x_fetch_me(token)
            user_id = profile.get("id", "")

            yield Document(
                doc_id=f"twitter-profile-{user_id}",
                source="twitter",
                doc_type="profile",
                content=json.dumps(profile),
                title=profile.get("name", "Twitter Profile"),
                timestamp=datetime.now(),
                metadata={
                    "user_id": user_id,
                    "username": profile.get("username", ""),
                    "followers": profile.get("public_metrics", {}).get("followers_count", 0),
                },
            )
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch Twitter profile: %s", exc)
            return

        # Sync tweets
        try:
            tweets = _x_fetch_user_tweets(token, user_id)
            for tweet in tweets:
                tweet_id = tweet.get("id", "")
                text = tweet.get("text", "")
                created_str = tweet.get("created_at", "")
                ts = (
                    datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if created_str
                    else datetime.now()
                )

                if since and ts < since:
                    continue

                metrics = tweet.get("public_metrics", {})
                yield Document(
                    doc_id=f"twitter-tweet-{tweet_id}",
                    source="twitter",
                    doc_type="tweet",
                    content=text,
                    title=text[:80] if text else "Tweet",
                    timestamp=ts,
                    url=f"https://x.com/i/status/{tweet_id}",
                    metadata={
                        "tweet_id": tweet_id,
                        "like_count": metrics.get("like_count", 0),
                        "retweet_count": metrics.get("retweet_count", 0),
                        "reply_count": metrics.get("reply_count", 0),
                        "impression_count": metrics.get("impression_count", 0),
                    },
                )
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch tweets: %s", exc)

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose MCP tool specs for Twitter/X actions."""
        return [
            ToolSpec(
                name="twitter_create_post",
                description=(
                    "Post a tweet to Syntra AI's Twitter/X account. "
                    "Max 280 characters."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The tweet text (max 280 characters).",
                        },
                        "reply_to": {
                            "type": "string",
                            "description": "Optional tweet ID to reply to.",
                        },
                    },
                    "required": ["text"],
                },
                category="social",
            ),
            ToolSpec(
                name="twitter_list_tweets",
                description=(
                    "List recent tweets from the authenticated Twitter/X account "
                    "with engagement metrics."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum tweets to return (default 10, max 100).",
                        },
                    },
                    "required": [],
                },
                category="social",
            ),
            ToolSpec(
                name="twitter_get_profile",
                description="Get the authenticated Twitter/X user's profile info.",
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                category="social",
            ),
        ]

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute a Twitter/X MCP tool."""
        token = self._get_access_token()

        if tool_name == "twitter_create_post":
            text = params["text"]
            if len(text) > 280:
                raise ValueError(f"Tweet exceeds 280 chars ({len(text)} chars).")
            reply_to = params.get("reply_to")
            return _x_create_tweet(token, text, reply_to=reply_to)
        elif tool_name == "twitter_list_tweets":
            user_id = self._get_user_id()
            if not user_id:
                me = _x_fetch_me(token)
                user_id = me["id"]
            limit = params.get("limit", 10)
            return _x_fetch_user_tweets(token, user_id, max_results=limit)
        elif tool_name == "twitter_get_profile":
            return _x_fetch_me(token)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
