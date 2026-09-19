#!/usr/bin/env python3
"""Local tests for the seed-benchmark importer. No network, no real DB.

Covers Joe's non-negotiables for the benchmark migration:
  - Decimal-only parsing (no floats carried forward)
  - sample_size is always 0
  - provenance + source as-of date on every row
  - malformed rows quarantine to null instead of raising
  - scope disambiguation never guesses (unique winner or None)
  - the upsert is idempotent by construction
  - the DDL the service applies is the migration file on disk
"""

import csv
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import benchmarks  # noqa: E402


def _row(**overrides):
    base = {
        "City": "Denver",
        "Trade": "Roofing",
        "Service_Type": "Asphalt Shingle (2000 sqft)",
        "Basis": "flat",
        "Low_Price": "8600",
        "Avg_Price": "11000",
        "High_Price": "13600",
        "Labor_Rate_Low": "45",
        "Labor_Rate_High": "75",
        "Permit_Cost": "200-400",
        "Data_Source": "Angi, HomeAdvisor",
        "Notes": "",
    }
    base.update(overrides)
    return base


# ---- Decimal-only parsing -------------------------------------------------


def test_decimal_parsing_never_floats():
    assert benchmarks._to_decimal("$1,250.50") == Decimal("1250.50")
    assert benchmarks._to_decimal("  42 ") == Decimal("42")
    assert benchmarks._to_decimal("") is None
    assert benchmarks._to_decimal("N/A") is None
    assert benchmarks._to_decimal("underground service") is None
    lo, hi = benchmarks._parse_low_high("200-400")
    assert lo == Decimal("200") and hi == Decimal("400")
    lo, hi = benchmarks._parse_low_high("$1,000 - $1,500")
    assert lo == Decimal("1000") and hi == Decimal("1500")
    lo, hi = benchmarks._parse_low_high("175")
    assert lo == Decimal("175") and hi == Decimal("175")
    lo, hi = benchmarks._parse_low_high("")
    assert lo is None and hi is None
    lo, hi = benchmarks._parse_low_high("underground service")
    assert lo is None and hi is None


def test_normalized_row_is_decimal_and_tagged():
    n = benchmarks._normalize_row(_row())
    assert isinstance(n["low_price"], Decimal)
    assert n["avg_price"] == Decimal("11000")
    assert isinstance(n["high_price"], Decimal)
    assert n["labor_rate_low"] == Decimal("45")
    assert n["labor_rate_high"] == Decimal("75")
    assert n["permit_cost_low"] == Decimal("200")
    assert n["permit_cost_high"] == Decimal("400")
    assert n["sample_size"] == 0
    assert n["region"] == "Denver"
    assert n["trade"] == "roofing"
    assert n["service_type"] == "Asphalt Shingle (2000 sqft)"
    assert n["zip3"] is None  # city-level rows never claim a zip3
    assert "sample_size=0" in n["provenance"]
    assert "not documented" in n["provenance"].lower()
    assert "legacy scaffolding" not in n["provenance"].lower()


def test_normalized_row_malformed_low_becomes_null():
    n = benchmarks._normalize_row(
        _row(
            City="San Francisco",
            Trade="Electrical",
            Service_Type="Panel Upgrade (200 amp)",
            Low_Price="underground service",
            Avg_Price="2600",
        )
    )
    assert n["low_price"] is None
    assert n["avg_price"] == Decimal("2600")  # avg still parsed
    assert n["sample_size"] == 0


def test_normalized_row_without_avg_is_skipped():
    assert benchmarks._normalize_row(_row(Avg_Price="")) is None


def test_as_of_dates_follow_dataset_generation():
    atl = benchmarks._normalize_row(_row(City="Atlanta"))
    chi = benchmarks._normalize_row(_row(City="Chicago"))
    den = benchmarks._normalize_row(_row(City="Denver"))
    assert atl["as_of_date"] == "2026-08-09"  # expansion city
    assert chi["as_of_date"] == "2026-08-03"  # original 7-city data
    assert den["as_of_date"] == "2026-08-03"


def test_kitchen_remodel_trade_normalizes():
    n = benchmarks._normalize_row(_row(Trade="Kitchen Remodel"))
    # costdata.normalize_trade is the single normalizer used by both the
    # importer and the lookup path; whatever it returns is consistent.
    from costdata import normalize_trade  # noqa

    assert n["trade"] == normalize_trade("kitchen remodel") == "kitchen remodel"


def test_service_basis_mapping():
    assert benchmarks._service_basis("Cost per Sq Ft (Asphalt)") == "per_sqft"
    assert benchmarks._service_basis("Panel Upgrade (200 amp)") == "flat"
    assert benchmarks._service_basis("Install per square") == "per_square"


# ---- regions --------------------------------------------------------------


def test_zip3_routing_covers_every_source_city():
    with benchmarks.CSV_PATH.open(newline="", encoding="utf-8-sig") as fh:
        cities = {r["City"] for r in csv.DictReader(fh)}
    assert cities, "vendored CSV is empty"
    for city in cities:
        assert city in benchmarks._REGION_ZIP3S, f"{city} has no zip3 routing"
        assert city in benchmarks._REGION_AS_OF_DATE


