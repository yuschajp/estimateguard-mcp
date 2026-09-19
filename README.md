# EstimateGuard MCP

Minimal remote MCP server exposing two tools over streamable HTTP.
Scaffolding: prices come from a hardcoded seed table in `costdata.py`, not a
real dataset.

- MCP endpoint: `https://<service>/mcp` (streamable HTTP, no auth)
- Health check: `GET https://<service>/health` -> `{"status":"ok","service":"estimateguard-mcp"}`

## Tool: get_cost_range

**Get Regional Cost Range** — look up what a home project usually costs near a
US ZIP code.

Input: `trade` (string), `scope` (string), `zip` (string)

Output: `low`, `median`, `high` (USD decimal strings, rounded to cents),
`unit`, `sample_size`, `as_of_date`, and `reason` (null on success).

When there is no data for the trade + ZIP, all price fields are null and
`reason` explains why. The tool never guesses.

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

Output: `parsed_line_items`, `computed_total`, `quoted_total`,
`total_discrepancy`, `per_line_variance` (flags `low`/`normal`/`high`/`no_data`,
truncated to the 15 largest-dollar lines), `overall_flag`
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

Covers: valid input, unknown ZIP, malformed input.

Unit tests for the estimate evaluator (no network):

```sh
python3 tests/test_evaluate.py
```

Unit tests for the observation store's PII stripping (no network, no DB):

```sh
python3 tests/test_observations.py
```

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
