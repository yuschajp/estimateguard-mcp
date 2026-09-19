"""Observation store for EstimateGuard.

Writes one row per evaluated line item to Render Postgres. Only the
whitelisted, PII-free fields are ever persisted:

    observed_at, zip3, trade, scope, line_description,
    quantity, unit, unit_price, computed_line_total

PII is stripped from the estimate text during parsing, *before* any
observation row is constructed. Strip counts are logged per category for
auditing. The insert path never raises: if the database is unavailable the
tool result is unaffected and the failure is logged server-side.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

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
    computed_line_total NUMERIC(12, 2) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_estimate_observations_trade_scope_zip3
    ON estimate_observations (trade, scope, zip3);
CREATE INDEX IF NOT EXISTS idx_estimate_observations_observed_at
    ON estimate_observations (observed_at DESC);
"""

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


def strip_pii(text: str) -> tuple[str, dict[str, int]]:
    """Remove PII from estimate text.

    Email/phone/license/address patterns are unambiguous and apply to the
    whole text. Person names and contractor business names are stripped from
    the header block only, so priced line items are never altered.

    Returns (cleaned_text, counts) where counts maps each category to the
    number of substitutions made. Categories: email, phone, license,
    address, contractor, name.
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
    return "\n".join(header_lines + lines[body_at:]), counts


def log_strip_counts(counts: dict[str, int]) -> None:
    parts = " ".join(f"{cat}={counts.get(cat, 0)}" for cat in
                     ("name", "address", "phone", "email", "contractor", "license"))
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
) -> list[dict]:
    """Build whitelisted observation rows from evaluated line internals.

    Each line dict carries: description, quantity (str), unit (str),
    unit_price (str), computed_line_total (str), scope (str).
    """
    zip3 = zip3_of(zip_code)
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
                     quantity, unit, unit_price, computed_line_total)
                VALUES (now(), %(zip3)s, %(trade)s, %(scope)s,
                        %(line_description)s, %(quantity)s, %(unit)s,
                        %(unit_price)s, %(computed_line_total)s)
                """,
                rows,
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

    The query is a single aggregate: count(*) is exact (needed so callers
    can detect that new writes landed) and max(observed_at) rides the
    observed_at index. No row content is read.
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
            cur.execute(
                "SELECT count(*) AS n, max(observed_at) AS last_write_at "
                "FROM estimate_observations"
            )
            n, last_write_at = cur.fetchone()
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
