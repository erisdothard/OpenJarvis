"""Unified social media publishing tool — post to multiple platforms at once.

Orchestrates LinkedIn, Instagram, Facebook, and Twitter/X connectors
to publish content from a single tool call.  Supports optional scheduling
via a simple SQLite-backed queue with Telegram approval before publishing.
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
_DEFAULT_PHONE = "+16152439891"

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
            result TEXT DEFAULT '',
            approval_id TEXT DEFAULT ''
        )"""
    )
    # Migrate: add approval_id column if missing (existing DBs)
    try:
        conn.execute("SELECT approval_id FROM scheduled_posts LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE scheduled_posts ADD COLUMN approval_id TEXT DEFAULT ''"
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Telegram notification helpers
# ---------------------------------------------------------------------------

def _send_text(message: str) -> bool:
    """Send a Telegram notification."""
    from openjarvis.notifications import send_telegram

    return send_telegram(message)


def _get_server_port() -> int:
    """Read the configured server port (default 8000)."""
    try:
        from openjarvis.core.config import load_config
        return load_config().server.port
    except Exception:
        return 8000


def _send_approval_text(
    platforms: List[str], content: str, approval_id: str,
) -> None:
    """Send Eris a post preview and ask for approval via Telegram."""
    platform_str = ", ".join(p.title() for p in platforms)
    preview = content[:500]
    port = _get_server_port()
    approve_url = f"http://localhost:{port}/v1/approvals/{approval_id}"
    message = (
        f"JARVIS — Post Ready\n\n"
        f"Platforms: {platform_str}\n\n"
        f"{preview}\n\n"
        f"Tap to approve/deny:\n{approve_url}"
    )
    if not _send_text(message):
        _log.warning("Failed to send approval notification for %s", approval_id)


def _send_confirmation_text(
    platforms: List[str],
    status: str,
    published: List[Dict[str, Any]],
    failed: List[Dict[str, Any]],
) -> None:
    """Notify Eris of the publish result via Telegram."""
    if status == "published":
        names = ", ".join(p.title() for p in platforms)
        _send_text(f"JARVIS — Posted to {names} successfully!")
    elif status == "partial":
        ok = ", ".join(r["platform"].title() for r in published)
        fail = ", ".join(r["platform"].title() for r in failed)
        _send_text(f"JARVIS — Posted to {ok}. Failed: {fail}.")
    else:
        names = ", ".join(p.title() for p in platforms)
        _send_text(f"JARVIS — Failed to post to {names}.")


def _send_denial_text(platforms: List[str]) -> None:
    """Notify Eris that the post was cancelled via Telegram."""
    names = ", ".join(p.title() for p in platforms)
    _send_text(f"JARVIS — Post to {names} cancelled.")


# ---------------------------------------------------------------------------
# Platform publishing
# ---------------------------------------------------------------------------

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
        # ConnectorRegistry may return a class — instantiate if needed
        if isinstance(connector, type):
            connector = connector()
    except Exception:
        return {
            "platform": platform,
            "status": "error",
            "error": f"Connector '{connector_id}' not available. Configure it first.",
        }

    try:
        connected = connector.is_connected()
    except Exception:
        connected = False

    if not connected:
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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

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
                "INSERT INTO scheduled_posts (platforms, content, media_urls, schedule_time, status) VALUES (?, ?, ?, ?, ?)",
                (
                    json.dumps(platforms),
                    content,
                    json.dumps(media_urls),
                    schedule_time,
                    "scheduled",
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

        lines = ["## Social Publish Results\n"]
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
            description="List all pending/scheduled social media posts.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "scheduled",
                            "awaiting_approval",
                            "published",
                            "denied",
                            "failed",
                            "all",
                        ],
                        "description": "Filter by status (default: scheduled).",
                    },
                },
                "required": [],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        status_filter = params.get("status", "scheduled")

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


@ToolRegistry.register("social_schedule_post")
class SocialSchedulePostTool(BaseTool):
    """Schedule a post for future publishing with iMessage approval."""

    tool_id = "social_schedule_post"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="social_schedule_post",
            description=(
                "Schedule a post for publishing at a specific time. When the time "
                "arrives, Jarvis will send you the post for approval via Telegram. "
                "The post only publishes after you approve it."
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
                        "description": "The post content.",
                    },
                    "schedule_time": {
                        "type": "string",
                        "description": (
                            "When to request approval and publish. ISO 8601 datetime "
                            "(e.g. '2026-06-13T09:00:00'). Must be in the future."
                        ),
                    },
                    "media_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional public image/video URLs to attach.",
                    },
                },
                "required": ["platforms", "content", "schedule_time"],
            },
            category="social",
        )

    def execute(self, **params: Any) -> ToolResult:
        platforms: List[str] = params.get("platforms", [])
        content: str = params.get("content", "")
        schedule_time: str = params.get("schedule_time", "")
        media_urls: List[str] = params.get("media_urls", [])

        if not platforms or not content or not schedule_time:
            return ToolResult(
                tool_name="social_schedule_post",
                content="Missing required fields: platforms, content, schedule_time.",
                success=False,
            )

        try:
            scheduled_dt = datetime.fromisoformat(schedule_time)
        except ValueError:
            return ToolResult(
                tool_name="social_schedule_post",
                content=f"Invalid schedule_time: {schedule_time}. Use ISO 8601 format.",
                success=False,
            )

        if scheduled_dt <= datetime.now():
            return ToolResult(
                tool_name="social_schedule_post",
                content="schedule_time must be in the future.",
                success=False,
            )

        conn = _ensure_schedule_db()
        conn.execute(
            "INSERT INTO scheduled_posts (platforms, content, media_urls, schedule_time, status) VALUES (?, ?, ?, ?, ?)",
            (
                json.dumps(platforms),
                content,
                json.dumps(media_urls),
                schedule_time,
                "scheduled",
            ),
        )
        conn.commit()
        post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()

        platform_str = ", ".join(p.title() for p in platforms)
        return ToolResult(
            tool_name="social_schedule_post",
            content=(
                f"Post #{post_id} scheduled for {schedule_time} on {platform_str}.\n\n"
                f"**Content preview:**\n{content[:200]}...\n\n"
                f"At {schedule_time}, Jarvis will notify you via Telegram for approval before publishing."
            ),
            success=True,
            metadata={
                "post_id": post_id,
                "schedule_time": schedule_time,
                "platforms": platforms,
            },
        )


