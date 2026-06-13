# ruff: noqa: E501
"""Digest collection tool — fetches recent data from configured connectors."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry, ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

# ---------------------------------------------------------------------------
# Section definitions: ordered list of (section_name, connector_ids)
# ---------------------------------------------------------------------------

_SECTION_ORDER: List[tuple] = [
    ("HEALTH", {"oura", "apple_health", "strava"}),
    (
        "MESSAGES",
        {
            "gmail",
            "gmail_syntra",
            "gmail_imap",
            "google_tasks",
            "slack",
            "imessage",
            "whatsapp",
            "outlook",
            "notion",
            "github_notifications",
        },
    ),
    ("CALENDAR", {"gcalendar", "apple_calendar"}),
    ("STOCKS", {"stocks"}),
    ("WORLD", {"weather", "hackernews", "news_rss"}),
    ("MUSIC", {"spotify", "apple_music"}),
    ("SOCIAL", {"facebook", "instagram", "linkedin"}),
]

_CONNECTOR_TO_SECTION: Dict[str, str] = {}
for _section_name, _ids in _SECTION_ORDER:
    for _cid in _ids:
        _CONNECTOR_TO_SECTION[_cid] = _section_name


# ---------------------------------------------------------------------------
# Per-connector human-readable formatters
# ---------------------------------------------------------------------------


def _format_duration(seconds: float) -> str:
    """Format seconds as 'Xh Ym'."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _time_ago(ts: datetime) -> str:
    """Return a human-readable relative time like '2h ago' or '15m ago'."""
    now = datetime.now(tz=timezone.utc)
    ts_aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    delta = now - ts_aware
    total_seconds = max(0, int(delta.total_seconds()))
    if total_seconds < 60:
        return "just now"
    if total_seconds < 3600:
        return f"{total_seconds // 60}m ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h ago"
    days = total_seconds // 86400
    return f"{days}d ago"


def _format_date(ts: datetime) -> str:
    """Format a datetime as 'April 1' style."""
    return ts.strftime("%B %-d") if hasattr(ts, "strftime") else str(ts)


def _format_time(ts: datetime) -> str:
    """Format a datetime as '10:30 AM' style."""
    return ts.strftime("%-I:%M %p") if hasattr(ts, "strftime") else str(ts)


def _parse_content_json(doc: Document) -> Dict[str, Any]:
    """Try to parse the document content as JSON; return {} on failure."""
    try:
        return json.loads(doc.content)
    except (json.JSONDecodeError, TypeError):
        return {}


def _format_oura(doc: Document) -> str:
    """Format an Oura Ring document into a readable line."""
    data = _parse_content_json(doc)
    data_type = doc.metadata.get("data_type", "")
    day = doc.metadata.get("day", "")
    day_str = day or _format_date(doc.timestamp)

    if data_type == "sleep":
        hr = data.get("average_heart_rate", "?")
        hrv = data.get("average_hrv", data.get("average_heart_rate_variability", "?"))
        total = data.get("total_sleep_duration")
        awake = data.get("awake_time")
        score = data.get("score")
        parts = []
        if score is not None:
            parts.append(f"score {score}")
        parts.append(f"avg HR {hr} bpm")
        if hrv != "?":
            parts.append(f"HRV {hrv}")
        if total is not None:
            parts.append(f"{_format_duration(total)} total sleep")
        if awake is not None:
            parts.append(f"awake {_format_duration(awake)}")
        return f"[oura] Sleep — {day_str}: {', '.join(parts)}"

    if data_type == "daily_readiness":
        score = data.get("score", "?")
        temp = data.get(
            "temperature_deviation",
            data.get("temperature_trend_deviation"),
        )
        line = f"[oura] Readiness — {day_str}: score {score}"
        if temp is not None:
            sign = "+" if temp >= 0 else ""
            line += f", temperature deviation {sign}{temp}"
        return line

    if data_type == "daily_activity":
        steps = data.get("steps", "?")
        cal = data.get("total_calories", data.get("active_calories", "?"))
        score = data.get("score")
        parts = []
        if score is not None:
            parts.append(f"score {score}")
        parts.append(f"steps {steps}")
        parts.append(f"calories {cal}")
        return f"[oura] Activity — {day_str}: {', '.join(parts)}"

    # Fallback for unknown Oura doc types
    return f"[oura] {doc.title}"


