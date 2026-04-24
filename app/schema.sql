CREATE TABLE IF NOT EXISTS orders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT    NOT NULL UNIQUE,
    customer_email TEXT,
    amount_total   INTEGER,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
