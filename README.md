# o11yguard

Read-only cost control for your observability bill (Datadog-first).

## What it does

o11yguard helps you understand and control metric-cardinality spend:

- Cardinality analysis: totals, top-N cost drivers, and top tag offenders from your stored series.
- Budget alerts: set a monthly USD budget and check the estimated run rate against it, with CI/cron-friendly exit codes.
- Ranked action plans: concrete recommendations (drop a tag, pre-aggregate/rollup, shorten retention, sample, move to logs, decommission) ordered by estimated monthly savings, plus what-if estimates for dropping a tag.

Read-only and offline-first: there are no write APIs, no apply command, and no network calls. Every subcommand reads local files or the local SQLite store. You work on exported data (Datadog usage exports or your own CSV/JSON).

All cost figures are estimates, not billed amounts.

## Install

Python >= 3.10. Zero dependencies (standard library only).

    pip install .

or isolated:

    pipx install .

Then:

    o11yguard --version
    # o11yguard 0.1.1

## Quickstart

Run everything in a scratch dir so nothing touches your real state:

    export O11YGUARD_DATA_DIR=$(mktemp -d)
    o11yguard sample > s.json
    o11yguard ingest s.json --provider datadog --batch demo
    # ingested 5 series records (batch 'demo', skipped 0)
    o11yguard analyze

The built-in sample dataset contains 5 metric families:

    web.request.duration      2,400,000 series  tags: service, endpoint, status, request_id
    k8s.container.cpu_usage     120,000 series  tags: cluster, namespace, pod
    db.query.time                48,000 series  tags: service, query_hash
    app.logins.total              2,400 series  tags: service, region
    legacy.heartbeat                  12 series  tags: env

Totals: 2,570,412 series. At the default rate of $45.00 per 1k series/month
that is an estimated $115,668.54/month, broken down as:

    web.request.duration      2,400,000 series  est. $108,000.00/mo
    k8s.container.cpu_usage     120,000 series  est. $5,400.00/mo
    db.query.time                48,000 series  est. $2,160.00/mo
    app.logins.total              2,400 series  est. $108.00/mo
    legacy.heartbeat                  12 series  est. $0.54/mo

So `analyze` prints (exact rendering):

    o11yguard estimate (all costs are estimates)
      metrics: 5
      series:  2,570,412
      est. monthly cost: $115,668.54
      est. projected run rate: $115,668.54

    top 5 cost drivers:
      1. web.request.duration  series=2,400,000  tags=service, endpoint, status, request_id  est. $108,000.00/mo
      2. k8s.container.cpu_usage  series=120,000  tags=cluster, namespace, pod  est. $5,400.00/mo
      3. db.query.time  series=48,000  tags=service, query_hash  est. $2,160.00/mo
      4. app.logins.total  series=2,400  tags=service, region  est. $108.00/mo
      5. legacy.heartbeat  series=12  tags=env  est. $0.54/mo

    top 5 tag offenders by est. cost:
      1. service  metrics=3  series=2,450,400  est. $110,268.00/mo
      2. endpoint  metrics=1  series=2,400,000  est. $108,000.00/mo
      3. request_id  metrics=1  series=2,400,000  est. $108,000.00/mo
      4. status  metrics=1  series=2,400,000  est. $108,000.00/mo
      5. cluster  metrics=1  series=120,000  est. $5,400.00/mo

Continue the end-to-end flow:

    o11yguard budget set 200000
    # budget set: $200,000.00 (warn at 80%)
    o11yguard budget status
    # budget:   $200,000.00 / month
    # warn at:  80%
    # run rate: est. $115,668.54 / month
    # state:    ok
    o11yguard plan --top 3
    # | Metric | Lever | Est. savings/mo | Confidence | Detail |
    # |---|---|---|---|---|
    # | web.request.duration | drop-tag | ... | medium | Drop tag '...' ... |
    # | ... (ranked by estimated savings, highest first) |
    # Total est. savings: $.../mo (estimates only)

## Command reference

### o11yguard --version

Print the version and exit 0.

    o11yguard --version

### o11yguard sample

Print the built-in sample cardinality dataset as JSON to stdout. Nothing is written to the store.

    o11yguard sample > s.json
    o11yguard sample | python3 -m json.tool | head -20

### o11yguard ingest

Parse a local JSON/CSV file and replace its batch in the store. Re-ingesting the same `--batch` name replaces that batch's rows; other batches are untouched.

    o11yguard ingest s.json --provider datadog --batch demo
    o11yguard ingest metrics.csv --provider generic --batch july
    o11yguard ingest metrics.csv --provider generic --batch july --per-1k-rate 45.0

Flags:

- `file` (positional): path to a local JSON or CSV file.
- `--provider datadog|generic` (default: `datadog`): input shape (see "Ingest formats").
- `--batch NAME` (default: `cli`): batch name; re-ingesting a name replaces its rows.
- `--per-1k-rate RATE`: USD per 1000 series per month applied when a row carries no cost value (default: `45.0`). Must be > 0.

