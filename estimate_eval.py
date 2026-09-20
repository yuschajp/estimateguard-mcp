"""Deterministic contractor-estimate evaluation.

HARD CONSTRAINT — separation of parsing and arithmetic:
  * The parser extracts ONLY typed strings and raw numeric literals from the
    estimate text: description, quantity, unit, unit_price, stated_line_total.
  * Every number downstream is recomputed by the named pure functions below
    using Decimal. There is no model in this process, so no number can
    originate from model output. Rounding to cents happens only at output,
    ROUND_HALF_UP.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from costdata import (
    CENT,
    ZIP_RE,
    basis_from_unit,
    normalize_trade,
    seed_row,
    trade_has_coverage,
)
from observations import (
    build_rows,
    log_strip_counts,
    record_observations,
    strip_pii,
)

MAX_INPUT_CHARS = 50_000
VARIANCE_HIGH = Decimal("0.25")  # more than 25% above median -> "high"
VARIANCE_LOW = Decimal("-0.25")  # more than 25% below median -> "low"
MAX_VARIANCE_LINES = 15
MAX_FINDINGS = 8
MAX_TRAIL_STEPS = 40
RESPONSE_CHAR_BUDGET = 7800  # ~2000 tokens


# ---------------------------------------------------------------------------
# Named pure arithmetic functions. Every number in the response flows through
# one of these. Inputs and outputs are Decimal; floats never appear.
# ---------------------------------------------------------------------------

def dec_mul(a: Decimal, b: Decimal) -> Decimal:
    """Pure: multiply two Decimals."""
    return a * b


def dec_add(a: Decimal, b: Decimal) -> Decimal:
    """Pure: add two Decimals."""
    return a + b


def dec_sub(a: Decimal, b: Decimal) -> Decimal:
    """Pure: subtract two Decimals."""
    return a - b


def dec_div(a: Decimal, b: Decimal) -> Decimal:
    """Pure: divide two Decimals. Raises on a zero divisor."""
    if b == 0:
        raise ZeroDivisionError("division by zero in dec_div")
    return a / b


def dec_abs(a: Decimal) -> Decimal:
    """Pure: absolute value of a Decimal."""
    return abs(a)


def round_cents(value: Decimal) -> Decimal:
    """Pure: round to cents, ROUND_HALF_UP. Output quantization only."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def round_4(value: Decimal) -> Decimal:
    """Pure: round a ratio to 4 decimal places, ROUND_HALF_UP."""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def pct_points(ratio: Decimal) -> Decimal:
    """Pure: convert a fraction to percentage points (0.41 -> 41)."""
    return dec_mul(ratio, Decimal("100"))


