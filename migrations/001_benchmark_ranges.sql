-- 001_benchmark_ranges.sql
--
-- Seed benchmark table for EstimateGuard. This table is SEPARATE from
-- estimate_observations on purpose and must never be aggregated with it:
--   benchmark_ranges  -> "what do published cost guides say?" (seed data,
--                        sample_size = 0, provenance on every row)
--   estimate_observations -> "what did EstimateGuard actually see?"
--
-- Provenance (established read-only from the legacy estimate-reviewer repo):
--   2026-08-04  "Fairness scoring live: 7-city benchmarks" (151 rows:
--               Chicago, Denver, Los Angeles, Miami, NYC, Phoenix, Seattle)
--   2026-08-09  "feat: expand benchmark coverage to 12 cities"
--               (+120 rows: Atlanta, Boston, Dallas-Fort Worth,
--               San Francisco, Washington DC; commit message: "researched
--               from Angi/HomeAdvisor/regional cost guides, 2026 pricing")
-- No notebook, scraper, or detailed methodology was found in the source
-- repo, so the exact collection method is not documented. Figures are
-- presented as published-guide data, not independently verified
-- measurements.
--
-- Money is NUMERIC (Decimal in Python). Rounding to cents happens at output.
-- Idempotent: the service upserts on (region, trade, service_type).

CREATE TABLE IF NOT EXISTS benchmark_ranges (
    id BIGSERIAL PRIMARY KEY,
    region TEXT NOT NULL,
    zip3 CHAR(3),
    trade TEXT NOT NULL,
    service_type TEXT NOT NULL,
    basis TEXT NOT NULL,
    low_price NUMERIC(12, 2),
    avg_price NUMERIC(12, 2) NOT NULL,
    high_price NUMERIC(12, 2),
    labor_rate_low NUMERIC(12, 2),
    labor_rate_high NUMERIC(12, 2),
    permit_cost_low NUMERIC(12, 2),
    permit_cost_high NUMERIC(12, 2),
    sample_size INTEGER NOT NULL DEFAULT 0,
    provenance TEXT NOT NULL,
    as_of_date DATE NOT NULL,
    UNIQUE (region, trade, service_type)
);
CREATE INDEX IF NOT EXISTS idx_benchmark_ranges_region_trade_basis
    ON benchmark_ranges (region, trade, basis);
