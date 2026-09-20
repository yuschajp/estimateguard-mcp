"""Per-area basis conversion, unit parsing, and seed-import guards.

These cover three defects found by exercising the live server:

1. A per-square query found nothing in a metro whose roofing benchmark is
   published per square foot (and vice versa), even though a roofing square
   is exactly 100 square feet.
2. "40 linear feet @ $12.50" resolved to the per-square-foot basis, so a
   length-priced line could be rated against an area price.
3. An unquoted comma in the seed CSV shifted every later column, storing a
   project price as an hourly labor rate.
"""

from decimal import Decimal

import benchmarks
import costdata


# ---------------------------------------------------------------------------
# 1. convert_basis: exact, labeled, and refused when it would be a guess
# ---------------------------------------------------------------------------

def _row(basis, low, median, high):
    return {
        "basis": basis,
        "low": low,
        "median": median,
        "high": high,
        "provenance": "Seed benchmark.",
        "labor_rate_low": Decimal("150"),
        "permit_cost_low": Decimal("249"),
    }


def test_sqft_to_square_multiplies_by_one_hundred():
    converted = benchmarks.convert_basis(
        _row("per_sqft", None, Decimal("6.50"), Decimal("7.50")), "per_square"
    )
    assert converted["median"] == Decimal("650.00")
    assert converted["high"] == Decimal("750.00")
    assert converted["low"] is None
    assert converted["basis"] == "per_square"
    assert converted["converted_from"] == "per_sqft"


def test_square_to_sqft_divides_by_one_hundred():
    converted = benchmarks.convert_basis(
        _row("per_square", None, Decimal("647"), None), "per_sqft"
    )
    assert converted["median"] == Decimal("6.47")


def test_conversion_is_lossless_round_trip():
    original = _row("per_sqft", Decimal("5"), Decimal("6.50"), Decimal("7.50"))
    there = benchmarks.convert_basis(original, "per_square")
    back = benchmarks.convert_basis(there, "per_sqft")
    assert back["low"] == original["low"]
    assert back["median"] == original["median"]
    assert back["high"] == original["high"]


def test_conversion_labels_itself_in_provenance():
    converted = benchmarks.convert_basis(
        _row("per_sqft", None, Decimal("6.50"), None), "per_square"
    )
    assert "Restated by EstimateGuard" in converted["provenance"]
    assert "1 roofing square = 100 square feet" in converted["provenance"]


def test_conversion_leaves_labor_and_permit_costs_alone():
    # Hourly rates and per-project permit fees are not per-area figures.
    converted = benchmarks.convert_basis(
        _row("per_sqft", None, Decimal("6.50"), None), "per_square"
    )
    assert converted["labor_rate_low"] == Decimal("150")
    assert converted["permit_cost_low"] == Decimal("249")


def test_flat_prices_never_convert():
    # A project price cannot become a unit price without the job size.
    assert benchmarks.convert_basis(
        _row("flat", Decimal("9000"), Decimal("12164"), Decimal("15000")),
        "per_square",
    ) is None
    assert benchmarks.convert_basis(
        _row("per_sqft", None, Decimal("6.50"), None), "flat"
    ) is None


def test_same_basis_returns_row_unchanged():
    row = _row("per_sqft", None, Decimal("6.50"), None)
    assert benchmarks.convert_basis(row, "per_sqft") is row


# ---------------------------------------------------------------------------
# 2. Length units never resolve to an area basis
# ---------------------------------------------------------------------------

def test_length_units_have_no_basis():
    for unit in ("linear feet", "linear foot", "lin ft", "lf", "feet", "ft"):
        assert costdata.basis_from_unit(unit) is None, unit
        assert costdata.is_length_unit(unit) is True, unit


def test_area_and_count_units_still_resolve():
    assert costdata.basis_from_unit("squares") == costdata.BASIS_PER_SQUARE
    assert costdata.basis_from_unit("sq ft") == costdata.BASIS_PER_SQFT
    assert costdata.basis_from_unit("sqft") == costdata.BASIS_PER_SQFT
    assert costdata.basis_from_unit("square feet") == costdata.BASIS_PER_SQFT
    assert costdata.basis_from_unit("each") == costdata.BASIS_FLAT
    assert costdata.is_length_unit("squares") is False


