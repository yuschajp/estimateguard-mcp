"""Observation store for EstimateGuard.

Writes one row per evaluated line item to Render Postgres. Only the
whitelisted, PII-free fields are ever persisted:

    observed_at, zip3, trade, scope, line_description,
    quantity, unit, unit_price, computed_line_total, source

PII is stripped from the estimate text during parsing, *before* any
observation row is constructed. Strip counts are logged per category for
auditing. The insert path never raises: if the database is unavailable the
tool result is unaffected and the failure is logged server-side.

Provenance: every row carries a ``source`` of 'production' | 'test' |
'verification' (default 'production'). Rows written by the test suite or by
verification tooling are stamped automatically (see ``resolve_source``) so
they can never be mistaken for real evaluations. Any aggregation that feeds
benchmarks MUST filter to source='production' -- use
``fetch_production_observations``.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg
except Exception:  # pragma: no cover - optional at test time
    psycopg = None  # type: ignore[assignment]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS estimate_observations (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    zip3 CHAR(3) NOT NULL,
    trade TEXT NOT NULL,
    scope TEXT NOT NULL,
    line_description TEXT NOT NULL,
    quantity NUMERIC NOT NULL,
    unit TEXT NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    computed_line_total NUMERIC(12, 2) NOT NULL,
    source TEXT NOT NULL DEFAULT 'production'
);
CREATE INDEX IF NOT EXISTS idx_estimate_observations_trade_scope_zip3
    ON estimate_observations (trade, scope, zip3);
CREATE INDEX IF NOT EXISTS idx_estimate_observations_observed_at
    ON estimate_observations (observed_at DESC);
-- Maintained counter for the /health probe. A separate table on purpose:
-- estimate_observations itself is never modified. The counter is seeded from
-- the exact row count once, then incremented atomically alongside every
-- insert, so health checks stay O(1) no matter how large the corpus grows.
CREATE TABLE IF NOT EXISTS health_counters (
    name TEXT PRIMARY KEY,
    n BIGINT NOT NULL DEFAULT 0
);
"""

# Name of the counter row tracking estimate_observations.
OBSERVATION_COUNTER_NAME = "estimate_observations"

# Allowed provenance values for the `source` column.
SOURCE_VALUES = ("production", "test", "verification")

# Server-side env var stamping the source of written rows. The test suite
# forces this to 'test' (see tests/conftest.py); operators can set it to
# 'verification' on a staging/verification deployment.
SOURCE_ENV_VAR = "ESTIMATEGUARD_OBSERVATION_SOURCE"

# Values a client may select via the X-EstimateGuard-Source request header.
# 'production' is deliberately excluded: a caller can only ever downgrade its
# own rows out of the production corpus, never launder rows into it.
HEADER_SOURCES = ("test", "verification")

# Migration DDL lives in migrations/002_observation_source.sql and is loaded
# here so the file the service applies and the file a human reviews cannot
# drift (same pattern as benchmarks.py / 001).
_MIGRATION_002_PATH = (
    Path(__file__).resolve().parent / "migrations" / "002_observation_source.sql"
)


def _load_migration_sql() -> str:
    ddl = _MIGRATION_002_PATH.read_text(encoding="utf-8")
    stmts = [
        line for line in ddl.splitlines() if not line.lstrip().startswith("--")
    ]
    return "\n".join(stmts).strip() + "\n"


_MIGRATION_002_SQL = _load_migration_sql()


def resolve_source(explicit: str | None = None) -> str:
    """Resolve the provenance stamp for rows about to be written.

    Precedence: an explicit per-request value (the X-EstimateGuard-Source
    header, already restricted to test/verification by the caller) beats the
    ``ESTIMATEGUARD_OBSERVATION_SOURCE`` env var, which beats the default
    'production'. Invalid values are ignored with a stderr warning -- never
    persisted -- so a typo can neither pollute the corpus nor crash the tool.
    """
    if explicit is not None:
        norm = explicit.strip().lower()
        if norm in HEADER_SOURCES:
            return norm
        print(
            f"[observations] ignoring invalid explicit source {explicit!r}; "
            "falling back to env/default",
            file=sys.stderr,
            flush=True,
        )
    env = (os.environ.get(SOURCE_ENV_VAR) or "").strip().lower()
    if env in SOURCE_VALUES:
        return env
    if env:
        print(
            f"[observations] ignoring invalid {SOURCE_ENV_VAR}={env!r}; "
            "defaulting to 'production'",
            file=sys.stderr,
            flush=True,
        )
    return "production"


