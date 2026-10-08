"""Cost-saving recommendations derived from stored series estimates.

All money figures are estimates, not billed amounts. Series-reduction
heuristics below are rough geometric-mean approximations: dropping one tag
key out of ``n`` is assumed to cut series to
``ceil(series ** ((n - 1) / n))``. Percentage savings (80/30/50/100%) are
planning placeholders, not measured outcomes. Nothing here touches the
network.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from o11yguard import pricing

Plan = list[dict[str, Any]]

LEVER_NAMES = (
    "drop-tag",
    "rollup",
    "move-to-logs",
    "reduce-retention",
    "sample",
    "decommission",
)


def _money(value: float) -> float:
    rounded = round(float(value), 2)
    return 0.0 if rounded == 0 else rounded


def _offender_series(store: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    records = store.all_series()
    for record in records:
        series = int(record.get("series", 0) or 0)
        for key in record.get("tag_keys") or []:
            k = str(key)
            totals[k] = totals.get(k, 0) + series
    return totals


def _pick_drop_tag(tag_keys: list[str], offender_series: dict[str, int]) -> str:
    present = [t for t in offender_series if t in set(tag_keys)]
    if present:
        present.sort(key=lambda t: (-offender_series[t], t))
        return present[0]
    return tag_keys[-1]


def build_plan(store: Any, top: int = 10) -> Plan:
    """Build up to ``top`` recommendations, at most one per stored record."""
    offender_series = _offender_series(store)
    records = store.all_series()
    plan: Plan = []
    for record in records:
        metric = str(record.get("metric", "") or "")
        series = int(record.get("series", 0) or 0)
        cost = float(record.get("monthly_cost_usd", 0.0) or 0.0)
        tag_keys = [str(t) for t in (record.get("tag_keys") or [])]
        n = len(tag_keys)
        lname = metric.lower()

        candidates: dict[str, dict[str, Any]] = {}

        if n >= 2 and series > 0:
            tag = _pick_drop_tag(tag_keys, offender_series)
            new_series = math.ceil(series ** ((n - 1) / n))
            savings = pricing.cost_for_series(series) - pricing.cost_for_series(
                new_series
            )
            candidates["drop-tag"] = {
                "tag": tag,
                "savings": savings,
                "detail": f"Drop tag '{tag}' from '{metric}' to cut series from {series:,} to ~{new_series:,}.",
                "confidence": "medium",
                "risk": f"Dashboards or alerts grouping by '{tag}' on '{metric}' may break.",
            }

        if series >= 10_000 and n <= 1:
            candidates["rollup"] = {
                "tag": "",
                "savings": cost * 0.5,
                "detail": f"Pre-aggregate '{metric}' (roll up to coarser time buckets): with {series:,} series and only {n} tag key(s), dropping tags cannot cut volume, but aggregation can roughly halve it.",
                "confidence": "medium",
                "risk": f"Pre-aggregating '{metric}' loses fine-grained per-series drilldown.",
            }

        if "log" in lname or "event" in lname:
            candidates["move-to-logs"] = {
                "tag": "",
                "savings": cost * 0.8,
                "detail": f"Move '{metric}' to logs instead of a high-cardinality metric.",
                "confidence": "low",
                "risk": f"Log-based queries for '{metric}' are slower than metric queries.",
            }

        if cost >= 1000 and n <= 2:
            candidates["reduce-retention"] = {
                "tag": "",
                "savings": cost * 0.3,
                "detail": f"Shorten retention for '{metric}' to cut storage cost.",
                "confidence": "medium",
                "risk": f"Older history for '{metric}' will no longer be queryable.",
            }

        if 100 <= cost <= 1000:
            candidates["sample"] = {
                "tag": "",
                "savings": cost * 0.5,
                "detail": f"Sample '{metric}' at 50% to halve ingest volume.",
                "confidence": "low",
                "risk": f"Sampling '{metric}' may miss rare spikes or outliers.",
            }

        if series < 100 and cost < 10:
            candidates["decommission"] = {
                "tag": "",
                "savings": cost,
                "detail": f"Decommission '{metric}' as it has only {series:,} series.",
                "confidence": "low",
                "risk": f"Removing '{metric}' loses visibility if anything still uses it.",
            }

        chosen = None
        for lever in LEVER_NAMES:
            if lever in candidates:
                chosen = (lever, candidates[lever])
                break
        if chosen is None:
            continue
        lever, cand = chosen
        savings = _money(cand["savings"])
        if savings < 1:
            continue
        if lever == "drop-tag":
            rec_id = f"{lever}:{metric}:{cand['tag']}"
        else:
            rec_id = f"{lever}:{metric}"
        plan.append(
            {
                "id": rec_id,
                "metric": metric,
                "lever": lever,
                "detail": cand["detail"],
                "est_monthly_savings_usd": savings,
                "confidence": cand["confidence"],
                "risk": cand["risk"],
            }
        )

    plan.sort(key=lambda r: (-r["est_monthly_savings_usd"], r["metric"], r["id"]))
    return plan[: max(int(top), 0)]


def filter_plan(
    plan: Plan, levers: Sequence[str] | None = None, min_savings: float = 0.0
) -> Plan:
    """Keep only recommendations matching ``levers`` and ``min_savings``."""
    wanted = set(levers) if levers else None
    kept = []
    for rec in plan:
        if wanted is not None and rec.get("lever") not in wanted:
            continue
        if float(rec.get("est_monthly_savings_usd", 0.0) or 0.0) < min_savings:
            continue
        kept.append(rec)
    return kept


def what_if_drop_tag(store: Any, metric: str, tag: str) -> dict[str, Any]:
    """Estimate the effect of dropping ``tag`` from ``metric`` (read-only)."""
    matched = [r for r in store.all_series() if str(r.get("metric", "")) == metric]
    if not matched:
        raise KeyError(metric)
    tag_keys: list[str] = []
    for record in matched:
        for key in record.get("tag_keys") or []:
            k = str(key)
            if k not in tag_keys:
                tag_keys.append(k)
    if tag not in tag_keys:
        raise ValueError(f"tag '{tag}' not on metric '{metric}'")
    old_series = sum(int(r.get("series", 0) or 0) for r in matched)
    n = len(tag_keys)
    if n <= 1:
        new_series = 1
    else:
        new_series = math.ceil(old_series ** ((n - 1) / n)) if old_series > 0 else 0
    old_cost = _money(pricing.cost_for_series(old_series))
    new_cost = _money(pricing.cost_for_series(new_series))
    return {
        "metric": metric,
        "tag": tag,
        "old_series": int(old_series),
        "new_series": int(new_series),
        "old_cost_usd": old_cost,
        "new_cost_usd": new_cost,
        "est_savings_usd": _money(old_cost - new_cost),
        "note": "Estimate only: assumes dropping one tag key leaves ceil(series ** ((n-1)/n)) series.",
    }


def to_json(plan: Plan) -> str:
    """Serialize ``plan`` to indented, key-sorted JSON."""
    return json.dumps(plan, indent=2, sort_keys=True)


def _yaml_quote(value: str) -> str:
    if value == "":
        return "''"
    special = set(":#{}[],&*!|>'\"%@`-? \t\n\r")
    needs = any(c in special for c in value) or value != value.strip()
    needs = needs or value.lower() in (
        "true",
        "false",
        "null",
        "~",
        "yes",
        "no",
        "on",
        "off",
    )
    needs = needs or value[0].isdigit() or "\n" in value
    if not needs and all(c.isalnum() or c in "_-./" for c in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if value is None:
        return "null"
    return _yaml_quote(str(value))


def to_yaml(plan: Plan) -> str:
    """Serialize ``plan`` to minimal YAML for this flat list-of-dicts schema."""
    if not plan:
        return "[]\n"
    lines = []
    for rec in plan:
        lines.append(f"- id: {_yaml_value(rec.get('id'))}")
        for key in (
            "metric",
            "lever",
            "detail",
            "est_monthly_savings_usd",
            "confidence",
            "risk",
        ):
            lines.append(f"  {key}: {_yaml_value(rec.get(key))}")
    return "\n".join(lines) + "\n"


def format_md(plan: Plan) -> str:
    """Render ``plan`` as a markdown table plus a total savings line."""
    lines = [
        "| Metric | Lever | Est. savings/mo | Confidence | Detail |",
        "|---|---|---|---|---|",
    ]
    total = 0.0
    for rec in plan:
        total += float(rec.get("est_monthly_savings_usd", 0.0) or 0.0)
        lines.append(
            f"| {rec.get('metric')} | {rec.get('lever')} | "
            f"${float(rec.get('est_monthly_savings_usd', 0.0)):,.2f} | "
            f"{rec.get('confidence')} | {rec.get('detail')} |"
        )
    if not plan:
        lines.append("| - | - | - | - | (no recommendations) |")
    lines.append("")
    lines.append(f"Total est. savings: ${total:,.2f}/mo (estimates only)")
    return "\n".join(lines)
