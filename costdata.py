"""Shared regional cost data for EstimateGuard.

Holds the seed table, trade/basis normalization, and the raw lookup used by
both ``get_cost_range`` and ``evaluate_estimate``. Money is Decimal;
rounding to cents happens only at output.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

CENT = Decimal("0.01")
ZIP_RE = re.compile(r"^\d{5}$")

# Pricing bases.
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
    "squares": BASIS_PER_SQUARE,
    "sq": BASIS_PER_SQUARE,
    # per square foot
    "per sq ft": BASIS_PER_SQFT,
    "per sqft": BASIS_PER_SQFT,
    "per square foot": BASIS_PER_SQFT,
    "sq ft": BASIS_PER_SQFT,
    "sqft": BASIS_PER_SQFT,
    "sf": BASIS_PER_SQFT,
    "foot": BASIS_PER_SQFT,
    "feet": BASIS_PER_SQFT,
    # flat / per project
    "flat": BASIS_FLAT,
    "per project": BASIS_FLAT,
    "lump sum": BASIS_FLAT,
    "fixed price": BASIS_FLAT,
    "per job": BASIS_FLAT,
    "job": BASIS_FLAT,
    "jobs": BASIS_FLAT,
    "lot": BASIS_FLAT,
    "lots": BASIS_FLAT,
    "each": BASIS_FLAT,
    "ea": BASIS_FLAT,
    "per unit": BASIS_FLAT,
    "unit": BASIS_FLAT,
    "units": BASIS_FLAT,
    "per system": BASIS_FLAT,
    "system": BASIS_FLAT,
    "per door": BASIS_FLAT,
    "door": BASIS_FLAT,
    "per panel": BASIS_FLAT,
    "panel": BASIS_FLAT,
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


def cents(value: Decimal) -> str:
    """Round a Decimal to cents for output. Never called on floats."""
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def normalize_trade(trade: str) -> str:
    """Canonical trade name, lowercased, aliases resolved."""
    return _TRADE_ALIASES.get(trade.strip().lower(), trade.strip().lower())


def normalize_basis(scope: str) -> Optional[str]:
    """Map a free-text scope/unit to a pricing basis, or None."""
    text = scope.strip().lower().replace("  ", " ")
    if text in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text]
    # Fall back to keyword matching for longer descriptions.
    if "square foot" in text or "sq ft" in text or "sqft" in text:
        return BASIS_PER_SQFT
    if "square" in text:
        return BASIS_PER_SQUARE
    if any(
        word in text
        for word in (
            "flat", "project", "lump", "fixed", "installed", "replacement",
            "remodel", "upgrade", "repaint",
        )
    ):
        return BASIS_FLAT
    return None


def basis_from_unit(unit: str) -> Optional[str]:
    """Map a line-item unit word to a pricing basis, or None if unknown."""
    text = unit.strip().lower().replace(".", "")
    if text in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text]
    # crude singularization: "boxes" -> "box"
    if text.endswith("s") and text[:-1] in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text[:-1]]
    return None


def seed_row(trade: str, basis: str, zip_code: str) -> Optional[dict]:
    """Raw seed row for a normalized (trade, basis, zip), or None."""
    return SEED.get((trade, basis, zip_code))


def bases_for_trade_zip(trade: str, zip_code: str) -> list[str]:
    """Which pricing bases have seed data for this trade+zip."""
    return sorted({b for (t, b, z) in SEED if t == trade and z == zip_code})


def trade_has_coverage(trade: str, zip_code: str) -> bool:
    """True when any seed data exists for this trade+zip."""
    return any(True for (t, b, z) in SEED if t == trade and z == zip_code)
