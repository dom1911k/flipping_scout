-- Rebuilt from the workbook on every `resell import`; the workbook stays the source of truth.

CREATE TABLE IF NOT EXISTS sales (
    sheet_row        INTEGER PRIMARY KEY,   -- row number in "Sales Sheet"
    item             TEXT NOT NULL,
    source           TEXT,
    buy_date         TEXT,
    sell_date        TEXT,
    selling_place    TEXT,
    platform         TEXT,
    account          TEXT,
    price_paid       REAL,                  -- landed cost incl. all fees (as recorded)
    price_sold       REAL,
    profit           REAL,                  -- as recorded
    profit_recalc    REAL,                  -- price_sold - price_paid
    serial           TEXT,
    tracking         TEXT,
    comment          TEXT,
    profitability    REAL,                  -- as recorded (profit / price paid)
    -- derived
    category         TEXT,
    grade            INTEGER,
    flawed           INTEGER,               -- 0/1
    flaw_minor       INTEGER,               -- 0/1: every matched flaw was qualified as minor
    flaw_tags        TEXT,                  -- comma-separated groups
    flaw_matches     TEXT,                  -- the words that matched, for review
    dif_seller       INTEGER,
    seller           TEXT,
    seller_confidence TEXT,                 -- explicit | link | fingerprint | inferred | unknown
    listing_id       TEXT,                  -- Goofish/Taobao item id if the source is a link
    days_to_sell     INTEGER,
    is_stock         INTEGER,
    lost             INTEGER                -- lost in transit: a loss, but not a sale
);

CREATE TABLE IF NOT EXISTS purchases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet            TEXT NOT NULL,
    sheet_row        INTEGER NOT NULL,
    block            INTEGER,               -- header block within the tab
    item             TEXT NOT NULL,
    buy_date         TEXT,
    qty              REAL,
    currency         TEXT,                  -- CNY | JPY | USD | EUR
    unit_price_local REAL,
    eur_goods        REAL,                  -- goods value in EUR for the whole line
    fee_eur          REAL,
    shipping_eur     REAL,
    vat_incl_eur     REAL,                  -- Yen-era sheets: price incl. 19% VAT
    total_eur        REAL,                  -- "Overall" / "Final price"
    eff_rate         REAL,                  -- local currency per EUR (qty*unit / eur_goods)
    link             TEXT,
    note             TEXT,
    category         TEXT,
    kind             TEXT,                  -- unit | part
    excluded         TEXT,                  -- reason a line is left out of stats
    storefront       TEXT,                  -- Goofish storefront key from the link, if any
    sales_row        INTEGER                -- matched sales.sheet_row, if any
);

CREATE TABLE IF NOT EXISTS parse_issues (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet   TEXT,
    cells   TEXT,
    raw     TEXT,
    reason  TEXT
);
