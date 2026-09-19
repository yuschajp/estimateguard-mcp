"""Tests for observations.db_status() used by the /health endpoint.

Rules under test:
- counts + timestamp only; never observation content
- never raises; DB trouble -> {"connected": False, "reason": ...}
- missing table (nothing written yet) -> connected with zero rows
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import observations


class _FakeCursor:
    def __init__(self, results=None, exc=None):
        self._results = list(results or [])
        self._exc = exc
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.statements.append(sql)
        assert "line_description" not in sql, "health must not read row content"
        assert "unit_price" not in sql, "health must not read row content"
        if self._exc is not None:
            raise self._exc

    def fetchone(self):
        return self._results.pop(0) if self._results else None


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def close(self):
        pass


class _FakePsycopg:
    def __init__(self, cursor=None, connect_exc=None):
        self._cursor = cursor
        self._connect_exc = connect_exc

    def connect(self, dsn, connect_timeout=None):
        assert dsn, "must pass the DSN through"
        if self._connect_exc is not None:
            raise self._connect_exc
        return _FakeConn(self._cursor)


class _OpErr(Exception):
    pass


class _UndefinedTable(Exception):
    sqlstate = "42P01"


def _swap_psycopg(fake):
    real = observations.psycopg
    observations.psycopg = fake
    return real


def _with_env(dsn_value):
    """Set or clear DATABASE_URL, returning a restore function."""
    sentinel = object()
    old = os.environ.get("DATABASE_URL", sentinel)
    if dsn_value is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = dsn_value

    def restore():
        if old is sentinel:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old

    return restore


def test_db_status_reports_counts_and_timestamp():
    ts = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
    cursor = _FakeCursor(results=[(7,), (ts,)])
    fake = _FakePsycopg(cursor=cursor)
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st == {
        "connected": True,
        "observation_rows": 7,
        "last_write_at": "2026-09-19T12:00:00+00:00",
    }, st
    assert set(st) == {"connected", "observation_rows", "last_write_at"}


def test_db_status_never_table_scans_in_steady_state():
    # The steady-state probe must not run count(*) over the observations
    # table: health checks must not get slower as the corpus grows.
    ts = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
    cursor = _FakeCursor(results=[(1234567,), (ts,)])
    fake = _FakePsycopg(cursor=cursor)
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st["observation_rows"] == 1234567
    scans = [s for s in cursor.statements if "count(*)" in s.lower()]
    assert not scans, f"steady-state health ran a table scan: {scans}"


def test_db_status_seeds_counter_when_missing():
    # First health check before any write on this deploy: the counter row is
    # absent, so the probe seeds it with one exact count, then uses it.
    ts = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
    cursor = _FakeCursor(results=[None, (5,), (ts,)])
    fake = _FakePsycopg(cursor=cursor)
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st["connected"] is True
    assert st["observation_rows"] == 5, st
    assert st["last_write_at"] == "2026-09-19T12:00:00+00:00"
    seeds = [s for s in cursor.statements if "count(*)" in s.lower()]
    assert len(seeds) == 1, f"expected exactly one seeding count, got: {seeds}"


def test_db_status_null_timestamp_when_empty():
    cursor = _FakeCursor(results=[(0,), (None,)])
    fake = _FakePsycopg(cursor=cursor)
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st["connected"] is True
    assert st["observation_rows"] == 0
    assert st["last_write_at"] is None


def test_db_status_no_dsn():
    real, restore = _swap_psycopg(_FakePsycopg()), _with_env(None)
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st["connected"] is False
    assert st["reason"], st


def test_db_status_connect_failure_never_raises():
    fake = _FakePsycopg(connect_exc=_OpErr("connection refused"))
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()  # must not raise
    finally:
        observations.psycopg = real
        restore()
    assert st == {"connected": False, "reason": "_OpErr"}, st


def test_db_status_missing_table_means_zero_rows():
    fake = _FakePsycopg(cursor=_FakeCursor(exc=_UndefinedTable("no such table")))
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()
    finally:
        observations.psycopg = real
        restore()
    assert st == {
        "connected": True,
        "observation_rows": 0,
        "last_write_at": None,
    }, st


def test_db_status_query_failure_stays_200_safe():
    fake = _FakePsycopg(cursor=_FakeCursor(exc=RuntimeError("weird")))
    real, restore = _swap_psycopg(fake), _with_env("postgresql://fake/db")
    try:
        st = observations.db_status()  # must not raise
    finally:
        observations.psycopg = real
        restore()
    assert st == {"connected": False, "reason": "RuntimeError"}, st


def test_db_status_against_real_postgres():
    """End-to-end: evaluate writes rows, db_status sees them. Skips cleanly
    when no embedded Postgres is available."""
    try:
        import pgserver
    except Exception:
        print("SKIP test_db_status_against_real_postgres (no pgserver)")
        return
    from estimate_eval import evaluate

    try:
        srv = pgserver.get_server("/tmp/pgtest_health")
    except Exception as exc:
        print(f"SKIP test_db_status_against_real_postgres ({exc})")
        return
    restore = _with_env(srv.get_uri("postgres"))
    # make sure the lazy global connection from other tests is not reused
    observations._conn = None
    try:
        res = evaluate(
            "1. Tear off old shingles 20 squares @ $350.00 = $7,000.00\n"
            "2. Install architectural shingles 20 squares @ $425.00 = $8,500.00\n",
            "10001",
        )
        assert "error" not in res, res
        st = observations.db_status()
        assert st["connected"] is True, st
        assert st["observation_rows"] == 2, st
        assert set(st) == {"connected", "observation_rows", "last_write_at"}
        # last_write_at is ISO8601 and within the last minute
        ts = datetime.fromisoformat(st["last_write_at"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        assert 0 <= age < 60, f"stale last_write_at: {st['last_write_at']}"
    finally:
        restore()
        observations._conn = None
        try:
            srv.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
