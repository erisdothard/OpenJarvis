"""LinkedIn daily content tool — generate posts inspired by top AI builders.

Pulls recent content from specific LinkedIn creators in the AI automation
space, identifies what topics are getting engagement, then generates
original Syntra AI posts in a similar vein.  Not generic AI news — real
content from real builders who are winning on LinkedIn.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Creators to study — AI builders who post content that actually lands
# ──────────────────────────────────────────────────────────────────────

_CREATORS: Dict[str, Dict[str, str]] = {
    "nick_saraev": {
        "name": "Nick Saraev",
        "handle": "nick-saraev",
        "style": "Revenue transparency, automation teardowns, no-BS business advice. Shows real numbers.",
        "topics": "AI automation sales, n8n builds, client acquisition, pricing strategy",
    },
    "liam_ottley": {
        "name": "Liam Ottley",
        "handle": "liamottley",
        "style": "AAA model playbook, specific offer breakdowns, scaling agency revenue.",
        "topics": "AI agency model, voice AI solutions, $20K+ project positioning, Morningside AI",
    },
    "nate_herk": {
        "name": "Nate Herk",
        "handle": "nateherkelman",
        "style": "Corporate-to-solo journey, hands-on n8n tutorials, bridging learning and monetizing.",
        "topics": "AI agents, n8n workflows, leaving corporate for AI, automation society",
    },
    "cole_medin": {
        "name": "Cole Medin",
        "handle": "cole-medin-727752184",
        "style": "Live agent builds, open-source demos, technical credibility through code.",
        "topics": "AI agent architecture, RAG systems, local AI, Dynamous AI, code walkthroughs",
    },
    "ruben_hassid": {
        "name": "Ruben Hassid",
        "handle": "ruben-hassid",
        "style": "Short-form video demos, contrarian takes, massive engagement hooks.",
        "topics": "AI tools demos, LinkedIn growth with AI, EasyGen, AI content strategy",
    },
    "moritz_kremb": {
        "name": "Moritz Kremb",
        "handle": "moritzkremb",
        "style": "Framework-heavy posts, numbered steps, actionable systems for AI productivity.",
        "topics": "AI productivity frameworks, delegation systems, prompt engineering, Prohuman AI",
    },
    "matt_shumer": {
        "name": "Matt Shumer",
        "handle": "mattshumer",
        "style": "Build announcements with demos, ships agents and open-sources them.",
        "topics": "Self-operating computer, AI researcher agent, HyperWrite, agent demos",
    },
    "denis_popa": {
        "name": "Denis Popa",
        "handle": "denis-popa",
        "style": "Small agency operations, client results, scaling from zero.",
        "topics": "AI automation agency, chatbots, CRM automation, client delivery, AAAgency",
    },
}

_POST_TYPES = {
    "industry_news": "React to something happening in AI right now — with your own take",
    "hot_take": "Bold, contrarian opinion that challenges common AI assumptions",
    "case_study": "How you (or someone) used AI to solve a real business problem",
    "tip": "One specific, actionable AI automation tip someone can use today",
    "myth_bust": "Kill a common AI misconception with evidence",
    "trend": "Spot an emerging pattern before everyone else talks about it",
    "behind_scenes": "Show what it's actually like building AI systems for clients",
    "build_demo": "Show something you built — the architecture, the result, the lesson",
    "framework": "A numbered system or framework for solving an AI/business problem",
}


def _fetch_creator_content(
    creators: List[str],
    max_per_creator: int = 3,
) -> List[Dict[str, str]]:
    """Fetch recent content from specific LinkedIn creators via web search."""
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        year = datetime.now().year
        all_content: List[Dict[str, str]] = []

        for creator_key in creators:
            creator = _CREATORS.get(creator_key)
            if not creator:
                continue

            name = creator["name"]
            queries = [
                f'"{name}" linkedin post {year}',
                f'"{name}" AI automation {year}',
            ]

            for query in queries:
                try:
                    raw = list(ddgs.text(query, max_results=max_per_creator))
                    for r in raw:
                        all_content.append({
                            "creator": name,
                            "creator_key": creator_key,
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", ""),
                        })
                except Exception:
                    pass

        return all_content
    except Exception as exc:
        _log.warning("Creator content fetch failed: %s", exc)
        return []


def _fetch_topic_news(topics: List[str], max_results: int = 5) -> List[Dict[str, str]]:
    """Fetch news on specific AI topics relevant to Syntra."""
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        year = datetime.now().year
        results: List[Dict[str, str]] = []

        for topic in topics[:3]:
            try:
                raw = list(ddgs.text(f"{topic} {year}", max_results=max_results // 3 + 1))
                for r in raw:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
            except Exception:
                pass

        return results[:max_results]
    except Exception as exc:
        _log.warning("Topic news fetch failed: %s", exc)
        return []


def _build_post_prompt(
    creator_content: List[Dict[str, str]],
    topic_news: List[Dict[str, str]],
    post_type: str,
    custom_topic: Optional[str] = None,
    source_creators: Optional[List[str]] = None,
) -> str:
    """Build the LLM prompt — grounded in what real creators are posting."""

    # Build creator context
    creator_context = ""
    if creator_content:
        items = []
        for item in creator_content[:10]:
            items.append(
                f"- **{item['creator']}**: {item['title']}\n"
                f"  {item['snippet'][:200]}"
            )
        creator_context = "\n".join(items)

    # Build news context
    news_context = ""
    if topic_news:
        items = []
        for item in topic_news[:5]:
            items.append(f"- {item['title']}: {item['snippet'][:150]}")
        news_context = "\n".join(items)

    # Creator style references
    style_refs = ""
    if source_creators:
        refs = []
        for key in source_creators:
            c = _CREATORS.get(key)
            if c:
                refs.append(f"- **{c['name']}**: {c['style']}")
        style_refs = "\n".join(refs)

    type_desc = _POST_TYPES.get(post_type, _POST_TYPES["industry_news"])
    topic_section = ""
    if custom_topic:
        topic_section = f"\n## Specific Topic\nWrite about: {custom_topic}\n"

    return f"""You are writing a LinkedIn post as Eris Dothard, founder of Syntra AI — an AI consulting firm that builds custom agentic workflows, voice AI, and automation systems for businesses.

