"""LiveKit token endpoint for the OpenJarvis API server.

The frontend calls GET /v1/livekit/token to obtain a JWT that lets it join
a LiveKit room. The endpoint also explicitly dispatches the ``openjarvis``
agent worker to the room so the voice pipeline starts automatically.
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/livekit", tags=["livekit"])


def _check_livekit_env() -> tuple[str, str, str]:
    """Return (url, api_key, api_secret) or raise 503."""
    url = os.environ.get("LIVEKIT_URL", "")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if not all([url, api_key, api_secret]):
        raise HTTPException(
            status_code=503,
            detail="LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET.",
        )
    return url, api_key, api_secret


@router.get("/token")
async def get_token(
    room: str = Query(default="jarvis", description="Room name to join"),
    identity: str = Query(default="", description="Participant identity"),
) -> dict:
    """Mint a LiveKit access token and dispatch the voice agent to the room."""
    try:
        from livekit.api import AccessToken, VideoGrants
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="livekit-api package is not installed.",
        )

    url, api_key, api_secret = _check_livekit_env()

    # Default identity: unique per session so multiple tabs work
    if not identity:
        identity = f"user-{uuid.uuid4().hex[:8]}"

    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_name("Eris")
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_publish_data=True,
                can_subscribe=True,
            )
        )
    )

    jwt_token = token.to_jwt()

    return {
        "token": jwt_token,
        "url": url,
        "room": room,
        "identity": identity,
    }


@router.get("/dispatch")
async def dispatch_agent(
    room: str = Query(default="jarvis", description="Room to dispatch agent to"),
) -> dict:
    """Dispatch the voice agent to a room. Call after the client has connected."""
    try:
        from livekit.api import CreateAgentDispatchRequest, LiveKitAPI
    except ImportError:
        raise HTTPException(status_code=501, detail="livekit-api not installed.")

    url, api_key, api_secret = _check_livekit_env()

    try:
        lk = LiveKitAPI(url=url, api_key=api_key, api_secret=api_secret)
        await lk.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(agent_name="openjarvis", room=room)
        )
        await lk.aclose()
        logger.info("Dispatched agent to room %s", room)
        return {"dispatched": True, "room": room}
    except Exception as exc:
        logger.warning("Agent dispatch failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/health")
async def livekit_health() -> dict:
    """Check whether LiveKit credentials are configured."""
    try:
        _check_livekit_env()
        return {"available": True}
    except HTTPException:
        return {"available": False, "reason": "LiveKit env vars not set"}
