#!/usr/bin/env python3
"""One-off: append the September 2026 coverage rows to the seed CSV.

Kept in the repo as the record of where each appended figure came from. Every
row below was read off a published cost guide during this research pass; the
URL and publication date sit next to the row, and a figure no source printed
is never invented. Where a guide published a range but no typical value, the
midpoint is used and the row's Data_Source says so, so a derived middle is
never presented as a published one.

Run once (idempotent: refuses to append a (City, Trade, Service_Type) the CSV
already has):

    python3 scripts/add_coverage_rows_2026_09.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmarks_7cities.csv"

# Sources, for the record:
# NYC HVAC
#   https://www.angi.com/articles/insider-s-price-guide-new-heating-and-cooling-system/ny/new-york
#     (updated 2026-04-18)
#   https://hvacprojectcost.com/hvac-replacement-cost-new-york-city-ny/ (updated 2026-07-15)
#   https://universalservicescrp.com/blog/hvac/service-call-cost-nyc/ (updated 2026-02-17)
# Seattle HVAC
#   https://www.angi.com/articles/save-money-replacing-heat-ac-same-time/wa/seattle
#     (updated 2026-04-14)
#   https://www.costadia.com/hvac/furnace-replacement/seattle-wa (updated 2026-06)
# Houston
#   https://www.angi.com/articles/how-much-does-roof-replacement-cost/tx/houston (2026-08-01)
#   https://www.angi.com/articles/how-much-does-it-cost-repair-water-heater/tx/houston (2026-08-04)
#   https://www.angi.com/articles/how-much-does-tankless-water-heater-cost/tx/houston (2026-02-25)
#   https://www.angi.com/articles/ask-angie-what-does-it-cost-upgrade-200-amps/tx/houston (2026-07-09)
#   https://www.angi.com/articles/insider-s-price-guide-new-heating-and-cooling-system/tx/houston (2026-04-23)
#   https://www.angi.com/articles/how-much-does-installing-new-ac-cost/tx/houston (2026-07-09)
#   https://renovcost.com/kitchen-remodel/houston-tx (updated 2026-06)
# Austin
#   https://www.angi.com/articles/how-much-does-roof-replacement-cost/tx/austin (2026-07-09)
#   https://www.angi.com/articles/how-much-does-it-cost-repair-water-heater/tx/austin (2025-09-29)
#   https://www.angi.com/articles/how-much-does-tankless-water-heater-cost/tx/austin (2026-02-25)
#   https://www.angi.com/articles/ask-angie-what-does-it-cost-upgrade-200-amps/tx/austin (2026-07-09)
#   https://www.angi.com/articles/insider-s-price-guide-new-heating-and-cooling-system/tx/austin (2026-04-27)
#   https://www.angi.com/articles/how-much-hvac-repair-cost/tx/austin (2026-04-23)
#   https://renovcost.com/kitchen-remodel/austin-tx (updated 2026-06)

MID = "typical = midpoint of published range"

# City, Trade, Service_Type, Low, Avg, High, LaborLo, LaborHi, Permit, Notes,
# Data_Source, As_Of_Date
ROWS = [
    # --- NYC HVAC (the metro had no HVAC rows at all) ---
    ("NYC", "HVAC", "Diagnostic Service Call", "75", "137.50", "200", "N/A", "N/A", "N/A",
     "Flat diagnostic visit; after-hours calls run $150-$300",
     f"Universal Services NYC ({MID} $75-$200)", "2026-02-17"),
    ("NYC", "HVAC", "Full HVAC System Replacement", "5630", "8445", "14075", "100", "200", "200-1500",
     "Installed cost; complex brownstone projects can exceed $24,772",
     "Angi", "2026-04-18"),
    ("NYC", "HVAC", "AC Unit Replacement (Central)", "7000", "11000", "15000", "100", "200", "200-1500",
     "Central air equipment plus install", f"Angi ({MID} $7,000-$15,000)", "2026-04-18"),
    ("NYC", "HVAC", "Gas Furnace Replacement", "3800", "6900", "10000", "100", "200", "200-1500",
     "Gas furnace; electric runs $1,700-$7,100", f"Angi ({MID} $3,800-$10,000)", "2026-04-18"),
    ("NYC", "HVAC", "Ductwork Replacement (Add-on)", "5000", "8500", "12000", "100", "200", "200-1500",
     "Often paired with a system replacement", f"Angi ({MID} $5,000-$12,000)", "2026-04-18"),
    ("NYC", "HVAC", "Ductless Mini-Split Installation (Single Zone)", "3500", "4750", "6000",
     "N/A", "N/A", "200-500",
     "Multi-zone (3-4 heads) runs $8,000-$15,000",
     f"HVAC Project Cost ({MID} $3,500-$6,000)", "2026-07-15"),

    # --- Seattle HVAC (the metro had no HVAC rows at all) ---
    ("Seattle", "HVAC", "Full HVAC System Replacement", "5555", "8333", "13888", "N/A", "N/A", "95.90",
     "Local labor runs well above the national average", "Angi", "2026-04-14"),
    ("Seattle", "HVAC", "Ductless Mini-Split Installation", "2200", "9150", "16100", "N/A", "N/A", "95.90",
     "Wide range by zone count", f"Angi ({MID} $2,200-$16,100)", "2026-04-14"),
    ("Seattle", "HVAC", "Furnace Replacement", "4800", "6700", "8600", "N/A", "N/A", "250",
     "Heating-only system", "Costadia", "2026-06-01"),
    ("Seattle", "HVAC", "AC Unit Replacement (Central)", "6800", "10675", "14550", "N/A", "N/A", "250",
     "Central AC install", f"Costadia ({MID} $6,800-$14,550)", "2026-06-01"),
    ("Seattle", "HVAC", "Heat Pump Installation", "8150", "11500", "14850", "N/A", "N/A", "250",
     "Common choice in the Pacific Northwest climate",
     f"Costadia ({MID} $8,150-$14,850)", "2026-06-01"),

    # --- Houston (new metro) ---
    ("Houston", "Roofing", "Roof Replacement (1500 sqft)", "7200", "8400", "19800", "N/A", "N/A", "62.22",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-08-01"),
    ("Houston", "Roofing", "Roof Replacement (2000 sqft)", "8800", "10230", "24200", "N/A", "N/A", "62.22",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-08-01"),
    ("Houston", "Roofing", "Roof Replacement (2500 sqft)", "11200", "13000", "30800", "N/A", "N/A", "62.22",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-08-01"),
    ("Houston", "Roofing", "Cost per Sq Ft (Asphalt)", "4", "4.65", "6", "N/A", "N/A", "62.22",
     "All-in asphalt rate; all materials span $4-$11", "Angi", "2026-08-01"),
    ("Houston", "Roofing", "Metal Roofing", "N/A", "23100", "N/A", "N/A", "N/A", "62.22",
     "Source published an average only", "Angi", "2026-08-01"),
    ("Houston", "Plumbing", "Water Heater Repair", "237", "624", "1010", "50", "200", "N/A",
     "Repairs need no permit in Houston; replacement does", "Angi", "2026-08-04"),
    ("Houston", "Plumbing", "Water Heater Replacement", "1000", "1462.50", "1925", "50", "200", "33.10",
     "Tank replacement", f"Angi ({MID} $1,000-$1,925)", "2026-08-04"),
    ("Houston", "Plumbing", "Tankless Water Heater", "1977", "3206", "4462", "85", "150", "50-100",
     "Warm climate favors tankless efficiency", "Angi", "2026-02-25"),
    ("Houston", "Electrical", "Electrical Panel Upgrade (100-200 amp)", "1183", "1578", "1972",
     "N/A", "N/A", "50-295", "Four to eight hours of electrician work", "Angi", "2026-07-09"),
    ("Houston", "Electrical", "Panel Upgrade (400 amp)", "1975", "2962.50", "3950", "N/A", "N/A", "50-295",
     "High AC load often pushes service above 200 amp",
     f"Angi ({MID} $1,975-$3,950)", "2026-07-09"),
    ("Houston", "HVAC", "Full HVAC System Replacement", "4930", "7395", "12325", "75", "200", "50-300",
     "With new ductwork runs $10,000-$22,000", "Angi", "2026-04-23"),
    ("Houston", "HVAC", "AC Unit Replacement (Central)", "4100", "6375", "8700", "75", "200", "50-300",
     "Median 1,900 sq ft home", "Angi", "2026-07-09"),
    ("Houston", "HVAC", "Ductless Mini-Split Installation", "3500", "9250", "15000", "75", "200", "50-300",
     "Range widens with zone count", f"Angi ({MID} $3,500-$15,000)", "2026-04-23"),
    ("Houston", "HVAC", "Ductwork Installation (Add-on)", "3000", "6500", "10000", "75", "200", "50-300",
     "Duct sealing alone runs $400-$1,500", f"Angi ({MID} $3,000-$10,000)", "2026-04-23"),
    ("Houston", "HVAC", "Gas Furnace Replacement", "2500", "4750", "7000", "75", "200", "50-300",
     "Electric runs $1,500-$5,500", f"Angi ({MID} $2,500-$7,000)", "2026-04-23"),
    ("Houston", "Kitchen Remodel", "Cosmetic Refresh", "10900", "12100", "13300", "N/A", "N/A", "75-350",
     "Refaced cabinets, laminate counters, vinyl floor",
     f"RenovCost ({MID} $10,900-$13,300)", "2026-06-01"),
    ("Houston", "Kitchen Remodel", "Mid-Range Remodel", "28300", "34100", "41300", "N/A", "N/A", "75-350",
     "Semi-custom cabinets, quartz counters; ~3% below national average",
     "RenovCost", "2026-06-01"),
    ("Houston", "Kitchen Remodel", "High-End Remodel", "62900", "71600", "80300", "N/A", "N/A", "75-350",
     "Custom cabinetry, natural stone, pro-grade appliances",
     f"RenovCost ({MID} $62,900-$80,300)", "2026-06-01"),
    ("Houston", "Kitchen Remodel", "Per Sq Ft (Mid-Range)", "107", "189.50", "272", "N/A", "N/A", "75-350",
     "Mid-range tier rate", f"RenovCost ({MID} $107-$272)", "2026-06-01"),

    # --- Austin (new metro) ---
    ("Austin", "Roofing", "Roof Replacement (1500 sqft)", "7200", "9000", "19800", "N/A", "N/A", "333",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-07-09"),
    ("Austin", "Roofing", "Roof Replacement (2000 sqft)", "8800", "11000", "24200", "N/A", "N/A", "333",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-07-09"),
    ("Austin", "Roofing", "Roof Replacement (2500 sqft)", "11200", "14000", "30800", "N/A", "N/A", "333",
     "Range spans all roofing materials, not asphalt alone", "Angi", "2026-07-09"),
    ("Austin", "Roofing", "Cost per Sq Ft (Asphalt)", "4", "5", "6", "N/A", "N/A", "333",
     "Metal and tile run $7-$11 per sq ft", f"Angi ({MID} $4-$6)", "2026-07-09"),
    ("Austin", "Roofing", "Asphalt Shingle Replacement", "8000", "10000", "12000", "N/A", "N/A", "333",
     "Material-tier figure, roof size unspecified", f"Angi ({MID} $8,000-$12,000)", "2026-07-09"),
    ("Austin", "Roofing", "Metal Roofing", "14000", "18000", "22000", "N/A", "N/A", "333",
     "Same tier as clay and concrete tile", f"Angi ({MID} $14,000-$22,000)", "2026-07-09"),
    ("Austin", "Plumbing", "Water Heater Repair", "201", "602", "1011", "90", "200", "N/A",
     "Simple repairs $130-$240; complex $602-$964", "Angi", "2025-09-29"),
    ("Austin", "Plumbing", "Water Heater Replacement", "850", "1325", "1800", "90", "200", "N/A",
     "Tank replacement", f"Angi ({MID} $850-$1,800)", "2025-09-29"),
    ("Austin", "Plumbing", "Tankless Water Heater", "1187", "2239", "3491", "65", "110", "45-200",
     "Master plumber rates run $90-$110/hr", "Angi", "2026-02-25"),
    ("Austin", "Electrical", "Electrical Panel Upgrade (100-200 amp)", "1177", "1570", "1962",
     "N/A", "N/A", "185",
     "Austin Energy adds a $220 application fee", "Angi", "2026-07-09"),
    ("Austin", "Electrical", "Panel Upgrade (400 amp)", "1950", "2937.50", "3925", "N/A", "N/A", "185",
     "Austin Energy adds a $220 application fee",
     f"Angi ({MID} $1,950-$3,925)", "2026-07-09"),
    ("Austin", "HVAC", "Full HVAC System Replacement", "4905", "7358", "12263", "75", "125", "80-130",
     "With new ductwork runs $12,000-$25,000", "Angi", "2026-04-27"),
    ("Austin", "HVAC", "AC Unit Replacement (Central)", "5500", "8750", "12000", "75", "125", "80-130",
     "Change-Out Program permit runs about $70",
     f"Angi ({MID} $5,500-$12,000)", "2026-04-27"),
    ("Austin", "HVAC", "Ductless Mini-Split Installation", "3000", "9000", "15000", "75", "125", "80-130",
     "Range widens with zone count", f"Angi ({MID} $3,000-$15,000)", "2026-04-27"),
    ("Austin", "HVAC", "Gas Furnace Replacement", "3000", "5500", "8000", "75", "125", "80-130",
     "Electric runs $2,000-$7,500", f"Angi ({MID} $3,000-$8,000)", "2026-04-27"),
    ("Austin", "HVAC", "Ductwork Installation (Add-on)", "6000", "9000", "12000", "75", "125", "80-130",
     "Typical 2,000 sq ft home", f"Angi ({MID} $6,000-$12,000)", "2026-04-27"),
    ("Austin", "HVAC", "HVAC Repair", "98", "343", "2943", "75", "125", "N/A",
     "Central AC repairs run $440-$1,960", "Angi", "2026-04-23"),
    ("Austin", "Kitchen Remodel", "Cosmetic Refresh", "12300", "13650", "15000", "N/A", "N/A", "200-600",
     "Refaced cabinets, laminate counters, vinyl floor",
     f"RenovCost ({MID} $12,300-$15,000)", "2026-06-01"),
    ("Austin", "Kitchen Remodel", "Mid-Range Remodel", "31900", "38400", "46500", "N/A", "N/A", "200-600",
     "Semi-custom cabinets, quartz counters; ~9% above national average",
     "RenovCost", "2026-06-01"),
    ("Austin", "Kitchen Remodel", "High-End Remodel", "70600", "80450", "90300", "N/A", "N/A", "200-600",
     "Custom cabinetry, natural stone, pro-grade appliances",
     f"RenovCost ({MID} $70,600-$90,300)", "2026-06-01"),
    ("Austin", "Kitchen Remodel", "Per Sq Ft (Mid-Range)", "120", "212.50", "305", "N/A", "N/A", "200-600",
     "Mid-range tier rate", f"RenovCost ({MID} $120-$305)", "2026-06-01"),
]

HEADER_WITH_AS_OF = [
    "City", "Trade", "Service_Type", "Low_Price", "Avg_Price", "High_Price",
    "Labor_Rate_Low", "Labor_Rate_High", "Permit_Cost", "Notes", "Data_Source",
    "As_Of_Date",
]


def main() -> int:
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        existing_rows = list(reader)

    existing_keys = {(r[0], r[1], r[2]) for r in existing_rows if len(r) >= 3}
    new_rows = [r for r in ROWS if (r[0], r[1], r[2]) not in existing_keys]
    if not new_rows:
        print("nothing to append; all rows already present")
        return 0

    if header != HEADER_WITH_AS_OF:
        if header != HEADER_WITH_AS_OF[:-1]:
            print(f"unexpected header: {header}", file=sys.stderr)
            return 1
        header = HEADER_WITH_AS_OF

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        # Existing rows keep their own length: a row shorter than the header
        # simply has no As_Of_Date and falls back to its region's date.
        writer.writerows(existing_rows)
        writer.writerows(new_rows)

    print(f"appended {len(new_rows)} rows ({len(existing_rows) + len(new_rows)} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
