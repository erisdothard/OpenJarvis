# ruff: noqa: E501
"""Daily check-in agent — 4 PM outbound prompt + reply routing.

At 4 PM (configurable), Jarvis texts the user asking for updates.
When the user replies with free-form text, the agent:
1. Classifies each update (calendar, reminder, note, message)
2. Executes via existing tools (Apple Calendar, Reminders, iMessage)
3. Sends a confirmation summary back
4. If uncertain about any item, asks for clarification

Scheduling
----------
The agent self-registers a 4pm daily cron task when ``register_cron``
is called from app startup:

    from openjarvis.agents.checkin_agent import register_cron
    register_cron(scheduler, phone="+15551234567")
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.config import load_config
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall

logger = logging.getLogger(__name__)

_DB_PATH = str(Path.home() / "Library" / "Messages" / "chat.db")

_POLL_INTERVAL = 30  # seconds between polls
_BATCH_WAIT = 30  # seconds to wait for additional messages after first arrives

_CHECKIN_GREETING = (
    "Good afternoon, sir \u2014 any updates to log? "
    "Meetings, tasks, reminders, anything at all."
)

_NO_REPLY_ACK = "No worries, sir. I'll be here if anything comes up."
_EMPTY_UPDATE_ACK = "Got it \u2014 all clear. Let me know if anything comes up."

# ---------------------------------------------------------------------------
# Router system prompt — classifies free-form text into structured actions
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM_PROMPT = """\
You parse a user's free-form daily update into structured actions.

The user was asked "any updates?" and is reporting on their day.
Parse each distinct update into ONE action.

CALENDAR — meetings, appointments, schedule changes:
  "moved Friday's meeting to 2pm" -> calendar
  "lunch with Sarah tomorrow at noon" -> calendar

REMINDER — tasks, to-dos, follow-ups:
  "remind me to send the invoice Thursday" -> reminder
  "need to call John back" -> reminder

NOTE — information to save, progress, decisions:
  "finished the FreightX dashboard" -> note
  "decided to go with Supabase" -> note

IMESSAGE — text someone specific:
  "text Mom I'll be there at 6" -> imessage

If the user says "nothing", "nope", "all good", or similar -> return empty array [].

For each action output a JSON object:
  type: "calendar" | "reminder" | "note" | "imessage"
  summary: brief description of what the user said
  params: action parameters (see below)
  confidence: "high" if clear, "low" if ambiguous

Calendar params:
  summary (string) — event title
  start (string) — ISO 8601 datetime
  end (string) — ISO 8601 datetime (default 1h after start if not given)
  calendar (string, optional) — calendar name, default "Home"
  location (string, optional)
  notes (string, optional)

Reminder params:
  name (string) — reminder title
  due_date (string, optional) — ISO 8601 if mentioned
  body (string, optional) — additional notes
  list_name (string, optional) — default "Reminders"
  priority (int, optional) — 0=none 1=high 5=medium 9=low

Note params:
  title (string) — short label
  body (string) — full context

iMessage params:
  recipient (string) — phone or name. If no number given set confidence "low"
  message (string) — text to send

