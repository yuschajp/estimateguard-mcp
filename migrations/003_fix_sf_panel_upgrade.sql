-- 003: repair the mangled San Francisco panel-upgrade benchmark row.
--
-- data/benchmarks_7cities.csv carried an unquoted comma inside the service
-- type ("Panel Upgrade (200 amp, underground service)"). The import shifted
-- every later column, so the live row held:
--   service_type = 'Panel Upgrade (200 amp' (truncated)
--   low_price    = NULL (quarantined unparseable value)
--   avg/high     = 5500 / 12000  (actually the low/avg columns)
--   labor rates  = 18500 / 100   (actually high/labor-low columns)
--   permit       = 200 / 200      (actually the labor-high column)
--   provenance   = the Notes text; the real source (AlphaOmegaElectric) lost.
-- The CSV is fixed in this same change; this migration repairs the live
-- database. Idempotent: safe to run on every benchmark connect.

DELETE FROM benchmark_ranges
WHERE region = 'San Francisco'
  AND trade = 'electrical'
  AND service_type = 'Panel Upgrade (200 amp';

INSERT INTO benchmark_ranges
    (region, zip3, trade, service_type, basis,
     low_price, avg_price, high_price,
     labor_rate_low, labor_rate_high, permit_cost_low, permit_cost_high,
     sample_size, provenance, as_of_date)
VALUES
    ('San Francisco', NULL, 'electrical',
     'Panel Upgrade (200 amp, underground service)', 'flat',
     5500, 12000, 18500,
     100, 200, 500, 1200,
     0,
     'Seed benchmark compiled from published cost guides (Angi, HomeAdvisor, regional contractor sources). Exact collection methodology not documented in the source repository. Original 7-city data 2026-08-03; expanded to 12 cities 2026-08-09. Row source: AlphaOmegaElectric. Row repaired 2026-09-30: original CSV row had an unquoted comma in the service type that shifted all columns on import.',
     DATE '2026-08-09')
ON CONFLICT (region, trade, service_type) DO UPDATE SET
    low_price = EXCLUDED.low_price,
    avg_price = EXCLUDED.avg_price,
    high_price = EXCLUDED.high_price,
    labor_rate_low = EXCLUDED.labor_rate_low,
    labor_rate_high = EXCLUDED.labor_rate_high,
    permit_cost_low = EXCLUDED.permit_cost_low,
    permit_cost_high = EXCLUDED.permit_cost_high,
    provenance = EXCLUDED.provenance;
