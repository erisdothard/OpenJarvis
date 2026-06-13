"""Lightweight tool filtering — select relevant tools per request based on query content."""

from __future__ import annotations

import re
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Core tools — always included regardless of query content.
# These are fundamental reasoning, memory, and utility tools that are broadly
# useful for almost any request.
# ---------------------------------------------------------------------------

_CORE_TOOL_NAMES = frozenset({
    "think",          # reasoning — category: reasoning
    "retrieval",      # memory search — category: memory
    "memory_manage",  # memory read/write — category: memory
    "user_profile_manage",  # user context — category: memory
    "calculator",     # math/arithmetic — category: math
    "llm",            # sub-LLM calls — category: inference
})

# ---------------------------------------------------------------------------
# Category → keyword pattern mapping.
# Keywords are matched case-insensitively against the full query text.
# Matching ANY keyword in a group activates that category's tools.
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: List[tuple[str, List[str]]] = [
    # communication: email, messaging, SMS, Slack, notifications
    (
        "communication",
        [
            r"\bemail\b", r"\bmail\b", r"\bsend\b", r"\bmessage\b",
            r"\bnotif", r"\bslack\b", r"\bsms\b", r"\btext\b",
            r"\bcontact\b", r"\binbox\b", r"\bdraft\b",
        ],
    ),
    # productivity: calendar, scheduling, tasks, reminders
    (
        "productivity",
        [
            r"\bcalendar\b", r"\bschedule\b", r"\bmeeting\b", r"\bevent\b",
            r"\bremind", r"\bappointment\b", r"\btask\b", r"\btodo\b",
            r"\bto-do\b", r"\bdeadline\b",
        ],
    ),
    # filesystem: file and directory operations
    (
        "filesystem",
        [
            r"\bfile\b", r"\bfolder\b", r"\bdirector", r"\bread\b",
            r"\bwrite\b", r"\bsave\b", r"\bopen\b", r"\bcreate\b",
            r"\bdelete\b", r"\bpath\b", r"\bdownload\b", r"\bupload\b",
            r"\bpatch\b",
        ],
    ),
    # storage: key-value and blob storage
    (
        "storage",
        [
            r"\bstore\b", r"\bstorage\b", r"\bsave\b", r"\bpersist\b",
            r"\bcache\b", r"\blookup\b",
        ],
    ),
    # search: web and knowledge search
    (
        "search",
        [
            r"\bsearch\b", r"\bfind\b", r"\blook up\b", r"\bweb\b",
            r"\bbrowse\b", r"\burl\b", r"\bwebsite\b", r"\bgoogle\b",
            r"\bresearch\b",
        ],
    ),
    # browser: headless browser automation
    (
        "browser",
        [
            r"\bbrowser\b", r"\bchrome\b", r"\bnavigate\b", r"\bscreenshot\b",
            r"\bclick\b", r"\bfill\b", r"\bform\b", r"\bscrape\b",
            r"\burl\b", r"\bwebpage\b",
        ],
    ),
    # code: code execution and interpretation
    (
        "code",
        [
            r"\bcode\b", r"\brun\b", r"\bexecute\b", r"\bscript\b",
            r"\bdebug\b", r"\btest\b", r"\bbuild\b", r"\bpython\b",
            r"\bjavascript\b", r"\bprogram\b", r"\brepl\b",
        ],
    ),
    # system: shell execution
    (
        "system",
        [
            r"\bshell\b", r"\bterminal\b", r"\bcommand\b", r"\bbash\b",
            r"\bexec\b", r"\bprocess\b", r"\bsystem\b",
        ],
    ),
    # vcs: git and version control
    (
        "vcs",
        [
            r"\bgit\b", r"\bcommit\b", r"\bpush\b", r"\bpull\b",
            r"\bbranch\b", r"\bmerge\b", r"\brepo\b", r"\brepository\b",
            r"\bversion control\b",
        ],
    ),
    # database: SQL and data queries
    (
        "database",
        [
            r"\bsql\b", r"\bdatabase\b", r"\bquery\b", r"\bdb\b",
            r"\btable\b", r"\brecord\b", r"\brow\b",
        ],
    ),
    # media: images, audio, PDF
    (
        "media",
        [
            r"\bimage\b", r"\bphoto\b", r"\bpicture\b", r"\bvideo\b",
            r"\baudio\b", r"\bmusic\b", r"\bplay\b", r"\bpdf\b",
            r"\bdocument\b", r"\bfile\b",
        ],
    ),
    # audio TTS
    (
        "audio",
        [
            r"\bspeak\b", r"\bvoice\b", r"\btts\b", r"\btext.to.speech\b",
            r"\bsay\b", r"\bread aloud\b", r"\baudio\b",
        ],
    ),
    # knowledge graph
    (
        "knowledge_graph",
        [
            r"\bknowledge\b", r"\bgraph\b", r"\bentit", r"\brelation",
            r"\bonto", r"\bfact\b",
        ],
    ),
    # knowledge (SQL-based)
    (
        "knowledge",
        [
            r"\bknowledge\b", r"\bnote\b", r"\blearn\b", r"\bremember\b",
            r"\brecall\b", r"\bfact\b",
        ],
    ),
    # social media publishing
    (
        "social",
        [
            r"\btweet\b", r"\btwitter\b", r"\bx\.com\b", r"\bpost\b",
            r"\blinkedin\b", r"\binstagram\b", r"\bsocial\b",
            r"\bpublish\b", r"\bfeed\b",
        ],
    ),
    # content generation / drafting
    (
        "content",
        [
            r"\bcontent\b", r"\bdraft\b", r"\bwrite\b", r"\barticle\b",
            r"\bblog\b", r"\bcopy\b", r"\bpost\b", r"\bremix\b",
        ],
    ),
    # intelligence / competitor monitoring
    (
        "intelligence",
        [
            r"\bcompetitor\b", r"\bmonitor\b", r"\bad library\b",
            r"\bopportunity\b", r"\bmarket\b", r"\bintelligence\b",
            r"\bscrape\b",
        ],
    ),
    # job search
    (
        "job_search",
        [
            r"\bjob\b", r"\bcareer\b", r"\bresume\b", r"\blinkedin\b",
            r"\bappl", r"\binterview\b", r"\bjobright\b", r"\bhiring\b",
            r"\bposition\b", r"\bopening\b",
        ],
    ),
    # network / HTTP requests
    (
        "network",
        [
            r"\bhttp\b", r"\bapi\b", r"\brequest\b", r"\bcurl\b",
            r"\bfetch\b", r"\brest\b", r"\bwebhook\b", r"\bget\b",
            r"\bpost\b",
        ],
    ),
    # channel tools (internal messaging channels)
    (
        "channel",
        [
            r"\bchannel\b", r"\bbroadcast\b", r"\bpublish\b",
        ],
    ),
    # data / digest collection
    (
        "data",
        [
            r"\bdigest\b", r"\bcollect\b", r"\baggregat", r"\bdata\b",
            r"\bpipeline\b", r"\bingest\b",
        ],
    ),
    # agents
    (
        "agents",
        [
            r"\bagent\b", r"\borchestrat", r"\bdelegate\b", r"\bsubagent\b",
            r"\bsub-agent\b",
        ],
    ),
    # skill management
    (
        "skill",
        [
            r"\bskill\b", r"\bcapabilit", r"\blearn\b",
        ],
    ),
    # proactive / scheduled tasks
    (
        "proactive",
        [
            r"\bschedul", r"\bremind", r"\bautomati", r"\bproactive\b",
            r"\btrigger\b", r"\bwatch\b", r"\bmonitor\b",
        ],
    ),
    # template rendering
    (
        "template",
        [
            r"\btemplate\b", r"\brender\b", r"\bformat\b", r"\bjinja\b",
        ],
    ),
]