Exit code 1 on missing file, unknown provider, non-positive rate, or an unrecognized payload shape. Malformed rows are skipped with a warning on stderr (`skipped N` in the success line) rather than failing the whole ingest.

### o11yguard analyze

Report estimated monthly cost drivers from the stored records.

    o11yguard analyze
    o11yguard analyze --top 10
    o11yguard analyze --json
    o11yguard analyze --top 3 --json

Flags:

- `--top N` (default: `5`): rows per list (cost drivers and tag offenders). Must be >= 0.
- `--json`: emit machine-stable JSON instead of the human report.

Human output labels every money figure `est.` and ends with the two ranked lists shown in the quickstart.

### o11yguard budget set

Validate and persist the monthly budget (USD).

    o11yguard budget set 200000
    o11yguard budget set 120000 --warn-pct 90

Arguments:

- `amount` (positional): monthly budget in USD, must be > 0.
- `--warn-pct PCT` (default: `80`): warn once the run rate reaches this percentage; must be strictly between 0 and 100.

Prints e.g. `budget set: $200,000.00 (warn at 80%)`.

### o11yguard budget status

Compare the estimated run rate against the stored budget.

    o11yguard budget status
    o11yguard budget status --json

Flags:

- `--json`: emit machine-stable JSON instead of the human report.

State logic: `breach` when run rate >= budget, `warn` when run rate >= budget * warn_pct / 100, else `ok`. Exit codes are designed for CI/cron: 0 ok, 2 breach, 3 warn (see "Exit codes"). Exit code 1 if no budget was ever set.

Example (human):

    budget:   $200,000.00 / month
    warn at:  80%
    run rate: est. $115,668.54 / month
    state:    ok

### o11yguard plan

Print ranked cost-saving recommendations with estimated savings. Prints to stdout; nothing is written.

    o11yguard plan
    o11yguard plan --top 3
    o11yguard plan --format yaml
    o11yguard plan --format json

Flags:

- `--top N` (default: `10`): max recommendations. Must be >= 0.
- `--format md|yaml|json` (default: `md`): `md` renders a markdown table plus a `Total est. savings` line; `yaml` and `json` emit the recommendation list.
- `--lever LEVER` (repeatable): only show recommendations from the named lever(s). Valid levers: `drop-tag`, `rollup`, `move-to-logs`, `reduce-retention`, `sample`, `decommission`. An unknown lever is an error (exit 1).
- `--min-savings USD` (default: `0`): only show recommendations with at least this much estimated monthly savings. Must be >= 0.

Levers, in priority order per metric (at most one recommendation per stored record): `drop-tag`, `rollup`, `move-to-logs`, `reduce-retention`, `sample`, `decommission`. Recommendations with under $1 of estimated savings are omitted. The `drop-tag` saving assumes removing one of `n` tag keys cuts series to `ceil(series ** ((n-1)/n))` (a rough geometric-mean approximation); the percentage savings for the other levers (80/30/50/100%) are planning placeholders, not measured outcomes. `rollup` fires for metric families with >= 10,000 series and at most 1 tag key, where dropping a tag cannot help; its 50% saving is a placeholder like `sample`. The `--lever` and `--min-savings` flags filter the final ranked list, so they combine with `--top`.

### o11yguard series list

List stored series records, optionally filtered by metric prefix. Read-only.

    o11yguard series list
    o11yguard series list --metric web. --limit 5
    o11yguard series list --limit 50 --json
    o11yguard series list --sort name

Flags:

- `--metric PREFIX` (default: all): only metrics starting with this prefix.
- `--limit N` (default: `20`): max rows. Must be >= 0.
- `--sort cost|name` (default: `cost`): `cost` orders by highest monthly cost first; `name` orders alphabetically by metric. An invalid value is an argparse error (exit 2).
- `--json`: emit machine-stable JSON instead of the table.

Human output is a table with `METRIC`, `SERIES`, `EST. $/MO`, and `TAGS` columns, ordered by cost descending (or alphabetically by metric with `--sort name`). Prints `no series stored (nothing ingested yet)` when empty.

### o11yguard series drop-tag

What-if only: estimate the effect of dropping a tag from a metric. Does not mutate anything.

    o11yguard series drop-tag web.request.duration request_id

Arguments are exact names: `metric` must match a stored metric exactly, and `tag` must be one of its tag keys. Exit code 1 on unknown metric or a tag not present on that metric.

Example:

    what-if: drop tag 'request_id' from 'web.request.duration' (nothing was changed)
      series:   2,400,000 -> ~61,000
      est. monthly cost: $108,000.00 -> $~2,700.00
      est. monthly savings: $~105,200.00
      note: Estimate only: assumes dropping one tag key leaves ceil(series ** ((n-1)/n)) series.
    nothing was changed: this command only reports an estimate.

(Exact new-series/savings figures follow the `ceil(series ** ((n-1)/n))` heuristic; the series line above is rounded for illustration.)

## JSON schemas

All `--json` output is key-sorted. Money is rounded to 2 decimals; `sample` record costs come from the pricing function (4-decimal rounding).

