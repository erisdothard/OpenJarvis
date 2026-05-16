"""HTTP route: ``POST /api/research`` — agentic research over the knowledge store.

Drives :class:`openjarvis.agents.research_loop.ResearchAgent` and streams a
custom SSE event schema back to the client:

* ``search_call``     — about to invoke ``HybridSearch.search`` (with arguments)
* ``search_result``   — search returned (num_hits, top_titles)
* ``synthesis``       — final answer, emitted in word-window chunks for an
  incremental UX (the agent itself returns the full string in one shot;
  chunking happens in the router so we don't need to rewire the loop)
* ``done``            — sentinel marking the end of the stream

Clarify is **disabled for the web session** — the agent's clarify_handler is
overridden to return a fixed "no clarification available" string so the
loop never blocks waiting for terminal stdin. Bringing real clarify back
to the browser will require a two-step session protocol; that's a future
endpoint, not this one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from openjarvis.agents.research_loop import (
    DEFAULT_PLANNER_MODEL,
    ResearchAgent,
)
from openjarvis.connectors.embeddings import OllamaEmbedder
from openjarvis.connectors.hybrid_search import HybridSearch
from openjarvis.connectors.store import KnowledgeStore
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.types import TelemetryRecord
from openjarvis.engine.ollama import OllamaEngine
from openjarvis.telemetry.store import TelemetryStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["research"])

_WEB_CLARIFY_RESPONSE = "no clarification available in web session"

# Sentinel placed on the queue when the agent thread terminates.
_DONE = object()


def _record_research_telemetry(
    *,
    model: str,
    usage: Dict[str, int],
    latency_seconds: float,
) -> None:
    """Persist a research run into the telemetry DB so /v1/savings includes it.

    Failures are swallowed — telemetry persistence is best-effort and must
    never break the user-visible SSE stream.
    """
    if not usage:
        return
    db_path = DEFAULT_CONFIG_DIR / "telemetry.db"
    try:
        store = TelemetryStore(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("research telemetry: cannot open %s: %s", db_path, exc)
        return
    try:
        rec = TelemetryRecord(
            timestamp=time.time(),
            model_id=model,
            engine="ollama",
            agent="research",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            prompt_tokens_evaluated=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            latency_seconds=latency_seconds,
            is_streaming=True,
        )
        store.record(rec)
    except Exception as exc:  # noqa: BLE001
        logger.debug("research telemetry: failed to record: %s", exc)
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class ResearchRequest(BaseModel):
    query: str = Field(..., description="Natural-language question to research.")
    # Deep Research has its own model requirements (function-calling support,
    # sufficient reasoning capability) that the chat-model selector should not
    # override. We accept the field for forward-compat with older clients but
    # ignore it — the planner always runs on DEFAULT_PLANNER_MODEL.
    model: Optional[str] = Field(default=None, description="Ignored; retained for client compatibility.")


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse(event: Dict[str, Any]) -> str:
    """Serialize one event dict to an SSE ``data: ...\\n\\n`` frame."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _chunk_synthesis(text: str, window_chars: int = 40) -> list[str]:
    """Slice synthesis text into client-streaming-friendly chunks.

    We break on word boundaries so partial deltas always render cleanly in
    the browser. Each chunk is roughly ``window_chars`` characters long.
    """
    if not text:
        return []
    tokens = re.findall(r"\S+\s*", text)
    chunks: list[str] = []
    buf = ""
    for tok in tokens:
        if len(buf) + len(tok) > window_chars and buf:
            chunks.append(buf)
            buf = tok
        else:
            buf += tok
    if buf:
        chunks.append(buf)
    return chunks


# ---------------------------------------------------------------------------
# Stream generator
# ---------------------------------------------------------------------------


