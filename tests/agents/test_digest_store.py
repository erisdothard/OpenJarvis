"""Tests for DigestStore and DigestArtifact."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openjarvis.agents.digest_store import DigestArtifact, DigestStore


def test_store_and_retrieve(tmp_path):
    store = DigestStore(db_path=str(tmp_path / "digest.db"))

    artifact = DigestArtifact(
        text="Good morning sir.",
        audio_path=Path("/tmp/digest.mp3"),
        sections={"messages": "You have 3 emails.", "calendar": "2 meetings today."},
        sources_used=["gmail", "gcalendar"],
        generated_at=datetime(2026, 4, 1, 6, 0, 0),
        model_used="claude-sonnet-4-6",
        voice_used="jarvis-v1",
    )

    store.save(artifact)
    retrieved = store.get_latest()

    assert retrieved is not None
    assert retrieved.text == "Good morning sir."
    assert retrieved.sections["messages"] == "You have 3 emails."
    assert retrieved.sources_used == ["gmail", "gcalendar"]
    assert retrieved.voice_used == "jarvis-v1"

    store.close()


def test_get_today(tmp_path):
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    artifact = DigestArtifact(
        text="Today's digest",
        audio_path=Path("/tmp/today.mp3"),
        sections={"messages": "Nothing urgent."},
        sources_used=["gmail"],
        generated_at=datetime.now(tz=__import__("datetime").timezone.utc),
        model_used="test-model",
        voice_used="jarvis",
    )
    store.save(artifact)
    today = store.get_today(timezone_name="UTC")
    assert today is not None
    assert today.text == "Today's digest"
    store.close()


def test_get_today_returns_none_when_empty(tmp_path):
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    assert store.get_today() is None
    store.close()


def test_history(tmp_path):
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    for i in range(3):
        store.save(
            DigestArtifact(
                text=f"Digest {i}",
                audio_path=Path(f"/tmp/d{i}.mp3"),
                sections={},
                sources_used=[],
                generated_at=datetime(2026, 4, 1 + i, 6, 0, 0),
                model_used="test",
                voice_used="jarvis",
            )
        )
    history = store.history(limit=2)
    assert len(history) == 2
    assert history[0].text == "Digest 2"  # Most recent first
    store.close()


# ---------------------------------------------------------------------------
# Digest type tests
# ---------------------------------------------------------------------------


def test_digest_type_default(tmp_path):
    """Artifacts default to digest_type='morning'."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    store.save(
        DigestArtifact(
            text="Morning briefing",
            audio_path=Path(""),
            sections={},
            sources_used=[],
            generated_at=datetime.now(tz=__import__("datetime").timezone.utc),
            model_used="test",
            voice_used="jarvis",
        )
    )
    artifact = store.get_latest()
    assert artifact is not None
    assert artifact.digest_type == "morning"
    store.close()


def test_store_and_retrieve_by_type(tmp_path):
    """Can store and retrieve digests by type."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    now = datetime.now(tz=__import__("datetime").timezone.utc)

    for dtype in ("morning", "midday", "evening"):
        store.save(
            DigestArtifact(
                text=f"{dtype} briefing",
                audio_path=Path(""),
                sections={},
                sources_used=[],
                generated_at=now,
                model_used="test",
                voice_used="jarvis",
                digest_type=dtype,
            )
        )

    # get_latest with type filter
    morning = store.get_latest(digest_type="morning")
    assert morning is not None
    assert morning.digest_type == "morning"
    assert morning.text == "morning briefing"

    evening = store.get_latest(digest_type="evening")
    assert evening is not None
    assert evening.digest_type == "evening"
    assert evening.text == "evening briefing"

    # get_latest without filter returns the most recent (evening, inserted last)
    latest = store.get_latest()
    assert latest is not None
    assert latest.digest_type == "evening"

    store.close()


def test_get_today_by_type(tmp_path):
    """get_today can filter by digest type."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    now = datetime.now(tz=__import__("datetime").timezone.utc)

    store.save(
        DigestArtifact(
            text="Morning text",
            audio_path=Path(""),
            sections={},
            sources_used=[],
            generated_at=now,
            model_used="test",
            voice_used="jarvis",
            digest_type="morning",
        )
    )
    store.save(
        DigestArtifact(
            text="Midday text",
            audio_path=Path(""),
            sections={},
            sources_used=[],
            generated_at=now,
            model_used="test",
            voice_used="jarvis",
            digest_type="midday",
        )
    )

    morning = store.get_today(timezone_name="UTC", digest_type="morning")
    assert morning is not None
    assert morning.text == "Morning text"

    midday = store.get_today(timezone_name="UTC", digest_type="midday")
    assert midday is not None
    assert midday.text == "Midday text"

    # No evening saved yet
    evening = store.get_today(timezone_name="UTC", digest_type="evening")
    assert evening is None

    store.close()


def test_history_by_type(tmp_path):
    """history can filter by digest type."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))

    for i, dtype in enumerate(["morning", "midday", "evening", "morning", "midday"]):
        store.save(
            DigestArtifact(
                text=f"{dtype} {i}",
                audio_path=Path(""),
                sections={},
                sources_used=[],
                generated_at=datetime(2026, 4, 1 + i, 6, 0, 0),
                model_used="test",
                voice_used="jarvis",
                digest_type=dtype,
            )
        )

    all_history = store.history(limit=10)
    assert len(all_history) == 5

    morning_history = store.history(limit=10, digest_type="morning")
    assert len(morning_history) == 2
    assert all(a.digest_type == "morning" for a in morning_history)

    midday_history = store.history(limit=10, digest_type="midday")
    assert len(midday_history) == 2

    evening_history = store.history(limit=10, digest_type="evening")
    assert len(evening_history) == 1

    store.close()


def test_follow_up_questions_persisted(tmp_path):
    """follow_up_questions round-trips through save/retrieve."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    now = datetime.now(tz=__import__("datetime").timezone.utc)

    questions = [
        "Did anything come out of your 10 AM call?",
        "Any new tasks from the standup?",
    ]
    store.save(
        DigestArtifact(
            text="Midday update",
            audio_path=Path(""),
            sections={},
            sources_used=[],
            generated_at=now,
            model_used="test",
            voice_used="jarvis",
            digest_type="midday",
            follow_up_questions=questions,
        )
    )

    artifact = store.get_latest(digest_type="midday")
    assert artifact is not None
    assert artifact.follow_up_questions == questions
    assert len(artifact.follow_up_questions) == 2

    store.close()


def test_follow_up_questions_default_empty(tmp_path):
    """Artifacts without follow_up_questions default to empty list."""
    store = DigestStore(db_path=str(tmp_path / "digest.db"))
    now = datetime.now(tz=__import__("datetime").timezone.utc)

    store.save(
        DigestArtifact(
            text="Morning briefing",
            audio_path=Path(""),
            sections={},
            sources_used=[],
            generated_at=now,
            model_used="test",
            voice_used="jarvis",
        )
    )

    artifact = store.get_latest()
    assert artifact is not None
    assert artifact.follow_up_questions == []

    store.close()
