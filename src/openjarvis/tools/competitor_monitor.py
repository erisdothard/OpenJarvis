"""Competitor intelligence tool — monitor pricing, features, content, and ads.

Leverages web search (Tavily/DuckDuckGo) to gather real-time competitive
intelligence on demand.  Structured queries return actionable summaries.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)

# Pre-configured competitor profiles — AI automation agencies & consultancies
_COMPETITORS: Dict[str, Dict[str, str]] = {
    # Direct competitors (AI automation agencies/freelance)
    "synkrai": {"domain": "synkrai.com", "name": "SynkrAI"},
    "automaly": {"domain": "automaly.io", "name": "Automaly"},
    "prosperspark": {"domain": "prosperspark.com", "name": "ProsperSpark"},
    "theautomators": {"domain": "theautomators.ai", "name": "The Automators"},
    "thirdrock": {"domain": "thirdrocktechkno.com", "name": "Third Rock Techkno"},
    "prismetric": {"domain": "prismetric.com", "name": "Prismetric"},
    # AI agent dev companies
    "pearllemon": {"domain": "pearllemonai.com", "name": "Pearl Lemon AI"},
    "blackcube": {"domain": "blackcubelabs.com", "name": "Black Cube Labs"},
    "jadasquad": {"domain": "jadasquad.com", "name": "The JADA Squad"},
    "keystoneai": {"domain": "keyholesoftware.com", "name": "Keyhole Software"},
}

# Check-type to search query templates (tuned for AI automation agencies)
_QUERY_TEMPLATES: Dict[str, str] = {
    "pricing": '{name} pricing packages retainer cost project rates 2026 site:{domain} OR "{name}" pricing',
    "services": '{name} services offerings automation integration AI agents workflows 2026 site:{domain} OR "{name}" services',
    "content": '{name} blog articles case studies thought leadership 2026 site:{domain}/blog OR "{name}" blog',
    "ads": '{name} advertising campaigns Google Ads Meta ads 2026 "{name}" ad copy landing page',
    "reviews": '{name} reviews testimonials clients case studies 2026 "{name}" review',
    "hiring": '{name} hiring jobs careers team open roles 2026 site:{domain} OR "{name}" hiring',
    "funding": '{name} funding valuation investors revenue clients 2026 "{name}" raised',
    "clients": '{name} clients portfolio case studies projects results 2026 site:{domain} OR "{name}" client',
    "tech_stack": '{name} technology stack tools frameworks LangChain n8n CrewAI 2026 site:{domain} OR "{name}"',
    "positioning": '{name} about mission value proposition target market 2026 site:{domain} OR "{name}" about',
    "overview": '{name} AI automation agency company services 2026 site:{domain} OR "{name}"',
}


def _build_query(competitor: str, check_type: str) -> str:
    """Build a search query from competitor name and check type."""
    profile = _COMPETITORS.get(competitor.lower())
    if profile:
        name = profile["name"]
        domain = profile["domain"]
    else:
        name = competitor
        domain = competitor.lower().replace(" ", "") + ".com"

    template = _QUERY_TEMPLATES.get(check_type, _QUERY_TEMPLATES["overview"])
    return template.format(name=name, domain=domain)


def _do_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Run a web search and return structured results."""
    # Try Tavily first, fall back to DuckDuckGo
    try:
        import os

        from tavily import TavilyClient

        api_key = os.environ.get("TAVILY_API_KEY")
        if api_key:
            client = TavilyClient(api_key=api_key)
            response = client.search(query, max_results=max_results, search_depth="advanced")
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", "") or r.get("snippet", ""),
                }
                for r in response.get("results", [])
            ]
    except Exception:
        pass

    try:
        from ddgs import DDGS

        ddgs = DDGS()
        raw = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
    except Exception as exc:
        _log.warning("Search failed: %s", exc)
        return []


@ToolRegistry.register("competitor_monitor")
class CompetitorMonitorTool(BaseTool):
    """Gather competitive intelligence on AI agency competitors."""

    tool_id = "competitor_monitor"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="competitor_monitor",
            description=(
                "Monitor AI automation agency competitors — check their pricing, "
                "services, content, ads, reviews, clients, tech stack, and positioning. "
                "Pre-configured for: SynkrAI, Automaly, ProsperSpark, The Automators, "
                "Third Rock Techkno, Prismetric, Pearl Lemon AI, Black Cube Labs, "
                "The JADA Squad, Keyhole Software. Also accepts any custom company name."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "competitor": {
                        "type": "string",
                        "description": (
                            "Competitor name or key. Pre-configured: synkrai, automaly, "
                            "prosperspark, theautomators, thirdrock, prismetric, "
                            "pearllemon, blackcube, jadasquad, keystoneai. "
                            "Or any company name."
                        ),
                    },
                    "check_type": {
                        "type": "string",
                        "enum": [
                            "pricing",
                            "services",
                            "content",
                            "ads",
                            "reviews",
                            "hiring",
                            "funding",
                            "clients",
                            "tech_stack",
                            "positioning",
                            "overview",
                        ],
                        "description": "What aspect to investigate.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum search results to return (default 5).",
                    },
                },
                "required": ["competitor", "check_type"],
            },
            category="intelligence",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        competitor = params.get("competitor", "")
        check_type = params.get("check_type", "overview")
        max_results = params.get("max_results", 5)

        if not competitor:
            return ToolResult(
                tool_name="competitor_monitor",
                content="No competitor specified.",
                success=False,
            )

        query = _build_query(competitor, check_type)
        results = _do_search(query, max_results=max_results)

        if not results:
            return ToolResult(
                tool_name="competitor_monitor",
                content=f"No results found for {competitor} ({check_type}).",
                success=False,
            )

        # Format results
        profile = _COMPETITORS.get(competitor.lower(), {"name": competitor, "domain": "unknown"})
        sections = [f"## {profile['name']} — {check_type.title()} Intelligence\n"]
        for i, r in enumerate(results, 1):
            sections.append(
                f"### {i}. {r['title']}\n"
                f"Source: {r['url']}\n"
                f"{r['snippet']}\n"
            )

        content = "\n".join(sections)
        return ToolResult(
            tool_name="competitor_monitor",
            content=content,
            success=True,
            metadata={
                "competitor": competitor,
                "check_type": check_type,
                "num_results": len(results),
                "query": query,
            },
        )


__all__ = ["CompetitorMonitorTool"]