# Maximum number of tools to send per request (safety cap).
_MAX_TOOLS = 20


def _extract_query_text(messages: list[Dict[str, Any]]) -> str:
    """Pull the last user message from an OpenAI-format message list."""
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            return content if isinstance(content, str) else str(content)
    return ""


def _matched_categories(query: str) -> set[str]:
    """Return the set of category names whose keywords appear in *query*."""
    if not query:
        return set()
    matched: set[str] = set()
    query_lower = query.lower()
    for category, patterns in _CATEGORY_KEYWORDS:
        for pat in patterns:
            if re.search(pat, query_lower):
                matched.add(category)
                break  # one match is enough for this category
    return matched


def filter_tools_for_query(
    query: str,
    all_tools: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return a filtered subset of *all_tools* relevant to *query*.

    Algorithm
    ---------
    1. Always include the small "core" set (reasoning, memory, math).
    2. Match query keywords to additional tool categories.
    3. If no additional categories matched, fall back to the full tool list
       (safety net — better to send everything than to miss a needed tool).
    4. Cap the result at ``_MAX_TOOLS`` (core tools never count against the cap).

    Parameters
    ----------
    query:
        The user's message text.
    all_tools:
        Full list of tools in OpenAI function-calling format
        (``[{"type": "function", "function": {"name": ..., ...}}, ...]``).

    Returns
    -------
    List of tool dicts in OpenAI format.
    """
    if not all_tools:
        return all_tools

    # Build a name → tool dict for fast lookup.
    tool_by_name: Dict[str, Dict[str, Any]] = {}
    for tool in all_tools:
        name = tool.get("function", {}).get("name", "")
        if name:
            tool_by_name[name] = tool

    # Step 1: collect core tools (always included).
    core: List[Dict[str, Any]] = [
        tool_by_name[n] for n in _CORE_TOOL_NAMES if n in tool_by_name
    ]

    # Step 2: determine which categories to include.
    matched_cats = _matched_categories(query)

    if not matched_cats:
        # No category keywords found — fall back to full list.
        logger_msg = "tool_filter: no category match, returning all %d tools"
        _log(logger_msg, len(all_tools))
        return all_tools

    # Step 3: build the category-filtered list from the ToolRegistry.
    # We need the category from ToolSpec, which means instantiating tools.
    # Do this lazily and cache the result per-process.
    category_filtered = _tools_for_categories(matched_cats, all_tools)

    # Merge: core ∪ category_filtered, preserving order (core first).
    core_names = {t["function"]["name"] for t in core}
    extras = [t for t in category_filtered if t["function"]["name"] not in core_names]

    # Cap extras so total ≤ _MAX_TOOLS.
    cap = max(0, _MAX_TOOLS - len(core))
    extras = extras[:cap]

    result = core + extras

    _log(
        "tool_filter: query=%r → cats=%s → %d/%d tools selected",
        query[:60],
        sorted(matched_cats),
        len(result),
        len(all_tools),
    )

    return result


# ---------------------------------------------------------------------------
# Category-to-tool mapping — built once from the live ToolRegistry.
# ---------------------------------------------------------------------------

_category_cache: Dict[str, List[str]] | None = None  # category → [tool_name, ...]


def _build_category_cache() -> Dict[str, List[str]]:
    """Walk ToolRegistry once and return a mapping of category → [tool_names]."""
    import openjarvis.tools  # noqa: F401  # trigger @ToolRegistry.register decorators
    from openjarvis.core.registry import ToolRegistry

    mapping: Dict[str, List[str]] = {}
    for _key, tool_cls in ToolRegistry.items():
        try:
            instance = tool_cls() if callable(tool_cls) else tool_cls
            spec = instance.spec if hasattr(instance, "spec") else None
            if spec is None or not spec.category:
                continue
            mapping.setdefault(spec.category, []).append(spec.name)
        except Exception:
            continue
    return mapping


def _get_category_cache() -> Dict[str, List[str]]:
    global _category_cache
    if _category_cache is None:
        _category_cache = _build_category_cache()
    return _category_cache


def _tools_for_categories(
    categories: set[str],
    all_tools: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return the subset of *all_tools* whose category is in *categories*."""
    cache = _get_category_cache()
    # Collect all tool names in the matched categories.
    wanted_names: set[str] = set()
    for cat in categories:
        wanted_names.update(cache.get(cat, []))

    tool_by_name: Dict[str, Dict[str, Any]] = {
        t["function"]["name"]: t for t in all_tools if t.get("function", {}).get("name")
    }
    return [tool_by_name[n] for n in wanted_names if n in tool_by_name]


# ---------------------------------------------------------------------------
# Internal logger
# ---------------------------------------------------------------------------

import logging as _logging

_logger = _logging.getLogger("openjarvis.server.tool_filter")


def _log(msg: str, *args: Any) -> None:
    _logger.debug(msg, *args)