def test_region_for_zip3():
    assert benchmarks.region_for_zip3("100") == "NYC"
    assert benchmarks.region_for_zip3("802") == "Denver"
    assert benchmarks.region_for_zip3("999") is None
    assert benchmarks.region_for_zip3("") is None


# ---- scope disambiguation --------------------------------------------------


def test_content_tokens_drop_stop_words():
    toks = benchmarks._content_tokens("New Ductwork (Add-on)")
    assert toks == {"new", "ductwork", "add", "on"}, toks
    assert benchmarks._content_tokens("per square") == set()
    assert benchmarks._content_tokens("Roof Replacement") == {
        "shingle",
        "replacement",
    }


def test_resolve_candidates_never_guesses():
    cands = [
        {"service_type": "Asphalt Shingle (2000 sqft)"},
        {"service_type": "Asphalt Shingle (2500 sqft)"},
    ]
    assert benchmarks.resolve_candidates([], "anything") is None
    assert benchmarks.resolve_candidates([cands[0]], None) == cands[0]
    assert (
        benchmarks.resolve_candidates(cands, "asphalt shingle 2000 sqft")
        == cands[0]
    )
    # "flat roof" matches both -> ambiguous -> None, never a guess
    assert benchmarks.resolve_candidates(cands, "flat roof") is None
    assert benchmarks.resolve_candidates(cands, None) is None
    assert benchmarks.resolve_candidates(cands, "per square") is None


# ---- schema + import SQL ---------------------------------------------------


def test_schema_is_the_migration_file():
    on_disk = (
        Path(benchmarks.__file__).resolve().parent
        / "migrations"
        / "001_benchmark_ranges.sql"
    ).read_text(encoding="utf-8")
    assert "FLOAT" not in benchmarks.SCHEMA_SQL
    assert "DOUBLE" not in benchmarks.SCHEMA_SQL
    assert "NUMERIC" in benchmarks.SCHEMA_SQL
    assert "sample_size INTEGER NOT NULL DEFAULT 0" in benchmarks.SCHEMA_SQL
    assert "provenance TEXT NOT NULL" in benchmarks.SCHEMA_SQL
    assert "labor_rate_low NUMERIC" in benchmarks.SCHEMA_SQL
    assert "permit_cost_low NUMERIC" in benchmarks.SCHEMA_SQL
    # every executable statement in the migration file is applied verbatim
    for stmt in [s for s in benchmarks.SCHEMA_SQL.split(";") if s.strip()]:
        assert stmt.strip() in on_disk, f"drift: {stmt.strip()[:60]}"


class _FakeCursor:
    def __init__(self):
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_row().keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_import_upsert_is_idempotent_and_decimal():
    tmp = Path("/tmp/bench_test.csv")
    _write_csv(
        tmp,
        [
            _row(),  # Denver / original generation
            _row(
                City="San Francisco",
                Trade="Electrical",
                Service_Type="Panel Upgrade (200 amp)",
                Low_Price="underground service",
                Avg_Price="2600",
            ),
        ],
    )
    conn = _FakeConn()
    res = benchmarks.import_csv(conn, tmp)
    assert res == {"imported": 2}
    assert conn.commits == 1
    stmts = [s for s in conn.cur.statements if "INSERT INTO benchmark_ranges" in s[0]]
    assert len(stmts) == 2
    for sql, params in stmts:
        assert "ON CONFLICT (region, trade, service_type) DO UPDATE" in sql
        assert isinstance(params, dict)
        assert params["sample_size"] == 0
        for p in params.values():
            assert not isinstance(p, float), f"float leaked into params: {p!r}"
        assert "not documented" in params["provenance"].lower()
    denver, sf = stmts[0][1], stmts[1][1]
    assert denver["as_of_date"] == "2026-08-03"
    assert sf["as_of_date"] == "2026-08-09"
    # malformed row imported with null low, not dropped, not guessed
    assert sf["low_price"] is None
    assert sf["avg_price"] == Decimal("2600")


def test_import_missing_file_reports_cleanly():
    conn = _FakeConn()
    res = benchmarks.import_csv(conn, Path("/tmp/does-not-exist-bench.csv"))
    assert res["imported"] == 0
    assert res["skipped"] is True
    assert "reason" in res


def test_lookups_degrade_without_db():
    os.environ.pop("DATABASE_URL", None)
    assert benchmarks.db_reachable() is False
    assert benchmarks.lookup("roofing", "per_sqft", "100", "per sq ft") is None
    assert benchmarks.find_candidates("roofing", "per_sqft", "100") == []
    assert benchmarks.bases_for("roofing", "100") == []
    assert benchmarks.has_coverage("roofing", "100") is False
    assert benchmarks.count_rows() is None


def test_content_tokens_squash_thousands_commas():
    # "2,000" in a service type must match a hint's "2000".
    assert benchmarks._content_tokens("Asphalt Shingles (2,000 sq ft)") == {
        "asphalt", "shingle", "2000",
    }