Output a JSON array inside a ```json block.
Today is {date} at {time} ({timezone}).
Use this to resolve relative dates: "tomorrow", "Thursday", "next week", etc.
"""


def _extract_json_block(text: str) -> Optional[List[Dict[str, Any]]]:
    """Extract a JSON array from LLM output (reuses proactive_agent logic)."""
    try:
        from openjarvis.agents.proactive_agent import _extract_json_block as _extract

        return _extract(text)
    except ImportError:
        pass
    # Inline fallback
    import re

    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1).strip())
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            pass
    return None


@AgentRegistry.register("checkin")
class CheckinAgent(ToolUsingAgent):
    """Sends a daily check-in iMessage and routes the reply to the right tools."""

    agent_id = "checkin"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._phone: str = kwargs.pop("phone", "")
        self._reply_timeout: int = kwargs.pop("reply_timeout_minutes", 60)
        self._timezone: str = kwargs.pop("timezone", "America/Chicago")

        try:
            cfg = load_config()
            c = cfg.checkin
            if not self._phone:
                self._phone = c.phone
            if not self._reply_timeout:
                self._reply_timeout = c.reply_timeout_minutes
            if not self._timezone:
                self._timezone = c.timezone
        except Exception:
            pass

        # Inject the tools we need for routing updates
        from openjarvis.tools.connector_tools import (
            AppleCalendarCreateEvent,
            AppleRemindersCreate,
            SendIMessage,
        )

        checkin_tools: List[Any] = [
            AppleCalendarCreateEvent(),
            AppleRemindersCreate(),
            SendIMessage(),
        ]
        caller_tools: List[Any] = kwargs.pop("tools", None) or []
        kwargs["tools"] = checkin_tools + caller_tools
        kwargs.setdefault("max_tokens", 4096)
        kwargs.setdefault("temperature", 0.2)
        # The user already consented by replying to the check-in prompt,
        # so auto-approve tool confirmations.
        kwargs.setdefault("interactive", True)
        kwargs.setdefault("confirm_callback", lambda _prompt: True)

        super().__init__(*args, **kwargs)

        # AppleScript tools are slow under launchd (open app + execute).
        # Default 30s is too tight — give 120s per tool call.
        self._executor._default_timeout = 120

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(
        self,
        input: str = "",
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input or "checkin_run")

        if not self._phone:
            self._emit_turn_end(turns=1)
            return AgentResult(
                content="No phone configured for check-in. Set [checkin] phone in config.toml.",
                turns=1,
            )

        from openjarvis.channels.imessage_daemon import (
            _get_max_rowid,
            poll_new_messages,
            send_imessage,
        )

        # --- Phase 1: Send check-in greeting ---
        baseline_rowid = _get_max_rowid(_DB_PATH)
        sent = send_imessage(self._phone, _CHECKIN_GREETING)
        if not sent:
            self._emit_turn_end(turns=1)
            return AgentResult(content="Failed to send check-in iMessage.", turns=1)

        logger.info(
            "Check-in sent to %s, polling for reply (timeout: %d min)",
            self._phone,
            self._reply_timeout,
        )

        # --- Phase 2: Poll for reply ---
        reply_text = self._poll_for_reply(
            baseline_rowid, poll_new_messages, timeout_minutes=self._reply_timeout
        )

        if not reply_text:
            send_imessage(self._phone, _NO_REPLY_ACK)
            self._emit_turn_end(turns=1)
            return AgentResult(content="No reply received within timeout.", turns=1)

        logger.info("Check-in reply received (%d chars)", len(reply_text))

        # --- Phase 3: Classify via LLM ---
        classified = self._classify_updates(reply_text)
        if not classified:
            send_imessage(self._phone, _EMPTY_UPDATE_ACK)
            self._emit_turn_end(turns=1)
            return AgentResult(content="Reply contained no actionable updates.", turns=1)

        # --- Phase 4: Execute each update ---
        results: List[Dict[str, Any]] = []
        clarifications: List[Dict[str, Any]] = []

        for item in classified:
            if item.get("confidence") == "low":
                clarifications.append(item)
                continue
            result = self._execute_update(item)
            results.append(result)

        # --- Phase 5: Confirm ---
        confirmation = self._build_confirmation(results, clarifications)
        send_imessage(self._phone, confirmation)

        # --- Phase 6: Handle clarifications (one round) ---
        if clarifications:
            follow_up_results = self._handle_clarifications(
                clarifications, baseline_rowid, poll_new_messages, send_imessage
            )
            results.extend(follow_up_results)

        self._emit_turn_end(turns=1)
        return AgentResult(
            content=confirmation,
            turns=1,
            metadata={
                "updates_processed": len(results),
                "clarifications_needed": len(clarifications),
            },
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_for_reply(
        self,
        baseline_rowid: int,
        poll_fn: Any,
        *,
        timeout_minutes: int = 60,
    ) -> Optional[str]:
        """Poll chat.db for incoming messages after baseline_rowid."""
        deadline = datetime.now() + timedelta(minutes=timeout_minutes)
        last_rowid = baseline_rowid

        while datetime.now() < deadline:
            time.sleep(_POLL_INTERVAL)
            messages = poll_fn(
                db_path=_DB_PATH,
                last_rowid=last_rowid,
                chat_identifier=self._phone,
            )
            if messages:
                # First batch arrived — wait a bit for additional texts
                time.sleep(_BATCH_WAIT)
                more = poll_fn(
                    db_path=_DB_PATH,
                    last_rowid=messages[-1]["rowid"],
                    chat_identifier=self._phone,
                )
                all_msgs = messages + (more or [])
                parts = [m["text"] for m in all_msgs if m.get("text")]
                return "\n".join(parts) if parts else None

        return None

    # ------------------------------------------------------------------
    # LLM classification
    # ------------------------------------------------------------------

    def _classify_updates(self, text: str) -> List[Dict[str, Any]]:
        """Call LLM to parse free-form text into structured update actions."""
        now = datetime.now()
        system = _ROUTER_SYSTEM_PROMPT.format(
            date=now.strftime("%A, %B %d, %Y"),
            time=now.strftime("%I:%M %p"),
            timezone=self._timezone,
        )
        messages = [
            Message(role=Role.SYSTEM, content=system),
            Message(role=Role.USER, content=text),
        ]
        result = self._generate(messages)
        raw = self._strip_think_tags(result.get("content", ""))

        # Debug log
        try:
            from openjarvis.core.config import DEFAULT_CONFIG_DIR

            log_dir = DEFAULT_CONFIG_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "checkin_debug.log"
            with log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n===== {now.isoformat()} =====\n")
                f.write(f"--- user input ---\n{text}\n")
                f.write(f"--- llm raw ---\n{raw}\n")
        except Exception:
            pass

        return _extract_json_block(raw) or []

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_update(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single classified update via the mapped tool."""
        update_type = item.get("type", "")
        params = item.get("params", {})
        summary = item.get("summary", "")

        tool_name, tool_args = self._map_to_tool(update_type, params, summary)
        if not tool_name:
            return {
                "success": False,
                "type": update_type,
                "summary": summary,
                "error": f"Unknown update type: {update_type}",
            }

        try:
            call = ToolCall(
                id=f"checkin-{update_type}-{abs(hash(summary)) % 10000}",
                name=tool_name,
                arguments=json.dumps(tool_args),
            )
            result = self._executor.execute(call)
            return {
                "success": result.success,
                "type": update_type,
                "summary": summary,
                "tool": tool_name,
                "error": "" if result.success else result.content,
            }
        except Exception as exc:
            return {
                "success": False,
                "type": update_type,
                "summary": summary,
                "error": str(exc),
            }

    @staticmethod
    def _map_to_tool(
        update_type: str,
        params: Dict[str, Any],
        summary: str,
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Map a classified update type to a tool name and arguments."""
        if update_type == "calendar":
            return "apple_calendar_create_event", {
                "summary": params.get("summary", summary),
                "start": params.get("start", ""),
                "end": params.get("end", ""),
                "calendar": params.get("calendar", "Home"),
                "location": params.get("location", ""),
                "notes": params.get("notes", ""),
            }

        if update_type == "reminder":
            return "apple_reminders_create", {
                "name": params.get("name", summary),
                "due_date": params.get("due_date", ""),
                "body": params.get("body", ""),
                "list_name": params.get("list_name", "Reminders"),
                "priority": params.get("priority", 0),
            }

        if update_type == "note":
            # Store as a reminder in a "Notes" list until Apple Notes tool exists
            return "apple_reminders_create", {
                "name": params.get("title", summary),
                "body": params.get("body", ""),
                "list_name": "Notes",
            }

        if update_type == "imessage":
            return "send_imessage", {
                "recipient": params.get("recipient", ""),
                "message": params.get("message", ""),
            }

        return None, {}

    # ------------------------------------------------------------------
    # Confirmation message
    # ------------------------------------------------------------------

    @staticmethod
    def _build_confirmation(
        results: List[Dict[str, Any]],
        clarifications: List[Dict[str, Any]],
    ) -> str:
        """Build the iMessage confirmation summary."""
        lines: List[str] = []

        successes = [r for r in results if r.get("success")]
        failures = [r for r in results if not r.get("success")]

        if successes:
            lines.append("Done:")
            _icons = {
                "calendar": "\U0001f4c5",
                "reminder": "\u2713",
                "note": "\U0001f4dd",
                "imessage": "\U0001f4ac",
            }
            for r in successes:
                icon = _icons.get(r.get("type", ""), "\u2713")
                lines.append(f"  {icon} {r['summary']}")

        if failures:
            if lines:
                lines.append("")
            lines.append("Couldn't do:")
            for r in failures:
                lines.append(f"  \u2717 {r['summary']} \u2014 {r.get('error', 'unknown')}")

        if clarifications:
            if lines:
                lines.append("")
            lines.append("Wasn't sure about:")
            for c in clarifications:
                lines.append(f"  ? {c.get('summary', '')} \u2014 where should this go?")

        if not lines:
            return "All clear \u2014 nothing to route."

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Clarification handling
    # ------------------------------------------------------------------

    def _handle_clarifications(
        self,
        clarifications: List[Dict[str, Any]],
        baseline_rowid: int,
        poll_fn: Any,
        send_fn: Any,
    ) -> List[Dict[str, Any]]:
        """Wait for one clarification reply, re-classify, execute."""
        # Update baseline to latest rowid
        from openjarvis.channels.imessage_daemon import _get_max_rowid

        current_rowid = _get_max_rowid(_DB_PATH)

        reply = self._poll_for_reply(
            current_rowid, poll_fn, timeout_minutes=5
        )
        if not reply:
            return []

        re_classified = self._classify_updates(reply)
        follow_up_results = []
        for item in re_classified or []:
            result = self._execute_update(item)
            follow_up_results.append(result)

        if follow_up_results:
            confirmation = self._build_confirmation(follow_up_results, [])
            send_fn(self._phone, confirmation)

        return follow_up_results


# ---------------------------------------------------------------------------
# Convenience: register the 4pm cron task
# ---------------------------------------------------------------------------


def register_cron(
    scheduler: Any,
    *,
    phone: str = "",
    cron_expr: str = "",
    timezone: str = "",
) -> Any:
    """Register the check-in agent as a daily cron task.

    All defaults are read from ``config.toml [checkin]`` when not passed.
    Call once from app startup after the scheduler is started.
    """
    try:
        cfg = load_config()
        c = cfg.checkin
        phone = phone or c.phone
        cron_expr = cron_expr or c.schedule
        timezone = timezone or c.timezone
    except Exception:
        cron_expr = cron_expr or "0 16 * * *"
        timezone = timezone or "America/Chicago"

    return scheduler.create_task(
        prompt="Run the daily check-in: ask for updates, classify, and route.",
        schedule_type="cron",
        schedule_value=cron_expr,
        agent="checkin",
        context_mode="isolated",
        metadata={
            "phone": phone,
            "timezone": timezone,
        },
    )
