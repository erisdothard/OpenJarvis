"""Daily Digest Agent — synthesizes morning, midday, and evening briefings.

Thin orchestrator that delegates to digest_collect (data fetching),
the LLM (narrative synthesis), and text_to_speech (audio generation).
Supports three digest types:
  - morning: full daily briefing (priorities, schedule, messages, health, world)
  - midday: status update (new messages, schedule changes, quick check-in)
  - evening: circle-back (uncompleted items, carry-forward to tomorrow, day summary)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.agents.digest_store import DigestArtifact, DigestStore
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall


def _load_persona(persona_name: str) -> str:
    """Load a persona prompt file by name."""
    search_paths = [
        Path("configs/openjarvis/prompts/personas") / f"{persona_name}.md",
        Path.home() / ".openjarvis" / "prompts" / "personas" / f"{persona_name}.md",
    ]
    for p in search_paths:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


@AgentRegistry.register("morning_digest")
class MorningDigestAgent(ToolUsingAgent):
    """Pre-compute a daily digest from configured data sources."""

    agent_id = "morning_digest"

    # Valid digest types
    DIGEST_TYPES = ("morning", "midday", "evening")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Extract digest-specific kwargs before passing to parent
        self._persona = kwargs.pop("persona", "jarvis")
        self._sections = kwargs.pop(
            "sections", ["messages", "calendar", "health", "world"]
        )
        self._section_sources = kwargs.pop("section_sources", {})
        self._timezone = kwargs.pop("timezone", "America/Los_Angeles")
        self._voice_id = kwargs.pop("voice_id", "")
        self._voice_speed = kwargs.pop("voice_speed", 1.0)
        self._tts_backend = kwargs.pop("tts_backend", "openai_tts")
        self._digest_store_path = kwargs.pop("digest_store_path", "")
        self._honorific = kwargs.pop("honorific", "sir")
        self._digest_type = kwargs.pop("digest_type", "morning")
        super().__init__(*args, **kwargs)

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt from persona + briefing structure."""
        persona_text = _load_persona(self._persona)
        try:
            now = datetime.now(ZoneInfo(self._timezone))
        except Exception:
            now = datetime.now()
        honorific = getattr(self, "_honorific", "sir")
        digest_type = getattr(self, "_digest_type", "morning")

        preamble = (
            f"{persona_text}\n\n"
            f"Today is {now.strftime('%A, %B %d, %Y')}. "
            f"The time is {now.strftime('%I:%M %p')} in {self._timezone}.\n"
            f"The user's preferred honorific is: {honorific}\n\n"
            "You receive structured data from the user's connected services. "
            "The data has ALREADY been collected — it appears in the user "
            "message. You do NOT fetch anything yourself.\n\n"
        )

        rules = (
            "ABSOLUTE RULES (violations are unacceptable):\n"
            "- ONLY facts from the data. Zero hallucination.\n"
            "- NEVER mention disconnected or unavailable sources.\n"
            "- NEVER state raw health numbers. Say 'your sleep was solid' "
            "NOT 'heart rate 56 bpm' or 'HRV 53' or '6000 steps' or "
            "'readiness 82'. Interpret, never enumerate.\n"
            "- NEVER describe actions you are taking.\n"
            "- Acknowledge every source that returned data, even briefly.\n"
            "- No markdown, emojis, bullets, or headers.\n"
        )

        if digest_type == "midday":
            structure = self._midday_prompt()
        elif digest_type == "evening":
            structure = self._evening_prompt()
        else:
            structure = self._morning_prompt()

        return preamble + structure + "\n\n" + rules

    def _morning_prompt(self) -> str:
        """Full morning briefing structure."""
        return (
            "Produce a 2-4 minute spoken briefing in DECREASING order of "
            "importance:\n\n"
            "1. GREETING + PRIORITIES — Open with the honorific and "
            "immediately state what needs attention: overdue tasks, today's "
            "deadlines, events requiring preparation. Connect related items "
            "('Your rebuttals are overdue and you have a dinner at 6, so "
            "I'd tackle those first').\n\n"
            "2. SCHEDULE — Today's upcoming events with time context: 'You "
            "have 3 hours before your next meeting.' Skip past events.\n\n"
            "3. MESSAGES — Triage across ALL channels (email, texts, Slack):\n"
            "  - Emails tagged [IMPORTANT] are HIGH PRIORITY — summarize their "
            "content, who sent them, and what action is needed\n"
            "  - Emails tagged [REPLIED] mean the user already responded — "
            "acknowledge briefly but do NOT say 'you need to reply'\n"
            "  - Emails tagged [UNREAD] from real people need attention — "
            "summarize what they say and what response is expected\n"
            "  - Connect related items: if an email mentions an interview, "
            "check calendar for the corresponding event and mention both\n"
            "  - SKIP automated emails, newsletters, and marketing entirely\n"
            "  - Quote relevant message text when it adds context\n\n"
            "4. HEALTH — Interpret trends, not raw numbers. 'Your sleep has "
            "improved three nights running and your readiness is strong' — "
            "not 'HRV 53, HR 56.' If multiple days of data, compare.\n\n"
            "5. WORLD — Weather forecast, top news (AI/tech, business, "
            "general). Skip if no data.\n\n"
            "6. CLOSING — One forward-looking sentence with the honorific.\n\n"
            "- STRICT LIMIT: 200 words. Be concise."
        )

    def _midday_prompt(self) -> str:
        """Midday status check-in structure."""
        return (
            "Produce a short midday status update (60-90 seconds spoken). "
            "This is an INTERACTIVE check-in, NOT a one-way briefing.\n\n"
            "1. GREETING — Brief afternoon greeting with the honorific.\n\n"
            "2. NEW SINCE MORNING — Only items that arrived SINCE the morning "
            "briefing. New messages, new emails, schedule changes. Do NOT "
            "repeat items from the morning.\n\n"
            "3. AFTERNOON SCHEDULE — Remaining events for today. Time context "
            "relative to now ('You have a call in 2 hours').\n\n"
            "4. QUICK FLAGS — Anything time-sensitive that needs attention "
            "before end of day. Deadlines approaching, unanswered urgent "
            "messages.\n\n"
            "5. GAP CHECK — End with 2-3 short, specific questions to surface "
            "things the connectors may have missed. These should probe for:\n"
            "  - Tasks from meetings or conversations that aren't in any "
            "tool yet ('Did anything come out of your 10 AM call?')\n"
            "  - Ad-hoc commitments or promises made verbally\n"
            "  - Priorities that shifted since the morning\n"
            "  - Anything blocking progress that hasn't surfaced in messages\n"
            "Make the questions SPECIFIC to what's on today's schedule and "
            "what's been happening — not generic. Reference actual calendar "
            "events, contacts, or projects from the data.\n\n"
            "FORMAT: After the spoken briefing text, output the questions on "
            "separate lines prefixed with 'Q: ' — these will be extracted "
            "and shown as interactive prompts.\n\n"
            "- STRICT LIMIT: 150 words (including questions). Keep the status "
            "update tight so the questions have room."
        )

    def _evening_prompt(self) -> str:
        """Evening circle-back and carry-forward structure."""
        return (
            "Produce an evening circle-back briefing (90-120 seconds spoken). "
            "Focus on closure and carry-forward.\n\n"
            "1. GREETING — Evening greeting with the honorific.\n\n"
            "2. DAY IN REVIEW — Brief summary of what happened today: "
            "meetings attended, messages handled, tasks completed. Keep it "
            "factual and concise.\n\n"
            "3. STILL OPEN — Items that did NOT get completed or responded to "
            "today. Unanswered important emails, missed tasks, pending items. "
            "Be specific about what's outstanding.\n\n"
            "4. CARRY-FORWARD — What needs to go on tomorrow's plate. Frame "
            "it as 'Tomorrow you'll want to...' Connect related items.\n\n"
            "5. TOMORROW PREVIEW — If there are calendar events for tomorrow, "
            "mention the first one or two so the user knows what they're "
            "waking up to.\n\n"
            "6. CLOSING — Wind-down tone. One sentence with the honorific.\n\n"
            "- STRICT LIMIT: 150 words. Concise wrap-up, not a full briefing."
        )

    def _resolve_sources(self) -> List[str]:
        """Get the list of connector IDs to query."""
        default_source_map = {
            "messages": [
                "gmail",
                "slack",
                "google_tasks",
                "imessage",
                "outlook",
                "github_notifications",
            ],
            "calendar": ["gcalendar", "apple_calendar"],
            "health": ["oura", "apple_health"],
            "world": ["weather", "hackernews", "news_rss"],
            "music": ["spotify", "apple_music"],
            "social": ["linkedin", "instagram", "facebook"],
        }
        sources = set()
        for section in self._sections:
            section_sources = self._section_sources.get(
                section, default_source_map.get(section, [])
            )
            sources.update(section_sources)
        return list(sources)

    def _hours_back_for_type(self) -> float:
        """Return lookback window in hours based on digest type."""
        if self._digest_type == "midday":
            return 8  # Since morning (~6 AM to ~12 PM with buffer)
        if self._digest_type == "evening":
            return 14  # Full day context for carry-forward
        return 24  # Morning: full 24h

    def _user_message_for_type(self, collected_data: str) -> str:
        """Build the user prompt based on digest type."""
        base_rules = (
            "- For health: say 'solid', 'improving', 'dipped' "
            "— NEVER say any number (no 82, no 56, no 6000)\n"
            "- Do NOT invent reasons for health changes\n"
            "- Do NOT mention disconnected sources\n"
            "- Do NOT repeat the greeting in your closing\n"
            "- Use the honorific ONLY 2-3 times total\n"
            "- Skip notifications from the user themselves"
        )

        if self._digest_type == "midday":
            return (
                f"Here is the collected data from my sources:\n\n"
                f"{collected_data}\n\n"
                f"Synthesize my midday check-in. Remember:\n"
                f"- Focus on NEW items since this morning\n"
                f"- Highlight anything time-sensitive for this afternoon\n"
                f"- Keep the status update part brief\n"
                f"- End with 2-3 SPECIFIC questions prefixed with 'Q: ' to "
                f"catch things that didn't come through connectors\n"
                f"- Make questions reference actual events, contacts, or "
                f"projects from the data — not generic\n"
                f"{base_rules}\n"
                f"- STRICT LIMIT: 150 words maximum (including questions)"
            )

        if self._digest_type == "evening":
            return (
                f"Here is the collected data from my sources:\n\n"
                f"{collected_data}\n\n"
                f"Synthesize my evening circle-back. Remember:\n"
                f"- Focus on what's STILL OPEN — unanswered emails, incomplete tasks\n"
                f"- Identify what carries forward to tomorrow\n"
                f"- If there are tomorrow's calendar events, preview them\n"
                f"- Wind-down tone — the day is ending\n"
                f"{base_rules}\n"
                f"- STRICT LIMIT: 150 words maximum"
            )

        # Morning (default)
        return (
            f"Here is the collected data from my sources:\n\n"
            f"{collected_data}\n\n"
            f"Synthesize my morning briefing. Remember:\n"
            f"- Priority-first, connect related items\n"
            f"{base_rules}\n"
            f"- STRICT LIMIT: 200-250 words maximum"
        )

    @staticmethod
    def _extract_questions(text: str) -> tuple:
        """Extract 'Q: ...' lines from narrative, return (clean_text, questions)."""
        import re

        lines = text.split("\n")
        questions: List[str] = []
        body_lines: List[str] = []

        for line in lines:
            stripped = line.strip()
            # Match "Q: ...", "Q1: ...", "Q. ...", etc.
            match = re.match(r"^Q\d*[:.]\s*(.+)", stripped)
            if match:
                questions.append(match.group(1).strip())
            else:
                body_lines.append(line)

        clean_text = "\n".join(body_lines).strip()
        return clean_text, questions

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        # Allow runtime override of digest_type via kwargs or input
        digest_type = kwargs.pop("digest_type", None)
        if digest_type and digest_type in self.DIGEST_TYPES:
            self._digest_type = digest_type
        elif "midday" in input.lower():
            self._digest_type = "midday"
        elif "evening" in input.lower() or "circle" in input.lower():
            self._digest_type = "evening"

        self._emit_turn_start(input)

        # Step 1: Collect data from connectors
        sources = self._resolve_sources()
        hours_back = self._hours_back_for_type()

        # Evening digest also fetches unacted items and tomorrow's calendar
        collect_args: dict = {"sources": sources, "hours_back": hours_back}
        if self._digest_type == "evening":
            collect_args["unacted_only"] = True
        if self._digest_type == "midday":
            collect_args["unacted_only"] = True

        collect_call = ToolCall(
            id="digest-collect-1",
            name="digest_collect",
            arguments=json.dumps(collect_args),
        )
        collect_result = self._executor.execute(collect_call)
        collected_data = collect_result.content

        # Step 2: Synthesize narrative via LLM
        system_prompt = self._build_system_prompt()
        user_content = self._user_message_for_type(collected_data)
        messages = [
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=user_content),
        ]

        result = self._generate(messages)
        narrative = self._strip_think_tags(result.get("content", ""))

        # Step 2a: Extract follow-up questions from midday narratives
        follow_up_questions: List[str] = []
        if self._digest_type == "midday":
            narrative, follow_up_questions = self._extract_questions(narrative)

        # Step 2b: Self-evaluate and optionally regenerate
        quality_score = 0.0
        evaluator_feedback = ""
        try:
            from openjarvis.agents.digest_evaluator import DigestEvaluator

            evaluator = DigestEvaluator(self._engine, self._model)
            quality_score, evaluator_feedback = evaluator.evaluate(
                collected_data, narrative
            )

            if quality_score < 7.0 and evaluator_feedback:
                # Regenerate with feedback
                messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            f"Your briefing scored {quality_score:.1f}/10. "
                            f"Feedback: {evaluator_feedback}\n"
                            f"Please revise the briefing addressing this feedback."
                        ),
                    )
                )
                result = self._generate(messages)
                narrative = self._strip_think_tags(result.get("content", ""))
                if self._digest_type == "midday":
                    narrative, follow_up_questions = self._extract_questions(
                        narrative
                    )
        except Exception:  # noqa: BLE001
            pass  # Evaluator failure shouldn't block digest delivery

        # Step 3: Generate audio via TTS
        # Strip any markdown that slipped through (##, *, -, etc.)
        import re

        tts_text = re.sub(r"^#{1,6}\s+", "", narrative, flags=re.MULTILINE)
        tts_text = re.sub(r"^\s*[-*•]\s+", "", tts_text, flags=re.MULTILINE)
        tts_text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", tts_text)
        tts_text = tts_text.strip()

        output_dir = str(Path.home() / ".openjarvis" / "digests")
        tts_call = ToolCall(
            id="digest-tts-1",
            name="text_to_speech",
            arguments=json.dumps(
                {
                    "text": tts_text,
                    "voice_id": self._voice_id,
                    "backend": self._tts_backend,
                    "speed": self._voice_speed,
                    "output_dir": output_dir,
                }
            ),
        )
        tts_result = self._executor.execute(tts_call)
        audio_path = (
            tts_result.metadata.get("audio_path", "") if tts_result.success else ""
        )

        # Step 4: Store the artifact
        artifact = DigestArtifact(
            text=narrative,
            audio_path=Path(audio_path) if audio_path else Path(""),
            sections={},
            sources_used=sources,
            generated_at=datetime.now(),
            model_used=self._model,
            voice_used=self._voice_id,
            quality_score=quality_score,
            evaluator_feedback=evaluator_feedback,
            digest_type=self._digest_type,
            follow_up_questions=follow_up_questions,
        )

        store = DigestStore(db_path=self._digest_store_path)
        store.save(artifact)
        store.close()

        self._emit_turn_end(turns=1)
        return AgentResult(
            content=narrative,
            tool_results=[collect_result, tts_result],
            turns=1,
            metadata={
                "audio_path": audio_path,
                "sources_used": sources,
                "follow_up_questions": follow_up_questions,
            },
        )
