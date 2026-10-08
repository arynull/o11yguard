"""Command-line interface for o11yguard.

Offline only: every subcommand reads local files or the local SQLite store.
No network calls anywhere in this module.

Exit codes: ``0`` ok, ``1`` error, ``2`` budget breach, ``3`` budget warning.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .analyze import format_human, summarize, to_json
from .ingest import ingest_file
from .plan import (
    LEVER_NAMES,
    build_plan,
    filter_plan,
    format_md,
    to_yaml,
    what_if_drop_tag,
)
from .plan import to_json as plan_to_json
from .pricing import PER_1K_SERIES_DEFAULT
from .sample import built_in_sample
from .store import Store

if TYPE_CHECKING:  # pragma: no cover - typing only
    from argparse import _SubParsersAction

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BREACH = 2
EXIT_WARN = 3

DEFAULT_PROVIDER = "datadog"
DEFAULT_BATCH = "cli"
DEFAULT_WARN_PCT = 80.0
DEFAULT_TOP = 5
DEFAULT_PLAN_TOP = 10
DEFAULT_SERIES_LIMIT = 20

Handler = Callable[[argparse.Namespace], int]
ParserFactory = Callable[["_SubParsersAction"], argparse.ArgumentParser]


def _usd(value: float) -> str:
    """Format a USD amount with thousands separators and 2 decimals."""
    return f"{float(value):,.2f}"


def _fail(message: str) -> int:
    """Print ``error: <message>`` to stderr and return the error exit code."""
    print(f"error: {message}", file=sys.stderr)
    return EXIT_ERROR


# --------------------------------------------------------------- handlers ---


def cmd_sample(_args: argparse.Namespace) -> int:
    """Print the built-in sample dataset as JSON on stdout."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "datadog",
        "series": built_in_sample(),
    }
    print(json.dumps(payload, sort_keys=True))
    return EXIT_OK


def cmd_ingest(args: argparse.Namespace) -> int:
    """Parse a local JSON/CSV file and replace its batch in the store."""
    path = Path(args.file)
    if not path.is_file():
        return _fail(f"no such file: {path}")
    if args.per_1k_rate <= 0:
        return _fail(f"--per-1k-rate must be greater than 0, got {args.per_1k_rate}")
    try:
        with Store() as store:
            inserted, skipped = ingest_file(
                store, path, args.provider, args.batch, args.per_1k_rate
            )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return _fail(f"{path}: {exc}")
    print(
        f"ingested {inserted} series records (batch '{args.batch}', skipped {skipped})"
    )
    return EXIT_OK


def cmd_analyze(args: argparse.Namespace) -> int:
    """Report estimated monthly cost drivers from the stored records."""
    if args.top < 0:
        return _fail(f"--top must be >= 0, got {args.top}")
    with Store() as store:
        summary = summarize(store, top=args.top)
    print(to_json(summary) if args.json else format_human(summary))
    return EXIT_OK


def cmd_budget_set(args: argparse.Namespace) -> int:
    """Validate and persist the monthly budget."""
    if args.amount <= 0:
        return _fail(f"budget must be greater than 0, got {args.amount:g}")
    if not 0 < args.warn_pct < 100:
        return _fail(f"--warn-pct must be between 0 and 100, got {args.warn_pct:g}")
    with Store() as store:
        store.set_budget(args.amount, args.warn_pct)
    print(f"budget set: ${_usd(args.amount)} (warn at {args.warn_pct:g}%)")
    return EXIT_OK


def cmd_budget_status(args: argparse.Namespace) -> int:
    """Compare the estimated run rate against the stored budget."""
    with Store() as store:
        budget = store.get_budget()
        if budget is None:
            return _fail("no budget set")
        run_rate = float(summarize(store)["est_monthly_cost_usd"])

    amount = float(budget["monthly_budget_usd"])
    warn_pct = float(budget["warn_pct"])
    if run_rate >= amount:
        state, code = "breach", EXIT_BREACH
    elif run_rate >= amount * warn_pct / 100.0:
        state, code = "warn", EXIT_WARN
    else:
        state, code = "ok", EXIT_OK

    if args.json:
        print(
            json.dumps(
                {
                    "budget_usd": round(amount, 2),
                    "run_rate_usd": round(run_rate, 2),
                    "state": state,
                    "warn_pct": warn_pct,
                },
                sort_keys=True,
            )
        )
    else:
        print(f"budget:   ${_usd(amount)} / month")
        print(f"warn at:  {warn_pct:g}%")
        print(f"run rate: est. ${_usd(run_rate)} / month")
        print(f"state:    {state}")
    return code


