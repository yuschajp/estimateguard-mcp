-- 002: provenance tracking for estimate_observations.
--
-- Adds a `source` column: 'production' | 'test' | 'verification'.
-- Every row predating this migration comes from development / acceptance
-- testing (the service had no production traffic before this deploy), so
-- the backfill marks all pre-existing rows 'test' rather than guessing
-- 'production'. Rows with source = 'test' / 'verification' must never feed
-- benchmark aggregation; aggregation queries must filter source='production'.
--
-- Idempotent: safe to run on every write-path connect.

ALTER TABLE estimate_observations ADD COLUMN IF NOT EXISTS source TEXT;

-- Backfill: any row that existed before this column did is dev/test data.
UPDATE estimate_observations SET source = 'test' WHERE source IS NULL;

ALTER TABLE estimate_observations ALTER COLUMN source SET NOT NULL;
ALTER TABLE estimate_observations ALTER COLUMN source SET DEFAULT 'production';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'estimate_observations_source_check'
  ) THEN
    ALTER TABLE estimate_observations
      ADD CONSTRAINT estimate_observations_source_check
      CHECK (source IN ('production', 'test', 'verification'));
  END IF;
END
$$;
