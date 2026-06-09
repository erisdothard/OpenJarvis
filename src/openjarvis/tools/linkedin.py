# ruff: noqa: E501
"""LinkedIn job search and recruiter messaging tools — Playwright browser automation.

No usable Python SDK exists for LinkedIn, so these tools drive the browser directly.
Requires the user to be logged into LinkedIn in the browser session.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote_plus

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.linkedin.com"


class _LinkedInSession:
    """Manages a Playwright browser session for LinkedIn."""

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
        cookie_path = os.path.expanduser("~/.openjarvis/connectors/linkedin_cookies.json")
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


_session = _LinkedInSession()


# ---------------------------------------------------------------------------
# Tool 1: LinkedIn Job Search
# ---------------------------------------------------------------------------


@ToolRegistry.register("linkedin_job_search")
class LinkedInJobSearchTool(BaseTool):
    """Search for jobs on LinkedIn."""

    tool_id = "linkedin_job_search"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_job_search",
            description=(
                "Search for jobs on LinkedIn by keywords and location. "
                "Returns job titles, companies, locations, and URLs. "
                "Opens a browser — user must be logged into LinkedIn."
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

            search_url = f"{_BASE_URL}/jobs/search/?keywords={quote_plus(keywords)}"
            if location:
                search_url += f"&location={quote_plus(location)}"

            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # Check if we need to log in
            if "/login" in page.url or "/authwall" in page.url:
                return ToolResult(
                    tool_name="linkedin_job_search",
                    success=False,
                    content="LinkedIn requires login. The browser is open — please log in and retry.",
                )

            jobs: List[Dict[str, str]] = []

            # LinkedIn public job cards use base-search-card / base-card classes
            all_lis = page.query_selector_all("li")
            cards = [li for li in all_lis if li.query_selector("a[href*='/jobs/view/']")]

            if not cards:
                # Fallback: extract from page content via links
                content = page.content()
                job_links = re.findall(r'href="(https://www\.linkedin\.com/jobs/view/[^"]*)"', content)
                for link in job_links[:limit]:
                    jobs.append({"url": link})
            else:
                for card in cards[:limit]:
                    title_el = card.query_selector("h3, .base-search-card__title, [class*='title']")
                    company_el = card.query_selector("h4, .base-search-card__subtitle, [class*='subtitle']")
                    location_el = card.query_selector(".job-search-card__location, [class*='location']")
                    link_el = card.query_selector("a[href*='/jobs/view/']")

                    title = title_el.inner_text().strip() if title_el else ""
                    company = company_el.inner_text().strip() if company_el else ""
                    loc = location_el.inner_text().strip() if location_el else ""
                    href = ""
                    if link_el:
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
                tool_name="linkedin_job_search",
                success=True,
                content=json.dumps(jobs, indent=2),
                metadata={"count": len(jobs)},
            )
        except Exception as e:
            logger.error("LinkedIn job search failed: %s", e)
            return ToolResult(tool_name="linkedin_job_search", success=False, content=f"LinkedIn search failed: {e}")


# ---------------------------------------------------------------------------
# Tool 2: LinkedIn Send Message
# ---------------------------------------------------------------------------


@ToolRegistry.register("linkedin_message")
class LinkedInMessageTool(BaseTool):
    """Send a message to a LinkedIn connection or recruiter."""

    tool_id = "linkedin_message"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_message",
            description=(
                "Send a message to a LinkedIn connection. "
                "Navigates to their profile, opens the message box, and types the message. "
                "The browser must be logged into LinkedIn."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "profile_url": {
                        "type": "string",
                        "description": "LinkedIn profile URL of the recipient",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text to send",
                    },
                },
                "required": ["profile_url", "message"],
            },
            category="job_search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            page = _session.page
            profile_url = params["profile_url"].rstrip("/")
            message = params["message"]

            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "/login" in page.url or "/authwall" in page.url:
                return ToolResult(
                    tool_name="linkedin_message",
                    success=False,
                    content="LinkedIn requires login. The browser is open — please log in and retry.",
                )

            # Find and click the Message button
            msg_btn = page.query_selector(
                "button:has-text('Message'), "
                "a:has-text('Message'), "
                "[class*='message-anywhere-button']"
            )
            if not msg_btn or not msg_btn.is_visible():
                return ToolResult(
                    tool_name="linkedin_message",
                    success=False,
                    content="Could not find Message button. You may not be connected to this person.",
                )

            msg_btn.click()
            time.sleep(2)

            # Type the message into the compose box
            compose = page.query_selector(
                "div[role='textbox'], "
                "div.msg-form__contenteditable, "
                "div[contenteditable='true']"
            )
            if not compose:
                return ToolResult(
                    tool_name="linkedin_message",
                    success=False,
                    content="Message compose box did not open. Check the browser.",
                )

            compose.click()
            compose.fill(message)
            time.sleep(1)

            # Click Send
            send_btn = page.query_selector(
                "button:has-text('Send'), "
                "button[class*='msg-form__send-button']"
            )
            if send_btn and send_btn.is_visible():
                send_btn.click()
                time.sleep(1)
                return ToolResult(
                    tool_name="linkedin_message",
                    success=True,
                    content=f"Message sent to {profile_url}",
                )
            else:
                return ToolResult(
                    tool_name="linkedin_message",
                    success=False,
                    content="Message typed but Send button not found. Check the browser to send manually.",
                )
        except Exception as e:
            logger.error("LinkedIn message failed: %s", e)
            return ToolResult(tool_name="linkedin_message", success=False, content=f"LinkedIn message failed: {e}")


# ---------------------------------------------------------------------------
# Tool 3: LinkedIn Profile View
# ---------------------------------------------------------------------------


@ToolRegistry.register("linkedin_profile")
class LinkedInProfileTool(BaseTool):
    """View a LinkedIn profile."""

    tool_id = "linkedin_profile"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="linkedin_profile",
            description=(
                "View a LinkedIn profile — name, headline, summary, and about section. "
                "Useful for researching recruiters before messaging. "
                "Browser must be logged into LinkedIn."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "profile_url": {
                        "type": "string",
                        "description": "LinkedIn profile URL",
                    },
                },
                "required": ["profile_url"],
            },
            category="job_search",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        try:
            page = _session.page
            profile_url = params["profile_url"].rstrip("/")

            page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            if "/login" in page.url or "/authwall" in page.url:
                return ToolResult(
                    tool_name="linkedin_profile",
                    success=False,
                    content="LinkedIn requires login. The browser is open — please log in and retry.",
                )

            name = ""
            headline = ""
            about = ""
            location = ""

            name_el = page.query_selector("h1")
            if name_el:
                name = name_el.inner_text().strip()

            headline_el = page.query_selector("div.text-body-medium, [class*='headline']")
            if headline_el:
                headline = headline_el.inner_text().strip()

            location_el = page.query_selector("[class*='top-card-layout__first-subline'] span, span.text-body-small[class*='location']")
            if location_el:
                location = location_el.inner_text().strip()

            # Try to get the About section
            about_section = page.query_selector("section:has(#about) div.display-flex span[aria-hidden='true'], [class*='about'] span")
            if about_section:
                about = about_section.inner_text().strip()[:1500]

            data = {
                "name": name,
                "headline": headline,
                "about": about,
                "location": location,
                "profile_url": profile_url,
            }
            return ToolResult(
                tool_name="linkedin_profile",
                success=True,
                content=json.dumps(data, indent=2),
            )
        except Exception as e:
            logger.error("LinkedIn profile view failed: %s", e)
            return ToolResult(tool_name="linkedin_profile", success=False, content=f"LinkedIn profile failed: {e}")
