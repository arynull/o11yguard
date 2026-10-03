"""Cost estimation for metric series.

The estimate treats one active series as one billable custom metric at
Datadog's published ~$0.045/metric/month list price. Datadog actually bills
per metric name and high-cardinality tags multiply the bill, so figures here
are an estimate only. The rate is configurable via ``--per-1k-rate``.
"""

from __future__ import annotations

PER_1K_SERIES_DEFAULT: float = 45.0
"""USD per 1000 series per month (default estimate; see module docstring)."""


def cost_for_series(series: int, rate: float = PER_1K_SERIES_DEFAULT) -> float:
    """Return the estimated monthly USD cost for ``series`` active series.

    Args:
        series: Estimated number of active series (non-negative integer).
        rate: USD per 1000 series per month.

    Returns:
        Estimated monthly cost in USD, rounded to 4 decimal places.
    """
    count = max(int(series), 0)
    return round((count / 1000.0) * float(rate), 4)
