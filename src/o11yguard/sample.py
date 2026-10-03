"""Built-in sample cardinality dataset (offline, no ingest needed)."""

from __future__ import annotations

from .pricing import cost_for_series

_SAMPLE_ROWS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    (
        "web.request.duration",
        ("service", "endpoint", "status", "request_id"),
        2_400_000,
    ),
    ("db.query.time", ("service", "query_hash"), 48_000),
    ("k8s.container.cpu_usage", ("cluster", "namespace", "pod"), 120_000),
    ("app.logins.total", ("service", "region"), 2_400),
    ("legacy.heartbeat", ("env",), 12),
)


def built_in_sample() -> list[dict]:
    """Return the built-in sample series records.

    Each record has keys ``metric``, ``tag_keys`` (list[str]), ``series``
    (int) and ``monthly_cost_usd`` (float, from
    :func:`o11yguard.pricing.cost_for_series`).
    """
    return [
        {
            "metric": metric,
            "tag_keys": list(tag_keys),
            "series": series,
            "monthly_cost_usd": cost_for_series(series),
        }
        for metric, tag_keys, series in _SAMPLE_ROWS
    ]
