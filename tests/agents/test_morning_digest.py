"""Tests for MorningDigestAgent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjarvis.agents._stubs import AgentResult
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import ToolResult


def test_morning_digest_registered():
    from openjarvis.agents.morning_digest import MorningDigestAgent

    AgentRegistry.register_value("morning_digest", MorningDigestAgent)
    assert AgentRegistry.contains("morning_digest")


def test_morning_digest_run(tmp_path):
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Good morning sir. You have 3 emails and 2 meetings today.",
        "finish_reason": "stop",
        "usage": {},
    }

    # Mock collect result
    mock_collect_result = ToolResult(
        tool_name="digest_collect",
        content='=== MESSAGES ===\n[gmail] From: alice@co.com — "Budget" (1h ago)\n',
        success=True,
        metadata={"total_items": 2},
    )

    # Mock TTS result
    mock_tts_result = ToolResult(
        tool_name="text_to_speech",
        content=str(tmp_path / "digest.mp3"),
        success=True,
        metadata={"audio_path": str(tmp_path / "digest.mp3")},
    )

    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="neutral",
        digest_store_path=str(tmp_path / "digest.db"),
    )

    with patch.object(
        agent._executor,
        "execute",
        side_effect=[mock_collect_result, mock_tts_result],
    ):
        result = agent.run("Generate morning digest")

    assert isinstance(result, AgentResult)
    assert "Good morning" in result.content
    assert result.turns == 1
    assert len(result.tool_results) == 2


def test_load_persona():
    from openjarvis.agents.morning_digest import _load_persona

    # Nonexistent persona returns empty string
    result = _load_persona("nonexistent_persona_xyz")
    assert result == ""


# ---------------------------------------------------------------------------
# Digest type tests
# ---------------------------------------------------------------------------


def test_digest_types_constant():
    from openjarvis.agents.morning_digest import MorningDigestAgent

    assert MorningDigestAgent.DIGEST_TYPES == ("morning", "midday", "evening")


def test_morning_prompt_content():
    """Morning prompt includes full briefing structure."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)
    agent._persona = "neutral"
    agent._timezone = "UTC"
    agent._honorific = "sir"
    agent._digest_type = "morning"

    prompt = agent._build_system_prompt()
    assert "GREETING + PRIORITIES" in prompt
    assert "SCHEDULE" in prompt
    assert "MESSAGES" in prompt
    assert "HEALTH" in prompt
    assert "WORLD" in prompt
    assert "CLOSING" in prompt
    assert "200 words" in prompt


def test_midday_prompt_content():
    """Midday prompt focuses on check-in, not full briefing."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)
    agent._persona = "neutral"
    agent._timezone = "UTC"
    agent._honorific = "sir"
    agent._digest_type = "midday"

    prompt = agent._build_system_prompt()
    assert "midday status update" in prompt.lower()
    assert "NEW SINCE MORNING" in prompt
    assert "AFTERNOON SCHEDULE" in prompt
    assert "QUICK FLAGS" in prompt
    assert "150 words" in prompt
    # Should NOT contain morning-specific sections
    assert "HEALTH" not in prompt.split("ABSOLUTE RULES")[0]


def test_evening_prompt_content():
    """Evening prompt focuses on circle-back and carry-forward."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)
    agent._persona = "neutral"
    agent._timezone = "UTC"
    agent._honorific = "sir"
    agent._digest_type = "evening"

    prompt = agent._build_system_prompt()
    assert "circle-back" in prompt.lower()
    assert "STILL OPEN" in prompt
    assert "CARRY-FORWARD" in prompt
    assert "TOMORROW PREVIEW" in prompt
    assert "150 words" in prompt


def test_hours_back_varies_by_type():
    """Different digest types use different lookback windows."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)

    agent._digest_type = "morning"
    assert agent._hours_back_for_type() == 24

    agent._digest_type = "midday"
    assert agent._hours_back_for_type() == 8

    agent._digest_type = "evening"
    assert agent._hours_back_for_type() == 14


def test_user_message_varies_by_type():
    """User message content adapts to digest type."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)

    agent._digest_type = "morning"
    msg = agent._user_message_for_type("test data")
    assert "morning briefing" in msg.lower()
    assert "200-250 words" in msg

    agent._digest_type = "midday"
    msg = agent._user_message_for_type("test data")
    assert "midday check-in" in msg.lower()
    assert "150 words" in msg

    agent._digest_type = "evening"
    msg = agent._user_message_for_type("test data")
    assert "evening circle-back" in msg.lower()
    assert "150 words" in msg


