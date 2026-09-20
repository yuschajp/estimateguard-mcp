# EstimateGuard MCP

Minimal remote MCP server exposing two tools over streamable HTTP.

Cost figures come from a Postgres seed-benchmark table (`benchmark_ranges`,
322 city-level rows across 14 metros and 5 trades, imported from the legacy
estimate-reviewer's published-guide research). Every seed row carries
`sample_size=0` and a `provenance` string: seed benchmarks are published
guide data, never observed EstimateGuard jobs, and the table is never
aggregated with `estimate_observations`. When the database is unreachable
(local dev / tests), a hardcoded scaffolding table in `costdata.py` answers
instead; its rows are tagged as illustrative, not observed.

- MCP endpoint: `https://<service>/mcp` (streamable HTTP, no auth)
- Health check: `GET https://<service>/health` ->
  `{"status":"ok","service":"estimateguard-mcp","db":{"connected":true,"observation_rows":12,"last_write_at":"2026-09-19T20:15:00+00:00"}}`.
  `db` carries counts and a timestamp only, never observation content. If the
  database is unreachable the endpoint still returns 200 with
  `{"connected":false,"reason":"<error type>"}`.

## Tool: get_cost_range

**Get Regional Cost Range** — look up what a home project usually costs near a
US ZIP code.

Input: `trade` (string), `scope` (string), `zip` (string)

The scope can declare a pricing unit ("per square", "per sq ft") or just
name the job ("asphalt shingle 2000 sqft", "full hvac system replacement").
A declared unit constrains the search to that basis; a bare job description
searches every basis and the description disambiguates across them, so a
"2000 sqft" project matches the flat project-price row rather than a
per-square-foot row.

The two per-area bases interconvert, since a roofing square is exactly 100
square feet: a per-square query in a metro whose benchmark is published per
square foot is answered from that row restated (×100 / ÷100 on the price
range only — hourly labor rates and per-project permit costs carry over
untouched), and every restated row says so in its `provenance`. Flat project
prices never convert, because that would require inventing a job size.

Output: `low`, `median`, `high` (USD decimal strings, rounded to cents),
`unit`, `sample_size`, `as_of_date`, `provenance` (where the figures came
from), `service_type` (the specific benchmarked job), `region` (the metro
the benchmark covers — city-level data is never presented as ZIP-level
data), `labor_rate_low`/`labor_rate_high` and `permit_cost_low`/
`permit_cost_high` (when the source published them), and `reason` (null on
success).

When there is no data for the trade + ZIP, all price fields are null and
`reason` explains why. When a scope matches several benchmarks (e.g.
"flat roof" matching every flat roofing job in the metro), the reason lists
the options instead of guessing. The tool never guesses and never
interpolates between cities.

## Tool: evaluate_estimate

**Evaluate Contractor Estimate** — check a contractor's written estimate line
by line.

Input: `estimate_text` (string, required), `zip` (string, required),
`trade` (string, optional — inferred when absent), `quoted_total` (number,
optional).

Each priced line should look like one of these:

```
Install architectural shingles 20 squares @ $425.00/square = $8,500.00
20 squares of tear-off @ $350.00 = $7,000.00
```

The parser extracts only descriptions, quantities, units, unit prices, and
stated line totals. Python then recomputes every number (Decimal throughout,
rounded to cents at output only): line totals, the subtotal, per-line variance
against the regional benchmark, and the overall rating. If a printed line total
disagrees with quantity × unit price, the recomputed value is authoritative
and the mismatch is reported as a finding — never silently corrected.

A line is rated only when its own description names the benchmark it would be
rated against. Seed service types describe whole jobs — "Cost per Sq Ft
(Asphalt)" is a finished roof, tear-off and disposal included — so rating a
tear-off line against one would report a fair price as far below market.
Length units (`linear feet`, `lf`) resolve to no pricing basis at all, so a
length-priced line is never compared against an area price.

That leaves area trades unrated line by line, since no line of a roofing
estimate matches a whole-roof benchmark. `whole_estimate_comparison` closes
that gap: it takes the job size from the largest area-priced line (20
squares → 2,000 sq ft; area lines are never summed, because tear-off and
install describe the same roof), looks up the job-level range for that size,
and compares the recomputed total against it. A benchmark naming the exact
job size is preferred; otherwise the all-in per-square-foot figure is
multiplied by the area, and the response says which happened
(`scaled_by_area`). Sizes are never interpolated between two published rows.
When the line-by-line path produced no verdict, this comparison supplies
`overall_flag`.

Output: `parsed_line_items`, `computed_total`, `quoted_total`,
`total_discrepancy`, `per_line_variance` (flags `low`/`normal`/`high`/`no_data`,
truncated to the 15 largest-dollar lines), `whole_estimate_comparison` (null
when the estimate prices nothing by area, or no job-level benchmark applies),
`overall_flag`
(`below_range`/`within_range`/`above_range`/`insufficient_data`), `findings`
(plain homeowner language), `calculation_trail` (every computation step), and
`coverage_note`. Errors return `{"error", "reason"}` instead.

## Run locally

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
# MCP endpoint: http://localhost:8000/mcp
# Health:       http://localhost:8000/health
```

## Test against the live endpoint

```sh
.venv/bin/python tests/test_live.py https://<service>/mcp
# or: ESTIMATEGUARD_MCP_URL=https://<service>/mcp .venv/bin/python tests/test_live.py
```

Covers: seed benchmark lookups (3 trades, 3 cities), an honest null for a
basis with no data, an ambiguous scope, an unknown ZIP, and malformed input.

Unit tests for the seed benchmarks (no network, no DB):

```sh
python3 tests/test_benchmarks.py
```

Unit tests for the estimate evaluator (no network):

```sh
python3 tests/test_evaluate.py
```

Unit tests for the observation store's PII stripping (no network, no DB):

```sh
python3 tests/test_observations.py
```

## Seed benchmarks (Postgres)

`migrations/001_benchmark_ranges.sql` creates the `benchmark_ranges` table
(money is `NUMERIC`; the migration file is the single source of truth and is
loaded by `benchmarks.py`). Import the vendored CSV
(`data/benchmarks_7cities.csv`: the legacy estimate-reviewer's
`COMPETITIVE_DATA_7_CITIES.csv`, one field quoted to repair a shifted row,
plus the rows appended in September 2026 — both described below):

```sh
DATABASE_URL=postgres://... python3 scripts/import_benchmarks.py
```

The import upserts on `(region, trade, service_type)` — safe to rerun. The
service also seeds automatically on first boot when the table is empty.

The importer rejects a row whose columns have shifted or whose values
contradict each other (low above the average, high below it, an hourly labor
rate above $1,000) and logs it, rather than storing prices that are not what
their column names say. One shipped row needed this: an unquoted comma in
`Panel Upgrade (200 amp, underground service)` had shifted every later
column, so San Francisco served a $5,500 median for a $12,000 job and an
$18,500 hourly labor rate. The CSV field is now quoted.

Because the import only ever upserts, a row stored under a service type the
CSV no longer contains — including one stored under a corrupted name — stays
in the table and can still be served. To make the table match the CSV
exactly:

```sh
DATABASE_URL=postgres://... python3 scripts/sync_benchmarks.py --dry-run
DATABASE_URL=postgres://... python3 scripts/sync_benchmarks.py
```

It imports, then deletes any row the CSV no longer names. Idempotent, and
the dry run prints what it would delete. **A database seeded before the row
above was repaired still holds the corrupted row; run this once against it.**

Coverage: 14 metros (Atlanta, Austin, Boston, Chicago, Dallas-Fort Worth,
Denver, Houston, Los Angeles, Miami, NYC, Phoenix, San Francisco, Seattle,
Washington DC) × 5 trades (Electrical, HVAC, Kitchen Remodel, Plumbing,
Roofing), 322 rows. Every metro now carries every trade. Every row has
`sample_size=0` and a `provenance` string naming its source; rows are stored
at city level (`region`) and query ZIPs are routed to their metro for lookup
only.

Interior painting is not in the dataset, so painting estimates have nothing
to compare against.

Rows added in the September 2026 pass carry their own source date in the
CSV's optional `As_Of_Date` column, since they came from guides published
across several months; a row without one takes its dataset generation's
date. Where a guide published a range but no typical figure, the midpoint is
used and that row's `Data_Source` says so, so a derived middle never reads
as a published one. `scripts/add_coverage_rows_2026_09.py` is the record of
that pass, with each row's source URL and publication date.

## Observation store (Postgres)

Every successful `evaluate_estimate` call writes one row per line item to
the `estimate_observations` table. Only whitelisted, PII-free fields are
stored: `observed_at`, `zip3`, `trade`, `scope`, `line_description`,
`quantity`, `unit`, `unit_price`, `computed_line_total`.

PII (names, street addresses, phones, emails, contractor business names,
license numbers) is stripped from the estimate text during parsing, before
any observation row is built. Strip counts per category are logged for
auditing. The raw estimate text is never stored.

The table is indexed on `(trade, scope, zip3)` for future benchmark
aggregation, plus `observed_at`. Set `DATABASE_URL` on the service to
enable it; if the database is unreachable the tool still returns its
result and the failure is logged server-side.

## Deploy (Render)

Via blueprint (`render.yaml`): New > Blueprint > point at this repo.
Or via API: create a Docker web service from this repo with health check path
`/health`.
