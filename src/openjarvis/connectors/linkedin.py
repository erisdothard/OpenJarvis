"""LinkedIn connector — profile posts and engagement via Community Management API.

Uses OAuth2 tokens stored at ~/.openjarvis/connectors/linkedin.json.
Client credentials are read from environment variables first, falling back
to the JSON file for backward compatibility.
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

_LI_REST_BASE = "https://api.linkedin.com/rest"
_LI_API_VERSION = "202506"
_DEFAULT_TOKEN_PATH = str(DEFAULT_CONFIG_DIR / "connectors" / "linkedin.json")
_SYNTRA_ORG_ID = "119034070"


# ---------------------------------------------------------------------------
# Module-level API helpers
# ---------------------------------------------------------------------------


def _li_api_get(
    token: str, endpoint: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Call a LinkedIn REST API endpoint."""
    resp = httpx.get(
        f"{_LI_REST_BASE}/{endpoint}",
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": _LI_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        params=params or {},
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise PermissionError(
            "LinkedIn access token expired or invalid. "
            "Re-run the OAuth flow to get a new token."
        )
    resp.raise_for_status()
    return resp.json()


def _li_create_post(
    token: str, author_urn: str, commentary: str
) -> Dict[str, Any]:
    """Publish a text post to LinkedIn."""
    resp = httpx.post(
        f"{_LI_REST_BASE}/posts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "LinkedIn-Version": _LI_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        json={
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "visibility": "PUBLIC",
            "commentary": commentary,
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
        },
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise PermissionError(
            "LinkedIn access token expired or invalid."
        )
    if resp.status_code == 422:
        body = resp.json()
        errors = body.get("errorDetails", {}).get("inputErrors", [])
        if any(e.get("code") == "DUPLICATE_POST" for e in errors):
            raise ValueError("LinkedIn rejected this as a duplicate post.")
    resp.raise_for_status()
    # 201 Created — response body may be empty
    if resp.status_code == 201:
        location = resp.headers.get("x-restli-id", "")
        return {"id": location, "status": "created"}
    return resp.json() if resp.text else {"status": "created"}


def _li_fetch_profile(token: str) -> Dict[str, Any]:
    """Fetch the authenticated user's basic profile."""
    resp = httpx.get(
        "https://api.linkedin.com/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if resp.status_code == 401:
        raise PermissionError("LinkedIn access token expired or invalid.")
    resp.raise_for_status()
    return resp.json()


def _li_fetch_posts(token: str, author_urn: str, count: int = 25) -> List[Dict[str, Any]]:
    """Fetch recent posts authored by the user via Community Management API."""
    try:
        data = _li_api_get(
            token,
            "posts",
            params={"author": author_urn, "q": "author", "count": str(count)},
        )
        return data.get("elements", [])
    except httpx.HTTPStatusError as exc:
        _log.warning("Failed to fetch LinkedIn posts: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


@ConnectorRegistry.register("linkedin")
class LinkedInConnector(BaseConnector):
    """Sync posts and profile info from LinkedIn."""

    connector_id = "linkedin"
    display_name = "LinkedIn"
    auth_type = "oauth"

    def __init__(self, *, token_path: str = _DEFAULT_TOKEN_PATH) -> None:
        self._token_path = Path(token_path)
        self._status = SyncStatus()

    def _load_config(self) -> Dict[str, str]:
        if not self._token_path.exists():
            return {}
        return json.loads(self._token_path.read_text(encoding="utf-8"))

    def _get_access_token(self) -> str:
        return os.environ.get(
            "OPENJARVIS_LINKEDIN_ACCESS_TOKEN",
            self._load_config().get("access_token", ""),
        )

    def _get_member_id(self) -> str:
        return os.environ.get(
            "OPENJARVIS_LINKEDIN_MEMBER_ID",
            self._load_config().get("member_id", ""),
        )

    def _get_author_urn(self, target: str = "") -> str:
        """Return the URN to post as.

        ``target``:
          - ``"syntra"`` → Syntra AI company page
          - ``"personal"`` → Eris's personal profile
          - ``""`` (default) → reads from config, falls back to personal

        To switch default to Syntra page, set ``"default_target": "syntra"``
        in ~/.openjarvis/connectors/linkedin.json after LinkedIn approves
        the w_organization_social scope.
        """
        if not target:
            target = self._load_config().get("default_target", "personal")
        if target == "syntra":
            org_id = self._load_config().get("org_id", _SYNTRA_ORG_ID)
            return f"urn:li:organization:{org_id}"
        return f"urn:li:person:{self._get_member_id()}"

    def is_connected(self) -> bool:
        if os.environ.get("OPENJARVIS_LINKEDIN_ACCESS_TOKEN"):
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
        """Yield Documents for the user's LinkedIn profile and recent posts."""
        token = self._get_access_token()
        if not token:
            return

        member_id = self._get_member_id()

        # Sync profile info
        try:
            profile = _li_fetch_profile(token)
            name = profile.get("name", "")
            email = profile.get("email", "")
            sub = profile.get("sub", member_id)

            yield Document(
                doc_id=f"linkedin-profile-{sub}",
                source="linkedin",
                doc_type="profile",
                content=json.dumps(profile),
                title=name or "LinkedIn Profile",
                timestamp=datetime.now(),
                metadata={
                    "member_id": sub,
                    "name": name,
                    "email": email,
                },
            )
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch LinkedIn profile: %s", exc)

        # Sync posts
        try:
            author_urn = self._get_author_urn()
            posts = _li_fetch_posts(token, author_urn)
            for post in posts:
                # Parse created timestamp (epoch ms)
                created_at = post.get("createdAt", 0)
                ts = (
                    datetime.fromtimestamp(created_at / 1000)
                    if created_at
                    else datetime.now()
                )

                if since and ts < since:
                    continue

                post_id = post.get("id", "")
                commentary = post.get("commentary", "")
                visibility = post.get("visibility", "")
                likes = post.get("likeCount", 0)
                comments = post.get("commentCount", 0)

                yield Document(
                    doc_id=f"linkedin-post-{post_id}",
                    source="linkedin",
                    doc_type="post",
                    content=commentary,
                    title=commentary[:80] if commentary else "LinkedIn Post",
                    timestamp=ts,
                    metadata={
                        "post_id": post_id,
                        "visibility": visibility,
                        "like_count": likes,
                        "comment_count": comments,
                    },
                )
        except (httpx.HTTPStatusError, PermissionError) as exc:
            _log.warning("Failed to fetch LinkedIn posts: %s", exc)

        self._status.state = "idle"
        self._status.last_sync = datetime.now()

    def sync_status(self) -> SyncStatus:
        return self._status

    # ------------------------------------------------------------------
    # MCP tools
    # ------------------------------------------------------------------

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose MCP tool specs for LinkedIn actions."""
        return [
            ToolSpec(
                name="linkedin_create_post",
                description=(
                    "Publish a text post to LinkedIn. Defaults to the Syntra AI "
                    "company page. Set target to 'personal' for Eris's profile."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "commentary": {
                            "type": "string",
                            "description": "The text content of the LinkedIn post.",
                        },
                        "target": {
                            "type": "string",
                            "enum": ["syntra", "personal"],
                            "description": (
                                "Where to post: 'syntra' for the Syntra AI company "
                                "page (default), 'personal' for Eris's profile."
                            ),
                        },
                    },
                    "required": ["commentary"],
                },
                category="social",
            ),
            ToolSpec(
                name="linkedin_get_profile",
                description=(
                    "Get the authenticated LinkedIn user's profile info."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                category="social",
            ),
        ]

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute a LinkedIn MCP tool."""
        token = self._get_access_token()

        if tool_name == "linkedin_create_post":
            commentary = params["commentary"]
            target = params.get("target", "syntra")
            return _li_create_post(token, self._get_author_urn(target), commentary)
        elif tool_name == "linkedin_get_profile":
            return _li_fetch_profile(token)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
