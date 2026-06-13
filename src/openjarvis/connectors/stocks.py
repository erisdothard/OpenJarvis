# ruff: noqa: E501
"""Stock market connector — top indices for daily briefings.

Uses Yahoo Finance's public chart API (no API key required).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import httpx

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.registry import ConnectorRegistry

_DEFAULT_SYMBOLS = [
    "^GSPC",  # S&P 500
    "^DJI",   # Dow Jones Industrial Average
    "^IXIC",  # NASDAQ Composite
]

_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def _fetch_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetch current quote data for a single symbol."""
    try:
        resp = httpx.get(
            _CHART_URL.format(symbol=symbol),
            params={"range": "1d", "interval": "1d"},
            headers=_HEADERS,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
        if not meta.get("regularMarketPrice"):
            return None
        return meta
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        return None


@ConnectorRegistry.register("stocks")
class StocksConnector(BaseConnector):
    """Fetch top stock market indices for daily briefings."""

    connector_id = "stocks"
    display_name = "Stock Market"
    auth_type = "local"

    def __init__(self, *, symbols: Optional[List[str]] = None) -> None:
        self._symbols = symbols or _DEFAULT_SYMBOLS
        self._status = SyncStatus()

    def is_connected(self) -> bool:
        return True  # No credentials needed

    def disconnect(self) -> None:
        pass

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None
    ) -> Iterator[Document]:
        """Yield Documents for each tracked symbol with price and change data."""
        now = datetime.now(tz=timezone.utc)

        for symbol in self._symbols:
            meta = _fetch_quote(symbol)
            if not meta:
                continue

            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("chartPreviousClose", meta.get("previousClose", 0))
            name = meta.get("shortName") or meta.get("longName") or symbol

            change = price - prev_close if prev_close else 0
            pct = (change / prev_close * 100) if prev_close else 0
            direction = "up" if change > 0 else "down" if change < 0 else "flat"
            sign = "+" if change > 0 else ""

            summary = (
                f"{name}: {price:,.2f} ({sign}{change:,.2f}, {sign}{pct:.2f}%) — {direction}"
            )

            yield Document(
                doc_id=f"stocks:{symbol}",
                source="stocks",
                doc_type="quote",
                title=summary,
                content=summary,
                timestamp=now,
                metadata={
                    "symbol": symbol,
                    "price": price,
                    "change": round(change, 2),
                    "change_pct": round(pct, 2),
                    "prev_close": prev_close,
                    "name": name,
                    "direction": direction,
                },
            )

        self._status.state = "idle"
        self._status.last_sync = now

    def sync_status(self) -> SyncStatus:
        return self._status