def _format_apple_health(doc: Document) -> str:
    """Format an Apple Health document."""
    return f"[apple_health] {doc.title}"


def _format_strava(doc: Document) -> str:
    """Format a Strava activity document."""
    return f"[strava] {doc.title}"


def _format_gmail(doc: Document) -> str:
    """Format a Gmail email document with triage context.

    Includes reply status, importance flag, thread depth, and a longer
    body preview so the LLM can make informed triage decisions.
    """
    sender = doc.author or "Unknown"
    subject = doc.title or "(no subject)"
    ago = _time_ago(doc.timestamp)

    # Triage metadata (set by _triage_email_threads, may be absent)
    replied = doc.metadata.get("_replied", False)
    importance = doc.metadata.get("_importance", "normal")
    thread_count = doc.metadata.get("_thread_count", 1)

    # Status tags
    tags: List[str] = []
    if importance == "high":
        tags.append("IMPORTANT")
    if replied:
        tags.append("REPLIED")
    elif "UNREAD" in doc.metadata.get("labels", []):
        tags.append("UNREAD")
    if thread_count > 1:
        tags.append(f"{thread_count} msgs in thread")

    tag_str = f" [{', '.join(tags)}]" if tags else ""

    # Longer body preview for important emails, shorter for low
    max_preview = 400 if importance == "high" else 200
    body = doc.content.replace("\n", " ").strip()[:max_preview] if doc.content else ""

    line = f'[gmail id={doc.doc_id}]{tag_str} From: {sender} — "{subject}" ({ago})'
    if body:
        line += f"\n  Preview: {body}"
    return line


def _format_gmail_imap(doc: Document) -> str:
    """Format a Gmail IMAP email document."""
    sender = doc.author or "Unknown"
    subject = doc.title or "(no subject)"
    ago = _time_ago(doc.timestamp)
    return f'[gmail id={doc.doc_id}] From: {sender} — "{subject}" ({ago})'


def _format_google_tasks(doc: Document) -> str:
    """Format a Google Tasks document."""
    title = doc.title or "Untitled Task"
    status = doc.metadata.get("status", "")
    due = doc.metadata.get("due", "")
    parts = [f"[google_tasks] {title}"]
    extras = []
    if due:
        extras.append(f"due {due}")
    if status == "completed":
        extras.append("completed")
    if extras:
        parts.append(f"({', '.join(extras)})")
    return " ".join(parts)


def _format_slack(doc: Document) -> str:
    """Format a Slack message document."""
    author = doc.author or "Unknown"
    channel = doc.metadata.get("channel", "")
    ago = _time_ago(doc.timestamp)
    snippet = doc.content[:150].replace("\n", " ").strip()
    content_preview = snippet if doc.content else ""
    prefix = f"[slack] #{channel}" if channel else "[slack]"
    line = f"{prefix} {author} ({ago})"
    if content_preview:
        line += f": {content_preview}"
    return line


def _format_imessage(doc: Document) -> str:
    """Format an iMessage document."""
    sender = doc.author or "Unknown"
    ago = _time_ago(doc.timestamp)
    snippet = doc.content[:150].replace("\n", " ").strip()
    content_preview = snippet if doc.content else ""
    line = f"[imessage] {sender} ({ago})"
    if content_preview:
        line += f": {content_preview}"
    return line


def _format_whatsapp(doc: Document) -> str:
    """Format a WhatsApp message document."""
    sender = doc.author or "Unknown"
    content_preview = doc.content[:80].replace("\n", " ").strip() if doc.content else ""
    return f"[whatsapp] {sender}: {content_preview}"


def _format_outlook(doc: Document) -> str:
    """Format an Outlook email document."""
    sender = doc.author or "Unknown"
    subject = doc.title or "(no subject)"
    ago = _time_ago(doc.timestamp)
    return f'[outlook] From: {sender} — "{subject}" ({ago})'


