#!/usr/bin/env python3
"""Local tests for evaluate_estimate (parser + arithmetic). No network, no DB."""

import sys
from decimal import Decimal

sys.path.insert(0, ".")

from estimate_eval import (  # noqa: E402
    dec_mul,
    evaluate,
    infer_trade,
    parse_estimate_text,
    pct_display,
    round_cents,
    to_decimal,
)

CLEAN_ESTIMATE = """\
TopLine Roofing LLC
Estimate for: Jane Doe

1. Tear off old shingles 20 squares @ $350.00/square = $7,000.00
2. Install architectural shingles 20 squares @ $425.00/square = $8,500.00
3. Replace pipe flashing 6 each @ $85.00 = $510.00
4. Dumpster and disposal 1 lot @ $900.00 = $900.00

Subtotal: $16,910.00
Total: $16,910.00
"""

ERROR_ESTIMATE = """\
1. Tear off old shingles 20 squares @ $350.00 = $7,000.00
2. Install architectural shingles 20 squares @ $425.00 = $8,600.00
3. Replace pipe flashing 6 each @ $85.00 = $510.00

Total: $16,110.00
"""


def test_parse_clean():
    items, quoted = parse_estimate_text(CLEAN_ESTIMATE)
    assert len(items) == 4, f"expected 4 items, got {len(items)}"
    assert items[0].description == "Tear off old shingles"
    assert items[0].quantity == "20"
    assert items[0].unit == "squares"
    assert items[0].unit_price == "350.00"
    assert items[0].stated_line_total == "7000.00"
    assert quoted == "16910.00"


def test_infer_trade():
    assert infer_trade(CLEAN_ESTIMATE) == "roofing"
    assert infer_trade("paint the living room walls") == "interior painting"
    assert infer_trade("1. Mow lawn 1 lot @ $50.00") is None


def test_pure_arithmetic():
    assert dec_mul(Decimal("3"), Decimal("19.99")) == Decimal("59.97")
    assert round_cents(Decimal("2.675")) == Decimal("2.68")  # ROUND_HALF_UP
    assert round_cents(Decimal("2.674")) == Decimal("2.67")
    assert to_decimal("$7,000.00") == Decimal("7000.00")
    assert pct_display(Decimal("0.4167")) == 42


def test_clean_evaluation():
    res = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing")
    assert "error" not in res, res
    assert res["computed_total"] == "16910.00"
    assert res["quoted_total"] == "16910.00"
    assert res["total_discrepancy"] == "0.00"
    assert all(not li["mismatch"] for li in res["parsed_line_items"])
    # 20 squares @ 425 vs median 575 -> (425-575)/575 = -0.2609 -> low
    line2 = res["per_line_variance"][0]
    assert line2["flag"] == "low", line2
    assert line2["benchmark_median"] == "575.00"
    assert line2["variance_pct"] == "-0.2609"
    # line 1 @ $350 pulls the benchmarked subtotal below the low bound
    assert res["overall_flag"] == "below_range"
    assert res["calculation_trail"], "trail must be non-empty"


def test_arithmetic_error_is_authoritative():
    res = evaluate(ERROR_ESTIMATE, "10001", trade="roofing")
    assert "error" not in res, res
    line2 = res["parsed_line_items"][1]
    assert line2["mismatch"] is True
    assert line2["stated_line_total"] == "8600.00"
    assert line2["computed_line_total"] == "8500.00"  # Python's value wins
    assert res["computed_total"] == "16010.00"  # 7000+8500+510
    assert any("Line 2" in f and "$8500.00" in f for f in res["findings"]), res["findings"]
    ops = [s["operation"] for s in res["calculation_trail"]]
    assert "stated_compare" in ops
    cmp_step = next(s for s in res["calculation_trail"] if s["operation"] == "stated_compare" and s["inputs"]["line"] == 2)
    assert cmp_step["result"] == "true"


def test_variance_flags():
    text = "1. Install shingles 20 squares @ ${p} = ${t}"
    hi = evaluate(text.format(p="800.00", t="16000.00"), "10001", trade="roofing")
    assert hi["per_line_variance"][0]["flag"] == "high"  # (800-575)/575 = 0.3913
    assert hi["per_line_variance"][0]["variance_pct"] == "0.3913"
    lo = evaluate(text.format(p="400.00", t="8000.00"), "10001", trade="roofing")
    assert lo["per_line_variance"][0]["flag"] == "low"
    ok = evaluate(text.format(p="600.00", t="12000.00"), "10001", trade="roofing")
    assert ok["per_line_variance"][0]["flag"] == "normal"


