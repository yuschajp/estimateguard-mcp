"""Seed benchmark ranges for EstimateGuard.

City-level cost benchmarks compiled from published cost guides (Angi,
HomeAdvisor, regional contractor sources) in the legacy estimate-reviewer
repository: 7 cities compiled 2026-08-03, expanded to 12 cities 2026-08-09
(COMPETITIVE_DATA_7_CITIES.csv, vendored at data/benchmarks_7cities.csv).
A later pass on 2026-09-20 added Austin and Houston and filled the HVAC hole
in NYC and Seattle; those rows name their own publisher and carry their own
publication date in the CSV's As_Of_Date column, and
scripts/add_coverage_rows_2026_09.py records the source URL behind each one.
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
AS_OF_COVERAGE = "2026-09-20"  # +Austin, Houston, and the missing HVAC metros

# Source date follows the dataset generation a row came from. Rows added in
# the 2026-09 pass carry their own source page's date in the CSV's
# As_Of_Date column instead, which is more accurate than a generation date:
# the guides they came from were published across several months.
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
    "Austin": AS_OF_COVERAGE,
    "Houston": AS_OF_COVERAGE,
}

PROVENANCE_TEMPLATE = (
    "Seed benchmark compiled from published cost guides "
    "(Angi, HomeAdvisor, regional contractor sources). Exact collection "
    "methodology not documented in the source repository. "
    "Original 7-city data 2026-08-03; expanded to 12 cities 2026-08-09; "
    "Austin, Houston and the missing HVAC metros added 2026-09-20. "
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
    "Austin": ["786", "787"],
    "Boston": ["010", "011", "012", "013", "014", "015", "016", "017", "018",
               "019", "020", "021", "022", "023", "024", "025", "026", "027"],
    "Chicago": ["600", "601", "602", "603", "604", "605", "606", "607", "608"],
    "Dallas-Fort Worth": ["750", "751", "752", "753", "754", "760", "761", "762"],
    "Denver": ["800", "801", "802", "803", "804"],
    # Houston-The Woodlands-Sugar Land: city, Conroe, Katy/Sugar Land, and the
    # Galveston/Texas City end of the metro.
    "Houston": ["770", "771", "772", "773", "774", "775"],
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


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _row_as_of_date(csv_row: dict, region: str) -> str:
    """The date this row's figures were published, or its generation date.

    Rows compiled in one pass from guides published across several months
    are more honestly dated per row than per dataset generation, so the CSV
    carries an optional ``As_Of_Date``. A malformed value is ignored rather
    than stored: a wrong date would misrepresent how current a figure is.
    """
    raw = (csv_row.get("As_Of_Date") or "").strip()
    if _ISO_DATE_RE.match(raw):
        return raw
    if raw:
        print(
            f"[benchmarks] ignoring unparseable As_Of_Date {raw!r} for "
            f"{_row_label(csv_row)}; using the region's date",
            file=sys.stderr, flush=True,
        )
    return _REGION_AS_OF_DATE.get(region, AS_OF_EXPANSION)


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
    candidates: list[dict], hint: Optional[str], require_hint: bool = False
) -> Optional[dict]:
    """Pick one benchmark row from same-(region, trade, basis) candidates.

    One candidate -> it wins. Several -> the hint's content tokens must all
    appear in exactly one candidate's service type. Otherwise None: the
    caller reports the ambiguity instead of guessing.

    ``require_hint`` withdraws the free win for a lone candidate, and is set
    by callers that did not choose the service type themselves. Line-by-line
    estimate evaluation is the case: seed service types describe whole jobs
    ("Cost per Sq Ft (Asphalt)" is a finished roof, tear-off and disposal
    included), so letting a lone candidate win would rate a tear-off line
    against the price of the entire roof and report it as far below market.
    A homeowner asking ``get_cost_range`` named the trade and scope
    themselves, so there the lone candidate is the answer they asked for.
    """
    if not candidates:
        return None
    hint_toks = _content_tokens(hint) if hint else set()
    if len(candidates) == 1 and not require_hint:
        return candidates[0]
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


def _row_label(csv_row: dict) -> str:
    return f"{csv_row.get('City')} / {csv_row.get('Service_Type')}"


def _structurally_valid(csv_row: dict) -> bool:
    """Reject a CSV row whose columns have shifted.

    An unquoted comma inside a field (e.g. a service type written as
    ``Panel Upgrade (200 amp, underground service)``) pushes every later
    value one column left, so prices land in the wrong fields and a labor
    rate can end up holding a project price. ``csv.DictReader`` parks the
    overflow under the ``None`` key, which is the signal here. Such a row is
    skipped loudly rather than stored: a wrong price is worse than a
    missing one.
    """
    if None in csv_row:
        print(
            f"[benchmarks] skipping malformed row (extra unquoted commas, "
            f"columns shifted): {_row_label(csv_row)}; overflow="
            f"{csv_row.get(None)!r}",
            file=sys.stderr, flush=True,
        )
        return False
    return True


# A published hourly labor rate above this is not a labor rate; it is a
# project price that landed in the wrong column.
_MAX_PLAUSIBLE_LABOR_RATE = Decimal("1000")


def _ordering_valid(
    row: dict, csv_row: dict, label: Optional[str] = None
) -> bool:
    """Reject a normalized row whose prices contradict each other.

    low <= avg <= high must hold for any real range, and an hourly labor
    rate has a sane ceiling. A violation means the values are not what
    their column names say, so the row is skipped rather than served.
    """
    name = label or _row_label(csv_row)
    low, avg, high = row["low_price"], row["avg_price"], row["high_price"]
    problems = []
    if low is not None and low > avg:
        problems.append(f"low {low} > avg {avg}")
    if high is not None and high < avg:
        problems.append(f"high {high} < avg {avg}")
    for field in ("labor_rate_low", "labor_rate_high"):
        rate = row[field]
        if rate is not None and rate > _MAX_PLAUSIBLE_LABOR_RATE:
            problems.append(f"{field} {rate} exceeds hourly ceiling")
    if problems:
        print(
            f"[benchmarks] skipping implausible row ({'; '.join(problems)}): "
            f"{name}",
            file=sys.stderr, flush=True,
        )
        return False
    return True


def _normalize_row(csv_row: dict) -> Optional[dict]:
    # Deferred import: costdata imports this module lazily, so a top-level
    # import here would be circular.
    from costdata import normalize_trade

    if not _structurally_valid(csv_row):
        return None

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
    row = {
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
        # A row that names its own source date uses it; otherwise the date
        # follows the dataset generation the row came from.
        "as_of_date": _row_as_of_date(csv_row, region),
    }
    return row if _ordering_valid(row, csv_row) else None


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
        "basis": basis,
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


# ---------------------------------------------------------------------------
# Area-basis conversion (per roofing square <-> per square foot)
# ---------------------------------------------------------------------------
# A roofing square is exactly 100 square feet, so these two bases describe the
# same quantity at a fixed ratio and converting between them is arithmetic,
# not estimation. Nothing else converts: a flat project price cannot become a
# unit price without knowing the job size, and inventing that size would be a
# guess. Only the price range converts; labor rates are hourly and permit
# costs are per project, so both are carried across untouched.
SQFT_PER_SQUARE = Decimal("100")

_BASIS_TEXT = {"per_square": "per roofing square", "per_sqft": "per square foot"}

_CONVERTS_TO = {"per_square": "per_sqft", "per_sqft": "per_square"}


def convert_basis(row: dict, target_basis: str) -> Optional[dict]:
    """Restate a per-area benchmark row in the other per-area basis.

    Returns the row unchanged when it is already in ``target_basis``, a
    converted copy when the two bases are per-area (exact x100 / /100), and
    None when no exact conversion exists. A converted row says so in its
    provenance, so a homeowner is never shown a derived figure that claims
    to be a published one.
    """
    source_basis = row.get("basis")
    if source_basis == target_basis:
        return row
    if _CONVERTS_TO.get(source_basis) != target_basis:
        return None
    factor = (
        SQFT_PER_SQUARE
        if target_basis == "per_square"
        else Decimal(1) / SQFT_PER_SQUARE
    )
    converted = dict(row)
    for field in ("low", "median", "high"):
        value = row.get(field)
        converted[field] = value * factor if value is not None else None
    converted["basis"] = target_basis
    converted["converted_from"] = source_basis
    converted["provenance"] = (
        f"{row.get('provenance') or ''} Restated by EstimateGuard from "
        f"{_BASIS_TEXT[source_basis]} to {_BASIS_TEXT[target_basis]} "
        f"(1 roofing square = 100 square feet); the published figures are "
        f"{_BASIS_TEXT[source_basis]}."
    ).strip()
    return converted


def count_winners(candidates: list[dict], hint: Optional[str]) -> int:
    """How many candidates the hint uniquely identifies (0, 1, or more)."""
    if not candidates:
        return 0
    if len(candidates) == 1:
        return 1
    hint_toks = _content_tokens(hint) if hint else set()
    if not hint_toks:
        return len(candidates)
    return sum(
        1 for c in candidates if hint_toks <= _content_tokens(c["service_type"])
    )


def find_candidates(
    trade: str,
    basis: Optional[str],
    zip3: str,
    allow_conversion: bool = True,
) -> list[dict]:
    """All seed rows for (region, trade), optionally filtered to one basis.

    ``basis=None`` searches every basis: used when the scope names a job
    ("asphalt shingle 2000 sqft") without declaring a pricing unit, so the
    hint can disambiguate across per-sqft and flat rows alike.
    Empty list when none/DB down.

    When a per-area basis has no published rows, rows in the other per-area
    basis are restated into it (see ``convert_basis``). ``allow_conversion``
    is the internal guard that keeps that fallback one level deep.
    """
    region = region_for_zip3(zip3)
    if region is None:
        return []
    conn = _connect()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            if basis is None:
                cur.execute(
                    """
                    SELECT region, zip3, trade, service_type, basis,
                           low_price, avg_price, high_price,
                           labor_rate_low, labor_rate_high,
                           permit_cost_low, permit_cost_high,
                           sample_size, provenance, as_of_date
                    FROM benchmark_ranges
                    WHERE region = %s AND trade = %s
                    ORDER BY service_type
                    """,
                    (region, trade),
                )
            else:
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
            rows = [_row_to_result(r) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[benchmarks] lookup failed: {exc}", file=sys.stderr, flush=True)
        return []

    if rows or not allow_conversion or basis is None or basis not in _CONVERTS_TO:
        return rows

    # Nothing published in the requested per-area basis. The other per-area
    # basis describes the same thing at a fixed 100:1 ratio, so restate those
    # rows rather than reporting a miss a homeowner would read as "no data
    # for my area".
    converted = [
        row
        for row in (
            convert_basis(candidate, basis)
            for candidate in find_candidates(
                trade, _CONVERTS_TO[basis], zip3, allow_conversion=False
            )
        )
        if row is not None
    ]
    return converted


def lookup(
    trade: str,
    basis: Optional[str],
    zip3: str,
    hint: Optional[str] = None,
    require_hint: bool = False,
) -> Optional[dict]:
    """One seed row for (trade, zip), disambiguated by hint across bases."""
    return resolve_candidates(
        find_candidates(trade, basis, zip3), hint, require_hint=require_hint
    )


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
    """Which pricing bases can be answered for this trade in the ZIP's region.

    Includes a per-area basis that has no rows of its own but can be
    restated from the other per-area basis, since that is what the lookup
    will actually serve.
    """
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
            stored = {r[0] for r in cur.fetchall()}
    except Exception:
        return []
    servable = set(stored)
    for stored_basis in stored:
        derived = _CONVERTS_TO.get(stored_basis)
        if derived is not None:
            servable.add(derived)
    return sorted(servable)


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