def _format_notion(doc: Document) -> str:
    """Format a Notion page document."""
    title = doc.title or "Untitled"
    ago = _time_ago(doc.timestamp)
    return f"[notion] {title} (updated {ago})"


def _format_gcalendar(doc: Document) -> str:
    """Format a Google Calendar event document."""
    title = doc.title or "(No title)"
    time_str = _format_time(doc.timestamp)
    # Try to extract duration from content
    duration_match = (
        re.search(r"When:\s*(.+?)$", doc.content, re.MULTILINE) if doc.content else None
    )
    time_range = ""
    if duration_match:
        when = duration_match.group(1).strip()
        # Extract just the times from the ISO strings
        parts = when.split(" – ")
        if len(parts) == 2:
            try:
                start_dt = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
                diff = end_dt - start_dt
                mins = int(diff.total_seconds() / 60)
                if mins >= 60:
                    hrs = mins // 60
                    remaining = mins % 60
                    duration = f"{hrs} hour" + ("s" if hrs > 1 else "")
                    if remaining:
                        duration += f" {remaining} min"
                else:
                    duration = f"{mins} min"
                time_range = f" ({duration})"
            except (ValueError, TypeError):
                pass
    return f"[gcalendar id={doc.doc_id}] {time_str} — {title}{time_range}"


def _format_apple_calendar(doc: Document) -> str:
    """Format an Apple Calendar event document."""
    title = doc.title or "(No title)"
    start = doc.metadata.get("start", "")
    end = doc.metadata.get("end", "")
    location = doc.metadata.get("location", "")
    calendar_name = doc.metadata.get("calendar", "")
    parts = [f"[apple_calendar] {title}"]
    if start:
        time_range = start
        if end:
            time_range += f" – {end}"
        parts.append(time_range)
    if location:
        parts.append(f"at {location}")
    if calendar_name:
        parts.append(f"({calendar_name})")
    return " | ".join(parts)


def _format_spotify(doc: Document) -> str:
    """Format a Spotify recently-played track — returns 'Track — Artist'."""
    # doc.title is already "Track — Artist" from the connector
    return doc.title


def _format_apple_music(doc: Document) -> str:
    """Format an Apple Music track — returns 'Track — Artist'."""
    # doc.title is already "Track — Artist" from the connector
    return doc.title


def _format_weather(doc: Document) -> str:
    """Format a weather document."""
    data = _parse_content_json(doc)
    if doc.doc_type == "current":
        temp = data.get("temp_f", "?")
        cond = data.get("conditions", "?")
        humidity = data.get("humidity", "?")
        return f"[weather] Current: {temp}°F, {cond}, humidity {humidity}%"
    if doc.doc_type == "forecast":
        return f"[weather] Forecast: {doc.content[:200]}"
    return f"[weather] {doc.title}"


def _format_github_notifications(doc: Document) -> str:
    """Format a GitHub notification."""
    reason = doc.metadata.get("reason", "")
    repo = doc.metadata.get("repo", "")
    title = doc.title or "(no title)"
    ago = _time_ago(doc.timestamp)
    reason_str = f" ({reason})" if reason else ""
    repo_str = f" in {repo}" if repo else ""
    return f"[github] {title}{repo_str}{reason_str} ({ago})"


def _format_stocks(doc: Document) -> str:
    """Format a stock quote document."""
    return f"[stocks] {doc.title}"


def _format_hackernews(doc: Document) -> str:
    """Format a Hacker News story."""
    score = doc.metadata.get("score", "?")
    comments = doc.metadata.get("descendants", "?")
    return f"[hackernews] {doc.title} (score {score}, {comments} comments)"


def _format_news_rss(doc: Document) -> str:
    """Format an RSS news item."""
    feed_name = doc.metadata.get("feed_name", "")
    prefix = f"[{feed_name}]" if feed_name else "[news]"
    description = doc.content[:150].replace("\n", " ").strip() if doc.content else ""
    line = f"{prefix} {doc.title}"
    if description:
        line += f" — {description}"
    return line