# ---------------------------------------------------------------------------
# 3. Seed-import guards reject shifted columns instead of storing them
# ---------------------------------------------------------------------------

_GOOD_CSV_ROW = {
    "City": "San Francisco",
    "Trade": "Electrical",
    "Service_Type": "Panel Upgrade (200 amp, underground service)",
    "Low_Price": "5500",
    "Avg_Price": "12000",
    "High_Price": "18500",
    "Labor_Rate_Low": "100",
    "Labor_Rate_High": "200",
    "Permit_Cost": "500-1200",
    "Notes": "Underground service requires trenching",
    "Data_Source": "AlphaOmegaElectric",
}


def test_well_formed_row_imports():
    row = benchmarks._normalize_row(dict(_GOOD_CSV_ROW))
    assert row is not None
    assert row["avg_price"] == Decimal("12000")
    assert row["labor_rate_low"] == Decimal("100")


def test_row_with_overflow_columns_is_skipped():
    # csv.DictReader parks columns shifted by an unquoted comma under None.
    shifted = dict(_GOOD_CSV_ROW)
    shifted["Service_Type"] = "Panel Upgrade (200 amp"
    shifted["Low_Price"] = "underground service)"
    shifted[None] = ["AlphaOmegaElectric"]
    assert benchmarks._normalize_row(shifted) is None


def test_row_with_low_above_average_is_skipped():
    bad = dict(_GOOD_CSV_ROW, Low_Price="19000")
    assert benchmarks._normalize_row(bad) is None


def test_row_with_high_below_average_is_skipped():
    bad = dict(_GOOD_CSV_ROW, High_Price="900")
    assert benchmarks._normalize_row(bad) is None


def test_row_with_project_price_in_the_labor_column_is_skipped():
    bad = dict(_GOOD_CSV_ROW, Labor_Rate_Low="18500")
    assert benchmarks._normalize_row(bad) is None


def test_vendored_csv_imports_every_row_cleanly():
    # The shipped seed file must contain no row the guards reject.
    import csv

    with open(benchmarks.CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    normalized = [benchmarks._normalize_row(dict(r)) for r in rows]
    assert all(n is not None for n in normalized)
    assert len(normalized) == 338


# ---------------------------------------------------------------------------
# 4. A lone whole-job benchmark must not rate a component line
# ---------------------------------------------------------------------------

_WHOLE_ROOF = {
    "service_type": "Cost per Sq Ft (Asphalt)",
    "basis": "per_sqft",
    "median": Decimal("6.50"),
}


def test_lone_candidate_answers_an_explicit_cost_range_question():
    # The homeowner named the trade and scope, so the one row is the answer.
    assert benchmarks.resolve_candidates([_WHOLE_ROOF], "per sq ft") is _WHOLE_ROOF
    assert benchmarks.resolve_candidates([_WHOLE_ROOF], None) is _WHOLE_ROOF


def test_lone_candidate_does_not_rate_an_unrelated_estimate_line():
    # "Tear off" is one component of the roof that row prices in full.
    assert benchmarks.resolve_candidates(
        [_WHOLE_ROOF], "Tear off existing asphalt shingles", require_hint=True
    ) is None
    assert benchmarks.resolve_candidates(
        [_WHOLE_ROOF], "Install architectural shingles", require_hint=True
    ) is None


def test_lone_candidate_still_rates_a_line_that_names_it():
    # Every content token of the line must appear in the service type, so a
    # line has to be at least as specific as the benchmark it is rated
    # against. "Asphalt" clears that bar for "Cost per Sq Ft (Asphalt)";
    # "Asphalt roof tear-off" does not, and is left unrated.
    assert benchmarks.resolve_candidates(
        [_WHOLE_ROOF], "Asphalt", require_hint=True
    ) is _WHOLE_ROOF


def test_require_hint_needs_a_hint():
    assert benchmarks.resolve_candidates(
        [_WHOLE_ROOF], None, require_hint=True
    ) is None
