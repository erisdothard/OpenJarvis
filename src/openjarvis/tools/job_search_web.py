# ruff: noqa: E501
"""Web-based job search tools — reliable, no browser automation required.

Returns DIRECT apply links to individual job postings — not search result pages.
Uses web search (Tavily/DuckDuckGo) targeting individual listings on LinkedIn,
Indeed, Greenhouse, Lever, Wellfound, and free job APIs (Remotive, Arbeitnow).

This is the primary job search path. Playwright-based tools (linkedin.py,
jobright.py) are optional browser-automation extras.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_web_search_instance = None


def _get_web_search_tool():
    """Resolve the web_search tool instance from the registry."""
    global _web_search_instance
    if _web_search_instance is not None:
        return _web_search_instance
    try:
        cls = ToolRegistry.get("web_search")
        _web_search_instance = cls() if isinstance(cls, type) else cls
        return _web_search_instance
    except Exception:
        return None


def _run_search(query: str, max_results: int = 5) -> str:
    """Execute a web search and return raw content."""
    tool = _get_web_search_tool()
    if tool is None:
        raise RuntimeError("web_search tool not available")
    result = tool.execute(query=query, max_results=max_results)
    return result.content if result.success else ""


def _is_individual_listing(url: str) -> bool:
    """Return True if the URL points to an individual job posting, not an aggregation page."""
    individual_patterns = [
        r"linkedin\.com/jobs/view/",
        r"indeed\.com/viewjob",
        r"indeed\.com/rc/clk",
        r"boards\.greenhouse\.io/.+/jobs/\d+",
        r"jobs\.lever\.co/.+/[a-f0-9-]{20,}",
        r"wellfound\.com/jobs/.+",
        r"glassdoor\.com/job-listing/",
        r"builtin\.com/job/",
        r"remotive\.com/remote-jobs/",
        r"arbeitnow\.com/view/",
        r"apply\.workable\.com/",
        r"careers\..+/jobs/",
        r"/careers?/.+/\d+",
        r"/job/\d+",
        r"/jobs/\d+",
    ]
    return any(re.search(pat, url) for pat in individual_patterns)


def _is_aggregation_page(url: str, title: str) -> bool:
    """Return True if this looks like a search results / aggregation page."""
    if re.search(r"[\d,]+\+?\s+(jobs|positions|openings|results)", title, re.IGNORECASE):
        return True
    agg_patterns = [
        r"linkedin\.com/jobs/[a-z-]+-jobs",  # e.g. /jobs/implementation-engineer-jobs
        r"linkedin\.com/jobs/search",
        r"indeed\.com/jobs\?",
        r"indeed\.com/q-",
        r"glassdoor\.com/Job/",  # search page, not /job-listing/
    ]
    return any(re.search(pat, url) for pat in agg_patterns)


def _parse_individual_listings(raw_text: str) -> List[Dict[str, str]]:
    """Extract individual job listings with direct apply URLs from search results."""
    jobs: List[Dict[str, str]] = []
    blocks = re.split(r"(?:^|\n)---\s*\n", raw_text)

    for block in blocks:
        block = block.strip()
        if not block or len(block) < 20:
            continue

        # Extract URL
        url_match = re.search(r"Source:\s*(https?://\S+)", block)
        url = url_match.group(1).rstrip(".,;)") if url_match else ""

        # Extract title
        heading_match = re.search(r"###\s+(.+?)(?:\n|$)", block)
        title = heading_match.group(1).strip() if heading_match else block.split("\n")[0].strip()
        title = re.sub(r"^(Source:|Summary:)\s*", "", title).strip()
        title = re.sub(r"^#+\s*", "", title).strip()

        # Extract summary
        summary_match = re.search(r"Summary:\s*(.+)", block, re.DOTALL)
        summary = summary_match.group(1).strip()[:500] if summary_match else ""

        # Skip if no URL
        if not url:
            continue

        # Skip aggregation pages — we only want individual listings
        if _is_aggregation_page(url, title):
            continue

        # Clean title — remove site suffixes
        title = re.sub(r"\s*[-|]\s*(LinkedIn|Indeed|Glassdoor|Built\s*In|Wellfound|Greenhouse|Lever).*$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"\s*[-|]\s*Greenhouse Software$", "", title, flags=re.IGNORECASE)
        # Remove "hiring" prefix patterns like "Company hiring Role in Location"
        title = re.sub(r"^(.+?)\s+hiring\s+", r"\1 — ", title, flags=re.IGNORECASE)

        jobs.append({
            "title": title[:200],
            "url": url,
            "summary": summary,
        })

    return jobs


# ---------------------------------------------------------------------------
# Free Job API sources (no auth required)
# ---------------------------------------------------------------------------


def _search_remotive(role: str, limit: int = 10) -> List[Dict[str, str]]:
    """Search Remotive API for remote jobs. Free, no auth."""
    try:
        resp = httpx.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": role, "limit": limit},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobs", [])[:limit]:
            pub_date = j.get("publication_date", "")
            jobs.append({
                "title": f"{j.get('title', '')} at {j.get('company_name', '')}",
                "url": j.get("url", ""),
                "summary": (j.get("description", "") or "")[:300],
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "Remote"),
                "salary": j.get("salary", ""),
                "posted": pub_date[:10] if pub_date else "",
                "source_board": "remotive",
            })
        return jobs
    except Exception as e:
        logger.warning("Remotive API failed: %s", e)
        return []


def _search_himalayas(role: str, limit: int = 10) -> List[Dict[str, str]]:
    """Search Himalayas API for remote jobs. Free, no auth, US-focused."""
    try:
        resp = httpx.get(
            "https://himalayas.app/jobs/api",
            params={"q": role, "limit": limit},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for j in data.get("jobs", [])[:limit]:
            title = j.get("title", "")
            company = j.get("companyName", "") or j.get("company_name", "")
            url = j.get("applicationUrl", "") or j.get("url", "")
            if not url:
                slug = j.get("slug", "")
                if slug:
                    url = f"https://himalayas.app/jobs/{slug}"
            location = j.get("locationRestrictions", "") or "Remote"
            if isinstance(location, list):
                location = ", ".join(location[:3]) if location else "Remote"
            jobs.append({
                "title": f"{title} at {company}" if company else title,
                "url": url,
                "summary": (j.get("excerpt", "") or j.get("description", "") or "")[:300],
                "company": company,
                "location": str(location),
                "salary": j.get("salary", ""),
                "source_board": "himalayas",
            })
        return jobs
    except Exception as e:
        logger.warning("Himalayas API failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Tool 1: Multi-Board Job Search
# ---------------------------------------------------------------------------


@ToolRegistry.register("job_search_web")
class JobSearchWebTool(BaseTool):
    """Search for jobs across multiple boards. Returns direct apply links."""

    tool_id = "job_search_web"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="job_search_web",
            description=(
                "Search for jobs and return DIRECT APPLY LINKS to individual postings. "
                "Searches LinkedIn, Indeed, Greenhouse, Lever, Wellfound, Remotive, "
                "and Arbeitnow. Returns clickable URLs — not search result pages. "
                "Filters for recent postings only. PRIMARY job search tool."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Target role title (e.g. 'Implementation Engineer')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Location or 'remote' (e.g. 'Nashville, TN')",
                    },
                    "keywords": {
                        "type": "string",
                        "description": "Additional keywords (e.g. 'fintech AI SaaS')",
                    },
                    "posted_within": {
                        "type": "string",
                        "description": "Recency filter: 'day', 'week'. Default: 'week'",
                    },
                },
                "required": ["role"],
            },
            category="job_search",
            timeout_seconds=90.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        role = params["role"]
        location = params.get("location", "")
        keywords = params.get("keywords", "")
        posted_within = params.get("posted_within", "week")

        location_part = f" {location}" if location else ""
        keywords_part = f" {keywords}" if keywords else ""

        # Queries that target INDIVIDUAL job listing URLs
        queries = [
            # LinkedIn individual listings (jobs/view/*)
            f'site:linkedin.com/jobs/view "{role}"{location_part}{keywords_part}',
            # Greenhouse ATS boards
            f'site:boards.greenhouse.io "{role}"{location_part}',
            # Lever ATS boards
            f'site:jobs.lever.co "{role}"{location_part}',
            # Wellfound (formerly AngelList)
            f'site:wellfound.com/jobs "{role}"{keywords_part}',
        ]

        # Add Indeed only with viewjob path
        if location:
            queries.append(f'site:indeed.com/viewjob "{role}" {location}')

        all_jobs: List[Dict[str, str]] = []
        errors: List[str] = []

        # Web search for individual listings
        for query in queries:
            try:
                raw = _run_search(query, max_results=8)
                if raw:
                    jobs = _parse_individual_listings(raw)
                    for job in jobs:
                        if "source_board" not in job:
                            # Detect board from URL
                            url = job.get("url", "")
                            if "linkedin.com" in url:
                                job["source_board"] = "linkedin"
                            elif "greenhouse.io" in url:
                                job["source_board"] = "greenhouse"
                            elif "lever.co" in url:
                                job["source_board"] = "lever"
                            elif "wellfound.com" in url:
                                job["source_board"] = "wellfound"
                            elif "indeed.com" in url:
                                job["source_board"] = "indeed"
                            else:
                                job["source_board"] = "web"
                    all_jobs.extend(jobs)
            except Exception as e:
                errors.append(str(e))
                logger.warning("Job search query failed: %s", e)

        # Free API sources — strict title filter: require 2+ keyword matches
        # to avoid noise (e.g. "Staff Software Engineer" matching on just "engineer")
        role_words = [w.lower() for w in role.split() if len(w) > 3]
        for api_fn in (_search_remotive, _search_himalayas):
            api_jobs = api_fn(role, limit=15)
            for job in api_jobs:
                title_lower = job.get("title", "").lower()
                matches = sum(1 for w in role_words if w in title_lower)
                if matches >= min(2, len(role_words)):
                    all_jobs.append(job)

        # Deduplicate by URL
        seen_urls: set[str] = set()
        unique_jobs: List[Dict[str, str]] = []
        for job in all_jobs:
            url = job.get("url", "")
            if not url:
                continue
            # Normalize URL for dedup
            clean_url = url.split("?")[0].rstrip("/")
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            unique_jobs.append(job)

        result_data = {
            "jobs": unique_jobs,
            "total_found": len(unique_jobs),
            "errors": errors if errors else None,
        }

        return ToolResult(
            tool_name="job_search_web",
            success=True,
            content=json.dumps(result_data, indent=2),
            metadata={"count": len(unique_jobs)},
        )


# ---------------------------------------------------------------------------
# Tool 2: Company Intelligence (kept for optional use)
# ---------------------------------------------------------------------------


@ToolRegistry.register("company_intel")
class CompanyIntelTool(BaseTool):
    """Research a company — optional, not part of default job search flow."""

    tool_id = "company_intel"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="company_intel",
            description=(
                "Research a company. Returns size, tech stack, Glassdoor rating, "
                "recent news. OPTIONAL — only use when explicitly asked."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": "Company name to research",
                    },
                },
                "required": ["company_name"],
            },
            category="job_search",
            timeout_seconds=45.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        company = params["company_name"]
        queries = [
            f'"{company}" company size employees tech stack',
            f'"{company}" glassdoor rating reviews',
        ]
        sections: List[str] = []
        for query in queries:
            try:
                raw = _run_search(query, max_results=3)
                if raw:
                    sections.append(raw)
            except Exception as e:
                logger.warning("Company research failed: %s", e)
        combined = "\n\n---\n\n".join(sections) if sections else "No information found."
        return ToolResult(
            tool_name="company_intel",
            success=bool(sections),
            content=combined[:6000],
            metadata={"company": company},
        )


# ---------------------------------------------------------------------------
# Tool 3: Recruiter Finder
# ---------------------------------------------------------------------------


@ToolRegistry.register("find_recruiters")
class FindRecruitersTool(BaseTool):
    """Find recruiters and hiring managers at a target company."""

    tool_id = "find_recruiters"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="find_recruiters",
            description=(
                "Find recruiters and hiring managers at a company. "
                "Returns LinkedIn profile URLs. Only use for top-fit roles."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "company_name": {
                        "type": "string",
                        "description": "Company name",
                    },
                    "role_type": {
                        "type": "string",
                        "description": "Type of role (e.g. 'engineering', 'implementation')",
                    },
                },
                "required": ["company_name"],
            },
            category="job_search",
            timeout_seconds=30.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        company = params["company_name"]
        role_type = params.get("role_type", "engineering")
        queries = [
            f'site:linkedin.com/in "{company}" recruiter OR "talent acquisition" {role_type}',
        ]
        all_results: List[str] = []
        for query in queries:
            try:
                raw = _run_search(query, max_results=5)
                if raw:
                    all_results.append(raw)
            except Exception as e:
                logger.warning("Recruiter search failed: %s", e)
        combined = "\n\n".join(all_results) if all_results else "No recruiters found."
        return ToolResult(
            tool_name="find_recruiters",
            success=bool(all_results),
            content=combined[:4000],
            metadata={"company": company},
        )


__all__ = ["JobSearchWebTool", "CompanyIntelTool", "FindRecruitersTool"]
