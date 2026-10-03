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


SUBCOMMANDS: dict[str, ParserFactory] = {
    "sample": add_sample,
    "ingest": add_ingest,
    "analyze": add_analyze,
    "budget": add_budget,
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
