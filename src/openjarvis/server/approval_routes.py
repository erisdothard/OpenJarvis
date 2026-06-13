"""REST endpoints for the proactive-agent approval queue."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_DENIED,
    ApprovalStore,
    PendingAction,
)

try:
    from fastapi import APIRouter, HTTPException
except ImportError:
    raise ImportError("fastapi is required for approval routes")

logger = logging.getLogger(__name__)

router = APIRouter()

# Singleton that shares the same DB file as ProactiveAgent (WAL mode is safe)
_store: Optional[ApprovalStore] = None


def _get_store() -> ApprovalStore:
    global _store
    if _store is None:
        _store = ApprovalStore()
    return _store


def _serialize(action: PendingAction) -> Dict[str, Any]:
    return {
        "id": action.id,
        "action_type": action.action_type,
        "description": action.description,
        "payload": action.payload,
        "permission_key": action.permission_key,
        "tier": action.tier,
        "status": action.status,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
    }


@router.get("/v1/approvals/pending")
async def list_pending_approvals() -> Dict[str, Any]:
    store = _get_store()
    store.expire_stale()
    actions = store.list_pending()
    return {"actions": [_serialize(a) for a in actions], "count": len(actions)}


@router.post("/v1/approvals/{action_id}/approve")
async def approve_action(action_id: str) -> Dict[str, Any]:
    store = _get_store()
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    store.update_status(action_id, STATUS_APPROVED)
    logger.info("Action %s approved via UI", action_id)
    return {"status": "approved", "id": action_id}


@router.post("/v1/approvals/{action_id}/deny")
async def deny_action(action_id: str) -> Dict[str, Any]:
    store = _get_store()
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")
    store.update_status(action_id, STATUS_DENIED)
    logger.info("Action %s denied via UI", action_id)
    return {"status": "denied", "id": action_id}


# ── Quick-approve page (clickable from iMessage) ────────────────────────

try:
    from fastapi.responses import HTMLResponse
except ImportError:
    HTMLResponse = None  # type: ignore[assignment,misc]

_APPROVAL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Jarvis Approval</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 480px; margin: 40px auto;
         padding: 20px; background: #0a0a0a; color: #e0e0e0; }}
  .preview {{ background: #1a1a1a; border-radius: 12px; padding: 20px; margin: 20px 0;
              white-space: pre-wrap; line-height: 1.5; font-size: 14px; }}
  .buttons {{ display: flex; gap: 12px; margin-top: 24px; }}
  button {{ flex: 1; padding: 16px; border: none; border-radius: 10px; font-size: 16px;
           font-weight: 600; cursor: pointer; }}
  .approve {{ background: #22c55e; color: #000; }}
  .deny {{ background: #ef4444; color: #fff; }}
  .done {{ text-align: center; padding: 40px 0; font-size: 18px; }}
</style></head>
<body>
  <h2>JARVIS — Post Approval</h2>
  <p><strong>Platforms:</strong> {platforms}</p>
  <div class="preview">{preview}</div>
  <div class="buttons">
    <form method="post" action="/v1/approvals/{action_id}/approve" style="flex:1">
      <button type="submit" class="approve" style="width:100%">Approve</button>
    </form>
    <form method="post" action="/v1/approvals/{action_id}/deny" style="flex:1">
      <button type="submit" class="deny" style="width:100%">Deny</button>
    </form>
  </div>
</body></html>"""


@router.get("/v1/approvals/{action_id}")
async def view_approval(action_id: str):  # type: ignore[return]
    """Render an HTML page with Approve/Deny buttons — clickable from iMessage."""
    store = _get_store()
    action = store.get_action(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found")

    if action.status != "pending":
        html = (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width'>"
            f"<style>body{{font-family:-apple-system,sans-serif;max-width:480px;"
            f"margin:40px auto;padding:20px;background:#0a0a0a;color:#e0e0e0}}</style>"
            f"</head><body><div class='done'>"
            f"<h2>Already {action.status}</h2>"
            f"<p>This action was already {action.status}.</p>"
            f"</div></body></html>"
        )
        if HTMLResponse:
            return HTMLResponse(content=html)
        return {"status": action.status, "id": action_id}

    # Extract platform info from payload
    platforms = ", ".join(
        p.title() for p in action.payload.get("platforms", [])
    ) or action.action_type
    preview = action.payload.get("content", action.description)[:600]

    html = _APPROVAL_HTML.format(
        platforms=platforms,
        preview=preview,
        action_id=action_id,
    )
    if HTMLResponse:
        return HTMLResponse(content=html)
    return _serialize(action)


__all__ = ["router"]
