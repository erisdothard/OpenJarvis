"""Budget connector — personal finance tracking via local CSV/JSON or Plaid API.

Supports two modes:
1. **Local file** — reads transactions from a CSV or JSON file exported from
   your bank. Zero dependencies, zero API keys.
2. **Plaid API** (optional) — connects to bank accounts via Plaid for
   automatic transaction sync. Requires PLAID_CLIENT_ID and PLAID_SECRET
   environment variables.

All transactions are normalized to the universal Document schema.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from openjarvis.connectors._stubs import BaseConnector, Document, SyncStatus
from openjarvis.core.config import DEFAULT_CONFIG_DIR
from openjarvis.core.registry import ConnectorRegistry
from openjarvis.tools._stubs import ToolSpec

logger = logging.getLogger(__name__)

_DEFAULT_TRANSACTIONS_DIR = DEFAULT_CONFIG_DIR / "connectors" / "budget"
_SUPPORTED_EXTENSIONS = {".csv", ".json"}


@dataclass
class Transaction:
    """Intermediate representation of a financial transaction."""

    id: str
    date: datetime
    amount: float
    description: str
    category: str = ""
    account: str = ""
    merchant: str = ""
    pending: bool = False

    def to_document(self) -> Document:
        sign = "+" if self.amount > 0 else ""
        return Document(
            doc_id=f"budget:{self.id}",
            source="budget",
            doc_type="transaction",
            title=self.description,
            content=(
                f"{self.date.strftime('%Y-%m-%d')} | {sign}{self.amount:.2f} | "
                f"{self.category or 'uncategorized'} | {self.description}"
            ),
            timestamp=self.date,
            metadata={
                "amount": self.amount,
                "category": self.category,
                "account": self.account,
                "merchant": self.merchant,
                "pending": self.pending,
            },
        )


def _parse_csv_transactions(path: Path) -> Iterator[Transaction]:
    """Parse transactions from a CSV file.

    Expected columns (flexible matching): date, amount, description/memo,
    category (optional), account (optional).
    """
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return

        # Flexible column name matching
        col_map: Dict[str, str] = {}
        for col in reader.fieldnames:
            lower = col.lower().strip()
            if lower in ("date", "transaction date", "posted date"):
                col_map["date"] = col
            elif lower in ("amount", "value", "total"):
                col_map["amount"] = col
            elif lower in (
                "description",
                "memo",
                "name",
                "payee",
                "transaction description",
            ):
                col_map["description"] = col
            elif lower in ("category", "type", "transaction type"):
                col_map["category"] = col
            elif lower in ("account", "account name", "source"):
                col_map["account"] = col

        if "date" not in col_map or "amount" not in col_map:
            logger.warning("CSV %s missing required date/amount columns", path)
            return

        for i, row in enumerate(reader):
            try:
                date_str = row[col_map["date"]].strip()
                # Try common date formats
                date = None
                for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
                    try:
                        date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                if date is None:
                    logger.debug("Skipping row %d: unparseable date %r", i, date_str)
                    continue

                amount_str = (
                    row[col_map["amount"]]
                    .strip()
                    .replace("$", "")
                    .replace(",", "")
                )
                amount = float(amount_str)

                desc = row.get(col_map.get("description", ""), "").strip()
                category = row.get(col_map.get("category", ""), "").strip()
                account = row.get(col_map.get("account", ""), "").strip()

                yield Transaction(
                    id=f"csv-{path.stem}-{i}",
                    date=date,
                    amount=amount,
                    description=desc or "Unknown",
                    category=category,
                    account=account,
                )
            except (KeyError, ValueError) as exc:
                logger.debug("Skipping row %d in %s: %s", i, path, exc)
                continue


def _parse_json_transactions(path: Path) -> Iterator[Transaction]:
    """Parse transactions from a JSON file (array of objects)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("transactions", [])

    for i, item in enumerate(items):
        try:
            date_str = item.get("date", "")
            date = datetime.fromisoformat(date_str) if date_str else datetime.now()
            yield Transaction(
                id=f"json-{path.stem}-{i}",
                date=date,
                amount=float(item.get("amount", 0)),
                description=item.get("description", item.get("name", "Unknown")),
                category=item.get("category", ""),
                account=item.get("account", ""),
                merchant=item.get("merchant", ""),
                pending=item.get("pending", False),
            )
        except (ValueError, TypeError) as exc:
            logger.debug("Skipping item %d in %s: %s", i, path, exc)
            continue


