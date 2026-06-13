"""Content remix tool — take competitor content and generate better versions.

Fetches a competitor's post or article, analyzes it, then uses the LLM to
generate a Syntra AI-branded version that's better, with original angle and
stronger positioning.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._brand import brand_context
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)


def _fetch_content(url: str) -> Optional[str]:
    """Fetch and extract text content from a URL."""
    try:
        import httpx

        resp = httpx.get(
            url,
            follow_redirects=True,
            timeout=15.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            },
        )
        resp.raise_for_status()

        html = resp.text
        # Remove scripts and styles
        text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html)
        text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:4000]
    except Exception as exc:
        _log.warning("Failed to fetch content from %s: %s", url, exc)
        return None


def _build_remix_prompt(
    original_content: str,
    platform: str,
    angle: Optional[str] = None,
    tone: str = "professional",
) -> str:
    """Build the LLM prompt for content remixing."""
    platform_guides = {
        "linkedin": "Professional, thought-leadership. Hook in first line. Use line breaks. 3-5 hashtags. Max 3000 chars.",
        "twitter": "Concise, punchy. One key insight. 1-2 hashtags. Max 280 chars. Thread-friendly.",
        "instagram": "Visual-first caption. Story-driven. Emojis sparingly. CTA at end. 5-15 hashtags. Max 2200 chars.",
        "facebook": "Conversational, community-oriented. Questions work well. 1-3 hashtags.",
        "blog": "Long-form, SEO-optimized. 800-1500 words. Include headers, examples, data points.",
    }

    tone_guides = {
        "professional": "Authoritative, polished, data-driven. No slang.",
        "casual": "Friendly, approachable, conversational.",
        "technical": "Detailed, precise, industry terminology. For developers.",
        "storytelling": "Narrative-driven, personal anecdotes, transformation arcs.",
        "provocative": "Bold, contrarian, challenges assumptions. Generates discussion.",
    }

    platform_guide = platform_guides.get(platform, platform_guides["linkedin"])
    tone_guide = tone_guides.get(tone, tone_guides["professional"])
    angle_section = f"\n## Your Angle\n{angle}\n" if angle else ""

    return f"""You are a content strategist for the company described in the brand context below.

## Task
Analyze the competitor content below and create a BETTER branded version. Do NOT copy — use as inspiration to create something original that demonstrates deeper expertise.

## Original Content (Competitor)
{original_content[:3000]}
{angle_section}{brand_context()}## Platform: {platform.title()}
{platform_guide}

## Tone
{tone_guide}

## Requirements
1. Identify the key topic/insight from the competitor content
2. Take a stronger, more specific position
3. Add concrete examples or data points the competitor missed
4. Include a clear CTA relevant to the company's services
5. Make it genuinely better — more insightful, more actionable, more engaging

## Output Format
Return a JSON object with:
- "analysis": Brief analysis of what the competitor did well/poorly (2-3 sentences)
- "key_topic": The core topic/insight identified
- "content": The full remixed post ready to publish
- "hashtags": Array of hashtag strings (without #)
- "improvements": Array of 3-5 specific ways your version is better
- "estimated_engagement": "low" | "medium" | "high"

Return ONLY the JSON object, no other text."""


@ToolRegistry.register("content_remix")
class ContentRemixTool(BaseTool):
    """Remix competitor content into better Syntra-branded posts."""

    tool_id = "content_remix"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="content_remix",
            description=(
                "Take a competitor's post or article URL, analyze it, and generate "
                "a better Syntra AI-branded version. Identifies the core insight, "
                "takes a stronger position, and adapts for the target platform."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the competitor content to remix.",
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Direct text content to remix (use instead of URL "
                            "if you already have the text)."
                        ),
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["linkedin", "twitter", "instagram", "facebook", "blog"],
                        "description": "Target platform for the remixed content (default: linkedin).",
                    },
                    "tone": {
                        "type": "string",
                        "enum": ["professional", "casual", "technical", "storytelling", "provocative"],
                        "description": "Tone for the remixed content (default: professional).",
                    },
                    "angle": {
                        "type": "string",
                        "description": (
                            "Optional specific angle or perspective to take. "
                            "E.g. 'focus on enterprise security' or 'target logistics companies'."
                        ),
                    },
                },
                "required": [],
            },
            category="content",
            timeout_seconds=120.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        url = params.get("url")
        direct_content = params.get("content")
        platform = params.get("platform", "linkedin")
        tone = params.get("tone", "professional")
        angle = params.get("angle")

        # Get the content to remix
        original_content = None
        source = "direct"

        if url:
            original_content = _fetch_content(url)
            source = url
            if not original_content:
                return ToolResult(
                    tool_name="content_remix",
                    content=f"Failed to fetch content from {url}.",
                    success=False,
                )
        elif direct_content:
            original_content = direct_content[:4000]
            source = "provided text"
        else:
            return ToolResult(
                tool_name="content_remix",
                content="Provide either a 'url' or 'content' to remix.",
                success=False,
            )

        prompt = _build_remix_prompt(original_content, platform, angle, tone)

        # Use LLM to generate remix
        try:
            from openjarvis.tools._llm_helper import generate as llm_generate

            response_text = llm_generate(
                prompt=prompt,
                system_prompt="You are a content strategist. Output only valid JSON.",
            )

            if not response_text:
                return ToolResult(
                    tool_name="content_remix",
                    content="LLM generation failed — no API key or model unavailable.",
                    success=False,
                )

            # Parse response
            response_text = response_text.strip()
            if "```" in response_text:
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
                if json_match:
                    response_text = json_match.group(1).strip()

            try:
                remix = json.loads(response_text)
            except json.JSONDecodeError:
                return ToolResult(
                    tool_name="content_remix",
                    content=f"## Remixed Content\n\n{response_text}",
                    success=True,
                    metadata={"raw": True, "source": source},
                )

            # Format output
            hashtag_str = " ".join(f"#{h}" for h in remix.get("hashtags", []))
            improvements = remix.get("improvements", [])
            improvements_str = "\n".join(f"  - {imp}" for imp in improvements)

            content = (
                f"## Content Remix — {platform.title()}\n\n"
                f"**Source:** {source}\n"
                f"**Engagement Estimate:** {remix.get('estimated_engagement', 'medium')}\n\n"
                f"### Analysis\n{remix.get('analysis', 'N/A')}\n\n"
                f"### Key Topic\n{remix.get('key_topic', 'N/A')}\n\n"
                f"### Remixed Post\n{remix.get('content', '')}\n\n"
                f"**Hashtags:** {hashtag_str}\n\n"
                f"### How This Is Better\n{improvements_str}\n"
            )

            return ToolResult(
                tool_name="content_remix",
                content=content,
                success=True,
                metadata={
                    "remix": remix,
                    "source": source,
                    "platform": platform,
                    "tone": tone,
                },
            )

        except Exception as exc:
            _log.warning("Content remix failed: %s", exc)
            return ToolResult(
                tool_name="content_remix",
                content=(
                    f"## Content Remix (template mode — LLM unavailable)\n\n"
                    f"**Source:** {source}\n"
                    f"**Platform:** {platform}\n"
                    f"**Tone:** {tone}\n\n"
                    f"**Original content preview:**\n{original_content[:500]}...\n\n"
                    f"[LLM unavailable — draft your remix manually using the content above]"
                ),
                success=True,
                metadata={"fallback": True, "error": str(exc)},
            )


__all__ = ["ContentRemixTool"]