`sample` (top-level object):

    {
      "generated_at": "<UTC ISO-8601 timestamp>",
      "provider": "datadog",
      "series": [
        {"metric": str, "tag_keys": [str], "series": int, "monthly_cost_usd": float}
      ]
    }

`analyze --json` (summary object):

    {
      "drivers": [
        {"metric": str, "series": int, "tag_keys": [str], "est_monthly_cost_usd": float}
      ],
      "est_monthly_cost_usd": float,
      "estimates": true,
      "metrics": int,
      "projected_run_rate_usd": float,
      "series": int,
      "tag_offenders": [
        {"tag_key": str, "metrics": int, "series": int, "est_monthly_cost_usd": float}
      ]
    }

`drivers` are aggregated per metric name (series and cost summed, tag keys unioned) and sorted by cost descending. In `tag_offenders`, `metrics` is the count of distinct metric names carrying that tag key.

`budget status --json`:

    {
      "budget_usd": float,
      "run_rate_usd": float,
      "state": "ok|warn|breach",
      "warn_pct": float
    }

`plan --format json` (list of recommendations):

    [
      {
        "id": str,
        "metric": str,
        "lever": "drop-tag|rollup|reduce-retention|sample|move-to-logs|decommission",
        "detail": str,
        "est_monthly_savings_usd": float,
        "confidence": "low|medium|high",
        "risk": str
      }
    ]

`id` is `<lever>:<metric>` or, for drop-tag, `<lever>:<metric>:<tag>`.

`series list --json` (list of stored records, cost-descending):

    [
      {
        "metric": str,
        "tag_keys": [str],
        "series": int,
        "monthly_cost_usd": float,
        "source": str,
        "first_seen": "<UTC ISO-8601>",
        "last_seen": "<UTC ISO-8601>"
      }
    ]

## Ingest formats

Generic provider (`--provider generic`):

- JSON: a bare list of records, or an object with a `series` list (`{"series": [...]}`), or a single record object. Example:

      [{"metric": "api.hits", "tag_keys": ["service", "endpoint"], "series": 12000}]

- CSV: header row plus data rows. Columns: `metric`, `tag_keys` (semicolon-separated; commas also accepted), `series`, and optional `monthly_cost_usd`. Example:

      metric,tag_keys,series,monthly_cost_usd
      api.hits,service;endpoint,12000,540.00
      db.query.time,service;query_hash,48000,

Tolerant key aliases (generic JSON and CSV header):

- `metric`: `metric_name`, `name`
- `tag_keys`: `tags`, `tag_list`
- `series`: `series_count`, `custom_series`, `num_series`
- `monthly_cost_usd`: `cost`, `monthly_cost`

When no usable cost value is present, cost is computed as `series / 1000 * rate` with `--per-1k-rate`. Tag strings split on `;` (or `,`). Rows with a missing/blank metric, or a series count that is missing, non-numeric, non-integer, or <= 0, are skipped with a stderr warning.

Datadog provider (`--provider datadog`): accepts Datadog usage-export JSON. Per-metric records are searched in order under `data[].attributes.usage[]` (metrics usage API payload), `data[]` (API resource list), `usage[]` (bare usage object), or `series[]` (series-list payload). Per-record aliases: `metric_name`/`name`/`metric` for the metric; `num_series`/`series_count`/`custom_series`/`series` for the count; `tags`/`tag_list` for tag keys. Anything else raises a `ValueError` naming the expected shape.

## Pricing model

Default rate: $45.00 per 1k series/month. This is an estimate derived from Datadog's published ~$0.045/custom-metric/month list price at the time of writing (one active series treated as one billable custom metric; high-cardinality tags multiply the bill, so treat every figure as approximate). All cost figures in CLI output are labeled estimates. Override per ingest with `--per-1k-rate`.

## Exit codes

    0  ok (including --version/--help; budget status ok)
    1  user error: bad input, unknown file, unknown metric/tag, missing budget
    2  argparse usage error (bad flag/value, e.g. `--sort bogus`); also budget breached on `budget status` (run rate >= budget)
    3  warn threshold crossed on `budget status` (run rate >= budget * warn_pct / 100)

Note: argparse usage errors (bad flags) exit 2, matching argparse's own convention. `budget status` with no budget set exits 1.

## Data & state

- State directory: `O11YGUARD_DATA_DIR` when set, otherwise `~/.o11yguard/`. The SQLite database is `o11yguard.db` inside it.
- Tables: `series` (one aggregated row per ingested record: metric, tag keys as JSON, series count, monthly cost estimate, batch source, first/last seen) and `budget` (single row: amount, warn percent, currency, updated timestamp). Raw ingested files are never copied into the DB verbatim beyond aggregation.
- Budgets are in USD. All timestamps are UTC ISO-8601.
- `series drop-tag` never writes to the store; it only prints an estimate.

## Limitations

v0.1.1 non-goals: applying policies (no auto-remediation, no write path), APM/log/trace cost, multi-currency, hosted dashboards, provider pricing auto-sync. There is no `usage fetch` network command in this version; the tool is fully offline.

## License

MIT. See LICENSE.
