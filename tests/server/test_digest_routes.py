"""Tests for /api/digest endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")

from openjarvis.agents.digest_store import DigestArtifact, DigestStore


@pytest.fixture()
def store(tmp_path):
    db_path = str(tmp_path / "digest.db")
    s = DigestStore(db_path=db_path)
    s.save(
        DigestArtifact(
            text="Good morning sir.",
            audio_path=tmp_path / "digest.mp3",
            sections={"messages": "3 emails"},
            sources_used=["gmail"],
            generated_at=datetime.now(timezone.utc),
            model_used="test",
            voice_used="jarvis",
        )
    )
    # Write fake audio file
    (tmp_path / "digest.mp3").write_bytes(b"fake-mp3")
    yield s
    s.close()


def _make_app(db_path: str):
    """Create a FastAPI app with the digest router using get_latest as fallback."""
    from unittest.mock import patch

    from fastapi import FastAPI

    from openjarvis.agents.digest_store import DigestStore
    from openjarvis.server.digest_routes import create_digest_router

    # Patch get_today to fall back to get_latest — avoids timezone issues in CI
    original_get_today = DigestStore.get_today

    def _get_today_or_latest(self, timezone_name="UTC"):
        result = original_get_today(self, timezone_name=timezone_name)
        if result is None:
            return self.get_latest()
        return result

    app = FastAPI()
    with patch.object(DigestStore, "get_today", _get_today_or_latest):
        app.include_router(create_digest_router(db_path=db_path))
    return app


def test_get_digest(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] == "Good morning sir."
    assert data["sources_used"] == ["gmail"]


def test_get_digest_audio(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest/audio")
    assert resp.status_code == 200
    assert resp.content == b"fake-mp3"


def test_get_digest_404(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.digest_routes import create_digest_router

    app = FastAPI()
    app.include_router(create_digest_router(db_path=str(tmp_path / "empty.db")))

    client = TestClient(app)
    resp = client.get("/api/digest")
    assert resp.status_code == 404


def test_get_history(store, tmp_path):
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["voice_used"] == "jarvis"


# ---------------------------------------------------------------------------
# Digest type-aware route tests
# ---------------------------------------------------------------------------


def _make_typed_store(tmp_path):
    """Create a store with morning, midday, and evening digests."""
    db_path = str(tmp_path / "typed_digest.db")
    s = DigestStore(db_path=db_path)
    now = datetime.now(timezone.utc)
    for dtype in ("morning", "midday", "evening"):
        s.save(
            DigestArtifact(
                text=f"{dtype} briefing text",
                audio_path=tmp_path / f"{dtype}.mp3",
                sections={},
                sources_used=["gmail"],
                generated_at=now,
                model_used="test",
                voice_used="jarvis",
                digest_type=dtype,
            )
        )
        (tmp_path / f"{dtype}.mp3").write_bytes(b"fake-mp3")
    return s, db_path


def test_get_digest_with_type_param(tmp_path):
    """GET /api/digest?type=midday returns the midday digest."""
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.agents.digest_store import DigestStore
    from openjarvis.server.digest_routes import create_digest_router

    store, db_path = _make_typed_store(tmp_path)

    original_get_today = DigestStore.get_today

    def _get_today_or_latest(self, timezone_name="UTC", digest_type=""):
        result = original_get_today(self, timezone_name=timezone_name, digest_type=digest_type)
        if result is None:
            return self.get_latest(digest_type=digest_type)
        return result

    app = FastAPI()
    with patch.object(DigestStore, "get_today", _get_today_or_latest):
        app.include_router(create_digest_router(db_path=db_path))

    client = TestClient(app)

    # Fetch midday specifically
    resp = client.get("/api/digest?type=midday")
    assert resp.status_code == 200
    data = resp.json()
    assert data["digest_type"] == "midday"
    assert data["text"] == "midday briefing text"

    # Fetch evening
    resp = client.get("/api/digest?type=evening")
    assert resp.status_code == 200
    data = resp.json()
    assert data["digest_type"] == "evening"

    store.close()


def test_get_digest_response_includes_type(store, tmp_path):
    """Response always includes the digest_type field."""
    from fastapi.testclient import TestClient

    app = _make_app(str(tmp_path / "digest.db"))
    client = TestClient(app)
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    data = resp.json()
    assert "digest_type" in data
    assert data["digest_type"] == "morning"  # Default for legacy digests


def test_get_history_with_type_filter(tmp_path):
    """GET /api/digest/history?type=morning only returns morning digests."""
    from unittest.mock import patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.digest_routes import create_digest_router

    store, db_path = _make_typed_store(tmp_path)

    app = FastAPI()
    app.include_router(create_digest_router(db_path=db_path))
    client = TestClient(app)

    resp = client.get("/api/digest/history?type=morning")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["digest_type"] == "morning"

    resp = client.get("/api/digest/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3  # All types

    store.close()


def test_schedule_endpoint_returns_schedules(tmp_path):
    """GET /api/digest/schedule returns the schedules dict."""
    from unittest.mock import MagicMock, patch

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server.digest_routes import create_digest_router

    app = FastAPI()
    app.include_router(create_digest_router(db_path=str(tmp_path / "s.db")))
    client = TestClient(app)

    mock_cfg = MagicMock()
    mock_cfg.digest.enabled = True
    mock_cfg.digest.schedule = "0 6 * * *"
    mock_cfg.digest.schedules = {
        "morning": "0 6 * * *",
        "midday": "0 12 * * *",
        "evening": "0 18 * * *",
    }

    with patch("openjarvis.server.digest_routes.load_config", return_value=mock_cfg):
        resp = client.get("/api/digest/schedule")

    assert resp.status_code == 200
    data = resp.json()
    assert "schedules" in data
    assert data["schedules"]["midday"] == "0 12 * * *"
    assert data["schedules"]["evening"] == "0 18 * * *"
