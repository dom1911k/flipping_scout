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

-- Phase 4: category research (yours; never dropped)
CREATE TABLE IF NOT EXISTS candidates (
    id            TEXT PRIMARY KEY,   -- slug, e.g. "vita-oled"
    name          TEXT NOT NULL,
    calc_category TEXT,               -- cost profile to use (e.g. N3DS, iPod); null = new category defaults
    own_sales_match TEXT,             -- regex on your Sales Sheet item names to include your own sales as evidence
    match_terms   TEXT,               -- regex matching listing titles (Chinese and EU) to this candidate
    query_eu      TEXT,               -- eBay search query (German/English)
    exclude_terms TEXT,               -- eBay exclusions, e.g. "-defekt -ersatzteil -bastler -hülle -case"
    variant_terms TEXT,               -- regex the title must contain to be the variant you price (e.g. "ips")
    variant_question_zh TEXT,         -- what to ask the seller when the title doesn't say
    status        TEXT,               -- new | watching | shortlist | active | dropped
    discontinued  TEXT,               -- e.g. "2013 (OLED model); line ended 2019"; empty = unknown
    china_edge    TEXT,               -- modded | refurbished | arbitrage | none
    testability   TEXT,               -- easy | medium | hard
    hype          INTEGER DEFAULT 0,  -- 1 = you consider it hype-driven
    new_devices   INTEGER DEFAULT 0,  -- 1 = you'd import NEW electrical devices (ElektroG)
    weight_kg     REAL,
    keywords_zh   TEXT,               -- Xianyu search terms
    keywords_eu   TEXT,               -- eBay/Terapeak search terms
    notes         TEXT,
    updated       TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate  TEXT NOT NULL,
    criterion  INTEGER NOT NULL,     -- 1..5 (your five criteria), 0 = general
    text       TEXT NOT NULL,
    source     TEXT,                 -- URL or "own experience"
    added      TEXT
);

CREATE TABLE IF NOT EXISTS evidence (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate  TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- eu_sold | eu_asking | cn_asking | cn_sold
    source     TEXT,                 -- terapeak | ebay_sold | pricecharting | kleinanzeigen | xianyu | manual
    date       TEXT,                 -- sale/observation date
    price      REAL NOT NULL,        -- in `currency`
    currency   TEXT NOT NULL,        -- EUR | USD | GBP | CNY
    title      TEXT,
    condition  TEXT,
    url        TEXT,
    added      TEXT
);

-- Phase 4d: screen recordings and the listings read from them (yours; never dropped)
CREATE TABLE IF NOT EXISTS recordings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file        TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,     -- the same video is never processed (or paid for) twice
    kind        TEXT,                     -- xianyu_grid | ebay_sold | kleinanzeigen
    label       TEXT,                     -- e.g. seller name or search term
    processed   TEXT,
    frames      INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd    REAL
);

CREATE TABLE IF NOT EXISTS xianyu_listings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id  INTEGER NOT NULL,
    seller        TEXT,
    title         TEXT,
    price_rmb     REAL,
    price_text    TEXT,
    wants         INTEGER,
    badges        TEXT,                   -- JSON list
    original_price_rmb REAL,
    product_type  TEXT,
    product_class TEXT,
    likely_discontinued INTEGER,
    flags         TEXT,                   -- comma list: multi_sku, wanted_ad, service, accessory, no_title
    candidate     TEXT,
    landed_eur    REAL,
    max_rmb       REAL,
    sell_eur      REAL,
    sell_basis    TEXT,
    verdict       TEXT,
    follow_up_zh  TEXT,
    frames        TEXT,
    seen          TEXT
);
