"""Seed benchmark ranges for EstimateGuard.

City-level cost benchmarks compiled from published cost guides (Angi,
HomeAdvisor, regional contractor sources) in the legacy estimate-reviewer
repository: 7 cities compiled 2026-08-03, expanded to 12 cities 2026-08-09
(COMPETITIVE_DATA_7_CITIES.csv, vendored at data/benchmarks_7cities.csv).
The source repo's own commit message describes the expansion rows as
"researched from Angi/HomeAdvisor/regional cost guides, 2026 pricing"; no
notebook, scraper, or detailed methodology was found, so the exact
collection method is not documented and the figures are presented as
published-guide data, not independently verified measurements.

These are SEED benchmarks, not observed EstimateGuard data:

- every row carries ``sample_size=0`` and a ``provenance`` string,
- rows are stored at city (metro) level with an explicit ``region`` field.
  No zip3 is stored on a row: every covered city spans several zip3
  prefixes, so no city maps unambiguously to one zip3,
- query ZIPs are routed to their metro via ``ZIP3_REGION`` for lookup only;
  the row itself stays city-level and is labeled as such,
- this table is never aggregated with ``estimate_observations``; the two
  answer different questions ("what do guides say?" vs "what did we see?").

Money is Decimal (NUMERIC in Postgres); rounding to cents happens at output.
The lookup path never raises: an unreachable database yields ``None`` and
callers fall back to the legacy in-memory scaffolding seed.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

try:
    import psycopg
except Exception:  # pragma: no cover - optional at test time
    psycopg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Provenance (established read-only from the legacy repo before import)
# ---------------------------------------------------------------------------
# git log on the CSV:
#   2026-08-04 "Fairness scoring live: 7-city benchmarks" (151 rows:
#               Chicago, Denver, Los Angeles, Miami, NYC, Phoenix, Seattle)
#   2026-08-09 "feat: expand benchmark coverage to 12 cities"
#               (+120 rows: Atlanta, Boston, Dallas-Fort Worth,
#               San Francisco, Washington DC; commit message: "researched
#               from Angi/HomeAdvisor/regional cost guides, 2026 pricing")
# DATA_ANALYSIS_7_CITIES.md (2026-08-03) describes "200+ verified pricing
# data points across 5 major trades". Each CSV row names its publisher(s)
# in Data_Source.
# What was NOT found: any notebook, scraper, or detailed methodology, so
# the exact collection method is not documented. We repeat the source's own
# description ("researched from ... cost guides") without upgrading it to a
# claim of independent verification, and we do not claim the figures were
# manually compiled, scraped, or generated -- that was not established.
AS_OF_ORIGINAL = "2026-08-03"  # 7-city data (doc date; committed 2026-08-04)
AS_OF_EXPANSION = "2026-08-09"  # +5 cities (commit date)

# Source date follows the dataset generation a row came from.
_REGION_AS_OF_DATE = {
    "Chicago": AS_OF_ORIGINAL,
    "Denver": AS_OF_ORIGINAL,
    "Los Angeles": AS_OF_ORIGINAL,
    "Miami": AS_OF_ORIGINAL,
    "NYC": AS_OF_ORIGINAL,
    "Phoenix": AS_OF_ORIGINAL,
    "Seattle": AS_OF_ORIGINAL,
    "Atlanta": AS_OF_EXPANSION,
    "Boston": AS_OF_EXPANSION,
    "Dallas-Fort Worth": AS_OF_EXPANSION,
    "San Francisco": AS_OF_EXPANSION,
    "Washington DC": AS_OF_EXPANSION,
}

PROVENANCE_TEMPLATE = (
    "Seed benchmark compiled from published cost guides "
    "(Angi, HomeAdvisor, regional contractor sources). Exact collection "
    "methodology not documented in the source repository. "
    "Original 7-city data 2026-08-03; expanded to 12 cities 2026-08-09. "
    "Row source: {source}. "
    "Not observed EstimateGuard job data (sample_size=0)."
)

# ---------------------------------------------------------------------------
# Metro routing: zip3 -> region (city level)
# ---------------------------------------------------------------------------
# Curated groupings of the core zip3 prefixes per covered metro. Used ONLY to
# route a query ZIP to the right city-level benchmark row; the row itself is
# stored and labeled at city level, never as zip3-level data.
_REGION_ZIP3S: dict[str, list[str]] = {
    "Atlanta": ["300", "301", "302", "303"],
    "Boston": ["010", "011", "012", "013", "014", "015", "016", "017", "018",
               "019", "020", "021", "022", "023", "024", "025", "026", "027"],
    "Chicago": ["600", "601", "602", "603", "604", "605", "606", "607", "608"],
    "Dallas-Fort Worth": ["750", "751", "752", "753", "754", "760", "761", "762"],
    "Denver": ["800", "801", "802", "803", "804"],
    "Los Angeles": ["900", "901", "902", "903", "904", "905", "906", "907",
                    "908", "910", "911", "912", "913", "914", "915", "916",
                    "917", "918"],
    "Miami": ["330", "331", "332", "333", "334"],
    "NYC": ["100", "101", "102", "103", "104", "110", "111", "112", "113",
            "114", "116"],
    "Phoenix": ["850", "851", "852", "853", "855"],
    "San Francisco": ["940", "941", "943", "944", "945", "946", "947", "948",
                      "949", "950", "951"],
    "Seattle": ["980", "981", "982", "983", "984"],
    "Washington DC": ["200", "201", "202", "203", "204", "205", "206", "207",
                      "208", "209", "220", "221", "222", "223"],
}

ZIP3_REGION: dict[str, str] = {
    z: region for region, zips in _REGION_ZIP3S.items() for z in zips
}

COVERED_REGIONS = sorted(_REGION_ZIP3S)


def region_for_zip3(zip3: str) -> Optional[str]:
    """Metro region for a 3-digit ZIP prefix, or None when not covered."""
    return ZIP3_REGION.get(zip3 or "")


# ---------------------------------------------------------------------------
# Schema (migration). Separate table from estimate_observations, on purpose.
# ---------------------------------------------------------------------------
# The DDL lives in migrations/001_benchmark_ranges.sql and is loaded here so
# the file the service applies and the file a human reviews cannot drift.
_MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / "001_benchmark_ranges.sql"


def _load_schema_sql() -> str:
    ddl = _MIGRATION_PATH.read_text(encoding="utf-8")
    # Strip the header comment; keep only executable statements.
    stmts = [
        line for line in ddl.splitlines()
        if not line.lstrip().startswith("--")
    ]
    return "\n".join(stmts).strip() + "\n"


SCHEMA_SQL = _load_schema_sql()

CSV_PATH = Path(__file__).resolve().parent / "data" / "benchmarks_7cities.csv"


# ---------------------------------------------------------------------------
# CSV parsing / normalization (Decimal throughout; source floats never carried)
# ---------------------------------------------------------------------------
def _to_decimal(raw: str) -> Optional[Decimal]:
    text = (raw or "").strip().replace("$", "").replace(",", "")
    if text in ("", "N/A", "n/a", "-"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_low_high(raw: str) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """'150-300' -> (150, 300); '175' -> (175, 175); 'N/A' -> (None, None)."""
    text = (raw or "").strip()
    if text in ("", "N/A", "n/a", "-"):
        return None, None
    if "-" in text:
        lo_s, _, hi_s = text.partition("-")
        return _to_decimal(lo_s), _to_decimal(hi_s)
    value = _to_decimal(text)
    return value, value


def _service_basis(service_type: str) -> str:
    """Map a CSV service type to the MCP pricing basis."""
    text = service_type.lower()
    if "per sq ft" in text or "per sqft" in text:
        return "per_sqft"
    if "per square" in text:
        return "per_square"
    return "flat"


# Minimal synonym folding so "roof" matches "shingle" service types.
_TOKEN_SYNONYMS = {
    "roof": "shingle",
    "roofs": "shingle",
    "roofing": "shingle",
    "shingles": "shingle",
}

_STOP_TOKENS = frozenset(
    {
        "per", "a", "an", "the", "of", "and", "or", "with", "for",
        "square", "squares", "sq", "sqft", "ft", "foot", "feet",
        "flat", "project", "projects", "job", "jobs", "lot", "lots",
        "lump", "sum", "fixed", "price", "prices", "pricing",
        "installed", "installation", "cost", "estimate",
    }
)


def _content_tokens(text: str) -> set[str]:
    # "2,000" must tokenize as one number, matching a hint's "2000".
    squashed = re.sub(r"(?<=\d),(?=\d)", "", text or "")
    toks = re.findall(r"[a-z0-9]+", squashed.lower())
    return {_TOKEN_SYNONYMS.get(t, t) for t in toks if t not in _STOP_TOKENS}


def resolve_candidates(
    candidates: list[dict], hint: Optional[str]
) -> Optional[dict]:
    """Pick one benchmark row from same-(region, trade, basis) candidates.

    One candidate -> it wins. Several -> the hint's content tokens must all
    appear in exactly one candidate's service type. Otherwise None: the
    caller reports the ambiguity instead of guessing.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    hint_toks = _content_tokens(hint) if hint else set()
    if not hint_toks:
        return None
    winners = [
        c for c in candidates if hint_toks <= _content_tokens(c["service_type"])
    ]
    return winners[0] if len(winners) == 1 else None