def _format_facebook(doc: Document) -> str:
    """Format a Facebook post or page info document."""
    ago = _time_ago(doc.timestamp)
    if doc.doc_type == "page_info":
        data = _parse_content_json(doc)
        followers = data.get("followers_count", 0)
        return f"[facebook] Page: {doc.title} ({followers} followers)"
    # Post
    likes = doc.metadata.get("like_count", 0)
    comments = doc.metadata.get("comments_count", 0)
    shares = doc.metadata.get("shares_count", 0)
    snippet = doc.content[:120].replace("\n", " ").strip() if doc.content else ""
    stats = f"{likes} likes, {comments} comments, {shares} shares"
    line = f"[facebook] Post ({ago}): {stats}"
    if snippet:
        line += f"\n  {snippet}"
    return line


def _format_instagram(doc: Document) -> str:
    """Format an Instagram post or comment document."""
    ago = _time_ago(doc.timestamp)
    if doc.doc_type == "comment":
        author = doc.author or "Unknown"
        text = doc.content[:100].replace("\n", " ").strip() if doc.content else ""
        return f"[instagram] Comment by {author} ({ago}): {text}"
    # Post
    likes = doc.metadata.get("like_count", 0)
    comments = doc.metadata.get("comments_count", 0)
    media_type = doc.metadata.get("media_type", "").lower()
    caption = doc.content[:120].replace("\n", " ").strip() if doc.content else ""
    type_label = f" [{media_type}]" if media_type else ""
    line = f"[instagram]{type_label} Post ({ago}): {likes} likes, {comments} comments"
    if caption:
        line += f"\n  {caption}"
    return line


def _format_linkedin(doc: Document) -> str:
    """Format a LinkedIn profile or post document."""
    if doc.doc_type == "profile":
        data = _parse_content_json(doc)
        name = data.get("name", doc.title or "LinkedIn Profile")
        email = data.get("email", "")
        email_str = f" ({email})" if email else ""
        return f"[linkedin] Profile: {name}{email_str}"
    if doc.doc_type == "post":
        ago = _time_ago(doc.timestamp)
        likes = doc.metadata.get("like_count", 0)
        comments = doc.metadata.get("comment_count", 0)
        snippet = doc.content[:120].replace("\n", " ").strip() if doc.content else ""
        stats = f"{likes} likes, {comments} comments"
        line = f"[linkedin] Post ({ago}): {stats}"
        if snippet:
            line += f"\n  {snippet}"
        return line
    return f"[linkedin] {doc.title}"


# Map connector IDs to their formatting functions
_FORMATTERS: Dict[str, Any] = {
    "oura": _format_oura,
    "apple_health": _format_apple_health,
    "strava": _format_strava,
    "gmail": _format_gmail,
    "gmail_syntra": _format_gmail,
    "gmail_imap": _format_gmail_imap,
    "google_tasks": _format_google_tasks,
    "slack": _format_slack,
    "imessage": _format_imessage,
    "whatsapp": _format_whatsapp,
    "outlook": _format_outlook,
    "notion": _format_notion,
    "gcalendar": _format_gcalendar,
    "apple_calendar": _format_apple_calendar,
    "weather": _format_weather,
    "github_notifications": _format_github_notifications,
    "stocks": _format_stocks,
    "hackernews": _format_hackernews,
    "news_rss": _format_news_rss,
    "spotify": _format_spotify,
    "apple_music": _format_apple_music,
    "facebook": _format_facebook,
    "instagram": _format_instagram,
    "linkedin": _format_linkedin,
}


def _format_doc(source: str, doc: Document) -> str:
    """Format a document using the source-specific formatter, with fallback."""
    formatter = _FORMATTERS.get(source)
    if formatter:
        try:
            return formatter(doc)
        except Exception:  # noqa: BLE001
            pass
    # Fallback: connector name + title
    return f"[{source}] {doc.title}"


