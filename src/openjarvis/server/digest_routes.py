"""FastAPI routes for the daily digest (morning, midday, evening)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from openjarvis.agents.digest_store import DigestStore
from openjarvis.cli.digest_cmd import (
    _cancel_scheduler_tasks,
    _create_all_scheduler_tasks,
    _create_scheduler_task,
    _save_digest_schedule,
)
from openjarvis.core.config import load_config

VALID_DIGEST_TYPES = ("morning", "midday", "evening")


def _current_digest_type() -> str:
    """Determine which digest type is most relevant based on time of day."""
    hour = datetime.now().hour
    if hour < 10:
        return "morning"
    if hour < 16:
        return "midday"
    return "evening"


class ScheduleUpdate(BaseModel):
    """Request body for updating the digest schedule."""

    enabled: bool
    cron: Optional[str] = None
    schedules: Optional[dict[str, str]] = None


def create_digest_router(*, db_path: str = "") -> APIRouter:
    """Create a digest API router with the given store path."""
    router = APIRouter(prefix="/api/digest", tags=["digest"])
    store = DigestStore(db_path=db_path) if db_path else DigestStore()
    cfg = load_config()
    _digest_tz = getattr(cfg.digest, "timezone", "America/Chicago")

    @router.get("")
    async def get_digest(
        type: Optional[str] = Query(None, description="Digest type: morning, midday, evening"),
    ):
        """Return the latest digest artifact, auto-selecting type by time of day."""
        digest_type = type if type in VALID_DIGEST_TYPES else ""

        # Try the requested/auto type first, fall back to any today's digest
        artifact = store.get_today(timezone_name=_digest_tz, digest_type=digest_type)
        if artifact is None and digest_type:
            artifact = store.get_today(timezone_name=_digest_tz)
        if artifact is None:
            raise HTTPException(status_code=404, detail="No digest for today")
        return {
            "text": artifact.text,
            "sections": artifact.sections,
            "sources_used": artifact.sources_used,
            "generated_at": artifact.generated_at.isoformat(),
            "model_used": artifact.model_used,
            "voice_used": artifact.voice_used,
            "digest_type": artifact.digest_type,
            "audio_available": (
                artifact.audio_path.exists() if artifact.audio_path.name else False
            ),
            "follow_up_questions": artifact.follow_up_questions,
        }

    @router.get("/audio")
    async def get_digest_audio(
        type: Optional[str] = Query(None, description="Digest type"),
    ):
        """Stream the digest audio file."""
        digest_type = type if type in VALID_DIGEST_TYPES else ""
        artifact = store.get_today(timezone_name=_digest_tz, digest_type=digest_type)
        if artifact is None:
            artifact = store.get_today(timezone_name=_digest_tz)
        if artifact is None:
            raise HTTPException(status_code=404, detail="No digest for today")
        if not artifact.audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio not available")
        return FileResponse(
            str(artifact.audio_path),
            media_type="audio/mpeg",
            filename=f"digest-{artifact.digest_type}.mp3",
        )

    @router.post("/generate")
    async def generate_digest(
        type: Optional[str] = Query(None, description="Digest type to generate"),
    ):
        """Force re-generation of a digest."""
        digest_type = type if type in VALID_DIGEST_TYPES else _current_digest_type()
        label = {"morning": "morning", "midday": "midday", "evening": "evening"}[
            digest_type
        ]
        try:
            from openjarvis.sdk import Jarvis

            with Jarvis() as j:
                result = j.ask(
                    f"Generate my {label} digest", agent="morning_digest"
                )
            return {"status": "ok", "digest_type": digest_type, "text": result}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/history")
    async def get_digest_history(
        type: Optional[str] = Query(None, description="Filter by digest type"),
    ):
        """Return past digests."""
        digest_type = type if type in VALID_DIGEST_TYPES else ""
        history = store.history(limit=10, digest_type=digest_type)
        return [
            {
                "text": a.text[:200],
                "generated_at": a.generated_at.isoformat(),
                "model_used": a.model_used,
                "voice_used": a.voice_used,
                "digest_type": a.digest_type,
            }
            for a in history
        ]

    @router.get("/schedule")
    async def get_schedule():
        """Return the current digest schedule configuration."""
        cfg = load_config()
        schedules = getattr(cfg.digest, "schedules", {})
        return {
            "enabled": cfg.digest.enabled,
            "cron": cfg.digest.schedule,
            "schedules": schedules if schedules else {"morning": cfg.digest.schedule},
        }

    @router.post("/schedule")
    async def update_schedule(body: ScheduleUpdate):
        """Update the digest schedule configuration."""
        cfg = load_config()
        cron = body.cron if body.cron is not None else cfg.digest.schedule

        try:
            _save_digest_schedule(enabled=body.enabled, cron=cron)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to save config: {exc}",
            )

        # Sync with the TaskScheduler
        if body.enabled:
            if body.schedules:
                _create_all_scheduler_tasks(body.schedules)
            else:
                _create_scheduler_task(cron)
        else:
            _cancel_scheduler_tasks()

        return {
            "enabled": body.enabled,
            "cron": cron,
            "schedules": body.schedules,
        }

    return router