# Fields that may appear in an observation row. Anything else is dropped.
ALLOWED_ROW_FIELDS = (
    "zip3",
    "trade",
    "scope",
    "line_description",
    "quantity",
    "unit",
    "unit_price",
    "computed_line_total",
    "source",
)

# ---------------------------------------------------------------------------
# PII stripping
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# NOTE: separators are [-. ] only -- never \s, so a phone can never glue
# across a line break (e.g. the tail of a ZIP on the previous line).
_PHONE_RE = re.compile(
    r"(?:\+?1[-. ]?)?(?:\(\d{3}\)|\d{3})[-. ]\d{3}[-. ]\d{4}"
)
_LICENSE_RE = re.compile(
    r"\blic(?:ense)?\.?\s*(?:no\.?|number|#)?\s*[:#]?\s*[A-Za-z0-9][\w-]*",
    re.IGNORECASE,
)
_STREET_SUFFIX = (
    r"St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|Boulevard|"
    r"Way|Ct|Court|Pl|Place|Ter|Terrace|Pkwy|Parkway"
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z0-9][\w.'-]*"
    r"(?:\s+[A-Z][\w.'-]*){0,3}\s+(?:" + _STREET_SUFFIX + r")\b\.?"
    r"(?:\s*,\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s*,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?"
)
# Horizontal whitespace only between name words: the pattern must never
# glue "John Smith" on one line to "ABC Roofing LLC" on the next.
_CONTRACTOR_RE = re.compile(
    r"\b(?:[A-Z][\w&'.-]*[ \t]+){1,3}"
    r"(LLC|Inc\.?|Corp\.?|Corporation|Contracting|Roofing|Builders|"
    r"Construction|Services|Co\.?|Company)\b"
)
_LABELED_NAME_RE = re.compile(
    r"(?i)\b(prepared for|bill to|billed to|customer|client|homeowner|"
    r"attention|attn|contact|name)\s*:\s*"
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z'.-]{1,20}){0,2})"
)
_NON_NAME_LABELS = frozenset(
    {
        "estimate", "proposal", "quote", "invoice", "bid", "scope",
        "project", "summary", "details", "detail", "date", "total",
        "subtotal", "work", "description", "phone", "email", "address",
        "license", "contractor", "payment", "terms", "notes", "thank",
    }
)
_NAME_TOKEN_RE = re.compile(r"^[A-Z][a-z'-]{1,20}$")
_LINE_ITEM_HINT_RE = re.compile(r"^\s*\d+\s*[.)]\s+\S.*[$@]")

# Crew/labor-anchored worker-name stripping for line-item descriptions.
# Conservative by construction: a name is only removed when it is adjacent
# to an explicit crew/labor/foreman/installer word, or is an
# initial-plus-surname form (J. Martinez). Brand and material names never
# match: "Pella", "Owens Corning" and "James Hardie" appear without a crew
# word nearby, and chained initials ("A.O. Smith") are excluded by
# lookbehind. Bare "Martinez crew" (surname directly before a crew word,
# no other signal) is deliberately NOT stripped: it is indistinguishable
# from a product word in the same position ("Windows labor 8 hrs"), and
# mangling that would corrupt the user-facing description.
_CREW_WORD = r"(?i:crews?|labor|foreman|installers?)"
_PERSON_NAME = r"[A-Z][a-z]+(?:'[a-z]+)?"
_INITIAL_NAME = r"[A-Z]\.\s*" + _PERSON_NAME
# Crew word followed by a name: "foreman Johnson", "labor, Martinez",
# "crew J. Martinez". The Day lookahead keeps "Labor Day" intact.
_CREW_THEN_NAME_RE = re.compile(
    r"\b(" + _CREW_WORD + r")\s*,?\s*(?!Day\b)"
    r"(?:" + _INITIAL_NAME + r"|" + _PERSON_NAME + r")\b"
)
# Possessive name before a crew word: "Martinez's crew".
_POSSESSIVE_CREW_RE = re.compile(
    r"\b" + _PERSON_NAME + r"'s\s+(" + _CREW_WORD + r")\b"
)
# Initial-plus-surname anywhere ("J. Martinez"), except when chained to a
# previous initial ("A.O. Smith"). Two fixed-width lookbehinds because
# Python requires fixed-width lookbehind patterns.
_INITIAL_SURNAME_RE = re.compile(
    r"(?<![A-Z]\.)(?<![A-Z]\.\s)\b" + _INITIAL_NAME + r"\b"
)


