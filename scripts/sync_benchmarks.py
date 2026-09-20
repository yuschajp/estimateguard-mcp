#!/usr/bin/env python3
"""Make the benchmark table match the vendored CSV exactly, then report.

``import_benchmarks.py`` upserts on (region, trade, service_type), so it can
add and update rows but never remove one. A row whose service type changed
in the CSV — including one the importer previously stored under a corrupted
name, such as the truncated "Panel Upgrade (200 amp" that an unquoted comma
produced — therefore survives alongside its corrected replacement, and the
lookup can still serve it.

This script imports the CSV and then deletes any row the CSV no longer
names. Idempotent: rerunning it on a synced table deletes nothing.

Usage:
    DATABASE_URL=postgres://... python scripts/sync_benchmarks.py [path/to.csv]
    DATABASE_URL=postgres://... python scripts/sync_benchmarks.py --dry-run

Prints the rows it would delete before deleting them, so a run that plans a
surprising deletion can be stopped and inspected.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import benchmarks  # noqa: E402


def _csv_keys(csv_path: Path) -> set[tuple[str, str, str]]:
    """The (region, trade, service_type) keys the CSV defines.

    Built through ``_normalize_row`` so a row the import guards reject is
    not treated as present: a malformed row must not keep a stale database
    row alive.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = [benchmarks._normalize_row(dict(r)) for r in csv.DictReader(f)]
    return {
        (row["region"], row["trade"], row["service_type"])
        for row in rows
        if row is not None
    }


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    if benchmarks.psycopg is None:
        print("psycopg is not installed", file=sys.stderr)
        return 2

    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]
    csv_path = Path(args[0]) if args else benchmarks.CSV_PATH
    if not csv_path.exists():
        print(f"{csv_path} not found", file=sys.stderr)
        return 2

    expected = _csv_keys(csv_path)
    if not expected:
        print("refusing to sync: the CSV defines no valid rows", file=sys.stderr)
        return 1

    try:
        conn = benchmarks.psycopg.connect(dsn, connect_timeout=10)
    except Exception as exc:
        print(f"cannot connect: {exc}", file=sys.stderr)
        return 1

    try:
        with conn.cursor() as cur:
            cur.execute(benchmarks.SCHEMA_SQL)
        conn.commit()

        if not dry_run:
            result = benchmarks.import_csv(conn, csv_path)
            print(f"imported/updated {result.get('imported', 0)} rows from {csv_path}")

        with conn.cursor() as cur:
            cur.execute("SELECT region, trade, service_type FROM benchmark_ranges")
            stored = [tuple(r) for r in cur.fetchall()]

        stale = [key for key in stored if key not in expected]
        if not stale:
            print(f"no stale rows; table matches {csv_path.name}")
            return 0

        print(f"{len(stale)} row(s) not present in the CSV:")
        for region, trade, service_type in sorted(stale):
            print(f"  - {region} | {trade} | {service_type}")
        if dry_run:
            print("(dry run: nothing deleted)")
            return 0

        with conn.cursor() as cur:
            for key in stale:
                cur.execute(
                    "DELETE FROM benchmark_ranges "
                    "WHERE region = %s AND trade = %s AND service_type = %s",
                    key,
                )
        conn.commit()
        print(f"deleted {len(stale)} stale row(s)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