def _format_music_section(
    collected_docs: Dict[str, List[Document]],
    music_connectors: set,
) -> List[str]:
    """Format music connectors as grouped lists instead of per-track lines."""
    lines: List[str] = []
    for source in sorted(music_connectors):
        docs = collected_docs.get(source, [])
        if not docs:
            continue
        tracks = []
        for doc in docs:
            tracks.append(doc.title)
        label = "Recently played" if source == "spotify" else "Library"
        lines.append(f"[{source}] {label}: {', '.join(tracks)}")
    return lines


_IMPORTANCE_KEYWORDS = re.compile(
    r"interview|offer|recruiter|hiring|deadline|urgent|asap|action\s*required"
    r"|overdue|payment|invoice|contract|signed|approval|confirm|respond|reply"
    r"|schedule|calendar\s*invite|meeting\s*request",
    re.IGNORECASE,
)

_SKIP_SENDERS = re.compile(
    r"noreply|no-reply|notifications?@|mailer-daemon|newsletters?@"
    r"|marketing@|promo(tions)?@|updates?@|digest@|info@|support@"
    r"|donotreply|automated@|notification@|news@|deals@|offers@"
    r"|rewards@|receipts?@|orders?@|shipping@|delivery@|confirm",
    re.IGNORECASE,
)

# Specific brands whose emails are never relevant to a daily briefing
_JUNK_BRANDS = re.compile(
    r"sezzle|domino'?s|pizza\s*hut|papa\s*john'?s|little\s*caesars"
    r"|uber\s*eats|doordash|grubhub|postmates|instacart"
    r"|groupon|retailmenot|slickdeals|woot"
    r"|starbucks|chipotle|chick-fil-a|mcdonald'?s|wendy'?s|taco\s*bell|subway"
    r"|target\.com|walmart|bestbuy|best\s*buy|kohls|kohl'?s|macy'?s|old\s*navy"
    r"|nike\.com|adidas|foot\s*locker|shein|temu"
    r"|affirm|klarna|afterpay|zip\s*pay"
    r"|mint\.com|credit\s*karma|nerdwallet"
    r"|spotify|netflix|hulu|disney\+?|paramount|peacock"
    r"|cash\s*app|venmo\.com|zelle"
    r"|lyft|uber(?![\w])|fandango|ticketmaster|stubhub"
    r"|bed\s*bath|wayfair|etsy\.com|ebay\.com"
    r"|unsubscribe.*click|view\s+in\s+browser",
    re.IGNORECASE,
)

# Google Calendar IDs for birthday/holiday calendars to skip
_BIRTHDAY_CALENDAR_IDS = re.compile(
    r"addressbook#contacts@group\.v\.calendar\.google\.com"
    r"|contacts@group\.v\.calendar\.google\.com"
    r"|#contacts@group"
    r"|en\.usa#holiday@group\.v\.calendar\.google\.com"
    r"|holiday@group",
    re.IGNORECASE,
)

_BIRTHDAY_TITLES = re.compile(
    r"'s\s+birthday$|birthday\s*-\s|^birthday:|'s\s+bday$",
    re.IGNORECASE,
)


