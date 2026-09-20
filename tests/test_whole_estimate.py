"""Whole-estimate comparison against a job-level benchmark.

Seed benchmarks for area trades price a finished job; estimates split that
job across component lines. These cover the size derivation and the rules
that keep the comparison from becoming a guess.
"""

from decimal import Decimal

import estimate_eval
from estimate_eval import derive_job_sqft


def _line(quantity, unit, description="Work", price="100"):
    return {
        "description": description,
        "quantity": quantity,
        "unit": unit,
        "qty_dec": Decimal(quantity),
        "price_dec": Decimal(price),
    }


# ---------------------------------------------------------------------------
# Job size derivation
# ---------------------------------------------------------------------------

def test_squares_convert_to_square_feet():
    area, line = derive_job_sqft([_line("20", "squares")])
    assert area == Decimal("2000")
    assert line["unit"] == "squares"


def test_square_feet_are_taken_as_written():
    area, _ = derive_job_sqft([_line("1800", "sq ft")])
    assert area == Decimal("1800")


def test_area_lines_are_not_added_together():
    # Tear-off and install describe the same roof; summing would double it.
    area, _ = derive_job_sqft(
        [
            _line("20", "squares", "Tear off existing shingles"),
            _line("20", "squares", "Install architectural shingles"),
        ]
    )
    assert area == Decimal("2000")


def test_largest_area_line_wins_across_mixed_units():
    area, line = derive_job_sqft(
        [_line("15", "squares"), _line("2200", "sq ft")]
    )
    assert area == Decimal("2200")
    assert line["unit"] == "sq ft"


def test_length_and_count_lines_do_not_measure_the_job():
    assert derive_job_sqft(
        [_line("40", "linear feet"), _line("1", "each"), _line("12", "sheets")]
    ) is None


def test_no_area_lines_means_no_measurement():
    assert derive_job_sqft([]) is None


# ---------------------------------------------------------------------------
# Benchmark selection: exact size, else scaled per-square-foot, else nothing
# ---------------------------------------------------------------------------

def test_sqft_token_is_whole_feet():
    assert estimate_eval._sqft_token(Decimal("2000")) == "2000 sqft"
    assert estimate_eval._sqft_token(Decimal("2000.00")) == "2000 sqft"


def test_sized_benchmark_is_preferred_and_not_scaled(monkeypatch):
    calls = []

    def fake_seed_row(trade, basis, zip_code, hint=None, require_hint=False):
        calls.append(basis)
        if basis == estimate_eval.BASIS_FLAT:
            return {
                "service_type": "Asphalt Shingle (2000 sqft)",
                "low": Decimal("9000"),
                "median": Decimal("12164"),
                "high": Decimal("15000"),
            }
        raise AssertionError("per-sqft fallback should not be reached")

    monkeypatch.setattr(estimate_eval, "seed_row", fake_seed_row)
    bench = estimate_eval.whole_job_benchmark("roofing", "10001", Decimal("2000"))
    assert bench["scaled_by_area"] is False
    assert bench["median"] == Decimal("12164")
    assert calls == [estimate_eval.BASIS_FLAT]


def test_per_sqft_benchmark_is_scaled_by_area(monkeypatch):
    def fake_seed_row(trade, basis, zip_code, hint=None, require_hint=False):
        if basis == estimate_eval.BASIS_FLAT:
            return None  # no row names this job size
        return {
            "service_type": "Cost per Sq Ft (Asphalt)",
            "low": None,
            "median": Decimal("5.00"),
            "high": Decimal("7.00"),
        }

    monkeypatch.setattr(estimate_eval, "seed_row", fake_seed_row)
    bench = estimate_eval.whole_job_benchmark("roofing", "33101", Decimal("1800"))
    assert bench["scaled_by_area"] is True
    assert bench["median"] == Decimal("9000.00")
    assert bench["high"] == Decimal("12600.00")
    assert bench["low"] is None


def test_sizes_are_never_interpolated(monkeypatch):
    # Neither a sized row nor a per-sqft row: no comparison, not a guess
    # between the 1500 and 2000 sq ft rows that do exist.
    monkeypatch.setattr(
        estimate_eval,
        "seed_row",
        lambda *a, **k: None,
    )
    assert estimate_eval.whole_job_benchmark(
        "roofing", "10001", Decimal("1800")
    ) is None
