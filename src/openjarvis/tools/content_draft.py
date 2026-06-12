"""Content drafting tool — AI-generated social media posts per platform.

Uses the LLM engine to draft platform-adapted content with hashtags and
engagement estimates.  Can optionally analyze a competitor's post to riff on.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)

_PLATFORM_GUIDELINES: Dict[str, Dict[str, Any]] = {
    "linkedin": {
        "max_chars": 3000,
        "style": "Professional, thought-leadership tone. Use line breaks for readability. Hook in the first line.",
        "hashtag_count": "3-5",
    },
    "twitter": {
        "max_chars": 280,
        "style": "Concise, punchy. One key insight or CTA. Thread-friendly if longer.",
        "hashtag_count": "1-2",
    },
    "instagram": {
        "max_chars": 2200,
        "style": "Visual-first caption. Story-driven, relatable. Use emojis sparingly. CTA at end.",
        "hashtag_count": "5-15",
    },
    "facebook": {
        "max_chars": 63206,
        "style": "Conversational, community-oriented. Questions and engagement hooks work well.",
        "hashtag_count": "1-3",
    },
}

_TONE_DESCRIPTORS: Dict[str, str] = {
    "professional": "Authoritative and polished. Data-driven where possible. No slang.",
    "casual": "Friendly and approachable. Conversational. Like talking to a smart friend.",
    "technical": "Detailed and precise. Industry terminology welcome. For developers and engineers.",
    "storytelling": "Narrative-driven. Personal anecdotes, lessons learned, transformation arcs.",
}


def _build_draft_prompt(
    topic: str,
    platforms: List[str],
    tone: str,
    reference_content: Optional[str] = None,
) -> str:
    """Build the LLM prompt for content drafting."""
    tone_desc = _TONE_DESCRIPTORS.get(tone, _TONE_DESCRIPTORS["professional"])

    platform_specs = []
    for p in platforms:
        guide = _PLATFORM_GUIDELINES.get(p, _PLATFORM_GUIDELINES["linkedin"])
        platform_specs.append(
            f"### {p.title()}\n"
            f"- Max characters: {guide['max_chars']}\n"
            f"- Style: {guide['style']}\n"
            f"- Hashtags: {guide['hashtag_count']}\n"
        )

    reference_section = ""
    if reference_content:
        reference_section = (
            "\n## Reference Content (competitor post to riff on — do NOT copy, use as inspiration)\n"
            f"{reference_content[:2000]}\n"
        )

    return f"""You are a social media content strategist for Syntra AI, an AI consulting firm that builds voice AI agents, workflow automation, and custom AI systems for businesses.

## Task
Draft a social media post about the following topic for each specified platform.

## Topic
{topic}

## Tone
{tone_desc}

## Brand Voice
- Syntra AI positions as "Intelligent Systems Engineering"
- We build AI that runs businesses, not just answers questions
- Focus on practical results, not AI hype
- Credibility through case studies and technical depth
{reference_section}
## Platform Requirements
{"".join(platform_specs)}