def _sub_count(pattern: re.Pattern[str], text: str, repl: str = "") -> tuple[str, int]:
    """Substitute pattern with repl, returning (new_text, match_count)."""
    count = 0

    def _hit(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return repl

    return pattern.sub(_hit, text), count


def _split_header_body(text: str) -> tuple[list[str], int]:
    """Split text into lines; return (lines, body_start_index).

    The body starts at the first priced line item: a numbered item line or
    a line carrying both a dollar amount and an @/= price marker. Everything
    before that is the header block (names, addresses, letterhead), where
    person names and contractor business names are stripped.
    """
    lines = text.split("\n")
    body_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _LINE_ITEM_HINT_RE.match(line) or (
            "$" in stripped and ("@" in stripped or "=" in stripped)
        ):
            body_at = i
            break
    return lines, body_at


def _strip_standalone_names(header_lines: list[str]) -> tuple[list[str], int]:
    """Remove standalone person-name lines from header lines."""
    lines = list(header_lines)
    count = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.endswith(":"):
            continue
        if any(ch.isdigit() for ch in stripped):
            continue
        if any(ch in stripped for ch in "$@#"):
            continue
        tokens = stripped.split()
        if not 2 <= len(tokens) <= 4:
            continue
        if tokens[0].lower() in _NON_NAME_LABELS:
            continue
        if all(_NAME_TOKEN_RE.match(tok) for tok in tokens):
            lines[i] = ""
            count += 1
    return lines, count


def _strip_header_names(text: str) -> tuple[str, int]:
    """Remove standalone person-name lines from the pre-item header block."""
    lines, body_at = _split_header_body(text)
    header, count = _strip_standalone_names(lines[:body_at])
    return "\n".join(header + lines[body_at:]), count


def _strip_crew_names(text: str) -> tuple[str, int]:
    """Remove worker names anchored to crew/labor words, anywhere in the text.

    Targets the patterns that actually appear in line-item descriptions
    ("Labor, J. Martinez crew", "foreman Johnson") while leaving brand and
    material names untouched. Returns (cleaned_text, substitution_count).
    """
    count = 0
    # Each iteration strictly shortens the text, so this always terminates;
    # the loop lets multi-word names collapse ("crew Mary Johnson" ->
    # "crew Johnson" -> "crew").
    for _ in range(4):
        changed = False

        def _keep_crew(match: re.Match[str]) -> str:
            nonlocal count, changed
            count += 1
            changed = True
            return match.group(1)

        def _drop(match: re.Match[str]) -> str:
            nonlocal count, changed
            count += 1
            changed = True
            return ""

        text = _CREW_THEN_NAME_RE.sub(_keep_crew, text)
        text = _POSSESSIVE_CREW_RE.sub(_keep_crew, text)
        text = _INITIAL_SURNAME_RE.sub(_drop, text)
        if not changed:
            break
    return text, count


def strip_pii(text: str) -> tuple[str, dict[str, int]]:
    """Remove PII from estimate text.

    Email/phone/license/address patterns are unambiguous and apply to the
    whole text. Person names and contractor business names are stripped from
    the header block only. Additionally, worker names anchored to an
    explicit crew/labor/foreman/installer word ("Labor, J. Martinez crew",
    "foreman Johnson") are stripped everywhere, including inside line-item
    descriptions; brand and material names are never affected by that pass.

    Returns (cleaned_text, counts) where counts maps each category to the
    number of substitutions made. Categories: email, phone, license,
    address, contractor, name, crew.
    """
    counts: dict[str, int] = {}
    text, counts["email"] = _sub_count(_EMAIL_RE, text)
    text, counts["phone"] = _sub_count(_PHONE_RE, text)
    text, counts["license"] = _sub_count(_LICENSE_RE, text)
    text, counts["address"] = _sub_count(_ADDRESS_RE, text)

    def _labeled_name(match: re.Match[str]) -> str:
        counts["name"] = counts.get("name", 0) + 1
        return match.group(0)[: match.start(2) - match.start(0)]

    text = _LABELED_NAME_RE.sub(_labeled_name, text)

    lines, body_at = _split_header_body(text)
    header_text = "\n".join(lines[:body_at])
    header_text, counts["contractor"] = _sub_count(_CONTRACTOR_RE, header_text)
    header_lines = header_text.split("\n") if header_text else []
    header_lines, header_names = _strip_standalone_names(header_lines)
    counts["name"] = counts.get("name", 0) + header_names
    text = "\n".join(header_lines + lines[body_at:])
    text, counts["crew"] = _strip_crew_names(text)
    return text, counts


def log_strip_counts(counts: dict[str, int]) -> None:
    parts = " ".join(f"{cat}={counts.get(cat, 0)}" for cat in
                     ("name", "crew", "address", "phone", "email", "contractor", "license"))
    print(f"[pii-strip] {parts}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Row construction + persistence
# ---------------------------------------------------------------------------

def zip3_of(zip_code: str) -> str:
    digits = re.sub(r"\D", "", zip_code or "")
    return digits[:3] if len(digits) >= 3 else ""


def build_rows(
    *,
    zip_code: str,
    trade: str,
    lines: list[dict],
    source: str | None = None,
) -> list[dict]:
    """Build whitelisted observation rows from evaluated line internals.

    Each line dict carries: description, quantity (str), unit (str),
    unit_price (str), computed_line_total (str), scope (str).

    ``source`` is the provenance stamp; when omitted it is resolved
    automatically via ``resolve_source`` (env var, default 'production'),
    so test and verification callers never have to remember to pass it.
    """
    zip3 = zip3_of(zip_code)
    stamped = resolve_source(source)
    rows = []
    for line in lines:
        rows.append(
            {
                "zip3": zip3,
                "trade": trade or "",
                "scope": line.get("scope") or "",
                "line_description": line.get("description") or "",
                "quantity": line.get("quantity") or "",
                "unit": line.get("unit") or "",
                "unit_price": line.get("unit_price") or "",
                "computed_line_total": line.get("computed_line_total") or "",
                "source": stamped,
            }
        )
    for row in rows:
        for key in list(row):
            if key not in ALLOWED_ROW_FIELDS:
                del row[key]
    return rows


_conn = None


def _connect():
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
                # Migration 002: provenance tracking. Adds the `source`
                # column and backfills every pre-existing row as 'test'
                # (all of them predate this migration and come from
                # development / acceptance testing). Idempotent.
                cur.execute(_MIGRATION_002_SQL)
                # One-time seed of the health counter from the exact row
                # count, so rows written before this deploy are included.
                # Runs once per process; the ON CONFLICT makes it a no-op
                # afterwards. Later inserts increment the counter (see
                # record_observations), so it can never drift.
                cur.execute(
                    "INSERT INTO health_counters (name, n) "
                    "SELECT %s, count(*) FROM estimate_observations "
                    "ON CONFLICT (name) DO NOTHING",
                    (OBSERVATION_COUNTER_NAME,),
                )
            _conn.commit()
        return _conn
    except Exception as exc:  # never break the tool on DB trouble
        print(f"[observations] db unavailable: {exc}", file=sys.stderr, flush=True)
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn = None
        return None


def record_observations(rows: list[dict]) -> dict:
    """Insert observation rows. Never raises; returns {"inserted": n}."""
    if not rows:
        return {"inserted": 0}
    conn = _connect()
    if conn is None:
        print("[observations] skipped insert: no database connection",
              file=sys.stderr, flush=True)
        return {"inserted": 0, "skipped": True}
    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO estimate_observations
                    (observed_at, zip3, trade, scope, line_description,
                     quantity, unit, unit_price, computed_line_total, source)
                VALUES (now(), %(zip3)s, %(trade)s, %(scope)s,
                        %(line_description)s, %(quantity)s, %(unit)s,
                        %(unit_price)s, %(computed_line_total)s, %(source)s)
                """,
                rows,
            )
            # Maintain the /health counter in the same transaction as the
            # inserts: atomic, so the counter can never drift from the
            # table. The upsert is concurrency-safe (relative increment).
            cur.execute(
                """
                INSERT INTO health_counters (name, n)
                VALUES (%s, %s)
                ON CONFLICT (name)
                DO UPDATE SET n = health_counters.n + EXCLUDED.n
                """,
                (OBSERVATION_COUNTER_NAME, len(rows)),
            )
        conn.commit()
        return {"inserted": len(rows)}
    except Exception as exc:
        print(f"[observations] insert failed: {exc}", file=sys.stderr, flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"inserted": 0, "error": True}


def fetch_production_observations(
    conn,
    *,
    trade: str | None = None,
    scope: str | None = None,
    zip3: str | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Read observations for benchmark aggregation.

    ALWAYS restricted to source='production'. Test and verification rows
    must never feed benchmarks. Any future aggregation over
    estimate_observations must use this helper or replicate its filter.
    Raises on database errors (callers decide how to handle).
    """
    clauses = ["source = 'production'"]
    params: dict = {"limit": limit}
    if trade:
        clauses.append("trade = %(trade)s")
        params["trade"] = trade
    if scope:
        clauses.append("scope = %(scope)s")
        params["scope"] = scope
    if zip3:
        clauses.append("zip3 = %(zip3)s")
        params["zip3"] = zip3
    sql = (
        "SELECT observed_at, zip3, trade, scope, line_description,"
        " quantity, unit, unit_price, computed_line_total"
        " FROM estimate_observations WHERE " + " AND ".join(clauses) +
        " ORDER BY observed_at DESC LIMIT %(limit)s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_undefined_table(exc: Exception) -> bool:
    """True when the failure is just 'table does not exist yet'."""
    if getattr(exc, "sqlstate", None) == "42P01":
        return True
    try:
        from psycopg import errors as pg_errors

        return isinstance(exc, pg_errors.UndefinedTable)
    except Exception:
        return False


def db_status() -> dict:
    """Cheap, non-raising database probe for the /health endpoint.

    Returns counts and a timestamp only -- never observation content.
    Always returns 200-safe data: on any failure the result is
    {"connected": False, "reason": ...} and this function never raises.

    The probe is O(1) in the size of the observation corpus, so health
    checks never get slower as the corpus grows:
    - observation_rows comes from a maintained counter row (single PK
      lookup), incremented atomically alongside every insert;
    - last_write_at is max(observed_at), which rides the
      idx_estimate_observations_observed_at btree (backward index-only
      scan). No count(*) over the table, ever, in the steady state.

    The counter table is created and seeded here on first use (one exact
    count), because this probe opens its own connection and cannot rely
    on the write path having run yet on a fresh deploy.
    """
    if psycopg is None:
        return {"connected": False, "reason": "psycopg not installed"}
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return {"connected": False, "reason": "DATABASE_URL is not set"}
    try:
        conn = psycopg.connect(dsn, connect_timeout=5)
    except Exception as exc:
        return {"connected": False, "reason": type(exc).__name__}
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT n FROM health_counters WHERE name = %s",
                    (OBSERVATION_COUNTER_NAME,),
                )
                row = cur.fetchone()
            except Exception as exc:
                if not _is_undefined_table(exc):
                    raise
                row = None  # counter table does not exist yet
            if row is None:
                # First probe on this deploy (or fresh database): create
                # the counter table and seed it with one exact count.
                # Every later probe is a single PK lookup.
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS health_counters ("
                    "name TEXT PRIMARY KEY, n BIGINT NOT NULL DEFAULT 0)"
                )
                cur.execute(
                    "INSERT INTO health_counters (name, n) "
                    "SELECT %s, count(*) FROM estimate_observations "
                    "ON CONFLICT (name) DO NOTHING",
                    (OBSERVATION_COUNTER_NAME,),
                )
                cur.execute(
                    "SELECT n FROM health_counters WHERE name = %s",
                    (OBSERVATION_COUNTER_NAME,),
                )
                row = cur.fetchone()
                if row is None:
                    # Lost a race with a concurrent seeder; read its value.
                    cur.execute(
                        "SELECT n FROM health_counters WHERE name = %s",
                        (OBSERVATION_COUNTER_NAME,),
                    )
                    row = cur.fetchone()
            (n,) = row
            cur.execute("SELECT max(observed_at) FROM estimate_observations")
            (last_write_at,) = cur.fetchone()
        conn.commit()
    except Exception as exc:
        # Table missing means nothing has been written yet; the database
        # itself is reachable, so report connected with zero rows.
        if _is_undefined_table(exc):
            return {"connected": True, "observation_rows": 0, "last_write_at": None}
        return {"connected": False, "reason": type(exc).__name__}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {
        "connected": True,
        "observation_rows": int(n),
        "last_write_at": (
            last_write_at.isoformat() if last_write_at is not None else None
        ),
    }