async def _stream_research(query: str, model: str) -> AsyncGenerator[str, None]:
    """Drive ResearchAgent on a worker thread; yield SSE frames as they land.

    Three error envelopes — setup, worker, consumer — all funnel into the
    same two-frame contract: ``{"type": "error", ...}`` followed by
    ``{"type": "done", "usage": {...}}``. The client can rely on always
    seeing a ``done`` frame, even when the agent never started.
    """
    # Phase 1: setup. Failures here (Ollama daemon down, DB locked, etc.)
    # yield error + done and return — nothing has been emitted yet so the
    # client gets a clean two-frame stream instead of a dangling connection.
    try:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event: Dict[str, Any]) -> None:
            # Called from the agent's worker thread; bounce onto the event loop.
            loop.call_soon_threadsafe(queue.put_nowait, event)

        # Each request gets its own thin set of connectors. Constructing them
        # is cheap (SQLite open + HTTP keepalive) and avoids state leaks
        # between concurrent requests.
        store = KnowledgeStore()
        embedder = OllamaEmbedder()
        if not embedder.is_available():
            logger.warning("research: Ollama embedder unavailable; BM25-only retrieval.")
            embedder = None

        engine = OllamaEngine()
        agent = ResearchAgent(
            engine=engine,
            search=HybridSearch(store, embedder),
            model=model,
            clarify_handler=lambda question: _WEB_CLARIFY_RESPONSE,
            on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("research: setup failed before agent could run: %s", exc)
        yield _sse(
            {
                "type": "error",
                "message": f"Research failed: {type(exc).__name__}: {exc}",
            }
        )
        yield _sse({"type": "done", "usage": {}})
        return

    def _run() -> None:
        t0 = time.time()
        try:
            result = agent.run(query)
            usage_dict = dict(result.usage)
            # Persist the run's token usage to telemetry.db so /v1/savings
            # rolls research into the same cost-comparison ledger as chat.
            _record_research_telemetry(
                model=model,
                usage=usage_dict,
                latency_seconds=time.time() - t0,
            )
            # Forward the aggregated token usage so the consumer can attach it
            # to the terminal `done` frame. Internal event type — never sent to
            # the client directly.
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "_usage", "usage": usage_dict},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("research agent crashed: %s", exc)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"},
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    task = asyncio.create_task(asyncio.to_thread(_run))

    final_answer: Optional[str] = None
    final_usage: Dict[str, int] = {}
    try:
        while True:
            event = await queue.get()
            if event is _DONE:
                break
            if not isinstance(event, dict):
                continue

            etype = event.get("type")
            # Internal usage marker — capture for the done frame, don't forward.
            if etype == "_usage":
                final_usage = event.get("usage", {}) or {}
                continue
            # We translate the agent's `final_answer` event into a stream of
            # `synthesis` chunks so the client sees the answer materialize
            # incrementally rather than as a single blob.
            if etype == "final_answer":
                final_answer = event.get("text", "")
                for piece in _chunk_synthesis(final_answer or ""):
                    yield _sse({"type": "synthesis", "text": piece})
                continue

            yield _sse(event)

        # If the agent thread crashed before producing a final answer, the
        # client still gets the error frame (emitted above) followed by done.
        yield _sse({"type": "done", "usage": final_usage})
    except Exception as exc:  # noqa: BLE001
        # Consumer loop crashed unexpectedly (e.g. JSON serialization fault,
        # logic bug). Surface a clean error frame rather than letting the
        # SSE connection die mid-stream.
        logger.exception("research: stream consumer crashed: %s", exc)
        yield _sse(
            {
                "type": "error",
                "message": f"Research failed: {type(exc).__name__}: {exc}",
            }
        )
        yield _sse({"type": "done", "usage": final_usage})
    finally:
        # The worker may still be cleaning up (rarely) — make sure we don't
        # leak a dangling task. Swallow any straggler exception so a worker
        # failure during teardown doesn't escape the generator after we've
        # already emitted the terminal done frame.
        if not task.done():
            try:
                await task
            except Exception as exc:  # noqa: BLE001
                logger.debug("research: worker task ended with %s", exc)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/research")
async def research(req: ResearchRequest) -> StreamingResponse:
    """Run a research query and stream the agent's trace + synthesis via SSE.

    Response is ``text/event-stream`` with one JSON event per frame. See the
    module docstring for the schema; a final ``{"type": "done"}`` always
    terminates the stream so clients can detect end-of-response without
    parsing the underlying ``[DONE]`` sentinel used by OpenAI-style routes.
    """
    if req.model and req.model != DEFAULT_PLANNER_MODEL:
        logger.info(
            "research: ignoring client-supplied model=%r; using DEFAULT_PLANNER_MODEL=%r",
            req.model,
            DEFAULT_PLANNER_MODEL,
        )
    return StreamingResponse(
        _stream_research(req.query, DEFAULT_PLANNER_MODEL),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "ResearchRequest"]
