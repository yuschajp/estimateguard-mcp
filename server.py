"""EstimateGuard MCP server — regional home-project cost intelligence.

Exposes two tools over streamable HTTP:
- ``get_cost_range``: typical cost range for a trade near a ZIP code.
- ``evaluate_estimate``: parse a contractor estimate, recompute every number
  in Python (Decimal only), and compare each line against local benchmarks.

Rules enforced here:
- All currency is ``Decimal``; values are rounded to cents at output only.
- Unknown trade+zip returns nulls plus an explicit ``reason`` — never a guess.
- All errors return structured JSON with a ``reason`` field, never a traceback.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from costdata import (
    BASIS_LABEL,
    ZIP_RE,
    bases_for_trade_zip,
    cents as _cents,
    describe_options,
    normalize_basis,
    normalize_trade,
    seed_row,
)
from estimate_eval import evaluate as _evaluate_estimate
from observations import db_status
from benchmarks import count_rows as benchmark_count

mcp = FastMCP("EstimateGuard")


class CostRangeResult(BaseModel):
    """Tool output. Either a range (reason is null) or nulls with a reason."""

    low: Optional[str] = Field(
        default=None, description="Low end of the price range in USD, as a decimal string."
    )
    median: Optional[str] = Field(
        default=None, description="Typical (median) price in USD, as a decimal string."
    )
    high: Optional[str] = Field(
        default=None, description="High end of the price range in USD, as a decimal string."
    )
    unit: Optional[str] = Field(
        default=None, description="What the prices are per, e.g. 'USD per project'."
    )
    sample_size: Optional[int] = Field(
        default=None, description="How many past jobs the range is based on."
    )
    as_of_date: Optional[str] = Field(
        default=None, description="Date the figures were last refreshed (YYYY-MM-DD)."
    )
    provenance: Optional[str] = Field(
        default=None,
        description="Where the figures came from. Seed benchmarks are published "
        "guide data (sample_size 0), never observed EstimateGuard jobs.",
    )
    service_type: Optional[str] = Field(
        default=None,
        description="The specific benchmarked job, e.g. 'Water Heater Replacement'.",
    )
    region: Optional[str] = Field(
        default=None,
        description="The metro the benchmark covers, e.g. 'Denver'. City-level "
        "data is never presented as ZIP-level data.",
    )
    labor_rate_low: Optional[str] = Field(
        default=None,
        description="Low end of the hourly labor rate in USD, when the source "
        "published one. Null when the source had no labor rate.",
    )
    labor_rate_high: Optional[str] = Field(
        default=None,
        description="High end of the hourly labor rate in USD, when the source "
        "published one. Null when the source had no labor rate.",
    )
    permit_cost_low: Optional[str] = Field(
        default=None,
        description="Low end of the permit cost in USD, when the source "
        "published one. Null when the source had no permit cost.",
    )
    permit_cost_high: Optional[str] = Field(
        default=None,
        description="High end of the permit cost in USD, when the source "
        "published one. Null when the source had no permit cost.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Why no range is returned. Null when a range is returned.",
    )


def _no_range(reason: str) -> CostRangeResult:
    return CostRangeResult(reason=reason)


def _lookup(trade: str, scope: str, zip_code: str) -> CostRangeResult:
    if not isinstance(trade, str) or not trade.strip():
        return _no_range("Please tell me the trade, for example 'roofing' or 'interior painting'.")
    if not isinstance(scope, str) or not scope.strip():
        return _no_range(
            "Please describe the job, for example 'per square', 'per sq ft', or 'full replacement'."
        )
    if not isinstance(zip_code, str):
        return _no_range("Please give the ZIP code as text, for example '10001'.")
    zip_norm = zip_code.strip()
    if not ZIP_RE.match(zip_norm):
        return _no_range(f"'{zip_norm}' doesn't look like a 5-digit US ZIP code.")

    trade_norm = normalize_trade(trade)
    basis = normalize_basis(scope)
    if basis is None:
        return _no_range(
            f"I couldn't tell how '{scope.strip()}' is priced. "
            "Try 'per square', 'per sq ft', or 'flat'."
        )

    row = seed_row(trade_norm, basis, zip_norm, hint=scope)
    if row is not None:
        return CostRangeResult(
            low=_cents(row["low"]) if row["low"] is not None else None,
            median=_cents(row["median"]),
            high=_cents(row["high"]) if row["high"] is not None else None,
            unit=row["unit"],
            sample_size=row["sample_size"],
            as_of_date=row["as_of_date"],
            provenance=row.get("provenance"),
            service_type=row.get("service_type"),
            region=row.get("region"),
            labor_rate_low=(
                _cents(row["labor_rate_low"])
                if row.get("labor_rate_low") is not None
                else None
            ),
            labor_rate_high=(
                _cents(row["labor_rate_high"])
                if row.get("labor_rate_high") is not None
                else None
            ),
            permit_cost_low=(
                _cents(row["permit_cost_low"])
                if row.get("permit_cost_low") is not None
                else None
            ),
            permit_cost_high=(
                _cents(row["permit_cost_high"])
                if row.get("permit_cost_high") is not None
                else None
            ),
        )

    options = describe_options(trade_norm, basis, zip_norm)
    if options:
        shown = "; ".join(options[:6])
        more = f" (+{len(options) - 6} more)" if len(options) > 6 else ""
        return _no_range(
            f"For {trade.strip()} near ZIP {zip_norm}, '{scope.strip()}' matches "
            f"several benchmarks: {shown}{more}. Name the specific job and I'll "
            "look it up — I won't guess which one you mean."
        )

    same_trade_zip = sorted(
        {BASIS_LABEL[b] for b in bases_for_trade_zip(trade_norm, zip_norm)}
    )
    if not same_trade_zip:
        return _no_range(
            f"We don't have cost data for {trade.strip()} near ZIP {zip_norm} yet, "
            "so we won't guess at a range."
        )
    have = " or ".join(same_trade_zip)
    return _no_range(
        f"For {trade.strip()} near ZIP {zip_norm} we only have pricing {have}; "
        f"'{scope.strip()}' doesn't match, so we won't guess at a range."
    )


@mcp.tool(
    name="get_cost_range",
    title="Get Regional Cost Range",
    description=(
        "Look up what a home project usually costs near a US ZIP code. "
        "Give the trade (for example roofing, interior painting, or bathroom remodel), "
        "a short description of the job (for example 'per square', 'per sq ft', or 'full replacement'), "
        "and the 5-digit ZIP code. "
        "Returns the low, typical (median), and high price from published seed "
        "benchmarks, what the prices are per, and where the figures came from. "
        "Seed benchmarks are labeled with sample_size 0 and a provenance string: "
        "they are researched guide data, not observed EstimateGuard jobs. "
        "Labor rates and permit costs are included when the source published them. "
        "If there is no data for that trade and ZIP code, it says so plainly instead of guessing."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
)
def get_cost_range(trade: str, scope: str, zip: str) -> CostRangeResult:
    try:
        return _lookup(trade, scope, zip)
    except Exception:
        # Structured error, never a stack trace.
        return _no_range("Something went wrong looking up that cost range. Please try again.")


@mcp.tool(
    name="evaluate_estimate",
    title="Evaluate Contractor Estimate",
    description=(
        "Check a contractor's written estimate line by line. "
        "Paste the estimate text (each priced line should look like "
        "'Install shingles 20 squares @ $425.00 = $8,500.00'), give the "
        "5-digit ZIP code, and optionally the trade and the total printed on "
        "the estimate. Returns every line with its recomputed total, flags any "
        "line whose printed total doesn't match its quantity × price, compares "
        "each unit price against what's typical near your ZIP, and gives a "
        "plain-language verdict a homeowner can act on."
    ),
    annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
)
def evaluate_estimate(
    estimate_text: str,
    zip: str,
    trade: Optional[str] = None,
    quoted_total: Optional[float] = None,
) -> dict:
    try:
        return _evaluate_estimate(
            estimate_text, zip, trade=trade, quoted_total=quoted_total
        )
    except Exception:
        # Structured error, never a stack trace.
        return {
            "error": "internal_error",
            "reason": "Something went wrong evaluating that estimate. Please try again.",
        }


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    # db_status() never raises: a dead database yields
    # {"connected": False, "reason": ...} and this stays a 200.
    # Run in a thread so the sync psycopg probe never blocks the loop.
    db = await asyncio.to_thread(db_status)
    brows = await asyncio.to_thread(benchmark_count)
    if brows is not None:
        db["benchmark_rows"] = brows
    return JSONResponse(
        {"status": "ok", "service": "estimateguard-mcp", "db": db}
    )


app = mcp.http_app(transport="http", path="/mcp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