def cmd_plan(args: argparse.Namespace) -> int:
    """Print ranked cost-saving recommendations for the stored records."""
    if args.top < 0:
        return _fail(f"--top must be >= 0, got {args.top}")
    unknown = [lever for lever in (args.lever or []) if lever not in LEVER_NAMES]
    if unknown:
        return _fail(f"unknown lever: {unknown[0]} (valid: {', '.join(LEVER_NAMES)})")
    if args.min_savings < 0:
        return _fail(f"--min-savings must be >= 0, got {args.min_savings}")
    with Store() as store:
        recommendations = build_plan(store, top=args.top)
    recommendations = filter_plan(
        recommendations, levers=args.lever, min_savings=args.min_savings
    )
    if args.format == "yaml":
        print(to_yaml(recommendations), end="")
    elif args.format == "json":
        print(plan_to_json(recommendations))
    else:
        print(format_md(recommendations))
    return EXIT_OK


def cmd_series_list(args: argparse.Namespace) -> int:
    """List stored series records, optionally filtered by metric prefix."""
    if args.limit < 0:
        return _fail(f"--limit must be >= 0, got {args.limit}")
    with Store() as store:
        records = store.all_series()
    if args.metric:
        records = [
            record
            for record in records
            if str(record.get("metric", "")).startswith(args.metric)
        ]
    rows = records[: args.limit]

    if args.json:
        print(json.dumps(rows, indent=2, sort_keys=True))
        return EXIT_OK

    if not rows:
        print("no series stored (nothing ingested yet)")
        return EXIT_OK
    width = max(len(str(record.get("metric", ""))) for record in rows)
    print(f"{'METRIC'.ljust(width)}  {'SERIES':>12}  {'EST. $/MO':>12}  TAGS")
    for record in rows:
        metric = str(record.get("metric", ""))
        tags = ", ".join(str(tag) for tag in record.get("tag_keys") or []) or "-"
        print(
            f"{metric.ljust(width)}  "
            f"{int(record.get('series', 0) or 0):>12,}  "
            f"{_usd(record.get('monthly_cost_usd', 0.0) or 0.0):>12}  "
            f"{tags}"
        )
    return EXIT_OK


def cmd_series_drop_tag(args: argparse.Namespace) -> int:
    """What-if only: estimate dropping a tag. Never writes to the store."""
    with Store() as store:
        try:
            estimate = what_if_drop_tag(store, args.metric, args.tag)
        except KeyError:
            return _fail(f"unknown metric: {args.metric}")
        except ValueError as exc:
            return _fail(str(exc))
    print(
        f"what-if: drop tag '{estimate['tag']}' from "
        f"'{estimate['metric']}' (nothing was changed)"
    )
    print(f"  series:   {estimate['old_series']:,} -> {estimate['new_series']:,}")
    print(
        f"  est. monthly cost: ${_usd(estimate['old_cost_usd'])}"
        f" -> ${_usd(estimate['new_cost_usd'])}"
    )
    print(f"  est. monthly savings: ${_usd(estimate['est_savings_usd'])}")
    print(f"  note: {estimate['note']}")
    print("nothing was changed: this command only reports an estimate.")
    return EXIT_OK


# ---------------------------------------------------------------- parsers ---
# Each factory registers one top-level subcommand and its handler. Adding a
# new command (e.g. ``plan`` or ``series``) means adding one factory and one
# SUBCOMMANDS entry below - nothing else in this module changes.