def test_resolve_candidates_comma_number_hint():
    cands = [
        {"service_type": "Asphalt Shingles (1,500 sq ft)"},
        {"service_type": "Asphalt Shingles (2,000 sq ft)"},
        {"service_type": "Asphalt Shingles (2,500 sq ft)"},
    ]
    winner = benchmarks.resolve_candidates(cands, "asphalt shingle 2000 sqft")
    assert winner is not None
    assert winner["service_type"] == "Asphalt Shingles (2,000 sq ft)"
    # A hint that fits two candidates stays ambiguous.
    assert benchmarks.resolve_candidates(cands, "asphalt shingle") is None


def test_scaffolding_fallback_is_honestly_tagged():
    """Offline fallback rows (incl. the old $425/square NYC roofing row)
    must never be presentable as observed job data."""
    os.environ.pop("DATABASE_URL", None)
    import costdata  # noqa

    row = costdata.seed_row("roofing", "per_square", "10001")
    assert row is not None
    assert row["source"] == "scaffolding"
    assert "illustrative" in row["provenance"].lower()
    assert "not observed" in row["provenance"].lower()
    # and a miss stays a miss even offline
    assert costdata.seed_row("roofing", "per_square", "99999") is None


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001 - report and continue
            failed += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


def test_count_winners():
    cands = [
        {"service_type": "Asphalt Shingle (1000 sqft)"},
        {"service_type": "Asphalt Shingle (2000 sqft)"},
    ]
    assert benchmarks.count_winners([], "shingle") == 0
    assert benchmarks.count_winners(cands[:1], "anything") == 1
    assert benchmarks.count_winners(cands, "asphalt shingle 2000 sqft") == 1
    assert benchmarks.count_winners(cands, "asphalt shingle") == 2
    assert benchmarks.count_winners(cands, "water heater") == 0


def _canned_conn():
    rows = [
        ("Denver", None, "roofing", "Asphalt Shingle (2000 sqft)", "flat",
         Decimal("8600"), Decimal("11000"), Decimal("13600"),
         None, None, None, None, 0, "prov", "2026-08-03"),
        ("Denver", None, "roofing", "Cost per Sq Ft (Asphalt)", "per_sqft",
         None, Decimal("5.50"), Decimal("7"),
         None, None, None, None, 0, "prov", "2026-08-03"),
    ]

    class Cur(_FakeCursor):
        def execute(self, sql, params=None):
            self.statements.append((sql, params))
            return self

        def fetchall(self):
            params = self.statements[-1][1]
            basis = params[2] if len(params) == 3 else None
            return [
                r for r in rows
                if r[0] == params[0] and r[2] == params[1]
                and (basis is None or r[4] == basis)
            ]

    class Conn(_FakeConn):
        def __init__(self):
            self.cur = Cur()
            self.commits = 0

    return Conn()


def test_lookup_cross_basis_finds_sized_flat_row():
    conn = _canned_conn()
    real_connect = benchmarks._connect
    benchmarks._connect = lambda: conn  # noqa: E731
    try:
        # "2000 sqft" is a size hint, not a per-unit pricing declaration:
        # the flat project-price row wins across bases.
        row = benchmarks.lookup(
            "roofing", None, "802", "asphalt shingle 2000 sqft"
        )
        assert row is not None
        assert row["service_type"] == "Asphalt Shingle (2000 sqft)"
        assert row["basis"] == "flat"
        assert row["median"] == Decimal("11000")
        # A basis-constrained search with a single candidate returns it:
        # one candidate means nothing to disambiguate between.
        row = benchmarks.lookup("roofing", "per_sqft", "802", "cost per sq ft")
        assert row is not None
        assert row["median"] == Decimal("5.50")
    finally:
        benchmarks._connect = real_connect


def test_normalize_basis_is_conservative():
    import costdata  # noqa

    # Explicit per-unit pricing phrases declare a basis...
    assert costdata.normalize_basis("per sq ft") == "per_sqft"
    assert costdata.normalize_basis("What is the cost per square?") == "per_square"
    assert costdata.normalize_basis("lump sum") == "flat"
    # ...but a bare size mention is a hint, not a pricing basis.
    assert costdata.normalize_basis("asphalt shingle 2000 sqft") is None
    assert costdata.normalize_basis("full hvac system replacement") is None
    assert costdata.normalize_basis("water heater replacement") is None


def test_seed_row_labels_unit_from_row_basis():
    import costdata  # noqa

    conn = _canned_conn()
    real_connect = benchmarks._connect
    benchmarks._connect = lambda: conn  # noqa: E731
    try:
        row = costdata.seed_row(
            "roofing", None, "80202", hint="asphalt shingle 2000 sqft"
        )
        assert row is not None
        assert row["unit"] == "per project (flat price)"
        assert row["sample_size"] == 0
    finally:
        benchmarks._connect = real_connect


if __name__ == "__main__":
    _main()