## What Top AI Builders Are Posting Right Now
{creator_context}

## Current AI News
{news_context}

## Creators to Emulate (style, NOT content)
{style_refs}

## Post Type: {post_type}
{type_desc}
{topic_section}
## Who You Are (Eris / Syntra AI)
- 30, Nashville. Left fintech (CPI Card Group) + Google Fiber to build AI systems
- Co-founding an AI consulting firm with my father (16+ year enterprise developer, former IT Director)
- Built FreightX ($10K SaaS for trucking), DispatchRelay (voice AI), BridgeLink Core
- Enterprise-grade: CJIS security environment, HL7/FHIR healthcare data, OAuth 2.0
- Anthropic-certified (Claude Certified Architect)
- Stack: Python, FastAPI, React, LangChain, LangGraph, Claude API
- Freelance model — custom agentic workflow installations, not cookie-cutter SaaS

## What Makes a Great LinkedIn Post (studied from the creators above)
- FIRST LINE IS EVERYTHING. It's the only thing people see before "see more"
- Use short paragraphs and line breaks — LinkedIn rewards whitespace
- Show the work: "I built X" beats "5 tips about X" every time
- Be specific: real numbers, real tools, real outcomes
- Personal angle: your experience, your mistakes, your learnings
- End with engagement: a question, a hot take, or "drop a comment if..."
- 150-400 words. Don't ramble.
- Hashtags: 3-5, at the very end, relevant ones only
- ZERO emoji spam. 0-2 max if any. You're a builder, not a marketer.
- Sound like a person who builds things, not someone who reads about them

## Critical Rules
- Do NOT write generic "AI is transforming business" posts
- Do NOT use corporate buzzword language
- Do NOT sound like ChatGPT wrote it
- DO reference specific tools, frameworks, or experiences
- DO take a position — agree or disagree with something
- DO make it feel like something only YOU could write

