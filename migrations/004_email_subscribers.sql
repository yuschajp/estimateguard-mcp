-- 004: email capture for the web estimate-checker form.
--
-- Stores one row per subscriber: the address, when they subscribed, and
-- which surface they came from. No other personal data. Unsubscribes are
-- soft (unsubscribed_at set) so a re-subscribe is an update, not a dup.
--
-- Idempotent: safe to run on every connect.

CREATE TABLE IF NOT EXISTS email_subscribers (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source TEXT NOT NULL DEFAULT 'web_form',
    unsubscribed_at TIMESTAMPTZ
);

-- One active subscription per address (case-insensitive). Soft-deleted rows
-- don't block a fresh subscribe.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'email_subscribers_email_active'
  ) THEN
    CREATE UNIQUE INDEX email_subscribers_email_active
      ON email_subscribers (lower(email))
      WHERE unsubscribed_at IS NULL;
  END IF;
END
$$;