## Output Format
Respond with a JSON array where each element has:
- "platform": the platform name
- "content": the full post text ready to publish
- "hashtags": array of hashtag strings (without #)
- "hook": the opening line/hook used
- "estimated_engagement": "low" | "medium" | "high" based on topic virality and platform fit

Return ONLY the JSON array, no other text.
"""


@ToolRegistry.register("content_draft")
class ContentDraftTool(BaseTool):
    """Draft social media content adapted for each platform."""

    tool_id = "content_draft"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="content_draft",
            description=(
                "Draft social media posts for Syntra AI, adapted per platform. "
                "Generates ready-to-publish content with hashtags and engagement estimates. "
                "Can analyze a competitor's post or URL to riff on."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "What the post should be about.",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["linkedin", "twitter", "instagram", "facebook"],
                        },
                        "description": "Platforms to draft for (default: all).",
                    },
                    "tone": {
                        "type": "string",
                        "enum": ["professional", "casual", "technical", "storytelling"],
                        "description": "Tone of the content (default: professional).",
                    },
                    "reference_url": {
                        "type": "string",
                        "description": "Optional URL of a competitor post to use as inspiration.",
                    },
                },
                "required": ["topic"],
            },
            category="content",
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        topic = params.get("topic", "")
        platforms = params.get("platforms", ["linkedin", "twitter", "instagram", "facebook"])
        tone = params.get("tone", "professional")
        reference_url = params.get("reference_url")

        if not topic:
            return ToolResult(
                tool_name="content_draft",
                content="No topic provided.",
                success=False,
            )

        # Fetch reference content if URL provided
        reference_content = None
        if reference_url:
            try:
                import httpx

                resp = httpx.get(
                    reference_url,
                    follow_redirects=True,
                    timeout=15.0,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; OpenJarvis/1.0)"},
                )
                import re

                text = re.sub(r"<[^>]+>", " ", resp.text)
                text = re.sub(r"\s+", " ", text).strip()
                reference_content = text[:2000]
            except Exception as exc:
                _log.warning("Failed to fetch reference URL: %s", exc)

        prompt = _build_draft_prompt(topic, platforms, tone, reference_content)

        # Use the LLM to generate drafts
        try:
            from openjarvis.tools._llm_helper import generate as llm_generate

            response_text = llm_generate(
                prompt=prompt,
                system_prompt="You are a social media content strategist. Output only valid JSON.",
            )

            if not response_text:
                return ToolResult(
                    tool_name="content_draft",
                    content="LLM generation failed — no API key or model unavailable.",
                    success=False,
                )

            # Parse the LLM response
            response_text = response_text.strip()
            # Extract JSON from potential markdown code blocks
            if "```" in response_text:
                import re

                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
                if json_match:
                    response_text = json_match.group(1).strip()

            try:
                drafts = json.loads(response_text)
            except json.JSONDecodeError:
                # Return raw content if JSON parsing fails
                return ToolResult(
                    tool_name="content_draft",
                    content=f"## Drafts\n\n{result.content}",
                    success=True,
                    metadata={"raw": True},
                )

            # Format nicely
            lines = ["## Content Drafts\n"]
            for draft in drafts:
                platform = draft.get("platform", "unknown")
                content = draft.get("content", "")
                hashtags = draft.get("hashtags", [])
                hook = draft.get("hook", "")
                engagement = draft.get("estimated_engagement", "medium")

                hashtag_str = " ".join(f"#{h}" for h in hashtags) if hashtags else ""
                lines.append(
                    f"### {platform.title()} ({engagement} engagement)\n"
                    f"**Hook:** {hook}\n\n"
                    f"{content}\n\n"
                    f"**Hashtags:** {hashtag_str}\n"
                    f"---\n"
                )

            return ToolResult(
                tool_name="content_draft",
                content="\n".join(lines),
                success=True,
                metadata={
                    "drafts": drafts,
                    "topic": topic,
                    "platforms": platforms,
                    "tone": tone,
                },
            )

        except (KeyError, Exception) as exc:
            _log.warning("Content draft failed: %s", exc)
            # Fallback: generate a simple template-based draft
            lines = ["## Content Drafts (template mode — LLM unavailable)\n"]
            for platform in platforms:
                guide = _PLATFORM_GUIDELINES.get(platform, _PLATFORM_GUIDELINES["linkedin"])
                max_chars = guide["max_chars"]
                lines.append(
                    f"### {platform.title()}\n"
                    f"**Topic:** {topic}\n"
                    f"**Max chars:** {max_chars}\n"
                    f"**Style guide:** {guide['style']}\n"
                    f"**Suggested hashtags:** {guide['hashtag_count']}\n"
                    f"\n[Draft your {platform} post here — {tone} tone]\n"
                    f"---\n"
                )

            return ToolResult(
                tool_name="content_draft",
                content="\n".join(lines),
                success=True,
                metadata={"fallback": True, "error": str(exc)},
            )


__all__ = ["ContentDraftTool"]
