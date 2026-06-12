"""Competitor website scraper — extract structured data from competitor sites.

Directly scrapes competitor websites with httpx + BeautifulSoup to extract
pricing tables, feature lists, CTAs, tech stack indicators, page structure,
and social links.  Zero API cost.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

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
    "Accept-Language": "en-US,en;q=0.9",
}

# Tech stack fingerprints — strings in page source that reveal tools
_TECH_FINGERPRINTS: Dict[str, List[str]] = {
    "Next.js": ["_next/", "__NEXT_DATA__"],
    "React": ["react-root", "reactroot", "__react", "data-reactroot"],
    "Vue.js": ["__vue__", "v-cloak", "nuxt"],
    "WordPress": ["wp-content", "wp-includes", "wordpress"],
    "Webflow": ["webflow.js", "wf-page", "data-wf-"],
    "Framer": ["framer.com", "framerusercontent"],
    "Squarespace": ["squarespace.com", "sqs-block"],
    "Wix": ["wix.com", "wixstatic", "wix-dropdown"],
    "Shopify": ["shopify.com", "cdn.shopify"],
    "HubSpot": ["hubspot.com", "hs-script-loader", "hbspt"],
    "Calendly": ["calendly.com", "calendly-inline"],
    "Intercom": ["intercom.io", "intercom-container"],
    "Drift": ["drift.com", "drift-widget"],
    "Crisp": ["crisp.chat", "crisp-client"],
    "Google Analytics": ["google-analytics.com", "gtag(", "ga("],
    "Google Tag Manager": ["googletagmanager.com", "gtm.js"],
    "Hotjar": ["hotjar.com", "hj("],
    "Stripe": ["stripe.com", "stripe.js"],
    "Tailwind CSS": ["tailwindcss", "tw-"],
    "Bootstrap": ["bootstrap.min", "bootstrap.css"],
    "n8n": ["n8n.io", "n8n"],
    "Make (Integromat)": ["make.com", "integromat"],
    "Zapier": ["zapier.com"],
    "LangChain": ["langchain"],
    "CrewAI": ["crewai"],
}


def _fetch_page(url: str, timeout: float = 15.0) -> Optional[str]:
    """Fetch a page and return its HTML content."""
    try:
        import httpx

        resp = httpx.get(
            url,
            headers=_HEADERS,
            follow_redirects=True,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        _log.warning("Failed to fetch %s: %s", url, exc)
        return None


def _extract_text(html: str) -> str:
    """Strip HTML tags and return clean text."""
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _detect_tech_stack(html: str) -> List[str]:
    """Detect technologies used on the page."""
    html_lower = html.lower()
    detected = []
    for tech, fingerprints in _TECH_FINGERPRINTS.items():
        for fp in fingerprints:
            if fp.lower() in html_lower:
                detected.append(tech)
                break
    return sorted(detected)


def _extract_prices(text: str) -> List[str]:
    """Extract price-like patterns from text."""
    patterns = [
        r"\$[\d,]+(?:\.\d{2})?(?:\s*(?:/\s*(?:mo|month|yr|year|hr|hour|project|session))?)",
        r"(?:starting\s+(?:at|from)\s+)\$[\d,]+",
        r"(?:from\s+)\$[\d,]+",
        r"\$[\d,]+\s*(?:-|–|to)\s*\$[\d,]+",
    ]
    prices = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        prices.extend(m.strip() for m in matches)
    return list(dict.fromkeys(prices))[:20]  # dedupe, limit


def _extract_emails(text: str) -> List[str]:
    """Extract email addresses."""
    matches = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return list(dict.fromkeys(matches))[:10]


def _extract_social_links(html: str, base_url: str) -> Dict[str, str]:
    """Extract social media profile links."""
    socials: Dict[str, str] = {}
    social_patterns = {
        "linkedin": r'href=["\']([^"\']*linkedin\.com/(?:company|in)/[^"\']+)',
        "twitter": r'href=["\']([^"\']*(?:twitter\.com|x\.com)/[^"\']+)',
        "instagram": r'href=["\']([^"\']*instagram\.com/[^"\']+)',
        "facebook": r'href=["\']([^"\']*facebook\.com/[^"\']+)',
        "youtube": r'href=["\']([^"\']*youtube\.com/[^"\']+)',
        "github": r'href=["\']([^"\']*github\.com/[^"\']+)',
        "tiktok": r'href=["\']([^"\']*tiktok\.com/[^"\']+)',
    }
    for platform, pattern in social_patterns.items():
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            socials[platform] = match.group(1)
    return socials


def _extract_ctas(html: str) -> List[str]:
    """Extract call-to-action button/link text."""
    cta_patterns = [
        r'<(?:a|button)[^>]*class=["\'][^"\']*(?:btn|button|cta)[^"\']*["\'][^>]*>(.*?)</(?:a|button)>',
        r'<(?:a|button)[^>]*>(.*?(?:book|schedule|contact|get started|free|demo|trial|consult|call|quote|pricing).*?)</(?:a|button)>',
    ]
    ctas = []
    for pattern in cta_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE | re.DOTALL)
        for m in matches:
            clean = re.sub(r"<[^>]+>", "", m).strip()
            if clean and len(clean) < 100:
                ctas.append(clean)
    return list(dict.fromkeys(ctas))[:15]


def _extract_page_links(html: str, base_url: str) -> List[str]:
    """Extract internal page links (nav structure)."""
    matches = re.findall(r'href=["\']([^"\'#]+)', html)
    domain = urlparse(base_url).netloc
    internal = []
    for href in matches:
        full_url = urljoin(base_url, href)
        if urlparse(full_url).netloc == domain:
            internal.append(full_url)
    return list(dict.fromkeys(internal))[:30]


def _extract_headings(html: str) -> List[Dict[str, str]]:
    """Extract H1-H3 headings."""
    headings = []
    for level in ["h1", "h2", "h3"]:
        matches = re.findall(
            rf"<{level}[^>]*>(.*?)</{level}>", html, re.IGNORECASE | re.DOTALL
        )
        for m in matches:
            clean = re.sub(r"<[^>]+>", "", m).strip()
            if clean:
                headings.append({"level": level, "text": clean[:200]})
    return headings[:30]


def _scrape_site(url: str) -> Dict[str, Any]:
    """Full scrape of a single URL."""
    html = _fetch_page(url)
    if not html:
        return {"url": url, "error": "Failed to fetch page"}

    text = _extract_text(html)

    return {
        "url": url,
        "title": (re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL) or type("", (), {"group": lambda self, x: "Unknown"})()).group(1).strip()[:200],
        "tech_stack": _detect_tech_stack(html),
        "prices": _extract_prices(text),
        "emails": _extract_emails(text),
        "social_links": _extract_social_links(html, url),
        "ctas": _extract_ctas(html),
        "headings": _extract_headings(html),
        "internal_pages": _extract_page_links(html, url),
        "text_preview": text[:1500],
        "page_size_kb": round(len(html) / 1024, 1),
    }


@ToolRegistry.register("competitor_scraper")
class CompetitorScraperTool(BaseTool):
    """Scrape competitor websites for structured intelligence."""

    tool_id = "competitor_scraper"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="competitor_scraper",
            description=(
                "Directly scrape a competitor's website to extract pricing, "
                "tech stack, CTAs, social links, page structure, and content. "
                "Free — no API keys needed. Provide a URL or competitor name "
                "(uses pre-configured domains from competitor_monitor)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "URL to scrape. Or a competitor key (synkrai, automaly, "
                            "prosperspark, theautomators, thirdrock, prismetric, "
                            "pearllemon, blackcube, jadasquad, keystoneai) — "
                            "will auto-resolve to their domain."
                        ),
                    },
                    "pages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Additional sub-pages to scrape (e.g. ['/pricing', '/about', '/services']). "
                            "These are appended to the base URL."
                        ),
                    },
                },
                "required": ["url"],
            },
            category="intelligence",
            timeout_seconds=90.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        url_or_key = params.get("url", "")
        extra_pages: List[str] = params.get("pages", [])

        if not url_or_key:
            return ToolResult(
                tool_name="competitor_scraper",
                content="No URL or competitor key provided.",
                success=False,
            )

        # Resolve competitor keys to URLs
        from openjarvis.tools.competitor_monitor import _COMPETITORS

        competitor_key = url_or_key.lower().strip()
        if competitor_key in _COMPETITORS:
            profile = _COMPETITORS[competitor_key]
            base_url = f"https://{profile['domain']}"
            name = profile["name"]
        elif not url_or_key.startswith("http"):
            base_url = f"https://{url_or_key}"
            name = url_or_key
        else:
            base_url = url_or_key
            name = urlparse(url_or_key).netloc

        # Scrape main page
        urls_to_scrape = [base_url]
        for page in extra_pages:
            if page.startswith("http"):
                urls_to_scrape.append(page)
            else:
                urls_to_scrape.append(base_url.rstrip("/") + "/" + page.lstrip("/"))

        all_results = []
        for scrape_url in urls_to_scrape:
            result = _scrape_site(scrape_url)
            all_results.append(result)

        # Format output
        lines = [f"## {name} — Website Intelligence\n"]

        for result in all_results:
            if "error" in result:
                lines.append(f"### {result['url']}\nError: {result['error']}\n")
                continue

            lines.append(f"### {result['url']}")
            lines.append(f"**Title:** {result.get('title', 'N/A')}")
            lines.append(f"**Page size:** {result.get('page_size_kb', 0)} KB\n")

            tech = result.get("tech_stack", [])
            if tech:
                lines.append(f"**Tech Stack:** {', '.join(tech)}")

            prices = result.get("prices", [])
            if prices:
                lines.append(f"**Pricing Found:** {', '.join(prices)}")

            ctas = result.get("ctas", [])
            if ctas:
                lines.append(f"**CTAs:** {' | '.join(ctas[:8])}")

            emails = result.get("emails", [])
            if emails:
                lines.append(f"**Emails:** {', '.join(emails)}")

            socials = result.get("social_links", {})
            if socials:
                social_str = ", ".join(f"{k}: {v}" for k, v in socials.items())
                lines.append(f"**Socials:** {social_str}")

            headings = result.get("headings", [])
            if headings:
                lines.append("\n**Page Structure (headings):**")
                for h in headings[:15]:
                    lines.append(f"  {h['level'].upper()}: {h['text']}")

            text_preview = result.get("text_preview", "")
            if text_preview:
                lines.append(f"\n**Content Preview:**\n{text_preview[:800]}...")

            lines.append("\n---\n")

        content = "\n".join(lines)
        return ToolResult(
            tool_name="competitor_scraper",
            content=content,
            success=True,
            metadata={
                "name": name,
                "urls_scraped": len(all_results),
                "errors": sum(1 for r in all_results if "error" in r),
            },
        )


__all__ = ["CompetitorScraperTool"]