def add_sample(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``sample`` subcommand."""
    parser = subparsers.add_parser(
        "sample", help="print the built-in sample dataset as JSON"
    )
    parser.set_defaults(func=cmd_sample)
    return parser


def add_ingest(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``ingest`` subcommand."""
    parser = subparsers.add_parser(
        "ingest", help="ingest a local JSON/CSV series file into the store"
    )
    parser.add_argument("file", help="path to a local JSON or CSV file")
    parser.add_argument(
        "--provider",
        choices=["datadog", "generic"],
        default=DEFAULT_PROVIDER,
        help="input shape (default: %(default)s)",
    )
    parser.add_argument(
        "--batch",
        default=DEFAULT_BATCH,
        help="batch name; re-ingesting a name replaces its rows (default: %(default)s)",
    )
    parser.add_argument(
        "--per-1k-rate",
        type=float,
        default=PER_1K_SERIES_DEFAULT,
        help="USD per 1000 series per month when the file carries no cost column"
        " (default: %(default)s)",
    )
    parser.set_defaults(func=cmd_ingest)
    return parser


def add_analyze(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``analyze`` subcommand."""
    parser = subparsers.add_parser(
        "analyze", help="report estimated monthly cost drivers"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help="rows per list (default: %(default)s)",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of a report"
    )
    parser.set_defaults(func=cmd_analyze)
    return parser


def add_budget(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``budget`` subcommand and its set/status actions."""
    parser = subparsers.add_parser("budget", help="set or check the monthly budget")
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    parser.set_defaults(
        func=lambda _args: _fail("budget needs an action: set or status")
    )

    set_p = actions.add_parser("set", help="set the monthly budget")
    set_p.add_argument("amount", type=float, help="monthly budget in USD")
    set_p.add_argument(
        "--warn-pct",
        type=float,
        default=DEFAULT_WARN_PCT,
        help="warn once the run rate reaches this percentage (default: %(default)s)",
    )
    set_p.set_defaults(func=cmd_budget_set)

    status_p = actions.add_parser("status", help="check the run rate against budget")
    status_p.add_argument(
        "--json", action="store_true", help="emit JSON instead of a report"
    )
    status_p.set_defaults(func=cmd_budget_status)
    return parser


def add_plan(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``plan`` subcommand."""
    parser = subparsers.add_parser(
        "plan", help="print ranked cost-saving recommendations"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_PLAN_TOP,
        help="max recommendations (default: %(default)s)",
    )
    parser.add_argument(
        "--format",
        choices=["md", "yaml", "json"],
        default="md",
        help="output format (default: %(default)s)",
    )
    parser.add_argument(
        "--lever",
        action="append",
        default=None,
        metavar="LEVER",
        help="only show recommendations from this lever; repeatable"
        " (valid: drop-tag, rollup, move-to-logs, reduce-retention, sample,"
        " decommission)",
    )
    parser.add_argument(
        "--min-savings",
        type=float,
        default=0.0,
        help="only show recommendations with at least this much estimated"
        " monthly savings in USD (default: %(default)s)",
    )
    parser.set_defaults(func=cmd_plan)
    return parser


def add_series(subparsers: _SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``series`` subcommand and its list/drop-tag actions."""
    parser = subparsers.add_parser(
        "series", help="inspect stored series and estimate tag changes (read-only)"
    )
    actions = parser.add_subparsers(dest="action", metavar="ACTION")
    parser.set_defaults(
        func=lambda _args: _fail("series needs an action: list or drop-tag")
    )

    list_p = actions.add_parser("list", help="list stored series records")
    list_p.add_argument(
        "--metric",
        default="",
        help="only metrics starting with this prefix (default: all)",
    )
    list_p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_SERIES_LIMIT,
        help="max rows (default: %(default)s)",
    )
    list_p.add_argument(
        "--json", action="store_true", help="emit JSON instead of a table"
    )
    list_p.set_defaults(func=cmd_series_list)

    drop_p = actions.add_parser(
        "drop-tag",
        help="what-if only: estimate dropping a tag; nothing was changed",
        description=(
            "What-if only: prints the estimated effect of dropping a tag from a "
            "metric. Nothing was changed and no data is written."
        ),
    )
    drop_p.add_argument("metric", help="exact metric name")
    drop_p.add_argument("tag", help="tag key that would be dropped")
    drop_p.set_defaults(func=cmd_series_drop_tag)
    return parser


SUBCOMMANDS: dict[str, ParserFactory] = {
    "sample": add_sample,
    "ingest": add_ingest,
    "analyze": add_analyze,
    "budget": add_budget,
    "plan": add_plan,
    "series": add_series,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser and register every subcommand."""
    parser = argparse.ArgumentParser(
        prog="o11yguard",
        description="Read-only cost control for your observability bill.",
    )
    parser.add_argument(
        "--version", action="version", version=f"o11yguard {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for factory in SUBCOMMANDS.values():
        factory(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code.

    ``--version``/``--help`` exit ``0``; argparse usage errors exit ``1`` so
    that code ``2`` keeps its meaning of budget breach.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as exc:  # --version, --help, or a usage error
        code = exc.code
        return EXIT_OK if code in (0, None) else EXIT_ERROR
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return EXIT_ERROR
    try:
        return int(func(args))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        return _fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
