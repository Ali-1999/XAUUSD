CREATE TABLE IF NOT EXISTS msnr_runtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL,
    next_at INTEGER NOT NULL,
    lease_until INTEGER NOT NULL,
    payload TEXT
);