def _try_plaid_sync(
    *, since: Optional[datetime] = None,
) -> Optional[Iterator[Transaction]]:
    """Attempt Plaid API sync. Returns None if Plaid is not configured."""
    client_id = os.environ.get("PLAID_CLIENT_ID")
    secret = os.environ.get("PLAID_SECRET")
    access_token = os.environ.get("PLAID_ACCESS_TOKEN")

    if not (client_id and secret and access_token):
        return None

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; Plaid sync unavailable")
        return None

    plaid_env = os.environ.get("PLAID_ENV", "sandbox")
    base_url = {
        "sandbox": "https://sandbox.plaid.com",
        "development": "https://development.plaid.com",
        "production": "https://production.plaid.com",
    }.get(plaid_env, "https://sandbox.plaid.com")

    def _fetch() -> Iterator[Transaction]:
        body: Dict[str, Any] = {
            "client_id": client_id,
            "secret": secret,
            "access_token": access_token,
        }
        if since:
            body["start_date"] = since.strftime("%Y-%m-%d")
            body["end_date"] = datetime.now().strftime("%Y-%m-%d")
        else:
            body["start_date"] = "2020-01-01"
            body["end_date"] = datetime.now().strftime("%Y-%m-%d")

        resp = httpx.post(
            f"{base_url}/transactions/get",
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for i, txn in enumerate(data.get("transactions", [])):
            yield Transaction(
                id=f"plaid-{txn.get('transaction_id', i)}",
                date=datetime.strptime(txn["date"], "%Y-%m-%d"),
                amount=-txn.get("amount", 0),  # Plaid: positive = debit
                description=txn.get("name", "Unknown"),
                category=", ".join(txn.get("category", [])),
                account=txn.get("account_id", ""),
                merchant=txn.get("merchant_name", ""),
                pending=txn.get("pending", False),
            )

    return _fetch()


@ConnectorRegistry.register("budget")
class BudgetConnector(BaseConnector):
    """Personal finance connector — local CSV/JSON files or Plaid API."""

    connector_id = "budget"
    display_name = "Budget & Finances"
    auth_type = "local"  # or "api" when Plaid is configured

    def __init__(self, transactions_dir: str = "") -> None:
        self._dir = Path(transactions_dir) if transactions_dir else _DEFAULT_TRANSACTIONS_DIR
        self._status = SyncStatus()

    def is_connected(self) -> bool:
        """Connected if local files exist or Plaid env vars are set."""
        has_local = self._dir.exists() and any(
            f.suffix in _SUPPORTED_EXTENSIONS for f in self._dir.iterdir()
        ) if self._dir.exists() else False
        has_plaid = bool(os.environ.get("PLAID_ACCESS_TOKEN"))
        return has_local or has_plaid

    def disconnect(self) -> None:
        """Nothing to revoke for local files."""
        pass

    def sync(
        self, *, since: Optional[datetime] = None, cursor: Optional[str] = None,
    ) -> Iterator[Document]:
        """Yield transactions as Documents from all available sources."""
        self._status = SyncStatus(state="syncing")
        count = 0

        # Try Plaid first
        plaid_iter = _try_plaid_sync(since=since)
        if plaid_iter is not None:
            for txn in plaid_iter:
                if since and txn.date < since:
                    continue
                count += 1
                yield txn.to_document()

        # Then local files
        if self._dir.exists():
            for path in sorted(self._dir.iterdir()):
                if path.suffix == ".csv":
                    parser = _parse_csv_transactions(path)
                elif path.suffix == ".json":
                    parser = _parse_json_transactions(path)
                else:
                    continue

                for txn in parser:
                    if since and txn.date < since:
                        continue
                    count += 1
                    yield txn.to_document()

        self._status = SyncStatus(
            state="idle",
            items_synced=count,
            last_sync=datetime.now(),
        )

    def sync_status(self) -> SyncStatus:
        return self._status

    def mcp_tools(self) -> List[ToolSpec]:
        """Expose budget query tools to agents."""
        return [
            ToolSpec(
                name="budget_summary",
                description=(
                    "Get a spending summary for a time period. "
                    "Returns totals by category, top merchants, and net cash flow."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "days_back": {
                            "type": "integer",
                            "description": "Number of days to look back (default: 30)",
                            "default": 30,
                        },
                    },
                },
            ),
            ToolSpec(
                name="budget_search",
                description="Search transactions by description, category, or amount range.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search text to match against descriptions",
                        },
                        "category": {
                            "type": "string",
                            "description": "Filter by category name",
                        },
                        "min_amount": {
                            "type": "number",
                            "description": "Minimum transaction amount",
                        },
                        "max_amount": {
                            "type": "number",
                            "description": "Maximum transaction amount",
                        },
                    },
                },
            ),
        ]