def pct_display(ratio: Decimal) -> int:
    """Pure: whole-percent magnitude for homeowner-facing text."""
    return int(dec_abs(pct_points(ratio)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money_str(value: Decimal) -> str:
    """Pure: Decimal -> cents-rounded decimal string for output."""
    return str(round_cents(value))


class CalcTrail:
    """Ordered record of computation steps.

    The orchestrator appends one entry per named-function application, so a
    reader can verify every number in the response.
    """

    def __init__(self) -> None:
        self._steps: list[dict] = []

    def add(self, operation: str, inputs: dict, result: str) -> None:
        self._steps.append(
            {
                "step": len(self._steps) + 1,
                "operation": operation,
                "inputs": inputs,
                "result": result,
            }
        )

    def entries(self) -> list[dict]:
        return list(self._steps)


_PRIORITY_OPS = frozenset(
    {
        "stated_compare",
        "variance",
        "benchmarked_subtotal",
        "range_bounds",
        "coverage_ratio",
        "overall_flag",
        "total_discrepancy",
    }
)


def trim_trail(steps: list[dict], cap: int) -> list[dict]:
    """Pure: shrink a trail to ``cap`` entries, keeping the most informative
    operations and renumbering steps."""
    if len(steps) <= cap:
        return steps
    priority = [s for s in steps if s["operation"] in _PRIORITY_OPS]
    rest = [s for s in steps if s["operation"] not in _PRIORITY_OPS]
    kept = priority + rest[: max(0, cap - len(priority))]
    kept.sort(key=lambda s: s["step"])
    return [
        {"step": i + 1, **{k: v for k, v in s.items() if k != "step"}}
        for i, s in enumerate(kept)
    ]


# ---------------------------------------------------------------------------
# Parser. Output is ONLY typed strings and raw numeric literals.
# ---------------------------------------------------------------------------

@dataclass
class RawItem:
    description: str  # typed string
    quantity: str  # raw numeric literal, as written
    unit: str  # typed string
    unit_price: str  # raw numeric literal, as written
    stated_line_total: Optional[str]  # raw numeric literal, as written


def _money(name: str) -> str:
    return (
        rf"\$?\s*(?P<{name}>\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?"
        rf"|\d+(?:\.\d{{1,2}})?)"
    )


# "<desc> <qty> <unit> @ $<price> [= $<total>]", e.g.
# "Tear off old shingles 20 squares @ $350.00 = $7,000.00"
_LINE_1 = re.compile(
    r"^(?:\d{1,2}[.)]\s*)?"
    r"(?P<desc>.+?)\s+"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit>[A-Za-z]+(?:\s+[A-Za-z]+)?)\s*"
    r"(?:@|x|×)\s*" + _money("price") + r"(?:\s*/\s*[A-Za-z]+)?"
    r"(?:\s*=\s*" + _money("total") + r")?"
    r"\s*$"
)

# "<qty> <unit> [of] <desc> @ $<price> [= $<total>]", e.g.
# "20 squares of tear-off @ $350.00 = $7,000.00"
_LINE_2 = re.compile(
    r"^(?:\d{1,2}[.)]\s*)?"
    r"(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<unit>[A-Za-z]+(?:\s+[A-Za-z]+)?)\s+"
    r"(?:of\s+)?(?P<desc>.+?)\s+"
    r"(?:@|x|×)\s*" + _money("price") + r"(?:\s*/\s*[A-Za-z]+)?"
    r"(?:\s*=\s*" + _money("total") + r")?"
    r"\s*$"
)

_SKIP_START = re.compile(
    r"^\s*(estimate|proposal|quote|prepared\s+for|prepared\s+by|customer|client|"
    r"homeowner|bill\s+to|contractor|from\s*:|license|lic\.|phone|tel\.?|"
    r"e-?mail|address|date|valid\s+(until|through)|notes?|terms|warranty|"
    r"payment|thank|invoice)",
    re.IGNORECASE,
)
_SKIP_WORDS = re.compile(
    r"\b(sub\s*total|subtotal|grand\s+total|total(\s+due)?|tax(es)?|"
    r"balance(\s+due)?|amount\s+due)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}")

_QUOTED_TOTAL_RE = re.compile(
    r"\b(?:grand\s+total|total\s+due|amount\s+due|balance\s+due|total)\b"
    r"[^$\d]{0,25}\$?\s*"
    r"(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)",
    re.IGNORECASE,
)

_EXPLICIT_TRADE_RE = re.compile(r"^\s*trade\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

_TRADE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("roofing", ["roof", "shingle", "flashing", "gutter", "ridge vent"]),
    ("interior painting", ["paint", "primer", "drywall"]),
    ("bathroom remodel", ["bathroom", "vanity", "toilet", "tub", "shower"]),
    ("hvac replacement", ["hvac", "furnace", "air conditioner", "condenser", "heat pump"]),
    ("kitchen remodel", ["kitchen", "cabinet", "countertop", "backsplash"]),
    ("water heater replacement", ["water heater"]),
    ("electrical panel upgrade", ["panel upgrade", "breaker", "circuit panel", "amp service"]),
    ("deck building", ["deck"]),
    ("concrete driveway", ["driveway", "concrete"]),
    ("garage door replacement", ["garage door"]),
]


def _clean_num(literal: str) -> str:
    """Strip currency formatting from a raw numeric literal."""
    return literal.replace(",", "").replace("$", "").strip()


def to_decimal(literal: str) -> Decimal:
    """Parse a raw numeric literal to Decimal. Raises ValueError if malformed."""
    try:
        return Decimal(_clean_num(literal))
    except InvalidOperation as exc:
        raise ValueError(f"not a numeric literal: {literal!r}") from exc


def parse_estimate_text(text: str) -> tuple[list[RawItem], Optional[str]]:
    """Parse estimate text into raw line items plus an optional detected total.

    Returns only typed strings and raw numeric literals — no arithmetic
    happens here.
    """
    items: list[RawItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _EMAIL_RE.search(line) or _PHONE_RE.search(line):
            continue
        if _SKIP_START.match(line):
            continue
        if _SKIP_WORDS.search(line):
            continue
        match = _LINE_1.match(line) or _LINE_2.match(line)
        if not match:
            continue
        desc = re.sub(r"\s+", " ", match.group("desc")).strip(" -–—:;,.")[:120]
        if not desc:
            continue
        items.append(
            RawItem(
                description=desc,
                quantity=_clean_num(match.group("qty")),
                unit=match.group("unit").strip().lower(),
                unit_price=_clean_num(match.group("price")),
                stated_line_total=(
                    _clean_num(match.group("total")) if match.group("total") else None
                ),
            )
        )

    quoted: Optional[str] = None
    for raw_line in reversed(text.splitlines()):
        match = _QUOTED_TOTAL_RE.search(raw_line)
        if match:
            quoted = _clean_num(match.group(1))
            break
    return items, quoted


def infer_trade(text: str) -> Optional[str]:
    """Infer a canonical trade name from keywords, or None."""
    explicit = _EXPLICIT_TRADE_RE.search(text)
    if explicit:
        return normalize_trade(explicit.group(1))
    lowered = text.lower()
    for trade, keywords in _TRADE_KEYWORDS:
        if any(kw in lowered for kw in keywords):
            return trade
    return None


def _err(code: str, reason: str) -> dict:
    return {"error": code, "reason": reason}


def evaluate(
    estimate_text: str,
    zip_code: str,
    trade: Optional[str] = None,
    quoted_total=None,
    source: Optional[str] = None,
) -> dict:
    """Evaluate a contractor estimate. Returns the result dict or an
    ``{"error", "reason"}`` dict. All numbers are recomputed from the raw
    literals by the named pure functions above.

    ``source`` is the observation provenance stamp ('production' | 'test' |
    'verification'); when omitted it resolves automatically (env var,
    default 'production'), so test/verification callers stamp correctly
    without remembering to pass it.
    """
    trail = CalcTrail()

    # ---- input validation (no numbers involved) ----
    if not isinstance(estimate_text, str) or not estimate_text.strip():
        return _err(
            "empty_estimate",
            "The estimate text was empty. Paste the estimate text and I'll evaluate it.",
        )
    if len(estimate_text) > MAX_INPUT_CHARS:
        return _err(
            "input_too_large",
            f"The estimate text is {len(estimate_text):,} characters, over the "
            f"{MAX_INPUT_CHARS:,} character limit. Please shorten it and try again.",
        )
    if not isinstance(zip_code, str) or not ZIP_RE.match(zip_code.strip()):
        return _err(
            "invalid_zip",
            f"'{zip_code}' doesn't look like a 5-digit US ZIP code.",
        )
    zip_norm = zip_code.strip()

    # ---- PII strip: during parsing, before any observation is built ----
    # The parser below only ever sees the cleaned text, so descriptions can
    # never carry names, addresses, phones, emails, contractor identifiers
    # or license numbers into the response or the observation store.
    estimate_text, strip_counts = strip_pii(estimate_text)
    log_strip_counts(strip_counts)

    quoted_dec: Optional[Decimal] = None
    if quoted_total is not None:
        if isinstance(quoted_total, bool):
            return _err(
                "invalid_quoted_total",
                "quoted_total must be a number, for example 17500.",
            )
        try:
            quoted_dec = Decimal(str(quoted_total))
        except (InvalidOperation, ValueError):
            return _err(
                "invalid_quoted_total",
                "quoted_total must be a number, for example 17500.",
            )
        if not quoted_dec.is_finite() or quoted_dec < 0:
            return _err(
                "invalid_quoted_total",
                "quoted_total must be a non-negative number.",
            )

    # ---- parsing: strings + raw literals only ----
    raw_items, detected_total = parse_estimate_text(estimate_text)
    if not raw_items:
        return _err(
            "no_line_items",
            "I couldn't find any priced line items. Each line should look like: "
            "'Install shingles 20 squares @ $425.00 = $8,500.00'.",
        )
    if quoted_dec is None and detected_total is not None:
        quoted_dec = to_decimal(detected_total)

    # ---- trade ----
    trade_norm: Optional[str] = None
    if isinstance(trade, str) and trade.strip():
        trade_norm = normalize_trade(trade)
    if trade_norm is None:
        trade_norm = infer_trade(estimate_text)

    # ---- coverage gate: zero benchmark data for this trade+zip is an error --
    if trade_norm is not None and not trade_has_coverage(trade_norm, zip_norm):
        return _err(
            "no_coverage",
            f"We don't have benchmark cost data for {trade_norm} near ZIP "
            f"{zip_norm} yet, so there's nothing to compare this estimate against.",
        )

    # ---- per-line arithmetic (Python recomputes everything) ----
    lines: list[dict] = []
    for idx, raw in enumerate(raw_items, start=1):
        qty = to_decimal(raw.quantity)
        price = to_decimal(raw.unit_price)
        computed = dec_mul(qty, price)
        trail.add(
            "line_total",
            {
                "line": idx,
                "description": raw.description,
                "quantity": raw.quantity,
                "unit_price": raw.unit_price,
            },
            str(computed),
        )
        stated = to_decimal(raw.stated_line_total) if raw.stated_line_total else None
        mismatch = stated is not None and stated != computed
        if stated is not None:
            trail.add(
                "stated_compare",
                {
                    "line": idx,
                    "stated_line_total": raw.stated_line_total,
                    "computed_line_total": str(computed),
                },
                str(mismatch).lower(),
            )
        lines.append(
            {
                "n": idx,
                "description": raw.description,
                "quantity": raw.quantity,
                "unit": raw.unit,
                "unit_price": raw.unit_price,
                "qty_dec": qty,
                "price_dec": price,
                "computed_dec": computed,
                "stated_dec": stated,
                "mismatch": mismatch,
            }
        )

    computed_total = Decimal("0")
    for line in lines:
        computed_total = dec_add(computed_total, line["computed_dec"])
    trail.add(
        "subtotal",
        {"line_totals": [str(line["computed_dec"]) for line in lines]},
        str(computed_total),
    )

    # ---- benchmarks + per-line variance ----
    for line in lines:
        basis = basis_from_unit(line["unit"]) if trade_norm else None
        # require_hint: nothing here chose the service type, so a lone
        # benchmark row must still be named by the line's own description
        # before it rates that line. See benchmarks.resolve_candidates.
        row = (
            seed_row(
                trade_norm,
                basis,
                zip_norm,
                hint=line["description"],
                require_hint=True,
            )
            if (trade_norm and basis)
            else None
        )
        if row is None:
            line["benchmark"] = None
            line["variance"] = None
            line["flag"] = "no_data"
            continue
        median = row["median"]
        variance = dec_div(dec_sub(line["price_dec"], median), median)
        trail.add(
            "variance",
            {
                "line": line["n"],
                "quoted_unit_price": str(line["price_dec"]),
                "benchmark_median": str(median),
            },
            str(variance),
        )
        if variance > VARIANCE_HIGH:
            flag = "high"
        elif variance < VARIANCE_LOW:
            flag = "low"
        else:
            flag = "normal"
        line["benchmark"] = row
        line["variance"] = variance
        line["flag"] = flag

    bench_lines = [line for line in lines if line["benchmark"] is not None]

    # The overall range verdict needs real bounds. Seed rows may carry a
    # median without low/high (the source published only an average); those
    # lines still get a variance flag against the median, but they cannot
    # support a below/within/above-range verdict and are excluded from it.
    range_lines = [
        line
        for line in bench_lines
        if line["benchmark"]["low"] is not None
        and line["benchmark"]["high"] is not None
    ]

    bench_subtotal = Decimal("0")
    for line in bench_lines:
        bench_subtotal = dec_add(bench_subtotal, line["computed_dec"])
    trail.add(
        "benchmarked_subtotal",
        {"lines": [line["n"] for line in bench_lines]},
        str(bench_subtotal),
    )

    range_subtotal = Decimal("0")
    for line in range_lines:
        range_subtotal = dec_add(range_subtotal, line["computed_dec"])

    range_low = Decimal("0")
    range_high = Decimal("0")
    for line in range_lines:
        row = line["benchmark"]
        range_low = dec_add(range_low, dec_mul(row["low"], line["qty_dec"]))
        range_high = dec_add(range_high, dec_mul(row["high"], line["qty_dec"]))
    trail.add(
        "range_bounds",
        {
            "lines": [line["n"] for line in range_lines],
            "low_bounds": [
                str(dec_mul(line["benchmark"]["low"], line["qty_dec"]))
                for line in range_lines
            ],
            "high_bounds": [
                str(dec_mul(line["benchmark"]["high"], line["qty_dec"]))
                for line in range_lines
            ],
        },
        json.dumps({"range_low": str(range_low), "range_high": str(range_high)}),
    )

    # ---- overall flag ----
    if computed_total == 0:
        overall_flag = "insufficient_data"
        coverage_ratio = None
    else:
        bench_dollars = range_subtotal
        coverage_ratio = dec_div(bench_dollars, computed_total)
        trail.add(
            "coverage_ratio",
            {
                "benchmarked_dollars": str(bench_dollars),
                "computed_total": str(computed_total),
            },
            str(coverage_ratio),
        )
        if coverage_ratio < Decimal("0.5"):
            overall_flag = "insufficient_data"
        elif range_subtotal < range_low:
            overall_flag = "below_range"
        elif range_subtotal > range_high:
            overall_flag = "above_range"
        else:
            overall_flag = "within_range"
    trail.add(
        "overall_flag",
        {
            "benchmarked_subtotal": str(range_subtotal),
            "range_low": str(range_low),
            "range_high": str(range_high),
        },
        overall_flag,
    )

    # ---- quoted total discrepancy ----
    discrepancy: Optional[Decimal] = None
    if quoted_dec is not None:
        discrepancy = dec_sub(computed_total, quoted_dec)
        trail.add(
            "total_discrepancy",
            {
                "computed_total": str(computed_total),
                "quoted_total": str(quoted_dec),
            },
            str(discrepancy),
        )

    # ---- findings (homeowner language) ----
    findings: list[str] = []
    for line in lines:
        if line["mismatch"]:
            findings.append(
                f"Line {line['n']} (\"{line['description']}\") lists a total of "
                f"${money_str(line['stated_dec'])}, but {line['quantity']} × "
                f"${money_str(line['price_dec'])} works out to "
                f"${money_str(line['computed_dec'])}. We've used "
                f"${money_str(line['computed_dec'])} in our math."
            )
    for line in sorted(
        bench_lines, key=lambda ln: abs(ln["variance"]), reverse=True
    ):
        if line["flag"] == "high":
            findings.append(
                f"The unit price on line {line['n']} (\"{line['description']}\") is "
                f"about {pct_display(line['variance'])}% above what's typical near "
                f"{zip_norm}."
            )
        elif line["flag"] == "low":
            findings.append(
                f"The unit price on line {line['n']} (\"{line['description']}\") is "
                f"about {pct_display(line['variance'])}% below what's typical near "
                f"{zip_norm} — confirm the materials and scope match."
            )
    if quoted_dec is not None and discrepancy != 0:
        findings.append(
            f"The total written on the estimate (${money_str(quoted_dec)}) differs "
            f"from our line-by-line math (${money_str(computed_total)}) by "
            f"${money_str(dec_abs(discrepancy))}. Worth asking the contractor about."
        )
    no_data_lines = [line for line in lines if line["benchmark"] is None]
    if no_data_lines:
        names = ", ".join(f"line {line['n']} (\"{line['description']}\")" for line in no_data_lines)
        findings.append(
            f"We don't have local benchmark data for {len(no_data_lines)} of "
            f"{len(lines)} lines ({names}), so those weren't rated."
        )
    overall_text = {
        "below_range": (
            f"The priced lines we have data for come in below the typical range "
            f"for {trade_norm or 'this trade'} near {zip_norm} — double-check what's included."
        ),
        "within_range": (
            f"The priced lines we have data for sit within the typical range for "
            f"{trade_norm or 'this trade'} near {zip_norm}."
        ),
        "above_range": (
            f"The priced lines we have data for come in above the typical range "
            f"for {trade_norm or 'this trade'} near {zip_norm}."
        ),
        "insufficient_data": (
            "We don't have enough local benchmark data to rate this estimate overall."
        ),
    }[overall_flag]
    findings.append(overall_text)
    findings = findings[:MAX_FINDINGS]

    # ---- benchmark provenance labels (deduplicated at response level) ----
    # Provenance strings vary per benchmark row (each names its own published
    # source), so repeating the full ~250-char string on every line would eat
    # the response budget. Distinct strings go once in
    # resp["benchmark_provenance"]; each labeled variance entry carries a
    # short integer ref plus its sample_size, matching get_cost_range.
    benchmark_provenance: list[str] = []
    _provenance_index: dict[str, int] = {}
    for line in lines:
        bench = line["benchmark"]
        if not bench:
            continue
        prov = bench.get("provenance") or ""
        if prov not in _provenance_index:
            _provenance_index[prov] = len(benchmark_provenance)
            benchmark_provenance.append(prov)

    # ---- per-line variance (truncate to largest-dollar lines) ----
    variance_entries = [
        {
            "description": line["description"],
            "quoted_unit_price": money_str(line["price_dec"]),
            "benchmark_median": (
                money_str(line["benchmark"]["median"]) if line["benchmark"] else None
            ),
            "variance_pct": (
                str(round_4(line["variance"])) if line["variance"] is not None else None
            ),
            "flag": line["flag"],
            # Entries with flag "no_data" carry no benchmark and get no label.
            **(
                {
                    "sample_size": line["benchmark"].get("sample_size"),
                    "provenance_ref": _provenance_index[
                        line["benchmark"].get("provenance") or ""
                    ],
                }
                if line["benchmark"]
                else {}
            ),
        }
        for line in lines
    ]
    variance_entries.sort(
        key=lambda e: next(
            line["computed_dec"]
            for line in lines
            if line["description"] == e["description"]
        ),
        reverse=True,
    )
    omitted = max(0, len(variance_entries) - MAX_VARIANCE_LINES)
    variance_entries = variance_entries[:MAX_VARIANCE_LINES]

    # ---- coverage note ----
    coverage_pct = pct_display(coverage_ratio) if coverage_ratio is not None else 0
    coverage_note = (
        f"Benchmark coverage: {len(bench_lines)} of {len(lines)} lines "
        f"({coverage_pct}% by dollar value) had local data."
    )
    if no_data_lines:
        missing = ", ".join(
            f"line {line['n']} (\"{line['description']}\")" for line in no_data_lines
        )
        coverage_note += f" Lines without data were excluded from the overall rating: {missing}."
    partial_lines = [
        line for line in bench_lines
        if line["n"] not in {r["n"] for r in range_lines}
    ]
    if partial_lines:
        partial = ", ".join(
            f"line {line['n']} (\"{line['description']}\")" for line in partial_lines
        )
        coverage_note += (
            f" Lines with a published average but no low/high bounds were rated "
            f"per-line only, not in the overall range verdict: {partial}."
        )
    if omitted:
        coverage_note += (
            f" Showing the {MAX_VARIANCE_LINES} largest of "
            f"{len(lines)} lines below; {omitted} smaller lines omitted."
        )

    # ---- assemble ----
    parsed_line_items = [
        {
            "description": line["description"],
            "quantity": line["quantity"],
            "unit": line["unit"],
            "unit_price": money_str(line["price_dec"]),
            "computed_line_total": money_str(line["computed_dec"]),
            "stated_line_total": (
                money_str(line["stated_dec"]) if line["stated_dec"] is not None else None
            ),
            "mismatch": line["mismatch"],
        }
        for line in lines
    ]

    trail_steps = trim_trail(trail.entries(), MAX_TRAIL_STEPS)

    resp = {
        "parsed_line_items": parsed_line_items,
        "computed_total": money_str(computed_total),
        "quoted_total": money_str(quoted_dec) if quoted_dec is not None else None,
        "total_discrepancy": (
            money_str(discrepancy) if discrepancy is not None else None
        ),
        "per_line_variance": variance_entries,
        "overall_flag": overall_flag,
        "findings": findings,
        "calculation_trail": trail_steps,
        "coverage_note": coverage_note,
        # Distinct benchmark provenance strings (see per-line provenance_ref).
        "benchmark_provenance": benchmark_provenance,
    }

    # ---- response ceiling: ~2000 tokens ----
    if len(json.dumps(resp)) > RESPONSE_CHAR_BUDGET:
        resp["findings"] = resp["findings"][:4]
        resp["calculation_trail"] = trim_trail(trail.entries(), 20)
        resp["coverage_note"] += " (Response trimmed to fit the size limit.)"

    # ---- observation store: one whitelisted, PII-free row per line ----
    # Built from Decimal recomputations (never raw literals, never raw text).
    # record_observations never raises; a DB outage must not fail the tool.
    obs_lines = [
        {
            "description": line["description"],
            "quantity": str(line["qty_dec"]),
            "unit": line["unit"],
            "unit_price": str(line["price_dec"]),
            "computed_line_total": str(line["computed_dec"]),
            "scope": basis_from_unit(line["unit"]) or "",
        }
        for line in lines
    ]
    record_observations(
        build_rows(
            zip_code=zip_norm,
            trade=trade_norm or "",
            lines=obs_lines,
            source=source,
        )
    )

    return resp