def test_run_detects_type_from_input(tmp_path):
    """Agent auto-detects digest type from the input prompt string."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Good afternoon. Quick update for you.",
        "finish_reason": "stop",
        "usage": {},
    }

    mock_collect = ToolResult(
        tool_name="digest_collect",
        content="=== MESSAGES ===\nNothing new.",
        success=True,
        metadata={"total_items": 0},
    )
    mock_tts = ToolResult(
        tool_name="text_to_speech",
        content="",
        success=True,
        metadata={"audio_path": ""},
    )

    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="neutral",
        digest_store_path=str(tmp_path / "digest.db"),
    )

    with patch.object(
        agent._executor, "execute", side_effect=[mock_collect, mock_tts]
    ):
        result = agent.run("Generate my midday digest")

    assert isinstance(result, AgentResult)
    assert agent._digest_type == "midday"


def test_run_detects_evening_from_circle_back(tmp_path):
    """Agent detects evening type from 'circle' keyword."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": "Wrapping up your day.",
        "finish_reason": "stop",
        "usage": {},
    }

    mock_collect = ToolResult(
        tool_name="digest_collect",
        content="=== MESSAGES ===\n1 unanswered.",
        success=True,
        metadata={"total_items": 1},
    )
    mock_tts = ToolResult(
        tool_name="text_to_speech",
        content="",
        success=True,
        metadata={"audio_path": ""},
    )

    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="neutral",
        digest_store_path=str(tmp_path / "digest.db"),
    )

    with patch.object(
        agent._executor, "execute", side_effect=[mock_collect, mock_tts]
    ):
        result = agent.run("Circle back on today")

    assert isinstance(result, AgentResult)
    assert agent._digest_type == "evening"


def test_extract_questions():
    """_extract_questions parses Q: lines from narrative text."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    text = (
        "Good afternoon, Eris. A couple of new emails came in.\n\n"
        "You have a meeting at 3 PM with the design team.\n\n"
        "Q: Did anything actionable come out of your 10 AM call with 3 Aces?\n"
        "Q: Any new tasks from the FreightX standup that aren't in GitHub yet?\n"
        "Q: Has the priority on the Seismic pipeline changed since this morning?"
    )

    clean, questions = MorningDigestAgent._extract_questions(text)

    assert len(questions) == 3
    assert "3 Aces" in questions[0]
    assert "FreightX" in questions[1]
    assert "Seismic" in questions[2]
    assert "Q:" not in clean
    assert "Good afternoon" in clean
    assert "meeting at 3 PM" in clean


def test_extract_questions_numbered():
    """Handles Q1:, Q2:, Q3: format."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    text = (
        "Status update here.\n"
        "Q1: First question?\n"
        "Q2: Second question?\n"
        "Q3. Third question with a period prefix?"
    )

    clean, questions = MorningDigestAgent._extract_questions(text)
    assert len(questions) == 3
    assert "First question?" in questions[0]


def test_extract_questions_none():
    """No Q: lines means empty list and unchanged text."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    text = "Just a regular briefing with no questions."
    clean, questions = MorningDigestAgent._extract_questions(text)
    assert questions == []
    assert clean == text


def test_midday_run_extracts_questions(tmp_path):
    """Midday run extracts Q: lines into follow_up_questions metadata."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    mock_engine = MagicMock()
    mock_engine.generate.return_value = {
        "content": (
            "Good afternoon, Eris. Two new emails since this morning.\n\n"
            "Q: Did the 3 Aces call surface any new freight requirements?\n"
            "Q: Any blockers on the FreightX auth flow?"
        ),
        "finish_reason": "stop",
        "usage": {},
    }

    mock_collect = ToolResult(
        tool_name="digest_collect",
        content="=== MESSAGES ===\n2 new emails.",
        success=True,
        metadata={"total_items": 2},
    )
    mock_tts = ToolResult(
        tool_name="text_to_speech",
        content="",
        success=True,
        metadata={"audio_path": ""},
    )

    agent = MorningDigestAgent(
        mock_engine,
        "test-model",
        tools=[],
        persona="neutral",
        digest_store_path=str(tmp_path / "digest.db"),
    )

    with patch.object(
        agent._executor, "execute", side_effect=[mock_collect, mock_tts]
    ):
        result = agent.run("Generate my midday digest")

    assert agent._digest_type == "midday"
    assert len(result.metadata["follow_up_questions"]) == 2
    assert "3 Aces" in result.metadata["follow_up_questions"][0]
    # Narrative should NOT contain Q: lines
    assert "Q:" not in result.content


def test_midday_prompt_mentions_gap_check():
    """Midday system prompt instructs the LLM to ask follow-up questions."""
    from openjarvis.agents.morning_digest import MorningDigestAgent

    agent = MorningDigestAgent.__new__(MorningDigestAgent)
    agent._persona = "neutral"
    agent._timezone = "UTC"
    agent._honorific = "sir"
    agent._digest_type = "midday"

    prompt = agent._build_system_prompt()
    assert "GAP CHECK" in prompt
    assert "Q: " in prompt
    assert "interactive" in prompt.lower()
