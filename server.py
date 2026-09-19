"""EstimateGuard MCP server — regional home-project cost ranges.

Exposes a single tool, ``get_cost_range``, over streamable HTTP. Backed by a
small hardcoded seed table (scaffolding, not real data).

Rules enforced here:
- All currency is ``Decimal``; values are rounded to cents at output only.
- Unknown trade+zip returns nulls plus an explicit ``reason`` — never a guess.
- All errors return structured JSON with a ``reason`` field, never a traceback.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("EstimateGuard")

CENT = Decimal("0.01")
ZIP_RE = re.compile(r"^\d{5}$")

# Pricing bases. ``scope`` from the caller is normalized to one of these.
BASIS_PER_SQUARE = "per_square"  # per roofing square (100 sq ft)
BASIS_PER_SQFT = "per_sqft"  # per square foot
BASIS_FLAT = "flat"  # per project, installed

BASIS_LABEL = {
    BASIS_PER_SQUARE: "per roofing square",
    BASIS_PER_SQFT: "per square foot",
    BASIS_FLAT: "per project (flat price)",
}

_BASIS_SYNONYMS = {
    # per roofing square
    "per square": BASIS_PER_SQUARE,
    "per roofing square": BASIS_PER_SQUARE,
    "roofing square": BASIS_PER_SQUARE,
    "square": BASIS_PER_SQUARE,
    # per square foot
    "per sq ft": BASIS_PER_SQFT,
    "per sqft": BASIS_PER_SQFT,
    "per square foot": BASIS_PER_SQFT,
    "sq ft": BASIS_PER_SQFT,
    "sqft": BASIS_PER_SQFT,
    # flat / per project
    "flat": BASIS_FLAT,
    "per project": BASIS_FLAT,
    "lump sum": BASIS_FLAT,
    "fixed price": BASIS_FLAT,
    "per job": BASIS_FLAT,
    "each": BASIS_FLAT,
    "per unit": BASIS_FLAT,
    "per system": BASIS_FLAT,
    "per door": BASIS_FLAT,
    "per panel": BASIS_FLAT,
    "installed": BASIS_FLAT,
}

_TRADE_ALIASES = {
    "roof": "roofing",
    "roof replacement": "roofing",
    "re-roof": "roofing",
    "reroof": "roofing",
    "painting": "interior painting",
    "interior paint": "interior painting",
    "hvac": "hvac replacement",
    "ac replacement": "hvac replacement",
    "furnace replacement": "hvac replacement",
    "bathroom renovation": "bathroom remodel",
    "bath remodel": "bathroom remodel",
    "kitchen renovation": "kitchen remodel",
    "water heater": "water heater replacement",
    "panel upgrade": "electrical panel upgrade",
    "electrical panel": "electrical panel upgrade",
    "deck": "deck building",
    "driveway": "concrete driveway",
    "concrete": "concrete driveway",
    "garage door": "garage door replacement",
}

# Seed table: (trade, basis, zip) -> row. Scaffolding ranges, not real data.
# Money is Decimal; rounding to cents happens only at output.
SEED: dict[tuple[str, str, str], dict] = {
    ("roofing", BASIS_PER_SQUARE, "10001"): {
        "low": Decimal("425"),
        "median": Decimal("575"),
        "high": Decimal("800"),
        "unit": "USD per roofing square (100 sq ft)",
        "sample_size": 214,
        "as_of_date": "2026-08-15",
    },
    ("interior painting", BASIS_PER_SQFT, "60601"): {
        "low": Decimal("2.50"),
        "median": Decimal("3.755"),  # exercises cent-rounding (-> 3.76)
        "high": Decimal("6.00"),
        "unit": "USD per sq ft of painted surface",
        "sample_size": 188,
        "as_of_date": "2026-08-15",
    },
    ("bathroom remodel", BASIS_FLAT, "90210"): {
        "low": Decimal("9500"),
        "median": Decimal("16500"),
        "high": Decimal("32000"),
        "unit": "USD per project",
        "sample_size": 96,
        "as_of_date": "2026-08-15",
    },
    ("hvac replacement", BASIS_FLAT, "30301"): {
        "low": Decimal("6800"),
        "median": Decimal("9500"),
        "high": Decimal("14500"),
        "unit": "USD per project",
        "sample_size": 121,
        "as_of_date": "2026-08-15",
    },
    ("kitchen remodel", BASIS_FLAT, "10001"): {
        "low": Decimal("22000"),
        "median": Decimal("38000"),
        "high": Decimal("75000"),
        "unit": "USD per project",
        "sample_size": 74,
        "as_of_date": "2026-08-15",
    },
    ("water heater replacement", BASIS_FLAT, "60601"): {
        "low": Decimal("1400"),
        "median": Decimal("2100"),
        "high": Decimal("3400"),
        "unit": "USD per unit installed",
        "sample_size": 167,
        "as_of_date": "2026-08-15",
    },
    ("electrical panel upgrade", BASIS_FLAT, "30301"): {
        "low": Decimal("1800"),
        "median": Decimal("2800"),
        "high": Decimal("4500"),
        "unit": "USD per project",
        "sample_size": 88,
        "as_of_date": "2026-08-15",
    },
    ("deck building", BASIS_PER_SQFT, "90210"): {
        "low": Decimal("28"),
        "median": Decimal("45"),
        "high": Decimal("75"),
        "unit": "USD per sq ft of deck",
        "sample_size": 63,
        "as_of_date": "2026-08-15",
    },
    ("concrete driveway", BASIS_PER_SQFT, "60601"): {
        "low": Decimal("9"),
        "median": Decimal("13"),
        "high": Decimal("19"),
        "unit": "USD per sq ft",
        "sample_size": 112,
        "as_of_date": "2026-08-15",
    },
    ("garage door replacement", BASIS_FLAT, "10001"): {
        "low": Decimal("1200"),
        "median": Decimal("2200"),
        "high": Decimal("4200"),
        "unit": "USD per door installed",
        "sample_size": 91,
        "as_of_date": "2026-08-15",
    },
}


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
    reason: Optional[str] = Field(
        default=None,
        description="Why no range is returned. Null when a range is returned.",
    )


def _cents(value: Decimal) -> str:
    """Round a Decimal to cents for output. Never called on floats."""
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _no_range(reason: str) -> CostRangeResult:
    return CostRangeResult(reason=reason)


def _normalize_basis(scope: str) -> Optional[str]:
    text = scope.strip().lower()
    if text in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text]
    # Fall back to keyword matching for longer descriptions.
    if "square foot" in text or "sq ft" in text or "sqft" in text:
        return BASIS_PER_SQFT
    if "square" in text:
        return BASIS_PER_SQUARE
    if any(word in text for word in ("flat", "project", "lump", "fixed", "installed", "replacement", "remodel", "upgrade", "repaint")):
        return BASIS_FLAT
    return None


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

    trade_norm = _TRADE_ALIASES.get(trade.strip().lower(), trade.strip().lower())
    basis = _normalize_basis(scope)
    if basis is None:
        return _no_range(
            f"I couldn't tell how '{scope.strip()}' is priced. "
            "Try 'per square', 'per sq ft', or 'flat'."
        )

    row = SEED.get((trade_norm, basis, zip_norm))
    if row is not None:
        return CostRangeResult(
            low=_cents(row["low"]),
            median=_cents(row["median"]),
            high=_cents(row["high"]),
            unit=row["unit"],
            sample_size=row["sample_size"],
            as_of_date=row["as_of_date"],
        )

    same_trade_zip = sorted(
        {BASIS_LABEL[b] for (t, b, z) in SEED if t == trade_norm and z == zip_norm}
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
        "Returns the low, typical (median), and high price from recent local jobs, "
        "what the prices are per, how many jobs the range is based on, and the date. "
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


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "estimateguard-mcp"})


app = mcp.http_app(transport="http", path="/mcp")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