# ---------------------------------------------------------------------------
# Connection + idempotent import
# ---------------------------------------------------------------------------
_conn = None


def _connect():
    """Module-level connection; creates schema and seeds on first use."""
    global _conn
    if psycopg is None:
        return None
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg.connect(dsn, connect_timeout=10)
            with _conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
            _conn.commit()
            _ensure_seeded(_conn)
        return _conn
    except Exception as exc:  # never break the tool on DB trouble
        print(f"[benchmarks] db unavailable: {exc}", file=sys.stderr, flush=True)
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn = None
        return None


def db_reachable() -> bool:
    """True when the benchmark store is reachable (schema ensured)."""
    return _connect() is not None


def _normalize_row(csv_row: dict) -> Optional[dict]:
    # Deferred import: costdata imports this module lazily, so a top-level
    # import here would be circular.
    from costdata import normalize_trade

    avg = _to_decimal(csv_row.get("Avg_Price", ""))
    if avg is None:
        print(
            f"[benchmarks] skipping row without avg price: "
            f"{csv_row.get('City')} / {csv_row.get('Service_Type')}",
            file=sys.stderr, flush=True,
        )
        return None
    low = _to_decimal(csv_row.get("Low_Price", ""))
    high = _to_decimal(csv_row.get("High_Price", ""))
    if low is None and (csv_row.get("Low_Price") or "").strip() not in (
        "", "N/A", "n/a", "-",
    ):
        # Non-empty but unparseable low value: quarantine the field, keep row.
        print(
            f"[benchmarks] quarantined unparseable Low_Price "
            f"'{csv_row.get('Low_Price')}' for "
            f"{csv_row.get('City')} / {csv_row.get('Service_Type')}; "
            f"stored as NULL",
            file=sys.stderr, flush=True,
        )
    # Labor rates come as a low/high pair of columns, not a range string.
    labor_lo = _to_decimal(csv_row.get("Labor_Rate_Low", ""))
    labor_hi = _to_decimal(csv_row.get("Labor_Rate_High", ""))
    permit_lo, permit_hi = _parse_low_high(csv_row.get("Permit_Cost", ""))
    source = (csv_row.get("Data_Source") or "").strip() or "unspecified"
    region = (csv_row.get("City") or "").strip()
    return {
        "region": region,
        "zip3": None,  # city-level rows; no unambiguous zip3 mapping exists
        "trade": normalize_trade((csv_row.get("Trade") or "").strip().lower()),
        "service_type": (csv_row.get("Service_Type") or "").strip(),
        "basis": _service_basis(csv_row.get("Service_Type") or ""),
        "low_price": low,
        "avg_price": avg,
        "high_price": high,
        "labor_rate_low": labor_lo,
        "labor_rate_high": labor_hi,
        "permit_cost_low": permit_lo,
        "permit_cost_high": permit_hi,
        "sample_size": 0,
        "provenance": PROVENANCE_TEMPLATE.format(source=source),
        # Source date follows the dataset generation the row came from.
        "as_of_date": _REGION_AS_OF_DATE.get(region, AS_OF_EXPANSION),
    }


