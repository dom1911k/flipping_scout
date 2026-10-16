-- Tables YOU fill in (inspections, seller notes). `resell import` never drops these.

CREATE TABLE IF NOT EXISTS inspections (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created      TEXT NOT NULL,
    category     TEXT NOT NULL,
    item         TEXT,
    seller       TEXT,
    listing      TEXT,            -- Goofish/Taobao link or listing title
    storefront   TEXT,            -- storefront code parsed from the link
    buy_date     TEXT,
    price_paid   REAL,
    serial       TEXT,
    precheck     TEXT,            -- answered | partial | ignored | not_sent
    results      TEXT NOT NULL,   -- JSON {check_key: ok | minor | flaw | na}
    check_notes  TEXT,            -- JSON {check_key: note}
    comment      TEXT,
    flawed       INTEGER,
    flaw_minor   INTEGER,
    flaw_tags    TEXT
);

CREATE TABLE IF NOT EXISTS seller_notes (
    seller   TEXT PRIMARY KEY,
    status   TEXT,                -- preferred | ok | watch | avoid
    notes    TEXT,
    updated  TEXT
);
