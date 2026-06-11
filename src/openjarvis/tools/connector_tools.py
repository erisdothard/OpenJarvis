"""Adapter layer that exposes connector MCP tools as registered BaseTools.

Allows agents to call gmail_search_emails, gmail_list_unread,
calendar_get_events_today, etc. directly.
"""

from __future__ import annotations

import json
import logging
import subprocess
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
# Apple Calendar — create event
# ---------------------------------------------------------------------------


@ToolRegistry.register("apple_calendar_create_event")
class AppleCalendarCreateEvent(BaseTool):
    tool_id = "apple_calendar_create_event"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apple_calendar_create_event",
            description=(
                "Create a new event on Apple Calendar. "
                "Provide a title, start/end times (ISO 8601 or natural language), "
                "and optionally a calendar name, location, and notes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title / summary",
                    },
                    "start": {
                        "type": "string",
                        "description": "Start date/time in ISO 8601 format (e.g. 2026-06-15T09:00:00)",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date/time in ISO 8601 format (e.g. 2026-06-15T10:00:00)",
                    },
                    "calendar": {
                        "type": "string",
                        "description": "Calendar name (e.g. Home, Work). Defaults to Home.",
                        "default": "Home",
                    },
                    "location": {
                        "type": "string",
                        "description": "Event location",
                        "default": "",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Event notes / description",
                        "default": "",
                    },
                    "all_day": {
                        "type": "boolean",
                        "description": "Whether this is an all-day event",
                        "default": False,
                    },
                },
                "required": ["summary", "start", "end"],
            },
            category="productivity",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.connectors.apple_calendar import _applescript_create_event

        summary = params.get("summary", "")
        start_str = params.get("start", "")
        end_str = params.get("end", "")
        calendar_name = params.get("calendar", "Home")
        location = params.get("location", "")
        notes = params.get("notes", "")
        all_day = params.get("all_day", False)

        if not summary or not start_str or not end_str:
            return _err(self.tool_id, "summary, start, and end are required")

        try:
            start_dt = datetime.fromisoformat(start_str)
        except ValueError:
            return _err(self.tool_id, f"Invalid start date format: {start_str}")
        try:
            end_dt = datetime.fromisoformat(end_str)
        except ValueError:
            return _err(self.tool_id, f"Invalid end date format: {end_str}")

        result = _applescript_create_event(
            summary=summary,
            start_dt=start_dt,
            end_dt=end_dt,
            calendar_name=calendar_name,
            location=location,
            notes=notes,
            all_day=all_day,
        )

        if not result.get("success"):
            return _err(self.tool_id, result.get("error", "Failed to create event"))

        return _ok(self.tool_id, json.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# iMessage — send
# ---------------------------------------------------------------------------


@ToolRegistry.register("send_imessage")
class SendIMessage(BaseTool):
    tool_id = "send_imessage"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="send_imessage",
            description=(
                "Send an iMessage to a contact. Provide the recipient's phone "
                "number (E.164 format, e.g. +15551234567) or iMessage email, "
                "and the message text."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "recipient": {
                        "type": "string",
                        "description": "Phone number (E.164) or email of the recipient",
                    },
                    "message": {
                        "type": "string",
                        "description": "The message text to send",
                    },
                },
                "required": ["recipient", "message"],
            },
            category="communication",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.channels.imessage_daemon import send_imessage

        recipient = params.get("recipient", "")
        message = params.get("message", "")

        if not recipient or not message:
            return _err(self.tool_id, "Both recipient and message are required")

        success = send_imessage(recipient, message)
        if not success:
            return _err(self.tool_id, f"Failed to send iMessage to {recipient}")

        return _ok(
            self.tool_id,
            json.dumps({"sent": True, "recipient": recipient, "message": message}),
        )


# ---------------------------------------------------------------------------
# Apple Reminders — list and create
# ---------------------------------------------------------------------------