_UPSERT_SQL = """
INSERT INTO benchmark_ranges
    (region, zip3, trade, service_type, basis,
     low_price, avg_price, high_price,
     labor_rate_low, labor_rate_high, permit_cost_low, permit_cost_high,
     sample_size, provenance, as_of_date)
VALUES
    (%(region)s, %(zip3)s, %(trade)s, %(service_type)s, %(basis)s,
     %(low_price)s, %(avg_price)s, %(high_price)s,
     %(labor_rate_low)s, %(labor_rate_high)s, %(permit_cost_low)s, %(permit_cost_high)s,
     %(sample_size)s, %(provenance)s, %(as_of_date)s)
ON CONFLICT (region, trade, service_type) DO UPDATE SET
    zip3 = EXCLUDED.zip3,
    basis = EXCLUDED.basis,
    low_price = EXCLUDED.low_price,
    avg_price = EXCLUDED.avg_price,
    high_price = EXCLUDED.high_price,
    labor_rate_low = EXCLUDED.labor_rate_low,
    labor_rate_high = EXCLUDED.labor_rate_high,
    permit_cost_low = EXCLUDED.permit_cost_low,
    permit_cost_high = EXCLUDED.permit_cost_high,
    sample_size = EXCLUDED.sample_size,
    provenance = EXCLUDED.provenance,
    as_of_date = EXCLUDED.as_of_date;
"""


