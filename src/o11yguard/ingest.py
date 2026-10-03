"""Offline ingest of series records from JSON or CSV files.

Nothing here touches the network; files are read from local disk only.

Generic provider accepts either JSON or CSV:

* JSON: a bare list of records, or an object like ``{"series": [...]}``.
* CSV: columns ``metric``, ``tag_keys`` (semicolon-separated), ``series``
  and optionally ``monthly_cost_usd``.

Key aliases accepted in generic JSON (and the CSV header):

========  ================================
canonical  accepted aliases
========  ================================
metric     ``metric_name``, ``name``
tag_keys   ``tags``, ``tag_list``
series     ``series_count``, ``custom_series``, ``num_series``
monthly_cost_usd  ``cost``, ``monthly_cost``
========  ================================

When ``monthly_cost_usd`` is absent it is computed with
:func:`o11yguard.pricing.cost_for_series` at ``rate`` USD per 1000 series.

Datadog provider is a tolerant extractor. It searches these positions for a
list of per-metric records (in order):

1. ``data[].attributes.usage[]``  (metrics usage API payload)
2. ``data[]``                     (API resource list)
3. ``usage[]``                    (bare usage object)
4. ``series[]``                   (series-list payload)

Per-record aliases: ``metric_name``/``name``/``metric`` -> ``metric``,
``num_series``/``series_count``/``custom_series``/``series`` -> ``series``,
``tags``/``tag_list`` -> ``tag_keys``. If no known shape is found a
``ValueError`` naming the expected shape is raised.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from .pricing import PER_1K_SERIES_DEFAULT, cost_for_series

Record = dict
"""A dict with keys ``metric`` (str), ``tag_keys`` (list[str]),
``series`` (int) and ``monthly_cost_usd`` (float)."""

_METRIC_ALIASES = ("metric", "metric_name", "name")
_TAGS_ALIASES = ("tag_keys", "tags", "tag_list")
_SERIES_ALIASES = ("series", "series_count", "custom_series", "num_series")
_COST_ALIASES = ("monthly_cost_usd", "cost", "monthly_cost")

_DATADOG_EXPECTED = (
    "expected a Datadog-shaped payload with per-metric records under "
    "data[].attributes.usage[], data[], usage[] or series[]"
)


def _first(record: dict, keys: tuple[str, ...]) -> object | None:
    """Return the first present, non-None value among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value is not None:
            return value
    return None


def _as_tag_keys(value: object) -> list[str]:
    """Normalize ``tag_keys`` to list[str]; strings split on ";" or ","."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace(",", ";").split(";")
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _as_series(value: object) -> int | None:
    """Coerce ``series`` to a positive int; None when missing or unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace("_", ""))
        except ValueError:
            return None
    else:
        return None
    if not number.is_integer():
        return None
    return int(number) if number > 0 else None