def _triage_email_threads(docs: List[Document]) -> List[Document]:
    """Group Gmail docs by thread, detect reply status, and rank by importance.

    Returns one document per thread (the latest inbound message), annotated
    with metadata flags the formatter and LLM can use:
    - ``_replied``: True if the user's SENT message is the latest in the thread
    - ``_importance``: "high" | "normal" | "low"
    - ``_thread_count``: total messages in the thread within the window
    """
    from collections import defaultdict

    by_thread: Dict[str, List[Document]] = defaultdict(list)
    no_thread: List[Document] = []

    for doc in docs:
        tid = doc.thread_id
        if tid:
            by_thread[tid].append(doc)
        else:
            no_thread.append(doc)

    result: List[Document] = []

    for thread_docs in by_thread.values():
        thread_docs.sort(key=lambda d: d.timestamp)
        latest = thread_docs[-1]
        channel = latest.channel or ""
        labels = latest.metadata.get("labels", [])

        # Detect if user already replied: latest message is SENT
        user_replied = channel == "SENT" or "SENT" in labels

        # Find the latest INBOUND message to surface (skip if only sent)
        inbound = [d for d in thread_docs if (d.channel or "") != "SENT" and "SENT" not in d.metadata.get("labels", [])]
        representative = inbound[-1] if inbound else latest

        # Importance scoring
        subject = representative.title or ""
        body = representative.content or ""
        sender = representative.author or ""
        combined = f"{subject} {body[:500]} {sender}"

        if _SKIP_SENDERS.search(sender):
            importance = "low"
        elif _IMPORTANCE_KEYWORDS.search(combined):
            importance = "high"
        elif "UNREAD" in labels:
            importance = "normal"
        else:
            importance = "normal"

        # Annotate the representative doc with triage metadata
        representative.metadata["_replied"] = user_replied
        representative.metadata["_importance"] = importance
        representative.metadata["_thread_count"] = len(thread_docs)

        result.append(representative)

    # Solo messages (no thread_id)
    for doc in no_thread:
        sender = doc.author or ""
        subject = doc.title or ""
        body = doc.content or ""
        labels = doc.metadata.get("labels", [])
        combined = f"{subject} {body[:500]} {sender}"

        if _SKIP_SENDERS.search(sender):
            importance = "low"
        elif _IMPORTANCE_KEYWORDS.search(combined):
            importance = "high"
        else:
            importance = "normal"

        doc.metadata["_replied"] = False
        doc.metadata["_importance"] = importance
        doc.metadata["_thread_count"] = 1
        result.append(doc)

    # Sort: high importance first, then by timestamp descending
    priority_order = {"high": 0, "normal": 1, "low": 2}
    result.sort(
        key=lambda d: (
            priority_order.get(d.metadata.get("_importance", "normal"), 1),
            -d.timestamp.timestamp() if d.timestamp else 0,
        )
    )

    return result


def _filter_unanswered_threads(docs: List[Document]) -> List[Document]:
    """Keep only iMessage threads where the last message is NOT from the user.

    Groups by chat title, finds the most-recent message per chat, and returns
    only that message if ``author != "me"``.  Threads the user has already
    replied to are silently dropped.
    """
    from collections import defaultdict

    by_chat: Dict[str, List[Document]] = defaultdict(list)
    for doc in docs:
        by_chat[doc.title or doc.author or ""].append(doc)

    result: List[Document] = []
    for chat_docs in by_chat.values():
        latest = max(chat_docs, key=lambda d: d.timestamp)
        if latest.author != "me":
            result.append(latest)
    return result


def _filter_pending_invites(docs: List[Document]) -> List[Document]:
    """Keep only calendar events the user has not yet responded to."""
    pending: List[Document] = []
    for doc in docs:
        response_status = doc.metadata.get("response_status", "")
        # Include if status is explicitly needsAction, or if no status recorded
        # (connector may not populate it — safer to include than to drop)
        if response_status in ("needsAction", ""):
            pending.append(doc)
    return pending


