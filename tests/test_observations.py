"""Tests for the observation store: PII stripping, whitelisted rows, no-DB safety."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observations import (
    ALLOWED_ROW_FIELDS,
    build_rows,
    fetch_production_observations,
    record_observations,
    resolve_source,
    strip_pii,
    zip3_of,
)
from estimate_eval import evaluate

PII_ESTIMATE = """John Smith
123 Main Street, Anytown, NY 10001
(555) 123-4567
john.smith@example.com

ABC Roofing LLC
License #ROC123456

1. Tear off old shingles 20 squares @ $350.00 = $7,000.00
2. Install architectural shingles 20 squares @ $425.00 = $8,500.00

Total: $15,500.00
"""

FORBIDDEN = [
    "John Smith",
    "123 Main Street",
    "Anytown",
    "(555) 123-4567",
    "555-123-4567",
    "john.smith@example.com",
    "ABC Roofing LLC",
    "ROC123456",
]


def test_strip_pii_removes_everything():
    cleaned, counts = strip_pii(PII_ESTIMATE)
    for secret in FORBIDDEN:
        assert secret not in cleaned, f"leaked into cleaned text: {secret!r}"
    for cat in ("name", "address", "phone", "email", "contractor", "license"):
        assert counts.get(cat, 0) >= 1, f"no strips counted for {cat}: {counts}"
    # line items survive the strip
    assert "Tear off old shingles" in cleaned
    assert "$7,000.00" in cleaned


def test_strip_pii_does_not_eat_line_items():
    text = "1. Install shingles 20 squares @ $425.00 = $8,500.00\n"
    cleaned, _ = strip_pii(text)
    assert cleaned == text


def test_strip_pii_removes_crew_names_from_descriptions():
    cases = [
        # (raw line, names that must be gone, words that must survive)
        ("1. Labor, J. Martinez crew 8 hours @ $75.00 = $600.00\n",
         ["Martinez", "J. Martinez"], ["Labor", "crew"]),
        ("1. Supervision, foreman Johnson 4 hours @ $95.00 = $380.00\n",
         ["Johnson"], ["foreman"]),
        ("1. Martinez's crew 8 hours @ $75.00 = $600.00\n",
         ["Martinez"], ["crew"]),
        ("1. crew Mary Johnson 8 hours @ $75.00 = $600.00\n",
         ["Mary", "Johnson"], ["crew"]),
        ("1. Install, R. Smith labor 8 hours @ $75.00 = $600.00\n",
         ["R. Smith"], ["labor"]),
    ]
    for raw, gone, survivors in cases:
        cleaned, counts = strip_pii(raw)
        for name in gone:
            assert name not in cleaned, f"{name!r} leaked: {cleaned!r}"
        for word in survivors:
            assert word in cleaned, f"{word!r} wrongly removed: {cleaned!r}"
        assert counts.get("crew", 0) >= 1, f"no crew strips counted: {counts}"


def test_strip_pii_preserves_brand_names():
    # Conservative by design: product/brand names are never touched, even
    # when they look like personal names.
    brands = [
        "1. Install Pella windows 10 units @ $400.00 = $4,000.00\n",
        "1. Owens Corning shingles 20 squares @ $425.00 = $8,500.00\n",
        "1. James Hardie siding 1500 sq ft @ $8.50 = $12,750.00\n",
        "1. A.O. Smith water heater 1 each @ $1,200.00 = $1,200.00\n",
        "1. GAF Timberline shingles 20 squares @ $450.00 = $9,000.00\n",
    ]
    for raw in brands:
        cleaned, counts = strip_pii(raw)
        assert cleaned == raw, f"brand mangled: {cleaned!r}"
        assert counts.get("crew", 0) == 0, f"false positive: {counts}"


def test_strip_pii_crew_conservative_residuals():
    # Deliberately NOT stripped: a bare surname directly before a crew word
    # is indistinguishable from a product word in the same slot
    # ("Windows labor 8 hrs"), and mangling that would corrupt the
    # user-facing description. Documented limitation, not a bug.
    for raw in [
        "1. Martinez crew 8 hours @ $75.00 = $600.00\n",
        "1. Windows labor 8 hours @ $75.00 = $600.00\n",
    ]:
        cleaned, counts = strip_pii(raw)
        assert cleaned == raw, f"over-stripped: {cleaned!r}"
        assert counts.get("crew", 0) == 0


def test_zip3():
    assert zip3_of("10001") == "100"
    assert zip3_of("90210") == "902"
    assert zip3_of("ABCDE") == ""


def test_build_rows_whitelists_fields():
    rows = build_rows(
        zip_code="10001",
        trade="roofing",
        lines=[{
            "description": "Tear off old shingles",
            "quantity": "20",
            "unit": "squares",
            "unit_price": "350.00",
            "computed_line_total": "7000.00",
            "scope": "per square",
            "extra": "must be dropped",
        }],
    )
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == set(ALLOWED_ROW_FIELDS)
    assert row["zip3"] == "100"
    assert row["trade"] == "roofing"
    assert row["scope"] == "per square"
    assert row["computed_line_total"] == "7000.00"


def test_no_pii_anywhere_in_rows():
    # DATABASE_URL is unset here -> insert is skipped, never raises.
    os.environ.pop("DATABASE_URL", None)
    result = record_observations([{"zip3": "1"}])
    assert result["inserted"] == 0

    res = evaluate(PII_ESTIMATE, "10001")
    assert "error" not in res
    assert len(res["parsed_line_items"]) == 2

    # Rebuild the rows exactly as evaluate() does and assert no PII leaks.
    from estimate_eval import basis_from_unit  # noqa
    lines = [
        {
            "description": it["description"],
            "quantity": "20",
            "unit": "squares",
            "unit_price": it["unit_price"],
            "computed_line_total": it["computed_line_total"],
            "scope": "per square",
        }
        for it in res["parsed_line_items"]
    ]
    rows = build_rows(zip_code="10001", trade="roofing", lines=lines)
    blob = json.dumps(rows)
    for secret in FORBIDDEN:
        assert secret not in blob, f"PII leaked into observation row: {secret!r}"
    # and the stored fields are the whitelisted ones only
    for row in rows:
        assert set(row.keys()) == set(ALLOWED_ROW_FIELDS)


def test_evaluate_still_computes_with_pii_present():
    res = evaluate(PII_ESTIMATE, "10001")
    assert res["computed_total"] == "15500.00"
    assert res["quoted_total"] == "15500.00"


def _line():
    return {
        "description": "Tear off old shingles",
        "quantity": "20",
        "unit": "squares",
        "unit_price": "350.00",
        "computed_line_total": "7000.00",
        "scope": "per square",
    }


def test_resolve_source_defaults_to_production(monkeypatch):
    monkeypatch.delenv("ESTIMATEGUARD_OBSERVATION_SOURCE", raising=False)
    assert resolve_source() == "production"


def test_resolve_source_honors_env(monkeypatch):
    for value in ("production", "test", "verification"):
        monkeypatch.setenv("ESTIMATEGUARD_OBSERVATION_SOURCE", value)
        assert resolve_source() == value


def test_resolve_source_ignores_bad_env(monkeypatch, capsys):
    monkeypatch.setenv("ESTIMATEGUARD_OBSERVATION_SOURCE", "prod")
    assert resolve_source() == "production"
    assert "ignoring invalid" in capsys.readouterr().err


def test_resolve_source_explicit_beats_env(monkeypatch):
    monkeypatch.setenv("ESTIMATEGUARD_OBSERVATION_SOURCE", "production")
    assert resolve_source("test") == "test"
    assert resolve_source("verification") == "verification"


def test_resolve_source_rejects_production_via_explicit(monkeypatch, capsys):
    # The header path may never launder rows into production.
    monkeypatch.delenv("ESTIMATEGUARD_OBSERVATION_SOURCE", raising=False)
    assert resolve_source("production") == "production"  # falls back, warns
    assert "ignoring invalid explicit source" in capsys.readouterr().err


def test_build_rows_stamps_source_from_env():
    # conftest.py forces ESTIMATEGUARD_OBSERVATION_SOURCE=test for the suite.
    rows = build_rows(zip_code="10001", trade="roofing", lines=[_line()])
    assert rows[0]["source"] == "test"


def test_build_rows_explicit_source(monkeypatch):
    monkeypatch.setenv("ESTIMATEGUARD_OBSERVATION_SOURCE", "production")
    rows = build_rows(
        zip_code="10001", trade="roofing", lines=[_line()], source="verification"
    )
    assert rows[0]["source"] == "verification"


class _FakeCursor:
    def __init__(self):
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    @property
    def description(self):
        return [("observed_at",), ("zip3",)]

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj


def test_fetch_production_observations_always_filters_source():
    conn = _FakeConn()
    fetch_production_observations(conn, trade="roofing")
    assert "source = 'production'" in conn.cursor_obj.sql
    assert "test" not in conn.cursor_obj.sql.replace("source = 'production'", "")