def import_csv(conn, csv_path: Path | str = CSV_PATH) -> dict:
    """Idempotent import: upserts every CSV row. Safe to rerun."""
    path = Path(csv_path)
    if not path.exists():
        return {"imported": 0, "skipped": True, "reason": f"{path} not found"}
    normalized = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for csv_row in csv.DictReader(f):
            row = _normalize_row(csv_row)
            if row is not None:
                normalized.append(row)
    with conn.cursor() as cur:
        for row in normalized:
            cur.execute(_UPSERT_SQL, row)
    conn.commit()
    return {"imported": len(normalized)}


def _ensure_seeded(conn) -> None:
    """Seed from the vendored CSV when the table is empty (first boot)."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM benchmark_ranges")
            (n,) = cur.fetchone()
        if n == 0:
            result = import_csv(conn)
            print(f"[benchmarks] seeded {result.get('imported', 0)} rows",
                  file=sys.stderr, flush=True)
    except Exception as exc:
        print(f"[benchmarks] seed check failed: {exc}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Lookups (never raise)
# ---------------------------------------------------------------------------
def _row_to_result(db_row: tuple) -> dict:
    (region, zip3, trade, service_type, basis, low, avg, high,
     labor_lo, labor_hi, permit_lo, permit_hi,
     sample_size, provenance, as_of_date) = db_row
    return {
        "low": low,
        "median": avg,
        "high": high,
        "unit": None,  # filled by costdata from BASIS_LABEL
        "sample_size": sample_size,
        "as_of_date": as_of_date.isoformat() if hasattr(as_of_date, "isoformat") else str(as_of_date),
        "provenance": provenance,
        "service_type": service_type,
        "region": region,
        "zip3": zip3,
        "labor_rate_low": labor_lo,
        "labor_rate_high": labor_hi,
        "permit_cost_low": permit_lo,
        "permit_cost_high": permit_hi,
        "source": "seed_benchmark",
    }


def find_candidates(trade: str, basis: str, zip3: str) -> list[dict]:
    """All seed rows for (region, trade, basis). Empty list when none/DB down."""
    region = region_for_zip3(zip3)
    if region is None:
        return []
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT region, zip3, trade, service_type, basis,
                       low_price, avg_price, high_price,
                       labor_rate_low, labor_rate_high,
                       permit_cost_low, permit_cost_high,
                       sample_size, provenance, as_of_date
                FROM benchmark_ranges
                WHERE region = %s AND trade = %s AND basis = %s
                ORDER BY service_type
                """,
                (region, trade, basis),
            )
            return [_row_to_result(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[benchmarks] lookup failed: {exc}", file=sys.stderr, flush=True)
        return []


def lookup(
    trade: str, basis: str, zip3: str, hint: Optional[str] = None
) -> Optional[dict]:
    """One seed row for (trade, basis, zip), disambiguated by hint."""
    return resolve_candidates(find_candidates(trade, basis, zip3), hint)


def has_coverage(trade: str, zip3: str) -> bool:
    """True when any seed row exists for this trade in the ZIP's region."""
    region = region_for_zip3(zip3)
    if region is None:
        return False
    conn = _connect()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM benchmark_ranges WHERE region = %s AND trade = %s LIMIT 1",
                (region, trade),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def bases_for(trade: str, zip3: str) -> list[str]:
    """Which pricing bases have seed rows for this trade in the ZIP's region."""
    region = region_for_zip3(zip3)
    if region is None:
        return []
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT basis FROM benchmark_ranges WHERE region = %s AND trade = %s",
                (region, trade),
            )
            return sorted(r[0] for r in cur.fetchall())
    except Exception:
        return []


def count_rows() -> Optional[int]:
    """Number of seed rows. None when the DB is unreachable; never raises."""
    conn = _connect()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM benchmark_ranges")
            (n,) = cur.fetchone()
            return int(n)
    except Exception:
        return None