@ToolRegistry.register("digest_collect")
class DigestCollectTool(BaseTool):
    """Collect recent data from multiple connectors for digest synthesis."""

    tool_id = "digest_collect"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="digest_collect",
            description=(
                "Fetch recent data from configured connectors (email, calendar, "
                "health, tasks, social, etc.) and return a structured, human-readable "
                "summary grouped by section (Health, Messages, Calendar, Music, Social) "
                "for digest synthesis."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of connector IDs to fetch from "
                            "(e.g., ['gmail', 'oura', 'gcalendar'])."
                        ),
                    },
                    "hours_back": {
                        "type": "number",
                        "description": "How many hours back to look (default: 24).",
                    },
                    "unacted_only": {
                        "type": "boolean",
                        "description": (
                            "When true, only return items the user has not yet acted on: "
                            "unread emails, unanswered iMessage threads, pending calendar invites."
                        ),
                    },
                    "seen_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "doc_ids to exclude (already queued or acted on).",
                    },
                },
                "required": ["sources"],
            },
            category="data",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        # Ensure connectors are registered
        import openjarvis.connectors  # noqa: F401

        sources: List[str] = params.get("sources", [])
        hours_back: float = params.get("hours_back", 24)
        unacted_only: bool = bool(params.get("unacted_only", False))
        seen_ids: set = set(params.get("seen_ids", []))
        since = datetime.now(tz=timezone.utc) - timedelta(hours=hours_back)

        # Collect raw documents per source
        collected_docs: Dict[str, List[Document]] = {}
        errors: List[str] = []

        for source in sources:
            if not ConnectorRegistry.contains(source):
                errors.append(f"Connector '{source}' not available")
                continue

            try:
                connector_cls = ConnectorRegistry.get(source)
                connector = connector_cls()

                if not connector.is_connected():
                    errors.append(
                        f"Connector '{source}' not connected (no credentials)"
                    )
                    continue

                # Cap per-source to avoid overwhelming the LLM context.
                # Gmail gets a higher cap because thread deduplication reduces it.
                max_per_source = 30 if source == "gmail" else 15
                docs: List[Document] = []

                sync_kwargs: Dict[str, Any] = {"since": since}
                if unacted_only and source == "gmail":
                    sync_kwargs["query_extra"] = "is:unread"

                for d in connector.sync(**sync_kwargs):
                    if d.doc_id not in seen_ids:
                        docs.append(d)
                    if len(docs) >= max_per_source:
                        break

                if unacted_only and source == "imessage":
                    docs = _filter_unanswered_threads(docs)

                if unacted_only and source == "gcalendar":
                    docs = _filter_pending_invites(docs)

                # Filter Google Calendar birthday/holiday events
                if source == "gcalendar":
                    docs = [
                        d for d in docs
                        if not _BIRTHDAY_CALENDAR_IDS.search(d.metadata.get("calendar_id", ""))
                        and not _BIRTHDAY_TITLES.search(d.title or "")
                    ]

                # Always triage Gmail threads: detect replies, rank importance
                if source in ("gmail", "gmail_syntra", "gmail_imap"):
                    docs = _triage_email_threads(docs)
                    # Drop junk brand emails entirely
                    docs = [
                        d for d in docs
                        if not _JUNK_BRANDS.search(
                            f"{d.author or ''} {d.title or ''} {(d.content or '')[:300]}"
                        )
                    ]

                collected_docs[source] = docs
            except Exception as exc:
                errors.append(f"Error fetching from '{source}': {exc}")

        # Group by section and build human-readable output
        summary_parts: List[str] = []
        for section_name, section_connectors in _SECTION_ORDER:
            # Gather all sources that belong to this section and have data
            section_sources = [
                s for s in sources if s in section_connectors and s in collected_docs
            ]
            if not section_sources:
                continue

            section_lines: List[str] = []

            if section_name == "MUSIC":
                # Music gets special grouped formatting
                section_lines = _format_music_section(
                    collected_docs, section_connectors
                )
            else:
                for source in section_sources:
                    for doc in collected_docs[source]:
                        section_lines.append(_format_doc(source, doc))

            if section_lines:
                summary_parts.append(f"=== {section_name} ===")
                summary_parts.extend(section_lines)
                summary_parts.append("")  # blank line between sections

        # Handle any connectors not in a known section (fallback)
        known_connectors = set()
        for _, cids in _SECTION_ORDER:
            known_connectors |= cids

        uncategorized_sources = [
            s for s in sources if s not in known_connectors and s in collected_docs
        ]
        if uncategorized_sources:
            summary_parts.append("=== OTHER ===")
            for source in uncategorized_sources:
                for doc in collected_docs[source]:
                    summary_parts.append(_format_doc(source, doc))
            summary_parts.append("")

        # Errors at the end, not inline
        if errors:
            summary_parts.append("=== ERRORS ===")
            summary_parts.extend(errors)

        return ToolResult(
            tool_name="digest_collect",
            content="\n".join(summary_parts),
            success=True,
            metadata={
                "sources_queried": sources,
                "sources_ok": list(collected_docs.keys()),
                "sources_failed": errors,
                "total_items": sum(len(v) for v in collected_docs.values()),
            },
        )
