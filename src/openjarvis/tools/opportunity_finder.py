"""Opportunity finder — cross-reference competitors with Syntra's capabilities.

Analyzes competitor offerings, identifies gaps and advantages, and suggests
growth opportunities for Syntra AI.  Uses web search + LLM analysis.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_log = logging.getLogger(__name__)

# Syntra AI's known capabilities and differentiators
_SYNTRA_PROFILE = {
    "name": "Syntra AI",
    "type": "AI consulting / freelance",
    "services": [
        "Custom agentic workflow design & installation",
        "Voice AI agent development (DispatchRelay)",
        "Multi-role SaaS platforms (FreightX)",
        "3D scrollytelling & creative dev",
        "Workflow automation (LangChain, LangGraph, n8n)",
        "Enterprise data integration (OAuth 2.0, CJIS, HL7/FHIR)",
        "AI system architecture & consulting",
    ],
    "tech_stack": [
        "Python", "FastAPI", "React", "Next.js", "TypeScript",
        "LangChain", "LangGraph", "Claude API", "Anthropic SDK",
        "Supabase", "PostgreSQL", "Docker",
        "React Three Fiber", "GSAP", "Three.js",
    ],
    "differentiators": [
        "Enterprise background (CPI Card Group, Google Fiber)",
        "CJIS-environment experience (security-cleared)",
        "Healthcare data (HL7/FHIR) integration experience",
        "3D/creative development capabilities",
        "Digital products revenue stream ($99-$199)",
        "Full-stack: from AI agents to frontend to infrastructure",
        "Anthropic-certified (CCA Foundations)",
    ],
    "portfolio": [
        "FreightX — multi-role SaaS freight marketplace ($10K build)",
        "DispatchRelay — voice AI dispatch system",
        "BridgeLink Core — enterprise integration platform",
        "PropLogix — property management automation",
        "3 Aces Trucking — logistics AI deployment",
    ],
    "target_verticals": [
        "Logistics & trucking",
        "SMBs needing AI automation",
        "Operations-heavy industries",
    ],
}


def _gather_competitor_intel(competitor: str) -> str:
    """Gather fresh intelligence on a competitor via web search."""
    try:
        from ddgs import DDGS

        ddgs = DDGS()
        queries = [
            f'"{competitor}" AI automation services pricing 2026',
            f'"{competitor}" clients case studies portfolio',
            f'"{competitor}" technology stack tools',
        ]
        all_text = []
        for q in queries:
            try:
                results = list(ddgs.text(q, max_results=3))
                for r in results:
                    all_text.append(f"{r.get('title', '')}: {r.get('body', '')}")
            except Exception:
                pass
        return "\n".join(all_text)[:3000]
    except Exception as exc:
        _log.warning("Competitor intel gathering failed: %s", exc)
        return ""


def _build_analysis_prompt(
    competitors_intel: Dict[str, str],
    focus: Optional[str] = None,
) -> str:
    """Build the LLM prompt for opportunity analysis."""
    syntra_desc = json.dumps(_SYNTRA_PROFILE, indent=2)

    competitor_sections = []
    for name, intel in competitors_intel.items():
        competitor_sections.append(f"### {name}\n{intel[:1500]}\n")

    focus_section = ""
    if focus:
        focus_section = f"\n## Focus Area\nPay special attention to: {focus}\n"

    return f"""You are a competitive strategy analyst for Syntra AI.

## Syntra AI Profile
{syntra_desc}

## Competitor Intelligence
{"".join(competitor_sections)}
{focus_section}
## Task
Analyze the competitive landscape and identify specific growth opportunities for Syntra AI.

For each opportunity, consider:
1. What gap exists in competitors' offerings that Syntra can fill?
2. What does Syntra already have that competitors don't?
3. What's the effort level to capitalize on this?
4. What's the potential revenue impact?

## Output Format
Return a JSON object with:
- "market_position": Brief assessment of where Syntra stands vs competitors (2-3 sentences)
- "advantages": Array of strings — things Syntra has that competitors don't
- "gaps": Array of strings — things competitors offer that Syntra should consider
- "opportunities": Array of objects, each with:
  - "title": Short name for the opportunity
  - "description": What to do and why (2-3 sentences)
  - "effort": "low" | "medium" | "high"
  - "revenue_potential": "low" | "medium" | "high"
  - "priority": 1-5 (1 = highest priority)
  - "action_items": Array of 2-3 specific next steps
- "content_gaps": Array of strings — topics competitors cover that Syntra doesn't blog/post about
- "positioning_advice": 2-3 sentences on how Syntra should position itself differently

