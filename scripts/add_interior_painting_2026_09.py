#!/usr/bin/env python3
"""One-off: add interior painting as a sixth trade to the seed CSV.

Painting was absent from the dataset entirely, so a painting estimate had
nothing to compare against in any metro. Every metro gets the rate its
estimates are actually priced in, cost per square foot of interior.

Sources:
  https://brushquote.app/painting-cost/ (2026) publishes an interior
    per-square-foot range for 20 US cities; 13 of the 14 metros come from it.
    It prints a range and no typical value, so the midpoint is used and each
    row's Data_Source says so.
  https://www.angi.com/articles/how-much-does-it-cost-paint-interior-house/dc/washington
    (updated 2026-08-04) covers Washington DC, which BrushQuote does not, and
    publishes a typical rate outright ($4.70/sq ft walls and ceilings) plus a
    whole-home range.
  https://www.angi.com/articles/how-much-does-it-cost-paint-room/dc/washington
    (updated 2026-07-08) for the DC single-room row and painter hourly rates.

Run once (idempotent):

    python3 scripts/add_interior_painting_2026_09.py
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "benchmarks_7cities.csv"

BRUSHQUOTE = "BrushQuote (typical = midpoint of published range ${low}-${high})"

# region -> (low, high) interior $/sq ft, as published by BrushQuote.
# BrushQuote names cities; two map onto this dataset's metro names:
# "New York" -> NYC and "Dallas" -> Dallas-Fort Worth.
PER_SQFT = {
    "NYC": ("3.75", "7.50"),
    "San Francisco": ("3.50", "7.25"),
    "Boston": ("3.25", "6.75"),
    "Los Angeles": ("3", "6.50"),
    "Seattle": ("3", "6"),
    "Austin": ("2.75", "5.50"),
    "Denver": ("2.75", "5.50"),
    "Chicago": ("2.75", "5.50"),
    "Miami": ("2.75", "5.25"),
    "Dallas-Fort Worth": ("2.25", "4.75"),
    "Atlanta": ("2.25", "4.75"),
    "Phoenix": ("2", "4.50"),
    "Houston": ("1.75", "4.25"),
}

ROWS = []
for region, (low, high) in PER_SQFT.items():
    midpoint = (Decimal(low) + Decimal(high)) / 2
    ROWS.append((
        region, "Interior Painting", "Cost per Sq Ft (Interior)",
        low, str(midpoint), high, "N/A", "N/A", "N/A",
        "Walls, trim and ceilings; prep work included",
        BRUSHQUOTE.format(low=low, high=high), "2026-01-01",
    ))

# Washington DC: BrushQuote does not cover it, and Angi publishes a typical
# rate outright, so no midpoint is derived here.
ROWS += [
    ("Washington DC", "Interior Painting", "Cost per Sq Ft (Interior)",
     "2", "4.70", "6", "N/A", "N/A", "N/A",
     "Typical figure covers walls and ceilings; walls alone run $1.50-$3.50",
     "Angi", "2026-08-04"),
    ("Washington DC", "Interior Painting", "Whole-Home Interior Repaint",
     "1045", "2120", "3196", "N/A", "N/A", "N/A",
     "Labor is 75% to 95% of the total", "Angi", "2026-08-04"),
    ("Washington DC", "Interior Painting", "Single Room Repaint",
     "436", "1198", "1742", "50", "82", "N/A",
     "A 10x10 room runs $200-$600; a 16x16 room $515-$1,540",
     "Angi", "2026-07-08"),
]


def main() -> int:
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        existing = list(reader)

    keys = {(r[0], r[1], r[2]) for r in existing if len(r) >= 3}
    new_rows = [r for r in ROWS if (r[0], r[1], r[2]) not in keys]
    if not new_rows:
        print("nothing to append; all painting rows already present")
        return 0
    if len(header) != 12:
        print(f"unexpected header width {len(header)}: {header}", file=sys.stderr)
        return 1

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(existing)
        writer.writerows(new_rows)

    print(f"appended {len(new_rows)} painting rows ({len(existing) + len(new_rows)} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
