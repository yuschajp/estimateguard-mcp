"""Shared regional cost data for EstimateGuard.

Holds the seed table, trade/basis normalization, and the raw lookup used by
both ``get_cost_range`` and ``evaluate_estimate``. Money is Decimal;
rounding to cents happens only at output.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import benchmarks

CENT = Decimal("0.01")
ZIP_RE = re.compile(r"^\d{5}$")


def _zip3_of(zip_code: str) -> str:
    digits = re.sub(r"\D", "", zip_code or "")
    return digits[:3] if len(digits) >= 3 else ""


# Provenance tag for the legacy in-memory scaffolding rows below. They are
# illustrative values for offline use, never observed job data.
SCAFFOLD_PROVENANCE = (
    "Legacy scaffolding seed (illustrative values for offline use only). "
    "Not observed EstimateGuard job data (sample_size is illustrative)."
)

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
    "square foot": BASIS_PER_SQFT,
    "square feet": BASIS_PER_SQFT,
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
    """Map a free-text scope to a pricing basis, or None.

    Conservative on purpose: a per-unit basis is only declared when the
    scope phrases pricing per unit ("per sq ft", "per square", "$/sqft").
    A bare size mention ("asphalt shingle 2000 sqft") is a hint about the
    job, not a pricing basis -- returning None lets the lookup search every
    basis and the hint disambiguate across them.
    """
    text = scope.strip().lower().replace("  ", " ")
    if text in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text]
    if re.search(r"per\s+(sq\.?\s*ft|sqft|square\s*foot)", text):
        return BASIS_PER_SQFT
    if "/sq" in text or "a square foot" in text:
        return BASIS_PER_SQFT
    if re.search(r"per\s+(roofing\s+)?squares?", text):
        return BASIS_PER_SQUARE
    if any(
        phrase in text
        for phrase in ("lump sum", "per project", "fixed price", "flat rate")
    ):
        return BASIS_FLAT
    return None


# Length units. A ridge vent at $12.50 per linear foot and a roof at $6.50
# per square foot are priced per different things, so a length unit must
# never resolve to an area basis. Bare "foot"/"feet" is ambiguous on an
# estimate and is far more often linear (gutter, ridge, trim), so it is
# treated as a length unit too: no basis, hence no comparison, rather than a
# comparison against the wrong kind of price.
_LENGTH_UNITS = frozenset(
    {
        "linear foot", "linear feet", "linear ft", "lineal foot",
        "lineal feet", "lin ft", "lf", "foot", "feet", "ft",
        "yard", "yards", "inch", "inches",
    }
)


def is_length_unit(unit: str) -> bool:
    """True when a line-item unit measures length, not area or count."""
    return (unit or "").strip().lower().replace(".", "") in _LENGTH_UNITS


def basis_from_unit(unit: str) -> Optional[str]:
    """Map a line-item unit word to a pricing basis, or None if unknown."""
    text = unit.strip().lower().replace(".", "")
    if text in _LENGTH_UNITS:
        return None
    if text in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text]
    # crude singularization: "boxes" -> "box"
    if text.endswith("s") and text[:-1] in _BASIS_SYNONYMS:
        return _BASIS_SYNONYMS[text[:-1]]
    return None


def _scaffold_row(row: dict) -> dict:
    """Copy a scaffolding row with its honesty tag attached."""
    tagged = dict(row)
    tagged.setdefault("provenance", SCAFFOLD_PROVENANCE)
    tagged.setdefault("service_type", None)
    tagged.setdefault("region", None)
    tagged.setdefault("source", "scaffolding")
    return tagged


def seed_row(
    trade: str,
    basis: Optional[str],
    zip_code: str,
    hint: Optional[str] = None,
    require_hint: bool = False,
) -> Optional[dict]:
    """Raw seed row for a normalized (trade, zip), or None.

    ``basis=None`` searches every pricing basis and lets the hint
    disambiguate across them. When the benchmark database is reachable it
    is authoritative: rows come from the imported seed benchmarks with
    ``sample_size=0`` and a provenance string, and a miss stays a miss (no
    scaffolding fallback). When the database is unreachable (local dev /
    tests), the legacy in-memory scaffolding table answers instead.
    """
    if benchmarks.db_reachable():
        row = benchmarks.lookup(
            trade, basis, _zip3_of(zip_code), hint, require_hint=require_hint
        )
        if row is not None:
            row = dict(row)
            # The row's own basis labels the unit; fall back to the
            # requested basis only when the row doesn't name one.
            row["unit"] = BASIS_LABEL.get(
                row.get("basis") or basis, row.get("unit")
            )
        return row
    raw = SEED.get((trade, basis, zip_code))
    return _scaffold_row(raw) if raw is not None else None


def describe_options(
    trade: str, basis: Optional[str], zip_code: str
) -> list[str]:
    """Human-readable service-type options for an ambiguous (trade, basis).

    Used to explain a miss honestly instead of guessing. Empty when there
    are no candidates at all.
    """
    if benchmarks.db_reachable():
        cands = benchmarks.find_candidates(trade, basis, _zip3_of(zip_code))
        return [f"{c['service_type']} ({c['region']})" for c in cands]
    return [
        f"scaffolding {BASIS_LABEL.get(b, b)}"
        for b in bases_for_trade_zip(trade, zip_code)
    ]


def count_matches(
    trade: str, basis: Optional[str], zip_code: str, hint: Optional[str]
) -> Optional[int]:
    """How many seed rows the hint identifies (0, 1, or more).

    None when the database is unreachable; the caller then falls back to
    the scaffolding behavior.
    """
    if not benchmarks.db_reachable():
        return None
    cands = benchmarks.find_candidates(trade, basis, _zip3_of(zip_code))
    return benchmarks.count_winners(cands, hint)


def bases_for_trade_zip(trade: str, zip_code: str) -> list[str]:
    """Which pricing bases have seed data for this trade+zip."""
    if benchmarks.db_reachable():
        return benchmarks.bases_for(trade, _zip3_of(zip_code))
    return sorted({b for (t, b, z) in SEED if t == trade and z == zip_code})


def trade_has_coverage(trade: str, zip_code: str) -> bool:
    """True when any seed data exists for this trade+zip."""
    if benchmarks.db_reachable():
        return benchmarks.has_coverage(trade, _zip3_of(zip_code))
    return any(True for (t, b, z) in SEED if t == trade and z == zip_code)
