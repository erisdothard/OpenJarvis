"""Adapter layer that exposes connector MCP tools as registered BaseTools.

Allows agents to call gmail_search_emails, gmail_list_unread,
calendar_get_events_today, etc. directly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry, ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


def _docs_to_json(docs: List[Document], max_items: int = 20) -> str:
    items = []
    for d in docs[:max_items]:
        item: Dict[str, Any] = {"id": d.doc_id}
        if d.title:
            item["title"] = d.title
        if d.content:
            item["content"] = d.content[:500]
        if d.metadata:
            for k in ("from", "to", "subject", "start", "end", "location", "date"):
                if k in d.metadata:
                    item[k] = d.metadata[k]
        if d.timestamp:
            item["timestamp"] = d.timestamp.isoformat()
        items.append(item)
    return json.dumps(items, indent=2, default=str)


def _err(tool_name: str, msg: str) -> ToolResult:
    return ToolResult(tool_name=tool_name, content=msg, success=False)


def _ok(tool_name: str, content: str) -> ToolResult:
    return ToolResult(tool_name=tool_name, content=content)


# ---------------------------------------------------------------------------
# Gmail tools
# ---------------------------------------------------------------------------


@ToolRegistry.register("gmail_search_emails")
class GmailSearchEmails(BaseTool):
    tool_id = "gmail_search_emails"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_search_emails",
            description=(
                "Search Gmail messages using a query string. "
                "Supports Gmail search syntax (e.g. 'from:alice subject:report is:unread')."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum emails to return",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
            category="communication",
        )

    def execute(self, **params: Any) -> ToolResult:
        query = params.get("query", "")
        max_results = params.get("max_results", 20)

        connector_cls = ConnectorRegistry.get("gmail")
        if connector_cls is None:
            return _err(self.tool_id, "Gmail connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Gmail not connected — run OAuth setup first")

        docs = []
        for d in connector.sync(since=datetime.now() - timedelta(days=30), query_extra=query):
            docs.append(d)
            if len(docs) >= max_results:
                break

        return _ok(self.tool_id, _docs_to_json(docs, max_results))


@ToolRegistry.register("gmail_list_unread")
class GmailListUnread(BaseTool):
    tool_id = "gmail_list_unread"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_list_unread",
            description="List unread Gmail messages, optionally filtered by label.",
            parameters={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Gmail label to filter by",
                        "default": "INBOX",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum messages to return",
                        "default": 20,
                    },
                },
                "required": [],
            },
            category="communication",
        )

    def execute(self, **params: Any) -> ToolResult:
        max_results = params.get("max_results", 20)
        label = params.get("label", "INBOX")

        connector_cls = ConnectorRegistry.get("gmail")
        if connector_cls is None:
            return _err(self.tool_id, "Gmail connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Gmail not connected — run OAuth setup first")

        query_extra = f"is:unread label:{label}"
        docs = []
        for d in connector.sync(since=datetime.now() - timedelta(days=7), query_extra=query_extra):
            docs.append(d)
            if len(docs) >= max_results:
                break

        return _ok(self.tool_id, _docs_to_json(docs, max_results))


# ---------------------------------------------------------------------------
# Calendar tools
# ---------------------------------------------------------------------------


@ToolRegistry.register("calendar_get_events_today")
class CalendarGetEventsToday(BaseTool):
    tool_id = "calendar_get_events_today"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_get_events_today",
            description="Retrieve all Google Calendar events scheduled for today.",
            parameters={
                "type": "object",
                "properties": {
                    "calendar_id": {
                        "type": "string",
                        "description": "Calendar ID (defaults to 'primary')",
                        "default": "primary",
                    },
                },
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        connector_cls = ConnectorRegistry.get("gcalendar")
        if connector_cls is None:
            return _err(self.tool_id, "Google Calendar connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Google Calendar not connected — run OAuth setup first")

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        docs = []
        for d in connector.sync(since=today_start):
            if d.timestamp and d.timestamp.date() == today_start.date():
                docs.append(d)

        return _ok(self.tool_id, _docs_to_json(docs, 50))


@ToolRegistry.register("calendar_search_events")
class CalendarSearchEvents(BaseTool):
    tool_id = "calendar_search_events"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_search_events",
            description="Search Google Calendar events by keyword.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term to match against event fields",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum events to return",
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        query = params.get("query", "").lower()
        max_results = params.get("max_results", 20)

        connector_cls = ConnectorRegistry.get("gcalendar")
        if connector_cls is None:
            return _err(self.tool_id, "Google Calendar connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Google Calendar not connected — run OAuth setup first")

        docs = []
        for d in connector.sync(since=datetime.now() - timedelta(days=30)):
            title = (d.title or "").lower()
            content = (d.content or "").lower()
            if query in title or query in content:
                docs.append(d)
                if len(docs) >= max_results:
                    break

        return _ok(self.tool_id, _docs_to_json(docs, max_results))


@ToolRegistry.register("calendar_next_meeting")
class CalendarNextMeeting(BaseTool):
    tool_id = "calendar_next_meeting"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calendar_next_meeting",
            description="Find the next upcoming meeting on Google Calendar.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        connector_cls = ConnectorRegistry.get("gcalendar")
        if connector_cls is None:
            return _err(self.tool_id, "Google Calendar connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Google Calendar not connected — run OAuth setup first")

        now = datetime.now()
        for d in connector.sync(since=now):
            if d.timestamp and d.timestamp > now:
                return _ok(self.tool_id, _docs_to_json([d], 1))

        return _ok(self.tool_id, "No upcoming meetings found.")


# ---------------------------------------------------------------------------
# Apple Calendar tools
# ---------------------------------------------------------------------------


@ToolRegistry.register("apple_calendar_today")
class AppleCalendarToday(BaseTool):
    tool_id = "apple_calendar_today"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apple_calendar_today",
            description="Get today's events from Apple Calendar (macOS).",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        connector_cls = ConnectorRegistry.get("apple_calendar")
        if connector_cls is None:
            return _err(self.tool_id, "Apple Calendar connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Apple Calendar not accessible — grant Automation permission")

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        from openjarvis.connectors.apple_calendar import _applescript_get_events

        events = _applescript_get_events(today_start, today_end)
        if not events:
            return _ok(self.tool_id, "No events today.")

        return _ok(self.tool_id, json.dumps(events, indent=2, default=str))


@ToolRegistry.register("apple_calendar_upcoming")
class AppleCalendarUpcoming(BaseTool):
    tool_id = "apple_calendar_upcoming"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apple_calendar_upcoming",
            description="Get upcoming Apple Calendar events for the next N days.",
            parameters={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look ahead",
                        "default": 7,
                    },
                },
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        days = params.get("days", 7)

        connector_cls = ConnectorRegistry.get("apple_calendar")
        if connector_cls is None:
            return _err(self.tool_id, "Apple Calendar connector not available")

        connector = connector_cls()
        if not connector.is_connected():
            return _err(self.tool_id, "Apple Calendar not accessible — grant Automation permission")

        now = datetime.now()
        end = now + timedelta(days=days)

        from openjarvis.connectors.apple_calendar import _applescript_get_events

        events = _applescript_get_events(now, end)
        if not events:
            return _ok(self.tool_id, f"No events in the next {days} days.")

        return _ok(self.tool_id, json.dumps(events, indent=2, default=str))


# ---------------------------------------------------------------------------
# Social media tools (Facebook, Instagram, LinkedIn)
# ---------------------------------------------------------------------------


def _social_execute(
    tool_id: str, connector_id: str, tool_name: str, params: Dict[str, Any]
) -> ToolResult:
    """Shared executor for social connector tools."""
    connector_cls = ConnectorRegistry.get(connector_id)
    if connector_cls is None:
        return _err(tool_id, f"{connector_id} connector not available")
    connector = connector_cls()
    if not connector.is_connected():
        return _err(tool_id, f"{connector_id} not connected — add access token")
    try:
        result = connector.execute_tool(tool_name, params)
        return _ok(tool_id, json.dumps(result, indent=2, default=str))
    except Exception as e:
        return _err(tool_id, str(e))


@ToolRegistry.register("facebook_list_posts")
class FacebookListPosts(BaseTool):
    tool_id = "facebook_list_posts"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
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
                },
                "required": [],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "facebook", "facebook_list_posts", params)


@ToolRegistry.register("facebook_get_page_info")
class FacebookGetPageInfo(BaseTool):
    tool_id = "facebook_get_page_info"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="facebook_get_page_info",
            description=(
                "Get info about the Syntra AI Facebook page including name, "
                "category, fan count, follower count, and website."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "facebook", "facebook_get_page_info", params)


@ToolRegistry.register("facebook_create_post")
class FacebookCreatePost(BaseTool):
    tool_id = "facebook_create_post"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
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
                        "description": "Optional URL to include in the post.",
                    },
                },
                "required": ["message"],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "facebook", "facebook_create_post", params)


@ToolRegistry.register("instagram_list_posts")
class InstagramListPosts(BaseTool):
    tool_id = "instagram_list_posts"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
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
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "instagram", "instagram_list_posts", params)


@ToolRegistry.register("instagram_get_insights")
class InstagramGetInsights(BaseTool):
    tool_id = "instagram_get_insights"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="instagram_get_insights",
            description=(
                "Get engagement insights (impressions, reach, engagement) "
                "for a specific Instagram post by its media ID."
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
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "instagram", "instagram_get_insights", params)


@ToolRegistry.register("instagram_create_post")
class InstagramCreatePost(BaseTool):
    tool_id = "instagram_create_post"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
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
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "instagram", "instagram_create_post", params)


@ToolRegistry.register("linkedin_create_post")
class LinkedInCreatePost(BaseTool):
    tool_id = "linkedin_create_post"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_create_post",
            description=(
                "Publish a text post to Eris Dothard's LinkedIn profile."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "commentary": {
                        "type": "string",
                        "description": "The text content of the LinkedIn post.",
                    },
                },
                "required": ["commentary"],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "linkedin", "linkedin_create_post", params)


@ToolRegistry.register("linkedin_get_profile")
class LinkedInGetProfile(BaseTool):
    tool_id = "linkedin_get_profile"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_get_profile",
            description="Get the authenticated LinkedIn user's profile info.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        return _social_execute(self.tool_id, "linkedin", "linkedin_get_profile", params)