# ---------------------------------------------------------------------------
# Background scheduler — two-phase: request approval → publish on approval
# ---------------------------------------------------------------------------

def run_social_scheduler() -> None:
    """Background tick: send approval requests for due posts, publish approved ones.

    Called every 60s from a daemon thread in serve.py.

    Phase 1 — "scheduled" posts whose schedule_time arrived:
        Create a PendingAction in ApprovalStore → send Telegram notification → set "awaiting_approval"

    Phase 2 — "awaiting_approval" posts:
        Check ApprovalStore → if approved → publish → Telegram confirmation
                            → if denied  → cancel  → Telegram confirmation
                            → if expired → mark expired
    """
    if not _SCHEDULE_DB.exists():
        return

    now = datetime.now().isoformat()
    conn = _ensure_schedule_db()

    # ── Phase 1: request approval for due posts ──────────────────────────
    due_rows = conn.execute(
        "SELECT id, platforms, content, media_urls, schedule_time "
        "FROM scheduled_posts WHERE status = 'scheduled' AND schedule_time <= ?",
        (now,),
    ).fetchall()

    for row in due_rows:
        post_id, platforms_json, content, media_urls_json, schedule_time = row
        platforms = json.loads(platforms_json)

        try:
            from openjarvis.tools.approval_store import ApprovalStore, TIER_HIGH

            store = ApprovalStore()
            action = store.queue_action(
                action_type="social_publish",
                description=(
                    f"Publish to {', '.join(p.title() for p in platforms)}: "
                    f"{content[:100]}..."
                ),
                payload={
                    "post_id": post_id,
                    "platforms": platforms,
                    "content": content,
                    "media_urls": json.loads(media_urls_json) if media_urls_json else [],
                },
                permission_key=f"social_publish:post_{post_id}",
                tier=TIER_HIGH,
                ttl_hours=24,
            )

            # Send iMessage notification
            _send_approval_text(platforms, content, action.id)

            # Update post status
            conn.execute(
                "UPDATE scheduled_posts SET status = 'awaiting_approval', "
                "approval_id = ? WHERE id = ?",
                (action.id, post_id),
            )
            conn.commit()

            _log.info(
                "Post #%d: approval requested via Telegram (action %s)",
                post_id,
                action.id,
            )
            store.close()
        except Exception as exc:
            _log.error("Failed to request approval for post #%d: %s", post_id, exc)

    # ── Phase 2: check awaiting_approval posts for decisions ─────────────
    waiting_rows = conn.execute(
        "SELECT id, platforms, content, media_urls, approval_id "
        "FROM scheduled_posts "
        "WHERE status = 'awaiting_approval' AND approval_id != ''",
    ).fetchall()

    for row in waiting_rows:
        post_id, platforms_json, content, media_urls_json, approval_id = row
        platforms = json.loads(platforms_json)
        media_urls = json.loads(media_urls_json) if media_urls_json else []

        try:
            from openjarvis.tools.approval_store import (
                ApprovalStore,
                STATUS_APPROVED,
                STATUS_DENIED,
                STATUS_EXPIRED,
                STATUS_EXECUTED,
            )

            store = ApprovalStore()
            action = store.get_action(approval_id)

            if action is None:
                continue

            if action.status == STATUS_APPROVED:
                # Publish
                results = []
                for platform in platforms:
                    result = _publish_to_platform(platform, content, media_urls)
                    results.append(result)

                published = [r for r in results if r["status"] == "published"]
                failed = [r for r in results if r["status"] == "error"]

                if failed and not published:
                    new_status = "failed"
                elif failed:
                    new_status = "partial"
                else:
                    new_status = "published"

                conn.execute(
                    "UPDATE scheduled_posts SET status = ?, result = ? WHERE id = ?",
                    (new_status, json.dumps(results), post_id),
                )
                conn.commit()

                store.update_status(approval_id, STATUS_EXECUTED)
                _send_confirmation_text(platforms, new_status, published, failed)

                _log.info(
                    "Post #%d: %s (%d published, %d failed)",
                    post_id,
                    new_status,
                    len(published),
                    len(failed),
                )

            elif action.status == STATUS_DENIED:
                conn.execute(
                    "UPDATE scheduled_posts SET status = 'denied' WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                store.update_status(approval_id, STATUS_EXECUTED)
                _send_denial_text(platforms)
                _log.info("Post #%d: denied by user", post_id)

            elif action.status == STATUS_EXPIRED:
                conn.execute(
                    "UPDATE scheduled_posts SET status = 'expired' WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                _log.info("Post #%d: approval expired", post_id)

            # else: still pending — do nothing, check again next tick

            store.close()
        except Exception as exc:
            _log.error("Failed to check approval for post #%d: %s", post_id, exc)

    conn.close()


__all__ = [
    "SocialPublishTool",
    "SocialScheduleListTool",
    "SocialSchedulePostTool",
    "run_social_scheduler",
]
