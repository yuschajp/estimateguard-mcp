#!/usr/bin/env python3
"""Exercise the live EstimateGuard MCP endpoint from outside the deploy environment.

Covers:
  1-3. seed benchmark lookups -> a range with sample_size 0 + provenance
  4.    no matching benchmark  -> nulls with an explicit reason (no guessing)
  5.    ambiguous scope        -> nulls with the options listed
  6.    unknown zip            -> nulls with an explicit reason
  7.    malformed input        -> nulls with an explicit reason

Usage:
    python tests/test_live.py https://<service>/mcp
    ESTIMATEGUARD_MCP_URL=https://<service>/mcp python tests/test_live.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


# The sandbox's NO_PROXY carries bracketed IPv6 literals that this httpx build
# cannot parse; narrow it to the entries this test needs (process-local only).
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "localhost,127.0.0.1"


def endpoint() -> str:
    url = os.environ.get("ESTIMATEGUARD_MCP_URL")
    if not url and len(sys.argv) > 1:
        url = sys.argv[1]
    if not url:
        sys.exit(
            "usage: test_live.py https://<service>/mcp "
            "(or set ESTIMATEGUARD_MCP_URL)"
        )
    return url.rstrip("/")


async def call_tool(url: str, args: dict):
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool("get_cost_range", args)


def structured(result) -> dict:
    sc = result.structured_content or {}
    if isinstance(sc, dict) and "result" in sc and isinstance(sc["result"], dict):
        return sc["result"]
    return sc if isinstance(sc, dict) else {}


def check(name: str, data: dict, expect_range: bool, failures: list) -> None:
    print(f"--- {name}")
    print(json.dumps(data, indent=2))
    has_range = all(data.get(k) for k in ("low", "median", "high")) and data.get("unit")
    has_reason = bool(data.get("reason"))
    if expect_range:
        if not (has_range and not has_reason):
            failures.append(f"{name}: expected a range with null reason")
    else:
        if not (data.get("low") is None and has_reason):
            failures.append(f"{name}: expected nulls with an explicit reason")


async def main() -> None:
    url = endpoint()
    failures: list[str] = []

    # Seed benchmarks (sample_size 0 + provenance) resolve by region.
    data = structured(await call_tool(url, {"trade": "plumbing", "scope": "water heater replacement", "zip": "30301"}))
    check("seed benchmark: atlanta water heater", data, True, failures)

    data = structured(await call_tool(url, {"trade": "hvac", "scope": "full hvac system replacement", "zip": "80202"}))
    check("seed benchmark: denver hvac", data, True, failures)

    data = structured(await call_tool(url, {"trade": "roofing", "scope": "asphalt shingle 2000 sqft", "zip": "80202"}))
    check("seed benchmark: denver roofing", data, True, failures)

    # NYC publishes roofing per square foot, not per square. A roofing square
    # is exactly 100 square feet, so the per-square answer is that row
    # restated -- arithmetic, not a guess, and it says so in its provenance.
    # The source row published an average and a high but no low, so this is
    # checked field by field rather than with check()'s full-range rule.
    name = "per-square NYC answered by conversion"
    data = structured(await call_tool(url, {"trade": "roofing", "scope": "per square", "zip": "10001"}))
    print(f"--- {name}")
    print(json.dumps(data, indent=2))
    if data.get("reason"):
        failures.append(f"{name}: returned a reason instead of a figure")
    if data.get("median") != "650.00":
        failures.append(f"{name}: expected 650.00 (6.50/sq ft x 100)")
    if data.get("unit") != "per roofing square":
        failures.append(f"{name}: answer is not labeled per roofing square")
    if "Restated by EstimateGuard" not in (data.get("provenance") or ""):
        failures.append(f"{name}: conversion is not disclosed in provenance")

    # Ambiguous scope: several flat roofing benchmarks near Chicago.
    data = structured(await call_tool(url, {"trade": "roofing", "scope": "flat roof", "zip": "60601"}))
    check("ambiguous scope lists options", data, False, failures)

    data = structured(await call_tool(url, {"trade": "roofing", "scope": "per square", "zip": "99999"}))
    check("unknown zip", data, False, failures)

    data = structured(await call_tool(url, {"trade": "", "scope": "per square", "zip": "ABCDE"}))
    check("malformed input", data, False, failures)

    if failures:
        print("FAIL:")
        for failure in failures:
            print(f"  - {failure}")
        sys.exit(1)
    print(f"ALL CHECKS PASSED against {url}")


if __name__ == "__main__":
    asyncio.run(main())