def test_overall_flags():
    text = "1. Install shingles 20 squares @ ${p} = ${t}"
    above = evaluate(text.format(p="900.00", t="18000.00"), "10001", trade="roofing")
    assert above["overall_flag"] == "above_range"  # 18000 > 20*800
    within = evaluate(text.format(p="575.00", t="11500.00"), "10001", trade="roofing")
    assert within["overall_flag"] == "within_range"
    below = evaluate(text.format(p="400.00", t="8000.00"), "10001", trade="roofing")
    assert below["overall_flag"] == "below_range"  # 8000 < 20*425


def test_insufficient_data():
    text = (
        "1. Install shingles 20 squares @ $575.00 = $11,500.00\n"
        "2. Haul away debris 1 lot @ $20,000.00 = $20,000.00\n"
    )
    res = evaluate(text, "10001", trade="roofing")
    assert "error" not in res, res
    # line 2: 'lot' -> flat basis; roofing/10001 has no flat seed -> no_data.
    # per_line_variance is sorted by dollars desc, so the no_data line is first.
    assert res["per_line_variance"][0]["flag"] == "no_data"
    # benchmarked dollars 11500 / 31500 = 36.5% < 50%
    assert res["overall_flag"] == "insufficient_data"
    assert "excluded from the overall rating" in res["coverage_note"]


def test_truncation():
    lines = "\n".join(
        f"{i}. Install shingles 20 squares @ $575.00 = $11,500.00" for i in range(1, 21)
    )
    res = evaluate(lines, "10001", trade="roofing")
    assert len(res["per_line_variance"]) == 15
    assert "5 smaller lines omitted" in res["coverage_note"]


def test_quoted_total_discrepancy():
    res = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing", quoted_total=18000)
    assert res["quoted_total"] == "18000.00"
    assert res["total_discrepancy"] == "-1090.00"  # 16910 - 18000
    assert any("differs from our line-by-line math" in f for f in res["findings"])


def test_quoted_total_exact_match_is_exact_zero():
    # quoted_total arrives as a JSON float. When it exactly matches the
    # computed total, the discrepancy must be exactly 0.00 -- a near-zero
    # float artifact (e.g. 1E-12) would fail this assertion.
    res = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing", quoted_total=16910.00)
    assert "error" not in res, res
    assert res["computed_total"] == "16910.00"
    assert res["quoted_total"] == "16910.00"
    assert res["total_discrepancy"] == "0.00", res["total_discrepancy"]
    assert Decimal(res["total_discrepancy"]) == Decimal("0.00")


def test_quoted_total_discrepancy_exact_cents():
    # A float with fractional cents must convert exactly, not approximately:
    # 16910.00 - 17000.50 is exactly -90.50.
    res = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing", quoted_total=17000.50)
    assert "error" not in res, res
    assert res["quoted_total"] == "17000.50"
    assert res["total_discrepancy"] == "-90.50", res["total_discrepancy"]


def test_errors():
    r = evaluate("   ", "10001")
    assert r["error"] == "empty_estimate" and r["reason"]
    r = evaluate("x" * 50_001, "10001")
    assert r["error"] == "input_too_large" and r["reason"]
    r = evaluate("Hello, this is not an estimate at all.", "10001")
    assert r["error"] == "no_line_items" and r["reason"]
    r = evaluate("ABC Corp\nTotal: $100", "10001")
    assert r["error"] == "no_line_items" and r["reason"]
    r = evaluate(CLEAN_ESTIMATE, "99999", trade="roofing")
    assert r["error"] == "no_coverage" and "99999" in r["reason"]
    r = evaluate(CLEAN_ESTIMATE, "ABCDE", trade="roofing")
    assert r["error"] == "invalid_zip" and r["reason"]
    r = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing", quoted_total=float("nan"))
    assert r["error"] == "invalid_quoted_total" and r["reason"]


def test_no_floats_in_output():
    import json

    res = evaluate(CLEAN_ESTIMATE, "10001", trade="roofing")

    def walk(o):
        if isinstance(o, float):
            raise AssertionError(f"float leaked into output: {o!r}")
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(res)
    json.dumps(res)  # must serialize


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
