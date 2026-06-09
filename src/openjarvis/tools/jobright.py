# ruff: noqa: E501
"""Jobright.ai job search and application tools — Playwright browser automation.

No public API exists for Jobright, so these tools drive the browser directly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_BASE_URL = "https://jobright.ai"


class _JobrightSession:
    """Manages a Playwright browser session for Jobright."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright not installed. Install with: uv sync --extra browser"
            )
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=False)
        self._context = self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        )
        self._page = self._context.new_page()
        # Load saved cookies
        cookie_path = os.path.expanduser("~/.openjarvis/connectors/jobright_cookies.json")
        if os.path.exists(cookie_path):
            with open(cookie_path) as f:
                cookies = json.load(f)
            if cookies:
                self._context.add_cookies(cookies)

    @property
    def page(self):
        self._ensure_browser()
        return self._page

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._playwright = self._browser = self._context = self._page = None


_session = _JobrightSession()


# ---------------------------------------------------------------------------
# Tool 1: Jobright Job Search
# ---------------------------------------------------------------------------


@ToolRegistry.register("jobright_search")
class JobrightSearchTool(BaseTool):
    """Search for jobs on Jobright.ai."""

    tool_id = "jobright_search"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="jobright_search",
            description=(
                "Search for jobs on Jobright.ai by keywords and location. "
                "Returns job titles, companies, locations, and Jobright URLs. "
                "Opens a browser to scrape results."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "Job search keywords (e.g. 'Implementation Engineer')",
                    },
                    "location": {
                        "type": "string",
                        "description": "Location filter (e.g. 'Nashville, TN' or 'Remote')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 10)",
                    },
                },
                "required": ["keywords"],
            },
            category="job_search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            page = _session.page
            keywords = params["keywords"]
            location = params.get("location", "")
            limit = params.get("limit", 10)

            # Build search URL
            search_url = f"{_BASE_URL}/jobs?q={keywords.replace(' ', '+')}"
            if location:
                search_url += f"&l={location.replace(' ', '+')}"

            page.goto(search_url, wait_until="networkidle", timeout=30000)
            time.sleep(2)  # let dynamic content load

            # Extract job cards
            jobs: List[Dict[str, str]] = []
            cards = page.query_selector_all("[class*='job-card'], [class*='JobCard'], [class*='job_card'], a[href*='/jobs/info/']")

            if not cards:
                # Fallback: try extracting from page content
                content = page.content()
                # Look for job links
                import re
                job_links = re.findall(r'href="(/jobs/info/[^"]+)"', content)
                titles = re.findall(r'class="[^"]*[Tt]itle[^"]*"[^>]*>([^<]+)', content)

                for i, link in enumerate(job_links[:limit]):
                    jobs.append({
                        "title": titles[i] if i < len(titles) else "Unknown",
                        "url": f"{_BASE_URL}{link}",
                    })
            else:
                for card in cards[:limit]:
                    title_el = card.query_selector("[class*='title'], h3, h2")
                    company_el = card.query_selector("[class*='company'], [class*='Company']")
                    location_el = card.query_selector("[class*='location'], [class*='Location']")
                    link_el = card.query_selector("a[href*='/jobs/']") or card

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else ""
                    href = link_el.get_attribute("href") or ""
                    if href and not href.startswith("http"):
                        href = f"{_BASE_URL}{href}"

                    jobs.append({
                        "title": title,
                        "company": company,
                        "location": loc,
                        "url": href,
                    })

            return ToolResult(
                tool_name="jobright_search",
                success=True,
                content=json.dumps(jobs, indent=2),
                metadata={"count": len(jobs)},
            )
        except Exception as e:
            logger.error("Jobright search failed: %s", e)
            return ToolResult(tool_name="jobright_search", success=False, content=f"Jobright search failed: {e}")


# ---------------------------------------------------------------------------
# Tool 2: Jobright Job Details
# ---------------------------------------------------------------------------


@ToolRegistry.register("jobright_details")
class JobrightDetailsTool(BaseTool):
    """Get full details for a Jobright job listing."""

    tool_id = "jobright_details"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="jobright_details",
            description=(
                "Get full details for a specific Jobright job listing — "
                "description, requirements, salary, and apply link."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "job_url": {
                        "type": "string",
                        "description": "Jobright job URL (from jobright_search results)",
                    },
                },
                "required": ["job_url"],
            },
            category="job_search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            page = _session.page
            page.goto(params["job_url"], wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # Extract job details
            title = ""
            company = ""
            description = ""

            title_el = page.query_selector("h1, [class*='title']")
            if title_el:
                title = title_el.inner_text().strip()

            company_el = page.query_selector("[class*='company'], [class*='Company']")
            if company_el:
                company = company_el.inner_text().strip()

            desc_el = page.query_selector(
                "[class*='description'], [class*='Description'], "
                "[class*='job-detail'], [class*='JobDetail'], article, main"
            )
            if desc_el:
                description = desc_el.inner_text().strip()[:3000]

            # Find apply button/link
            apply_url = ""
            apply_el = page.query_selector(
                "a[class*='apply'], button[class*='apply'], "
                "a[href*='apply'], [class*='Apply']"
            )
            if apply_el:
                apply_url = apply_el.get_attribute("href") or ""

            return ToolResult(
                tool_name="jobright_details",
                success=True,
                content=json.dumps({
                    "title": title,
                    "company": company,
                    "description": description,
                    "apply_url": apply_url,
                    "source_url": params["job_url"],
                }, indent=2),
            )
        except Exception as e:
            logger.error("Jobright details failed: %s", e)
            return ToolResult(tool_name="jobright_details", success=False, content=f"Jobright details failed: {e}")


# ---------------------------------------------------------------------------
# Tool 3: Jobright Apply
# ---------------------------------------------------------------------------


@ToolRegistry.register("jobright_apply")
class JobrightApplyTool(BaseTool):
    """Apply to a job on Jobright.ai."""

    tool_id = "jobright_apply"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="jobright_apply",
            description=(
                "Apply to a job on Jobright.ai. Navigates to the job page "
                "and clicks the apply button. May require login — the browser "
                "will be visible so you can complete any auth steps."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "job_url": {
                        "type": "string",
                        "description": "Jobright job URL to apply to",
                    },
                },
                "required": ["job_url"],
            },
            category="job_search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            page = _session.page
            page.goto(params["job_url"], wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # Find and click apply button
            apply_selectors = [
                "button:has-text('Apply')",
                "a:has-text('Apply')",
                "[class*='apply'] button",
                "[class*='Apply'] button",
                "button:has-text('Easy Apply')",
                "a:has-text('Easy Apply')",
            ]

            clicked = False
            for selector in apply_selectors:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        el.click()
                        clicked = True
                        time.sleep(2)
                        break
                except Exception:
                    continue

            if clicked:
                return ToolResult(
                    tool_name="jobright_apply",
                    success=True,
                    content=f"Clicked apply for {params['job_url']}. Check the browser for any additional steps.",
                )
            else:
                return ToolResult(
                    tool_name="jobright_apply",
                    success=False,
                    content="Could not find apply button. The page may require login first.",
                )
        except Exception as e:
            logger.error("Jobright apply failed: %s", e)
            return ToolResult(tool_name="jobright_apply", success=False, content=f"Jobright apply failed: {e}")
