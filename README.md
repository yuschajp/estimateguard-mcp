# EstimateGuard MCP

Minimal remote MCP server exposing one tool, `get_cost_range`, over
streamable HTTP. Scaffolding: prices come from a hardcoded seed table in
`server.py`, not a real dataset.

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

## Deploy (Render)

Via blueprint (`render.yaml`): New > Blueprint > point at this repo.
Or via API: create a Docker web service from this repo with health check path
`/health`.
