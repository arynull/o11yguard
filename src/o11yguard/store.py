"""Local SQLite state: aggregated series records and the monthly budget.

State lives in ``O11YGUARD_DATA_DIR`` (or ``~/.o11yguard/``) as
``o11yguard.db``. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Self

DB_FILENAME = "o11yguard.db"
ENV_DATA_DIR = "O11YGUARD_DATA_DIR"

_SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS series ("
        "metric TEXT, "
        "tag_keys TEXT, "
        "series_count INTEGER, "
        "monthly_cost_usd REAL, "
        "source TEXT, "
        "first_seen TEXT, "
        "last_seen TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS budget ("
        "monthly_budget_usd REAL, "
        "warn_pct REAL DEFAULT 80, "
        "currency TEXT DEFAULT 'USD', "
        "updated_at TEXT)"
    ),
)


def data_dir() -> Path:
    """Return the state directory (created on demand).

    Uses ``O11YGUARD_DATA_DIR`` when set, otherwise ``~/.o11yguard/``.
    """
    raw = os.environ.get(ENV_DATA_DIR)
    path = Path(raw).expanduser() if raw else Path.home() / ".o11yguard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Read/write access to the local o11yguard database."""

    def __init__(self, path: Path | str | None = None) -> None:
        db_path = Path(path) if path is not None else data_dir() / DB_FILENAME
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        for statement in _SCHEMA:
            self.conn.execute(statement)
        self.conn.commit()

    def replace_batch(self, records: list[dict], batch: str) -> int:
        """Replace every row of ``batch`` with ``records``; returns rows written.

        ``records`` are dicts with ``metric``, ``tag_keys``, ``series`` and
        ``monthly_cost_usd``. ``tag_keys`` is stored as JSON.
        """
        now = _now_iso()
        with self.conn:
            self.conn.execute("DELETE FROM series WHERE source = ?", (batch,))
            for record in records:
                self.conn.execute(
                    "INSERT INTO series (metric, tag_keys, series_count,"
                    " monthly_cost_usd, source, first_seen, last_seen)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(record["metric"]),
                        json.dumps(list(record["tag_keys"])),
                        int(record["series"]),
                        float(record["monthly_cost_usd"]),
                        str(batch),
                        now,
                        now,
                    ),
                )
        return len(records)

    def all_series(self) -> list[dict]:
        """Return every stored series record as a dict."""
        rows = self.conn.execute(
            "SELECT metric, tag_keys, series_count, monthly_cost_usd, source,"
            " first_seen, last_seen FROM series ORDER BY monthly_cost_usd DESC, metric"
        ).fetchall()
        return [
            {
                "metric": row["metric"],
                "tag_keys": json.loads(row["tag_keys"]),
                "series": int(row["series_count"]),
                "monthly_cost_usd": float(row["monthly_cost_usd"]),
                "source": row["source"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
            for row in rows
        ]

    def set_budget(self, amount: float, warn_pct: float = 80.0) -> dict:
        """Set the single monthly budget row; returns the stored budget."""
        now = _now_iso()
        with self.conn:
            self.conn.execute("DELETE FROM budget")
            self.conn.execute(
                "INSERT INTO budget (monthly_budget_usd, warn_pct, currency, updated_at)"
                " VALUES (?, ?, 'USD', ?)",
                (float(amount), float(warn_pct), now),
            )
        budget = self.get_budget()
        assert budget is not None  # just inserted
        return budget

    def get_budget(self) -> dict | None:
        """Return the current budget as a dict, or None if never set."""
        row = self.conn.execute(
            "SELECT monthly_budget_usd, warn_pct, currency, updated_at"
            " FROM budget LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return {
            "monthly_budget_usd": float(row["monthly_budget_usd"]),
            "warn_pct": float(row["warn_pct"]),
            "currency": str(row["currency"]),
            "updated_at": row["updated_at"],
        }

    def close(self) -> None:
        """Close the underlying database connection."""
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
