"""Shared LLM helper for business tools that need to generate content.

Calls the Anthropic API directly using the API key from cloud-keys.env.
Falls back gracefully if the SDK or key is unavailable.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

_log = logging.getLogger(__name__)


def _load_cloud_keys() -> None:
    """Load API keys from ~/.openjarvis/cloud-keys.env if not already set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return

    from pathlib import Path

    keys_file = Path.home() / ".openjarvis" / "cloud-keys.env"
    if not keys_file.exists():
        return

    for line in keys_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def generate(
    prompt: str,
    system_prompt: str = "You are a helpful assistant. Output only valid JSON.",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> Optional[str]:
    """Generate text using the Anthropic API. Returns content string or None."""
    _load_cloud_keys()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        _log.warning("No ANTHROPIC_API_KEY available for LLM generation")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        if response.content:
            return response.content[0].text
        return None

    except ImportError:
        _log.warning("anthropic SDK not installed")
        return None
    except Exception as exc:
        _log.warning("LLM generation failed: %s", exc)
        return None
