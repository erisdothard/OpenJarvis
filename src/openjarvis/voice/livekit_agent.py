"""LiveKit voice agent for OpenJarvis.

Run in development mode:
    python -m openjarvis.voice.livekit_agent dev

Run in production:
    python -m openjarvis.voice.livekit_agent start

Requires env vars: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
DEEPGRAM_API_KEY, GOOGLE_API_KEY (or OPENAI_API_KEY), CARTESIA_API_KEY.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from datetime import datetime
from typing import Annotated

from dotenv import load_dotenv

# Load .env from project root before anything touches env vars
_env_path = pathlib.Path(__file__).resolve().parents[3] / ".env"
load_dotenv(_env_path)

# Ensure openjarvis package is importable
_project_src = str(pathlib.Path(__file__).resolve().parents[2])
if _project_src not in sys.path:
    sys.path.insert(0, _project_src)

from livekit.agents import AgentSession, Agent, JobContext, WorkerOptions, cli, llm  # noqa: E402
from livekit.plugins import deepgram, cartesia, silero, turn_detector  # noqa: E402

logger = logging.getLogger("openjarvis.voice")

# ---------------------------------------------------------------------------
# Persona prompt — adapted from configs/openjarvis/prompts/personas/eris.md
# Stripped of markdown and tuned for spoken conversation.
# ---------------------------------------------------------------------------
JARVIS_SYSTEM_PROMPT = """\
You are Jarvis, Eris's personal AI assistant. You are sharp, direct, and \
genuinely useful. You have the loyalty and anticipation of the original \
Jarvis, but adapted for Eris's style: no unnecessary formality, no fluff, \
just clear thinking delivered with dry wit when appropriate.

You are calm under pressure and never flustered. You anticipate needs before \
being asked. You are a strategic partner, not a secretary. When delivering \
bad news, pair it with a path forward.

Use "Eris" naturally — not every sentence, just where it adds warmth or \
emphasis. Two to three times per conversation max.

You have access to tools that let you check calendars, search emails, \
search the web, and manage knowledge. Use them proactively when relevant.

SPEAKING RULES:
- Keep responses concise and natural for spoken conversation.
- Never use markdown, asterisks, bullet points, headers, or emojis.
- Never say "Understood", "Absolutely", "Certainly", "Of course", \
"I'd be happy to", "Great question", or "Let me".
- Don't narrate what you're doing. Just do it and give the result.
- Speak in complete sentences, not fragments or lists.
- Be specific and concrete, not vague and generic.
"""

# ---------------------------------------------------------------------------
# Banned words filter — strip bot-sounding filler from LLM output
# ---------------------------------------------------------------------------
BANNED_PREFIXES = (
    "Understood",
    "Absolutely",
    "Certainly",
    "Of course",
    "I'd be happy to",
    "Great question",
    "Let me ",
)


# ---------------------------------------------------------------------------
# Voice agent tools — expose key OpenJarvis capabilities to voice
# ---------------------------------------------------------------------------


@llm.function_tool(description="Get today's calendar events from Google Calendar and Apple Calendar")
async def get_todays_calendar() -> str:
    """Fetch today's events from connected calendars."""
    from openjarvis.core.registry import ConnectorRegistry

    results = []

    # Google Calendar
    gcal_cls = ConnectorRegistry.get("gcalendar")
    if gcal_cls:
        try:
            connector = gcal_cls()
            if connector.is_connected():
                today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                for d in connector.sync(since=today):
                    if d.timestamp and d.timestamp.date() == today.date():
                        results.append(f"{d.title} at {d.timestamp.strftime('%I:%M %p')}")
        except Exception as e:
            logger.debug("GCal voice tool error: %s", e)

    # Apple Calendar
    acal_cls = ConnectorRegistry.get("apple_calendar")
    if acal_cls:
        try:
            from openjarvis.connectors.apple_calendar import _applescript_get_events
            from datetime import timedelta

            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            events = _applescript_get_events(today, today + timedelta(days=1))
            for e in events:
                results.append(f"{e.get('summary', 'Event')} at {e.get('start', '?')}")
        except Exception as e:
            logger.debug("Apple Calendar voice tool error: %s", e)

    if not results:
        return "No events scheduled for today."
    return "Today's events: " + ". ".join(results)