Return ONLY the JSON object, no other text."""


@ToolRegistry.register("opportunity_finder")
class OpportunityFinderTool(BaseTool):
    """Find growth opportunities by analyzing Syntra vs competitors."""

    tool_id = "opportunity_finder"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="opportunity_finder",
            description=(
                "Cross-reference competitor offerings with Syntra AI's capabilities "
                "to find growth opportunities, gaps, and advantages. Analyzes the "
                "competitive landscape and recommends specific actions with effort "
                "and revenue estimates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "competitors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of competitor names to analyze against. "
                            "Default: all pre-configured competitors."
                        ),
                    },
                    "focus": {
                        "type": "string",
                        "description": (
                            "Optional focus area for the analysis. "
                            "E.g. 'pricing strategy', 'content marketing', "
                            "'service differentiation', 'vertical targeting'."
                        ),
                    },
                },
                "required": [],
            },
            category="intelligence",
            timeout_seconds=180.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        competitors: List[str] = params.get("competitors", [])
        focus: Optional[str] = params.get("focus")

        # Default to key competitors
        if not competitors:
            from openjarvis.tools.competitor_monitor import _COMPETITORS

            competitors = [
                v["name"] for k, v in list(_COMPETITORS.items())[:5]
            ]

        # Gather intel on each competitor
        competitors_intel: Dict[str, str] = {}
        for comp in competitors:
            intel = _gather_competitor_intel(comp)
            if intel:
                competitors_intel[comp] = intel

        if not competitors_intel:
            return ToolResult(
                tool_name="opportunity_finder",
                content="Could not gather intelligence on any competitors. Check your internet connection.",
                success=False,
            )

        prompt = _build_analysis_prompt(competitors_intel, focus)

        # Use LLM to analyze
        try:
            from openjarvis.tools._llm_helper import generate as llm_generate

            response_text = llm_generate(
                prompt=prompt,
                system_prompt="You are a competitive strategy analyst. Output only valid JSON.",
            )

            if not response_text:
                return ToolResult(
                    tool_name="opportunity_finder",
                    content="LLM analysis failed — no API key or model unavailable.",
                    success=False,
                )

            # Parse response
            response_text = response_text.strip()
            if "```" in response_text:
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response_text)
                if json_match:
                    response_text = json_match.group(1).strip()

            try:
                analysis = json.loads(response_text)
            except json.JSONDecodeError:
                return ToolResult(
                    tool_name="opportunity_finder",
                    content=f"## Opportunity Analysis\n\n{result.content}",
                    success=True,
                    metadata={"raw": True},
                )

            # Format output
            lines = ["## Syntra AI — Growth Opportunity Analysis\n"]

            lines.append(f"### Market Position\n{analysis.get('market_position', 'N/A')}\n")

            advantages = analysis.get("advantages", [])
            if advantages:
                lines.append("### Syntra's Advantages")
                for adv in advantages:
                    lines.append(f"  + {adv}")
                lines.append("")

            gaps = analysis.get("gaps", [])
            if gaps:
                lines.append("### Gaps to Address")
                for gap in gaps:
                    lines.append(f"  - {gap}")
                lines.append("")

            opportunities = analysis.get("opportunities", [])
            if opportunities:
                # Sort by priority
                opportunities.sort(key=lambda x: x.get("priority", 5))
                lines.append("### Opportunities (by priority)\n")
                for i, opp in enumerate(opportunities, 1):
                    effort = opp.get("effort", "medium")
                    revenue = opp.get("revenue_potential", "medium")
                    lines.append(
                        f"**{i}. {opp.get('title', 'Opportunity')}** "
                        f"[Effort: {effort} | Revenue: {revenue}]\n"
                        f"{opp.get('description', '')}\n"
                    )
                    actions = opp.get("action_items", [])
                    for action in actions:
                        lines.append(f"  -> {action}")
                    lines.append("")

            content_gaps = analysis.get("content_gaps", [])
            if content_gaps:
                lines.append("### Content Gaps (topics to own)")
                for gap in content_gaps:
                    lines.append(f"  - {gap}")
                lines.append("")

            positioning = analysis.get("positioning_advice", "")
            if positioning:
                lines.append(f"### Positioning Advice\n{positioning}\n")

            content = "\n".join(lines)
            return ToolResult(
                tool_name="opportunity_finder",
                content=content,
                success=True,
                metadata={
                    "analysis": analysis,
                    "competitors_analyzed": list(competitors_intel.keys()),
                    "focus": focus,
                },
            )

        except Exception as exc:
            _log.warning("Opportunity analysis failed: %s", exc)

            # Fallback: return raw intel
            lines = ["## Opportunity Analysis (raw data — LLM unavailable)\n"]
            lines.append("### Syntra AI Capabilities")
            for svc in _SYNTRA_PROFILE["services"]:
                lines.append(f"  - {svc}")
            lines.append("\n### Competitor Intelligence Gathered")
            for name, intel in competitors_intel.items():
                lines.append(f"\n**{name}:**\n{intel[:500]}...")

            return ToolResult(
                tool_name="opportunity_finder",
                content="\n".join(lines),
                success=True,
                metadata={"fallback": True, "error": str(exc)},
            )


__all__ = ["OpportunityFinderTool"]
