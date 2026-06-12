"""Unified social media publishing tool — post to multiple platforms at once.

Orchestrates LinkedIn, Instagram, Facebook, and Twitter/X connectors
to publish content from a single tool call.  Supports optional scheduling
via a simple SQLite-backed queue.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry, ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)
_SCHEDULE_DB = DEFAULT_CONFIG_DIR / "social_schedule.db"

# Platform → connector_id → tool_name for posting
_PLATFORM_POST_TOOLS: Dict[str, Dict[str, str]] = {
    "linkedin": {"connector": "linkedin", "tool": "linkedin_create_post"},
    "instagram": {"connector": "instagram", "tool": "instagram_create_post"},
    "facebook": {"connector": "facebook", "tool": "facebook_create_post"},
    "twitter": {"connector": "twitter", "tool": "twitter_create_post"},
}


def _ensure_schedule_db() -> sqlite3.Connection:
    """Create the schedule DB and table if needed."""
    _SCHEDULE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SCHEDULE_DB))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scheduled_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platforms TEXT NOT NULL,
            content TEXT NOT NULL,
            media_urls TEXT DEFAULT '[]',
            schedule_time TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            result TEXT DEFAULT ''
        )"""
    )
    conn.commit()
    return conn


def _publish_to_platform(
    platform: str,
    content: str,
    media_urls: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Publish content to a single platform via its connector."""
    config = _PLATFORM_POST_TOOLS.get(platform)
    if not config:
        return {"platform": platform, "status": "error", "error": f"Unknown platform: {platform}"}

    connector_id = config["connector"]
    tool_name = config["tool"]

    try:
        connector = ConnectorRegistry.get(connector_id)
    except (KeyError, Exception):
        return {
            "platform": platform,
            "status": "error",
            "error": f"Connector '{connector_id}' not available. Configure it first.",
        }

    if not connector.is_connected():
        return {
            "platform": platform,
            "status": "error",
            "error": f"{platform.title()} not connected. Set up credentials first.",
        }

    # Build platform-specific params
    params: Dict[str, Any] = {}
    if platform == "linkedin":
        params["commentary"] = content
    elif platform == "instagram":
        if not media_urls:
            return {
                "platform": platform,
                "status": "error",
                "error": "Instagram requires an image_url for posting.",
            }
        params["image_url"] = media_urls[0]
        params["caption"] = content
    elif platform == "facebook":
        params["message"] = content
    elif platform == "twitter":
        params["text"] = content

    try:
        result = connector.execute_tool(tool_name, params)
        return {
            "platform": platform,
            "status": "published",
            "result": result,
        }
    except Exception as exc:
        return {
            "platform": platform,
            "status": "error",
            "error": str(exc),
        }


@ToolRegistry.register("social_publish")
class SocialPublishTool(BaseTool):
    """Publish content to multiple social media platforms at once."""

    tool_id = "social_publish"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="social_publish",
            description=(
                "Publish a post to one or more social media platforms simultaneously. "
                "Supports LinkedIn, Instagram, Facebook, and Twitter/X. "
                "Optionally schedule posts for a future time."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "platforms": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["linkedin", "instagram", "facebook", "twitter"],
                        },
                        "description": "Platforms to post to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content of the post.",
                    },
                    "media_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of public image/video URLs to attach.",
                    },
                    "schedule_time": {
                        "type": "string",
                        "description": (
                            "ISO 8601 datetime to schedule the post for (e.g. '2026-06-15T10:00:00'). "
                            "Omit to publish immediately."
                        ),
                    },
                },
                "required": ["platforms", "content"],
            },
            category="social",
            requires_confirmation=True,
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        platforms: List[str] = params.get("platforms", [])
        content: str = params.get("content", "")
        media_urls: List[str] = params.get("media_urls", [])
        schedule_time: Optional[str] = params.get("schedule_time")

        if not platforms:
            return ToolResult(
                tool_name="social_publish",
                content="No platforms specified.",
                success=False,
            )
        if not content:
            return ToolResult(
                tool_name="social_publish",
                content="No content provided.",
                success=False,
            )

        # Schedule for later
        if schedule_time:
            try:
                scheduled_dt = datetime.fromisoformat(schedule_time)
            except ValueError:
                return ToolResult(
                    tool_name="social_publish",
                    content=f"Invalid schedule_time format: {schedule_time}. Use ISO 8601.",
                    success=False,
                )

            if scheduled_dt <= datetime.now():
                return ToolResult(
                    tool_name="social_publish",
                    content="schedule_time must be in the future.",
                    success=False,
                )

            conn = _ensure_schedule_db()
            conn.execute(
                "INSERT INTO scheduled_posts (platforms, content, media_urls, schedule_time) VALUES (?, ?, ?, ?)",
                (
                    json.dumps(platforms),
                    content,
                    json.dumps(media_urls),
                    schedule_time,
                ),
            )
            conn.commit()
            conn.close()

            return ToolResult(
                tool_name="social_publish",
                content=(
                    f"Post scheduled for {schedule_time} on {', '.join(platforms)}.\n"
                    f"Content: {content[:100]}..."
                ),
                success=True,
                metadata={"scheduled": True, "schedule_time": schedule_time},
            )

        # Publish now
        results = []
        for platform in platforms:
            result = _publish_to_platform(platform, content, media_urls)
            results.append(result)

        # Format output
        published = [r for r in results if r["status"] == "published"]
        failed = [r for r in results if r["status"] == "error"]

        lines = [f"## Social Publish Results\n"]
        for r in published:
            lines.append(f"- **{r['platform'].title()}**: Published")
        for r in failed:
            lines.append(f"- **{r['platform'].title()}**: FAILED — {r['error']}")

        all_success = len(failed) == 0
        return ToolResult(
            tool_name="social_publish",
            content="\n".join(lines),
            success=all_success,
            metadata={
                "platform_statuses": results,
                "published_count": len(published),
                "failed_count": len(failed),
            },
        )


@ToolRegistry.register("social_schedule_list")
class SocialScheduleListTool(BaseTool):
    """List scheduled social media posts."""

    tool_id = "social_schedule_list"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="social_schedule_list",
            description="List all pending scheduled social media posts.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "published", "failed", "all"],
                        "description": "Filter by status (default: pending).",
                    },
                },
                "required": [],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        status_filter = params.get("status", "pending")

        if not _SCHEDULE_DB.exists():
            return ToolResult(
                tool_name="social_schedule_list",
                content="No scheduled posts found.",
                success=True,
            )

        conn = _ensure_schedule_db()
        if status_filter == "all":
            rows = conn.execute(
                "SELECT id, platforms, content, schedule_time, status FROM scheduled_posts ORDER BY schedule_time"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, platforms, content, schedule_time, status FROM scheduled_posts WHERE status = ? ORDER BY schedule_time",
                (status_filter,),
            ).fetchall()
        conn.close()

        if not rows:
            return ToolResult(
                tool_name="social_schedule_list",
                content=f"No {status_filter} posts found.",
                success=True,
            )

        lines = [f"## Scheduled Posts ({status_filter})\n"]
        for row in rows:
            platforms = json.loads(row[1])
            lines.append(
                f"**#{row[0]}** | {row[3]} | {', '.join(platforms)} | {row[4]}\n"
                f"  {row[2][:120]}...\n"
            )

        return ToolResult(
            tool_name="social_schedule_list",
            content="\n".join(lines),
            success=True,
            metadata={"count": len(rows)},
        )


__all__ = ["SocialPublishTool", "SocialScheduleListTool"]
