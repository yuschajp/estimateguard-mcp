"""Metro coverage: routing, per-row source dates, and the filled gaps.

The September 2026 pass added Austin and Houston as metros and filled the
HVAC hole in NYC and Seattle. These lock in the routing and the dating rule,
and assert the gaps stay filled if the CSV is edited later.
"""

import csv
from collections import defaultdict

import benchmarks


def _csv_rows():
    with open(benchmarks.CSV_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# ZIP routing
# ---------------------------------------------------------------------------

def test_austin_zips_route_to_austin():
    # 78701 is downtown Austin; 786 covers Round Rock and Georgetown.
    assert benchmarks.region_for_zip3("787") == "Austin"
    assert benchmarks.region_for_zip3("786") == "Austin"


def test_houston_zips_route_to_houston():
    for zip3 in ("770", "771", "772", "773", "774", "775"):
        assert benchmarks.region_for_zip3(zip3) == "Houston", zip3


def test_new_metros_do_not_collide_with_dallas():
    # Dallas-Fort Worth holds 750-754 and 760-762; Texas now has three metros.
    assert benchmarks.region_for_zip3("752") == "Dallas-Fort Worth"
    assert benchmarks.region_for_zip3("760") == "Dallas-Fort Worth"


def test_every_zip3_maps_to_one_region():
    seen = defaultdict(list)
    for region, zips in benchmarks._REGION_ZIP3S.items():
        for zip3 in zips:
            seen[zip3].append(region)
    duplicates = {z: r for z, r in seen.items() if len(r) > 1}
    assert not duplicates, f"zip3 claimed by several metros: {duplicates}"


def test_covered_regions_include_the_new_metros():
    assert "Austin" in benchmarks.COVERED_REGIONS
    assert "Houston" in benchmarks.COVERED_REGIONS
    assert len(benchmarks.COVERED_REGIONS) == 14


# ---------------------------------------------------------------------------
# Per-row source dates
# ---------------------------------------------------------------------------

def test_row_date_uses_its_own_published_date():
    row = {"As_Of_Date": "2026-04-18", "City": "NYC", "Service_Type": "x"}
    assert benchmarks._row_as_of_date(row, "NYC") == "2026-04-18"


def test_row_without_a_date_falls_back_to_its_generation():
    row = {"City": "NYC", "Service_Type": "x"}
    assert benchmarks._row_as_of_date(row, "NYC") == benchmarks.AS_OF_ORIGINAL
    assert benchmarks._row_as_of_date(row, "Boston") == benchmarks.AS_OF_EXPANSION


def test_malformed_date_is_ignored_rather_than_stored():
    row = {"As_Of_Date": "July 2026", "City": "Austin", "Service_Type": "x"}
    assert benchmarks._row_as_of_date(row, "Austin") == benchmarks.AS_OF_COVERAGE


def test_every_dated_row_parses():
    for row in _csv_rows():
        raw = (row.get("As_Of_Date") or "").strip()
        if raw:
            assert benchmarks._ISO_DATE_RE.match(raw), f"{row['City']} / {raw}"


# ---------------------------------------------------------------------------
# The gaps this pass closed
# ---------------------------------------------------------------------------

def _coverage():
    covered = defaultdict(set)
    for row in _csv_rows():
        covered[row["City"]].add(row["Trade"])
    return covered


def test_nyc_and_seattle_have_hvac():
    covered = _coverage()
    assert "HVAC" in covered["NYC"]
    assert "HVAC" in covered["Seattle"]


def test_austin_and_houston_cover_all_five_trades():
    covered = _coverage()
    five = {"Electrical", "HVAC", "Kitchen Remodel", "Plumbing", "Roofing"}
    assert covered["Austin"] == five
    assert covered["Houston"] == five


def test_every_metro_covers_every_trade():
    covered = _coverage()
    five = {"Electrical", "HVAC", "Kitchen Remodel", "Plumbing", "Roofing"}
    missing = {city: sorted(five - trades) for city, trades in covered.items() if five - trades}
    assert not missing, f"metros missing a trade: {missing}"


def test_a_derived_typical_says_so_in_its_source():
    # A midpoint the guide did not publish must never read as published.
    for row in _csv_rows():
        low, avg, high = row["Low_Price"], row["Avg_Price"], row["High_Price"]
        if "midpoint" in (row.get("Data_Source") or ""):
            assert low not in ("", "N/A") and high not in ("", "N/A"), row["Service_Type"]
            assert (float(low) + float(high)) / 2 == float(avg), row["Service_Type"]