def _as_cost(value: object) -> float | None:
    """Coerce a cost to float; None when missing or unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace("$", "").replace(",", ""))
        except ValueError:
            return None
    return None


def _build_record(raw: dict, rate: float) -> tuple[Record | None, str | None]:
    """Normalize one raw mapping.

    Returns ``(record, None)`` on success or ``(None, reason)`` when the row
    must be skipped.
    """
    metric = _first(raw, _METRIC_ALIASES)
    if metric is None or not str(metric).strip():
        return None, "missing metric"
    series = _as_series(_first(raw, _SERIES_ALIASES))
    if series is None:
        return None, "series missing, non-numeric or <= 0"
    cost = _as_cost(_first(raw, _COST_ALIASES))
    if cost is None:
        cost = cost_for_series(series, rate)
    return {
        "metric": str(metric).strip(),
        "tag_keys": _as_tag_keys(_first(raw, _TAGS_ALIASES)),
        "series": series,
        "monthly_cost_usd": float(cost),
    }, None


def _skip(reason: str) -> tuple[None, str]:
    print(f"o11yguard ingest: skipping record: {reason}", file=sys.stderr)
    return None, reason


def _finalize(raw_records: list[object], rate: float) -> tuple[list[Record], int]:
    """Normalize raw mappings, dropping malformed rows with a warning."""
    records: list[Record] = []
    skipped = 0
    for raw in raw_records:
        if not isinstance(raw, dict):
            _skip("row is not an object")
            skipped += 1
            continue
        record, reason = _build_record(raw, rate)
        if record is None:
            _skip(reason or "invalid record")
            skipped += 1
            continue
        records.append(record)
    return records, skipped


# ---------------------------------------------------------------- generic ---


def _parse_generic_json(text: str, rate: float) -> tuple[list[Record], int]:
    """Parse generic JSON: bare list or {"series": [...]}."""
    payload = json.loads(text)
    if isinstance(payload, list):
        raw_records: list[object] = payload
    elif isinstance(payload, dict):
        candidate = payload.get("series")
        if isinstance(candidate, list):
            raw_records = candidate
        else:
            raw_records = [payload]
    else:
        raise TypeError("generic JSON must be a list or an object with a 'series' list")
    return _finalize(raw_records, rate)


def _parse_generic_csv(text: str, rate: float) -> tuple[list[Record], int]:
    """Parse generic CSV with metric/tag_keys/series[/monthly_cost_usd] columns."""
    reader = csv.DictReader(text.splitlines())
    raw_records: list[object] = [
        {key: value for key, value in row.items() if key is not None} for row in reader
    ]
    return _finalize(raw_records, rate)


# --------------------------------------------------------------- datadog ---


def _datadog_candidates(payload: object) -> list[object]:
    """Return per-metric record lists found in known Datadog positions."""
    found: list[object] = []
    if not isinstance(payload, dict):
        return found
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes")
            if isinstance(attributes, dict):
                usage = attributes.get("usage")
                if isinstance(usage, list):
                    found.extend(usage)
        if not found:
            found.extend(data)
    elif isinstance(data, dict):
        usage = data.get("usage")
        if isinstance(usage, list):
            found.extend(usage)
    if not found:
        usage = payload.get("usage")
        if isinstance(usage, list):
            found.extend(usage)
    if not found:
        series = payload.get("series")
        if isinstance(series, list):
            found.extend(series)
    return found


def _parse_datadog(text: str, rate: float) -> tuple[list[Record], int]:
    payload = json.loads(text)
    candidates = _datadog_candidates(payload)
    if not candidates:
        raise ValueError(f"unrecognized Datadog payload: {_DATADOG_EXPECTED}")
    return _finalize(candidates, rate)


# ------------------------------------------------------------------ public ---


def parse_file(
    path: Path | str,
    provider: str,
    rate: float = PER_1K_SERIES_DEFAULT,
) -> tuple[list[Record], int]:
    """Parse ``path`` into records; returns ``(records, skipped_count)``.

    Args:
        path: Local JSON or CSV file to read (never a URL).
        provider: ``"generic"`` or ``"datadog"``.
        rate: USD per 1000 series per month for cost fallback.

    Raises:
        ValueError: unknown provider, or a Datadog file without a recognized
            shape.
        OSError: the file cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8")
    name = Path(path).name.lower()
    if provider == "generic":
        if name.endswith(".csv"):
            return _parse_generic_csv(text, rate)
        stripped = text.lstrip()
        if stripped.startswith(("[", "{")):
            try:
                return _parse_generic_json(text, rate)
            except json.JSONDecodeError:
                return _parse_generic_csv(text, rate)
        return _parse_generic_csv(text, rate)
    if provider == "datadog":
        return _parse_datadog(text, rate)
    raise ValueError(f"unknown provider {provider!r}; expected 'generic' or 'datadog'")


def ingest_file(
    store,
    path: Path | str,
    provider: str,
    batch: str,
    rate: float = PER_1K_SERIES_DEFAULT,
) -> tuple[int, int]:
    """Parse ``path`` and replace ``batch`` in ``store``.

    Returns ``(inserted, skipped)`` counts. Skipped rows never reach the
    store.
    """
    records, skipped = parse_file(path, provider, rate)
    inserted = store.replace_batch(records, batch)
    return inserted, skipped
