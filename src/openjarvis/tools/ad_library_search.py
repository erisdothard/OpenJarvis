"""Ad library search — see what ads competitors are running.

Queries Meta Ad Library (free, public) and searches Google Ads Transparency
Center for competitor ad activity.  Zero cost.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Meta Ad Library API (public, no auth needed for page searches)
_META_AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"
_META_AD_LIBRARY_API = "https://www.facebook.com/ads/library/async/search_ads/"


def _search_meta_ads(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Search Meta Ad Library via DuckDuckGo (the API requires auth now).

    Falls back to searching for the ads via web search since Meta's public
    API endpoint requires authentication as of 2025.
    """
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        search_query = f'site:facebook.com/ads/library "{query}" active ad'
        raw = list(ddgs.text(search_query, max_results=max_results))
        results = []
        for r in raw:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": "meta_ad_library",
            })

        # Also search for their Facebook page ads directly
        search_query2 = f'"{query}" facebook ad campaign sponsored 2026'
        raw2 = list(ddgs.text(search_query2, max_results=5))
        for r in raw2:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": "meta_ads_web",
            })

        return results
    except Exception as exc:
        _log.warning("Meta ad search failed: %s", exc)
        return []


def _search_google_ads(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Search Google Ads Transparency Center via web search.

    Google Ads Transparency Center (adstransparency.google.com) is public
    but requires JS rendering. We search for cached/indexed results.
    """
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        # Search for ads on Google Ads Transparency Center
        search_query = f'site:adstransparency.google.com "{query}"'
        raw = list(ddgs.text(search_query, max_results=max_results))
        results = []
        for r in raw:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": "google_ads_transparency",
            })

        # Also search for their Google Ads landing pages
        search_query2 = f'"{query}" google ad landing page sponsored 2026'
        raw2 = list(ddgs.text(search_query2, max_results=5))
        for r in raw2:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": "google_ads_web",
            })

        return results
    except Exception as exc:
        _log.warning("Google ad search failed: %s", exc)
        return []


def _search_linkedin_ads(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Search for LinkedIn sponsored content from competitors."""
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        search_query = f'"{query}" linkedin sponsored post ad campaign 2026'
        raw = list(ddgs.text(search_query, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
                "source": "linkedin_ads",
            }
            for r in raw
        ]
    except Exception as exc:
        _log.warning("LinkedIn ad search failed: %s", exc)
        return []


@ToolRegistry.register("ad_library_search")
class AdLibrarySearchTool(BaseTool):
    """Search ad libraries for competitor advertising activity."""

    tool_id = "ad_library_search"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ad_library_search",
            description=(
                "Search Meta Ad Library, Google Ads Transparency Center, and "
                "LinkedIn for competitor advertising activity. See what ads "
                "they're running, their ad copy, landing pages, and targeting. "
                "Free — no API keys needed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "competitor": {
                        "type": "string",
                        "description": (
                            "Competitor name to search for ads. Use their brand name "
                            "(e.g. 'SynkrAI', 'Automaly', 'ProsperSpark')."
                        ),
                    },
                    "platforms": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["meta", "google", "linkedin", "all"],
                        },
                        "description": "Ad platforms to search (default: all).",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results per platform (default 10).",
                    },
                },
                "required": ["competitor"],
            },
            category="intelligence",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        competitor = params.get("competitor", "")
        platforms = params.get("platforms", ["all"])
        max_results = params.get("max_results", 10)

        if not competitor:
            return ToolResult(
                tool_name="ad_library_search",
                content="No competitor specified.",
                success=False,
            )

        # Resolve competitor key to name
        from openjarvis.tools.competitor_monitor import _COMPETITORS

        comp_key = competitor.lower().strip()
        if comp_key in _COMPETITORS:
            competitor = _COMPETITORS[comp_key]["name"]

        search_all = "all" in platforms

        all_results: List[Dict[str, str]] = []

        if search_all or "meta" in platforms:
            all_results.extend(_search_meta_ads(competitor, max_results))

        if search_all or "google" in platforms:
            all_results.extend(_search_google_ads(competitor, max_results))

        if search_all or "linkedin" in platforms:
            all_results.extend(_search_linkedin_ads(competitor, min(max_results, 5)))

        if not all_results:
            return ToolResult(
                tool_name="ad_library_search",
                content=(
                    f"No ad activity found for '{competitor}'. "
                    "This could mean they're not running paid ads, or their ads "
                    "aren't indexed yet."
                ),
                success=True,
                metadata={"competitor": competitor, "num_results": 0},
            )

        # Group by source
        by_source: Dict[str, List[Dict[str, str]]] = {}
        for r in all_results:
            source = r.get("source", "unknown")
            by_source.setdefault(source, []).append(r)

        lines = [f"## {competitor} — Ad Intelligence\n"]

        source_labels = {
            "meta_ad_library": "Meta Ad Library",
            "meta_ads_web": "Meta/Facebook Ads (Web)",
            "google_ads_transparency": "Google Ads Transparency",
            "google_ads_web": "Google Ads (Web)",
            "linkedin_ads": "LinkedIn Sponsored Content",
        }

        for source, results in by_source.items():
            label = source_labels.get(source, source)
            lines.append(f"### {label}\n")
            for i, r in enumerate(results, 1):
                lines.append(
                    f"**{i}. {r['title']}**\n"
                    f"Link: {r['url']}\n"
                    f"{r['snippet']}\n"
                )
            lines.append("---\n")

        content = "\n".join(lines)
        return ToolResult(
            tool_name="ad_library_search",
            content=content,
            success=True,
            metadata={
                "competitor": competitor,
                "num_results": len(all_results),
                "platforms_searched": list(by_source.keys()),
            },
        )


__all__ = ["AdLibrarySearchTool"]
