"""Quick-capture API — lightweight inbox for phone/shortcut integration.

Provides a single endpoint that accepts text, voice memos, URLs, or images
and routes them into the appropriate Jarvis subsystem (memory, tasks, agent).

Designed for iOS Shortcuts, Android Tasker, or any HTTP client.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/capture", tags=["capture"])


class CaptureRequest(BaseModel):
    """Inbound capture payload."""

    content: str
    kind: str = "note"  # note | task | ask | url | voice_memo
    tags: List[str] = []
    metadata: Dict[str, Any] = {}
    # If kind == "ask", Jarvis processes it and returns a response.
    # Otherwise, it stores it and returns confirmation.


class CaptureResponse(BaseModel):
    """Response from capture endpoint."""

    status: str  # "stored" | "processing" | "done"
    id: str = ""
    response: str = ""
    tags: List[str] = []


@router.post("", response_model=CaptureResponse)
async def capture(req: CaptureRequest, request: Request):
    """Quick-capture endpoint — the phone inbox for Jarvis.

    Accepts notes, tasks, questions, URLs, or voice memo transcripts.
    Routes to the appropriate subsystem based on `kind`.

    Examples:
        POST /v1/capture {"content": "Remember to follow up with 3 Aces", "kind": "task"}
        POST /v1/capture {"content": "What's on my calendar tomorrow?", "kind": "ask"}
        POST /v1/capture {"content": "https://arxiv.org/...", "kind": "url", "tags": ["research"]}
    """
    now = datetime.now()
    capture_id = f"capture-{now.strftime('%Y%m%d-%H%M%S')}-{id(req) % 10000:04d}"

    # Route based on kind
    if req.kind == "ask":
        return await _handle_ask(req, request, capture_id)
    else:
        return await _handle_store(req, request, capture_id, now)


async def _handle_store(
    req: CaptureRequest,
    request: Request,
    capture_id: str,
    now: datetime,
) -> CaptureResponse:
    """Store content in memory for later processing."""
    backend = getattr(request.app.state, "memory_backend", None)

    enriched_content = (
        f"[{req.kind.upper()}] [{now.strftime('%Y-%m-%d %H:%M')}] "
        f"{req.content}"
    )

    metadata = {
        "capture_id": capture_id,
        "kind": req.kind,
        "tags": req.tags,
        "captured_at": now.isoformat(),
        **req.metadata,
    }

    if backend is not None:
        try:
            backend.store(enriched_content, metadata=metadata)
        except Exception as exc:
            logger.warning("Memory store failed for capture %s: %s", capture_id, exc)
            raise HTTPException(status_code=500, detail="Failed to store capture") from exc
    else:
        logger.warning("No memory backend — capture %s stored in traces only", capture_id)

    # Also record in traces if available
    trace_store = getattr(request.app.state, "trace_store", None)
    if trace_store is not None:
        try:
            from openjarvis.traces._stubs import Trace, TraceStep

            trace = Trace(
                trace_id=capture_id,
                agent="capture",
                model="",
                steps=[
                    TraceStep(
                        role="user",
                        content=req.content,
                        metadata=metadata,
                    )
                ],
            )
            trace_store.save(trace)
        except Exception:
            pass  # Trace failure shouldn't block capture

    kind_labels = {
        "note": "noted",
        "task": "task saved",
        "url": "URL saved for review",
        "voice_memo": "voice memo stored",
    }
    label = kind_labels.get(req.kind, "captured")

    return CaptureResponse(
        status="stored",
        id=capture_id,
        response=f"Got it — {label}.",
        tags=req.tags,
    )


async def _handle_ask(
    req: CaptureRequest,
    request: Request,
    capture_id: str,
) -> CaptureResponse:
    """Route a question to the active agent and return the response."""
    agent = getattr(request.app.state, "agent", None)
    engine = getattr(request.app.state, "engine", None)

    if agent is None and engine is None:
        raise HTTPException(
            status_code=503,
            detail="No agent or engine available to process questions",
        )

    try:
        if agent is not None:
            result = await asyncio.to_thread(agent.run, req.content)
            response_text = result.content if hasattr(result, "content") else str(result)
        else:
            from openjarvis.core.types import Message, Role

            messages = [
                Message(role=Role.USER, content=req.content),
            ]
            result = await asyncio.to_thread(engine.generate, messages)
            response_text = result.get("content", "") if isinstance(result, dict) else str(result)

        return CaptureResponse(
            status="done",
            id=capture_id,
            response=response_text,
            tags=req.tags,
        )
    except Exception as exc:
        logger.error("Capture ask failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/file")
async def capture_file(request: Request):
    """Capture a file via multipart upload and store it.

    For voice memos (audio/*), transcribes and stores the text.
    For other files, saves to ~/.openjarvis/captures/.
    """
    import hashlib
    from pathlib import Path

    form = await request.form()
    file = form.get("file")
    kind = form.get("kind", "note")
    tags_raw = form.get("tags", "")

    if file is None:
        raise HTTPException(status_code=400, detail="No file provided")

    now = datetime.now()
    capture_id = f"capture-file-{now.strftime('%Y%m%d-%H%M%S')}"
    tag_list = [t.strip() for t in str(tags_raw).split(",") if t.strip()]

    file_bytes = await file.read()
    content_type = getattr(file, "content_type", "") or ""
    filename = getattr(file, "filename", "file") or "file"

    # Voice memo transcription
    if content_type.startswith("audio/"):
        try:
            from openjarvis.speech.stt import transcribe_bytes

            transcript = await asyncio.to_thread(transcribe_bytes, file_bytes)
            backend = getattr(request.app.state, "memory_backend", None)
            if backend:
                backend.store(
                    f"[VOICE_MEMO] [{now.strftime('%Y-%m-%d %H:%M')}] {transcript}",
                    metadata={
                        "capture_id": capture_id,
                        "kind": "voice_memo",
                        "tags": tag_list,
                        "original_filename": filename,
                    },
                )
            return {"status": "stored", "id": capture_id, "transcript": transcript, "tags": tag_list}
        except ImportError:
            logger.warning("Speech-to-text not available — storing raw file")

    # Generic file storage
    file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
    store_dir = Path.home() / ".openjarvis" / "captures"
    store_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(filename).suffix
    dest = store_dir / f"{capture_id}-{file_hash}{ext}"
    dest.write_bytes(file_bytes)

    backend = getattr(request.app.state, "memory_backend", None)
    if backend:
        backend.store(
            f"[FILE] [{now.strftime('%Y-%m-%d %H:%M')}] {filename} saved to {dest}",
            metadata={
                "capture_id": capture_id,
                "kind": str(kind),
                "tags": tag_list,
                "file_path": str(dest),
                "content_type": content_type,
                "size_bytes": len(file_bytes),
            },
        )

    return {"status": "stored", "id": capture_id, "file_path": str(dest), "size_bytes": len(file_bytes), "tags": tag_list}
