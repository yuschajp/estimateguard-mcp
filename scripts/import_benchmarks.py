#!/usr/bin/env python3
"""Import the vendored seed benchmarks into Postgres.

Idempotent: every row is upserted on (region, trade, service_type), so this
is safe to rerun any number of times. The service also seeds automatically
on first boot when the table is empty; this script is for manual (re)imports.

Usage:
    DATABASE_URL=postgres://... python import_benchmarks.py [path/to.csv]

Defaults to data/benchmarks_7cities.csv (vendored copy of the legacy
estimate-reviewer's COMPETITIVE_DATA_7_CITIES.csv).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import benchmarks


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if benchmarks.psycopg is None:
        print("psycopg is not installed", file=sys.stderr)
        return 2
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else benchmarks.CSV_PATH
    try:
        conn = benchmarks.psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:
        print(f"cannot connect: {exc}", file=sys.stderr)
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(benchmarks.SCHEMA_SQL)
        conn.commit()
        result = benchmarks.import_csv(conn, csv_path)
        print(f"imported={result.get('imported', 0)} from {csv_path}")
        n = benchmarks.count_rows()
        print(f"benchmark_ranges rows now: {n}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
