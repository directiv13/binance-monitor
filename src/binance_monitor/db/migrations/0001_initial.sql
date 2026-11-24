CREATE TABLE accounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    api_key_enc    TEXT NOT NULL,
    api_secret_enc TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'connecting',
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    created_by     INTEGER
);

CREATE TABLE subscribers (
    telegram_user_id INTEGER PRIMARY KEY,
    pushover_key     TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE orders (
    account_id    INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    order_id      INTEGER NOT NULL,
    is_algo       INTEGER NOT NULL DEFAULT 0,
    symbol        TEXT NOT NULL,
    side          TEXT NOT NULL,
    position_side TEXT NOT NULL,
    type          TEXT NOT NULL,
    price         TEXT NOT NULL DEFAULT '0',
    stop_price    TEXT NOT NULL DEFAULT '0',
    qty           TEXT NOT NULL DEFAULT '0',
    filled_qty    TEXT NOT NULL DEFAULT '0',
    status        TEXT NOT NULL,
    reduce_only   INTEGER NOT NULL DEFAULT 0,
    direction     TEXT NOT NULL,
    update_time   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, order_id, is_algo)
);

CREATE TABLE positions (
    account_id     INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    symbol         TEXT NOT NULL,
    position_side  TEXT NOT NULL,
    amount         TEXT NOT NULL,
    entry_price    TEXT NOT NULL DEFAULT '0',
    mark_price     TEXT NOT NULL DEFAULT '0',
    unrealized_pnl TEXT NOT NULL DEFAULT '0',
    leverage       INTEGER NOT NULL DEFAULT 0,
    update_time    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, symbol, position_side)
);

CREATE TABLE kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