## Output
Return a JSON object:
- "hook": The opening line (make me want to click "see more")
- "post": Full post text, ready to copy-paste into LinkedIn
- "hashtags": Array of 3-5 hashtags (without #)
- "inspired_by": Which creator/topic inspired this
- "post_type": The post type used
- "why_it_works": One sentence on why this post should get engagement

Return ONLY valid JSON."""


@ToolRegistry.register("linkedin_daily")
class LinkedInDailyTool(BaseTool):
    """Generate daily LinkedIn posts modeled after top AI builders."""

    tool_id = "linkedin_daily"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_daily",
            description=(
                "Generate a ready-to-post LinkedIn post for today. Studies what "
                "top AI builders (Nick Saraev, Liam Ottley, Nate Herk, Cole Medin, "
                "Ruben Hassid, etc.) are posting, then generates original content "
                "in Syntra AI's voice inspired by their topics and style."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "post_type": {
                        "type": "string",
                        "enum": list(_POST_TYPES.keys()),
                        "description": (
                            "Type of post: industry_news, hot_take, case_study, "
                            "tip, myth_bust, trend, behind_scenes, build_demo, "
                            "framework. Default: auto-rotates by day."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "description": "Specific topic to write about. If omitted, picks from creator feed.",
                    },
                    "emulate": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(_CREATORS.keys()),
                        },
                        "description": (
                            "Which creators to study for inspiration. Options: "
                            "nick_saraev, liam_ottley, nate_herk, cole_medin, "
                            "ruben_hassid, moritz_kremb, matt_shumer, denis_popa. "
                            "Default: rotates through all."
                        ),
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of post options to generate (default: 3, max: 5).",
                    },
                },
                "required": [],
            },
            category="content",
            timeout_seconds=180.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        post_type = params.get("post_type")
        custom_topic = params.get("topic")
        emulate = params.get("emulate")
        count = min(params.get("count", 3), 5)

        # Auto-select post type by day of week
        if not post_type:
            day_rotation = {
                0: "industry_news",
                1: "tip",
                2: "hot_take",
                3: "case_study",
                4: "trend",
                5: "build_demo",
                6: "framework",
            }
            post_type = day_rotation.get(datetime.now().weekday(), "industry_news")

        # Pick creators to study — rotate through if not specified
        if not emulate:
            creator_keys = list(_CREATORS.keys())
            day_of_year = datetime.now().timetuple().tm_yday
            # Pick 3 creators, rotating daily
            start = (day_of_year * 3) % len(creator_keys)
            emulate = [
                creator_keys[(start + i) % len(creator_keys)]
                for i in range(3)
            ]

        # Fetch what these creators are posting
        creator_content = _fetch_creator_content(emulate, max_per_creator=3)

        # Fetch relevant topic news
        topic_queries = [
            "AI automation agency client acquisition",
            "AI agents enterprise deployment",
            "LangChain Claude workflow automation business",
        ]
        if custom_topic:
            topic_queries = [custom_topic] + topic_queries[:1]

        topic_news = _fetch_topic_news(topic_queries, max_results=5)

        # Generate posts
        all_posts: List[Dict[str, Any]] = []
        for i in range(count):
            # Vary post type for multiple options
            if i > 0 and not params.get("post_type"):
                types = list(_POST_TYPES.keys())
                idx = types.index(post_type) if post_type in types else 0
                post_type_variant = types[(idx + i) % len(types)]
            else:
                post_type_variant = post_type

            prompt = _build_post_prompt(
                creator_content,
                topic_news,
                post_type_variant,
                custom_topic,
                emulate,
            )

            try:
                from openjarvis.tools._llm_helper import generate as llm_generate

                response_text = llm_generate(
                    prompt=prompt,
                    system_prompt=(
                        "You are a LinkedIn content strategist who has studied "
                        "what works for AI builders on LinkedIn. Output only valid JSON."
                    ),
                )

                if not response_text:
                    continue

                response_text = response_text.strip()
                if "```" in response_text:
                    json_match = re.search(
                        r"```(?:json)?\s*([\s\S]*?)```", response_text
                    )
                    if json_match:
                        response_text = json_match.group(1).strip()

                try:
                    post_data = json.loads(response_text)
                    post_data["option"] = i + 1
                    all_posts.append(post_data)
                except json.JSONDecodeError:
                    all_posts.append({
                        "option": i + 1,
                        "post": result.content,
                        "raw": True,
                    })

            except Exception as exc:
                _log.warning("Post generation %d failed: %s", i + 1, exc)

        # Fallback if LLM is unavailable
        if not all_posts:
            lines = [
                f"## LinkedIn Content Feed — {datetime.now().strftime('%B %d, %Y')}\n",
                f"**Post type:** {post_type} — {_POST_TYPES.get(post_type, '')}\n",
                "**Creators studied:**",
            ]
            for key in emulate:
                c = _CREATORS.get(key)
                if c:
                    lines.append(f"  - {c['name']}: {c['style']}")

            lines.append("\n**What they're posting about:**")
            for item in creator_content[:6]:
                lines.append(f"  - [{item['creator']}] {item['title'][:80]}")

            lines.append("\n**Relevant news:**")
            for item in topic_news[:3]:
                lines.append(f"  - {item['title'][:80]}")

            lines.append(
                "\n*LLM unavailable — use these topics and styles to write your post.*"
            )

            return ToolResult(
                tool_name="linkedin_daily",
                content="\n".join(lines),
                success=True,
                metadata={
                    "fallback": True,
                    "creators": emulate,
                    "content_items": len(creator_content),
                },
            )

        # Format output
        creator_names = [_CREATORS[k]["name"] for k in emulate if k in _CREATORS]
        lines = [
            f"## LinkedIn Posts — {datetime.now().strftime('%B %d, %Y')}\n",
            f"**Studying:** {', '.join(creator_names)}",
            f"**Post type:** {post_type} — {_POST_TYPES.get(post_type, '')}\n",
        ]

        for post_data in all_posts:
            option = post_data.get("option", "?")
            lines.append(f"---\n### Option {option}\n")

            if post_data.get("raw"):
                lines.append(post_data.get("post", ""))
            else:
                hook = post_data.get("hook", "")
                post = post_data.get("post", "")
                hashtags = post_data.get("hashtags", [])
                inspired = post_data.get("inspired_by", "")
                why = post_data.get("why_it_works", "")

                lines.append(f"**Hook:** {hook}\n")
                lines.append(f"```\n{post}\n```\n")

                if hashtags:
                    lines.append(
                        f"**Hashtags:** {' '.join(f'#{h}' for h in hashtags)}"
                    )
                if inspired:
                    lines.append(f"**Inspired by:** {inspired}")
                if why:
                    lines.append(f"**Why it works:** {why}")

            lines.append("")

        lines.append(
            "---\n*Copy any option and paste into LinkedIn. Edit to make it yours.*"
        )

        return ToolResult(
            tool_name="linkedin_daily",
            content="\n".join(lines),
            success=True,
            metadata={
                "post_type": post_type,
                "options_generated": len(all_posts),
                "creators_studied": emulate,
                "creator_content_items": len(creator_content),
                "date": datetime.now().isoformat(),
            },
        )


__all__ = ["LinkedInDailyTool"]
