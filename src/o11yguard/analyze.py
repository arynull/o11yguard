"""Summaries and rendering of stored series cost estimates.

Every money figure produced here is an estimate: active series counts are
multiplied by a configurable USD-per-1000-series rate (see
:mod:`o11yguard.pricing`). Nothing in this module touches the network.

A summary has the shape::

    {
      "estimates": True,
      "metrics": 5,                 # distinct metric names
      "series": 2570412,            # total active series
      "est_monthly_cost_usd": 115668.54,
      "projected_run_rate_usd": 115668.54,
      "drivers": [{"metric", "series", "tag_keys", "est_monthly_cost_usd"}],
      "tag_offenders": [{"tag_key", "metrics", "series", "est_monthly_cost_usd"}],
    }

``metrics`` inside a tag offender is the number of distinct metric names
carrying that tag key; a metric is counted once per tag key even when
several stored records share it. In ``drivers``, records are aggregated per
metric name (series and cost summed, tag keys unioned) so a metric ingested
in more than one batch still appears once.
"""

from __future__ import annotations

import json
from typing import Any

Summary = dict[str, Any]


def _money(value: float) -> float:
    """Round a USD figure to 2 decimals, avoiding a signed zero."""
    rounded = round(float(value), 2)
    return 0.0 if rounded == 0 else rounded


def _usd(value: float) -> str:
    """Format a USD figure with thousands separators."""
    return f"${float(value):,.2f}"


def _count(value: float) -> str:
    """Format an integer-ish figure with thousands separators."""
    return f"{int(value):,}"


def summarize(store: Any, top: int = 5) -> Summary:
    """Summarize the series records held by ``store``.

    Args:
        store: Any object exposing ``all_series() -> list[dict]`` (normally
            :class:`o11yguard.store.Store`).
        top: How many cost drivers and tag offenders to include.

    Returns:
        The summary dict described in the module docstring. All money is
        rounded to 2 decimal places.
    """
    limit = max(int(top), 0)

    total_series = 0
    total_cost = 0.0
    metrics: dict[str, dict[str, Any]] = {}
    tags: dict[str, dict[str, Any]] = {}

    for record in store.all_series():
        metric = str(record.get("metric", "") or "")
        series = int(record.get("series", 0) or 0)
        cost = float(record.get("monthly_cost_usd", 0.0) or 0.0)
        total_series += series
        total_cost += cost

        bucket = metrics.setdefault(
            metric,
            {"metric": metric, "series": 0, "cost": 0.0, "tag_keys": []},
        )
        bucket["series"] += series
        bucket["cost"] += cost

        for tag_key in record.get("tag_keys") or []:
            key = str(tag_key)
            if key not in bucket["tag_keys"]:
                bucket["tag_keys"].append(key)
            offender = tags.setdefault(
                key,
                {"tag_key": key, "series": 0, "cost": 0.0, "metrics": set()},
            )
            offender["series"] += series
            offender["cost"] += cost
            offender["metrics"].add(metric)

    drivers = sorted(
        metrics.values(), key=lambda item: (-item["cost"], item["metric"])
    )[:limit]
    offenders = sorted(
        tags.values(), key=lambda item: (-item["cost"], item["tag_key"])
    )[:limit]

    return {
        "estimates": True,
        "metrics": len(metrics),
        "series": total_series,
        "est_monthly_cost_usd": _money(total_cost),
        "projected_run_rate_usd": _money(total_cost),
        "drivers": [
            {
                "metric": driver["metric"],
                "series": int(driver["series"]),
                "tag_keys": list(driver["tag_keys"]),
                "est_monthly_cost_usd": _money(driver["cost"]),
            }
            for driver in drivers
        ],
        "tag_offenders": [
            {
                "tag_key": offender["tag_key"],
                "metrics": len(offender["metrics"]),
                "series": int(offender["series"]),
                "est_monthly_cost_usd": _money(offender["cost"]),
            }
            for offender in offenders
        ],
    }


def format_human(summary: Summary) -> str:
    """Render ``summary`` as a readable report.

    Every money figure is labelled ``est.`` because the numbers are derived
    estimates, not billed amounts.
    """
    drivers = summary.get("drivers") or []
    offenders = summary.get("tag_offenders") or []

    lines = [
        "o11yguard estimate (all costs are estimates)",
        f"  metrics: {_count(summary.get('metrics', 0))}",
        f"  series:  {_count(summary.get('series', 0))}",
        f"  est. monthly cost: {_usd(summary.get('est_monthly_cost_usd', 0.0))}",
        f"  est. projected run rate: {_usd(summary.get('projected_run_rate_usd', 0.0))}",
        "",
        f"top {len(drivers)} cost drivers:",
    ]
    if not drivers:
        lines.append("  (none - nothing ingested yet)")
    for index, driver in enumerate(drivers, start=1):
        tag_keys = ", ".join(driver.get("tag_keys") or []) or "-"
        lines.append(
            f"  {index}. {driver['metric']}"
            f"  series={_count(driver['series'])}"
            f"  tags={tag_keys}"
            f"  est. {_usd(driver['est_monthly_cost_usd'])}/mo"
        )

    lines.append("")
    lines.append(f"top {len(offenders)} tag offenders by est. cost:")
    if not offenders:
        lines.append("  (none)")
    for index, offender in enumerate(offenders, start=1):
        lines.append(
            f"  {index}. {offender['tag_key']}"
            f"  metrics={int(offender['metrics'])}"
            f"  series={_count(offender['series'])}"
            f"  est. {_usd(offender['est_monthly_cost_usd'])}/mo"
        )
    return "\n".join(lines)


def to_json(summary: Summary) -> str:
    """Serialize ``summary`` to indented, key-sorted JSON."""
    return json.dumps(summary, indent=2, sort_keys=True)