@llm.function_tool(description="Search emails in Gmail by query")
async def search_emails(
    query: Annotated[str, "Gmail search query like 'from:alice subject:report'"],
) -> str:
    """Search Gmail for matching emails."""
    from openjarvis.core.registry import ConnectorRegistry

    gmail_cls = ConnectorRegistry.get("gmail")
    if not gmail_cls:
        return "Gmail is not connected."
    connector = gmail_cls()
    if not connector.is_connected():
        return "Gmail is not connected."

    try:
        from datetime import timedelta

        docs = []
        for d in connector.sync(since=datetime.now() - timedelta(days=30), query_extra=query):
            docs.append(d)
            if len(docs) >= 5:
                break
        if not docs:
            return f"No emails found matching '{query}'."
        summaries = []
        for d in docs:
            subj = d.metadata.get("subject", d.title or "No subject")
            frm = d.metadata.get("from", "Unknown")
            summaries.append(f"From {frm}: {subj}")
        return " | ".join(summaries)
    except Exception as e:
        return f"Email search failed: {e}"


@llm.function_tool(description="Search the web for current information")
async def search_web(
    query: Annotated[str, "What to search for on the web"],
) -> str:
    """Search the web using DuckDuckGo or Tavily."""
    try:
        from openjarvis.tools.web_search import WebSearchTool

        ws = WebSearchTool()
        result = ws.execute(query=query, max_results=3)
        return result.content[:1000] if result.success else f"Search failed: {result.content}"
    except Exception as e:
        return f"Web search failed: {e}"


@llm.function_tool(description="Search your personal knowledge base including emails, notes, and documents")
async def search_knowledge(
    query: Annotated[str, "What to search for in the knowledge base"],
) -> str:
    """Search the OpenJarvis knowledge store."""
    try:
        from openjarvis.connectors.store import KnowledgeStore

        ks = KnowledgeStore()
        results = ks.retrieve(query, top_k=3)
        if not results:
            return f"No knowledge found for '{query}'."
        summaries = []
        for r in results:
            source = r.source or "unknown"
            content = r.content[:200]
            summaries.append(f"[{source}] {content}")
        return " | ".join(summaries)
    except Exception as e:
        return f"Knowledge search failed: {e}"


JARVIS_TOOLS = llm.Toolset(
    id="jarvis_voice",
    tools=[get_todays_calendar, search_emails, search_web, search_knowledge],
)


class JarvisAgent(Agent):
    """Voice persona for the OpenJarvis assistant."""

    def __init__(self) -> None:
        super().__init__(
            instructions=JARVIS_SYSTEM_PROMPT,
            tools=[JARVIS_TOOLS],
        )


def _build_llm():
    """Select the best available LLM plugin based on configured keys."""
    from livekit.plugins import openai as lk_openai

    # Prefer Gemini (free tier, fast)
    google_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if google_key:
        logger.info("LLM: Gemini 2.5 Flash via OpenAI-compat")
        return lk_openai.LLM(
            model="gemini-2.5-flash",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=google_key,
        )

    # Groq (fast inference, free tier)
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        logger.info("LLM: Groq llama-3.3-70b")
        return lk_openai.LLM.with_groq(model="llama-3.3-70b-versatile")

    # OpenAI fallback
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        logger.info("LLM: OpenAI gpt-4o-mini")
        return lk_openai.LLM(model="gpt-4o-mini")

    raise RuntimeError(
        "No LLM API key found. Set GOOGLE_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY."
    )


async def entrypoint(ctx: JobContext) -> None:
    """Called by LiveKit when a user joins the room and the agent is dispatched."""
    await ctx.connect()

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=_build_llm(),
        tts=cartesia.TTS(
            model="sonic-2",
            voice="a0e99841-438c-4a64-b679-ae501e7d6091",  # British Butler
        ),
        vad=silero.VAD.load(),
        turn_detection=turn_detector.EOUPlugin(runner=turn_detector._EUORunnerEn),
    )

    await session.start(
        room=ctx.room,
        agent=JarvisAgent(),
    )

    logger.info("Jarvis voice agent started in room %s", ctx.room.name)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="openjarvis",
        ),
    )
