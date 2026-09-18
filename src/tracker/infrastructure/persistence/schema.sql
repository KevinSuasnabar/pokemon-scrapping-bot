PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS store (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,                        -- ISO-8601 UTC
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','completed','failed'))
);

CREATE TABLE IF NOT EXISTS store_run (
  run_id INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  store_slug TEXT NOT NULL REFERENCES store(slug),
  status TEXT NOT NULL CHECK (status IN ('ok','failed')),
  offer_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  PRIMARY KEY (run_id, store_slug)
);

CREATE TABLE IF NOT EXISTS product (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_slug TEXT NOT NULL REFERENCES store(slug),
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  language TEXT NOT NULL,
  product_type TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  UNIQUE (store_slug, external_id)
);

CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  run_id     INTEGER NOT NULL REFERENCES run(id) ON DELETE CASCADE,
  price_cents INTEGER,                              -- NULL = price not published
  currency TEXT NOT NULL DEFAULT 'PEN',
  availability TEXT NOT NULL
    CHECK (availability IN ('in_stock','out_of_stock','unknown')),
  observed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_product_time
  ON observation (product_id, observed_at DESC, id DESC);   -- serves "last observation per product"
CREATE INDEX IF NOT EXISTS idx_obs_run     ON observation (run_id);
CREATE INDEX IF NOT EXISTS idx_product_store ON product (store_slug);
