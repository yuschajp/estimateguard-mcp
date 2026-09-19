#!/usr/bin/env python3
"""Exercise the live EstimateGuard MCP endpoint from outside the deploy environment.

Covers:
  1. valid input      -> a cost range, reason is null
  2. unknown zip      -> nulls with an explicit reason
  3. malformed input  -> nulls with an explicit reason

Usage:
    python tests/test_live.py https://<service>/mcp
    ESTIMATEGURD_MCP_URL=https://<service>/mcp python tests/test_live.py
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
    url = os.environ.get("ESTIMATEGURD_MCP_URL")
    if not url and len(sys.argv) > 1:
        url = sys.argv[1]
    if not url:
        sys.exit(
            "usage: test_live.py https://<service>/mcp "
            "(or set ESTIMATEGURD_MCP_URL)"
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

    data = structured(await call_tool(url, {"trade": "roofing", "scope": "per square", "zip": "10001"}))
    check("valid input", data, True, failures)

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