def _run_applescript(script: str, *, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Run AppleScript via a temp file (avoids subprocess -e timeouts)."""
    import tempfile
    import os

    fd, path = tempfile.mkstemp(suffix=".scpt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        return subprocess.run(
            ["osascript", path],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _applescript_list_reminders(
    list_name: str = "Reminders", include_completed: bool = False
) -> List[Dict[str, Any]]:
    """Read reminders from a Reminders.app list via AppleScript."""
    escaped_list = list_name.replace("\\", "\\\\").replace('"', '\\"')
    completed_filter = "" if include_completed else " whose completed is false"
    script = f'''tell application "Reminders"
    set output to ""
    set rList to every reminder of list "{escaped_list}"{completed_filter}
    repeat with r in rList
        set rName to name of r
        set rDue to "none"
        try
            set rDue to due date of r as string
        end try
        set rBody to ""
        try
            set rBody to body of r
        end try
        set output to output & rName & "||" & rDue & "||" & rBody & return
    end repeat
    return output
end tell'''

    try:
        result = _run_applescript(script)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        logger.error("AppleScript reminders read failed: %s", result.stderr.strip())
        return []

    reminders: List[Dict[str, Any]] = []
    for line in result.stdout.strip().split("\n"):
        parts = line.split("||")
        if len(parts) < 2:
            continue
        reminders.append({
            "name": parts[0].strip(),
            "due_date": parts[1].strip() if parts[1].strip() != "none" else None,
            "body": parts[2].strip() if len(parts) > 2 else "",
        })
    return reminders


def _applescript_create_reminder(
    *,
    name: str,
    list_name: str = "Reminders",
    body: str = "",
    due_date: str = "",
    priority: int = 0,
) -> Dict[str, Any]:
    """Create a reminder in Reminders.app via AppleScript."""
    escaped_name = name.replace("\\", "\\\\").replace('"', '\\"')
    escaped_list = list_name.replace("\\", "\\\\").replace('"', '\\"')

    props = f'name:"{escaped_name}"'
    if body:
        escaped_body = body.replace("\\", "\\\\").replace('"', '\\"')
        props += f', body:"{escaped_body}"'
    if priority:
        props += f", priority:{priority}"

    # Ensure Reminders.app is running — AppleScript 'activate' can fail
    # with -600 on macOS Sequoia+; 'open -a' is reliable.
    import time as _time
    subprocess.run(["open", "-a", "Reminders"], check=False, timeout=5)
    _time.sleep(1)

    lines = [
        'tell application "Reminders"',
        f'  tell list "{escaped_list}"',
    ]
    if due_date:
        lines.append(f'    set dueDate to date "{due_date}"')
        lines.append(f"    set newReminder to make new reminder with properties {{{props}, due date:dueDate}}")
    else:
        lines.append(f"    set newReminder to make new reminder with properties {{{props}}}")
    lines.extend([
        "    return id of newReminder",
        "  end tell",
        "end tell",
    ])
    script = "\n".join(lines)

    try:
        result = _run_applescript(script)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"success": False, "error": "osascript not available"}

    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip()}

    return {
        "success": True,
        "id": result.stdout.strip(),
        "name": name,
        "list": list_name,
        "due_date": due_date or None,
    }


@ToolRegistry.register("apple_reminders_list")
class AppleRemindersList(BaseTool):
    tool_id = "apple_reminders_list"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apple_reminders_list",
            description=(
                "List reminders from Apple Reminders. "
                "By default shows incomplete reminders from the 'Reminders' list."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Reminders list name",
                        "default": "Reminders",
                    },
                    "include_completed": {
                        "type": "boolean",
                        "description": "Whether to include completed reminders",
                        "default": False,
                    },
                },
                "required": [],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        list_name = params.get("list_name", "Reminders")
        include_completed = params.get("include_completed", False)

        reminders = _applescript_list_reminders(list_name, include_completed)
        if not reminders:
            return _ok(self.tool_id, "No reminders found.")

        return _ok(self.tool_id, json.dumps(reminders, indent=2, default=str))


@ToolRegistry.register("apple_reminders_create")
class AppleRemindersCreate(BaseTool):
    tool_id = "apple_reminders_create"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apple_reminders_create",
            description=(
                "Create a new reminder in Apple Reminders. "
                "Provide a name and optionally a due date, notes, "
                "list name, and priority (0=none, 1=high, 5=medium, 9=low)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Reminder title",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Due date in ISO 8601 format (e.g. 2026-06-15T09:00:00)",
                        "default": "",
                    },
                    "body": {
                        "type": "string",
                        "description": "Additional notes for the reminder",
                        "default": "",
                    },
                    "list_name": {
                        "type": "string",
                        "description": "Reminders list name",
                        "default": "Reminders",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Priority: 0=none, 1=high, 5=medium, 9=low",
                        "default": 0,
                    },
                },
                "required": ["name"],
            },
            category="productivity",
            requires_confirmation=True,
        )

    def execute(self, **params: Any) -> ToolResult:
        name = params.get("name", "")
        if not name:
            return _err(self.tool_id, "Reminder name is required")

        due_date_str = params.get("due_date", "")
        body = params.get("body", "")
        list_name = params.get("list_name", "Reminders")
        priority = params.get("priority", 0)

        # Convert ISO 8601 to AppleScript date format
        applescript_due = ""
        if due_date_str:
            try:
                dt = datetime.fromisoformat(due_date_str)
                applescript_due = dt.strftime("%B %d, %Y at %I:%M:%S %p")
            except ValueError:
                return _err(self.tool_id, f"Invalid due_date format: {due_date_str}")

        result = _applescript_create_reminder(
            name=name,
            list_name=list_name,
            body=body,
            due_date=applescript_due,
            priority=priority,
        )

        if not result.get("success"):
            return _err(self.tool_id, result.get("error", "Failed to create reminder"))

        return _ok(self.tool_id, json.dumps(result, indent=2, default=str))


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
