"""Shared brand identity loader for business tools."""
from pathlib import Path
from functools import lru_cache

_BRAND_PATH = Path.home() / ".openjarvis" / "brand.md"


@lru_cache(maxsize=1)
def load_brand() -> str:
    """Load brand identity from ~/.openjarvis/brand.md."""
    if _BRAND_PATH.exists():
        return _BRAND_PATH.read_text().strip()
    return ""


def brand_context() -> str:
    """Return brand identity formatted for prompt injection."""
    brand = load_brand()
    if brand:
        return f"\n\n## Brand Context\n{brand}\n"
    return ""
