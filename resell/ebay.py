"""Phase 4b: weekly EU market snapshot via the official eBay Browse API (no scraping).

Per candidate and run: active listing count, price p25/median/p75, auction share, and the item IDs.
Comparing item IDs week over week gives "vanished before their end date" = PROBABLE sales - an estimate,
not sold data (items also vanish when sellers end or relist them).

Policy: eBay's API licence (June 2025) restricts feeding eBay data to generative AI tools. Nothing in this
module, or anything reading these tables, sends eBay data to Claude.

Credentials: EBAY_CLIENT_ID / EBAY_CLIENT_SECRET from the environment or a .env file (gitignored).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from . import db, research
from .config import Config, load_env

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ebay_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate     TEXT NOT NULL,
    run_date      TEXT NOT NULL,
    scope         TEXT NOT NULL,          -- de | eu
    query         TEXT,
    total         INTEGER,                -- eBay's total match count
    fetched       INTEGER,                -- items we fetched (capped)
    kept          INTEGER,                -- after exclusion terms
    p25           REAL, median REAL, p75 REAL,
    auction_share REAL,
    vanished      INTEGER,                -- in last run, gone now, not ended naturally
    probable_sales INTEGER,
    vanished_median REAL,
    complete      INTEGER,                -- 1 = we saw every match (needed for disappearance tracking)
    UNIQUE (candidate, run_date, scope)
);
CREATE TABLE IF NOT EXISTS ebay_items (
    snapshot_id INTEGER NOT NULL,
    item_id     TEXT NOT NULL,
    title       TEXT,
    price       REAL,
    currency    TEXT,
    buying      TEXT,                     -- FIXED_PRICE / AUCTION / BEST_OFFER (comma list)
    end_date    TEXT,
    country     TEXT,
    url         TEXT
);
"""


def _con(cfg: Config):
    con = db.connect(cfg.path(cfg.paths.db))
    con.executescript(SCHEMA)
    return con


# ----------------------------------------------------------------------------- credentials & token

def credentials(cfg: Config) -> tuple[str, str]:
    load_env(cfg.root / ".env")
    cid, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET missing - put them in .env (see README)")
    return cid, secret


def get_token(client: httpx.Client, cid: str, secret: str) -> str:
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = client.post(TOKEN_URL, headers={"Authorization": f"Basic {auth}",
                                        "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "client_credentials", "scope": SCOPE})
    if r.status_code != 200:
        raise RuntimeError(f"eBay token request failed ({r.status_code}): {r.text[:300]}")
    return r.json()["access_token"]


# ----------------------------------------------------------------------------- search

DEFAULT_CONDITION_IDS = (2000, 2010, 2020, 2030, 2500, 3000, 4000, 5000, 6000)


def _exclusions(text: str | None) -> list[str]:
    return [t[1:].lower() for t in (text or "").split() if t.startswith("-") and len(t) > 1]


def search_all(cfg: Config, client: httpx.Client, token: str, query: str, scope: str, cache_dir: Path,
               category_ids: str | None = None) -> dict:
    """All pages up to the cap. Returns {"total": int, "items": [...]}. Responses are cached per day."""
    e = (cfg.research or {}).get("ebay", {})
    limit, cap = 200, int(e.get("max_items", 600))
    # eBay returns only fixed-price items unless buyingOptions is given. Condition IDs: used grades plus
    # refurbished (2000-2500), without new (1000/1500) and for-parts (7000).
    cond = "|".join(str(c) for c in e.get("condition_ids", DEFAULT_CONDITION_IDS))
    filters = ["buyingOptions:{FIXED_PRICE|AUCTION}", f"conditionIds:{{{cond}}}"]
    if scope == "de":
        filters.append(f"itemLocationCountry:{e.get('country', 'DE')}")
    items, total, offset = [], 0, 0
    while offset < cap:
        key = re.sub(r"[^a-z0-9]+", "_", f"{query}_{scope}_{offset}".lower()) + "_" + hashlib.sha1(
            ",".join(filters + [category_ids or ""]).encode()).hexdigest()[:8]   # a filter change in config.yaml must not hit old pages
        cached = cache_dir / f"{key}.json"
        if cached.exists():
            data = json.loads(cached.read_text(encoding="utf-8"))
        else:
            r = client.get(SEARCH_URL, params={"q": query, "filter": ",".join(filters), "limit": limit, "offset": offset,
                                                    **({"category_ids": category_ids} if category_ids else {})},
                           headers={"Authorization": f"Bearer {token}",
                                    "X-EBAY-C-MARKETPLACE-ID": e.get("marketplace", "EBAY_DE"),
                                    "Accept-Language": "de-DE"})
            if r.status_code == 429:
                raise RuntimeError("eBay rate limit reached - try again tomorrow (one run per week is plenty)")
            if r.status_code != 200:
                raise RuntimeError(f"eBay search failed ({r.status_code}) for '{query}': {r.text[:300]}")
            data = r.json()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(float(e.get("pause_seconds", 0.5)))
        total = int(data.get("total", 0))
        page = data.get("itemSummaries", []) or []
        items += page
        offset += limit
        if offset >= total or not page:
            break
    return {"total": total, "items": items}


def _price(it: dict) -> tuple[float | None, str | None]:
    p = it.get("price") or it.get("currentBidPrice") or {}
    try:
        return float(p.get("value")), p.get("currency")
    except (TypeError, ValueError):
        return None, None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return float(statistics.quantiles(values, n=100, method="inclusive")[int(q * 100) - 1])


@dataclass
class Snapshot:
    candidate: str
    scope: str
    query: str
    total: int
    fetched: int
    kept: int
    p25: float | None
    median: float | None
    p75: float | None
    auction_share: float | None
    complete: bool
    vanished: int | None = None
    probable_sales: int | None = None
    vanished_median: float | None = None
    items: list[dict] = field(default_factory=list)


def summarise(cand: dict, scope: str, query: str, raw: dict) -> Snapshot:
    excl = _exclusions(cand.get("exclude_terms"))
    # The same regex that matches Xianyu titles keeps games, chargers and other models out of the stats.
    pat = cand.get("match_terms")
    rx = re.compile(pat, re.I) if isinstance(pat, str) and pat.strip() else None
    kept = []
    for it in raw["items"]:
        title = (it.get("title") or "").lower()
        if any(x in title for x in excl) or (rx and not rx.search(title)):
            continue
        price, cur = _price(it)
        if price is None or (cur and cur != "EUR"):
            continue
        kept.append({"item_id": it.get("itemId"), "title": it.get("title"), "price": price, "currency": cur or "EUR",
                     "buying": ",".join(it.get("buyingOptions") or []), "end_date": it.get("itemEndDate"),
                     "country": (it.get("itemLocation") or {}).get("country"), "url": it.get("itemWebUrl")})
    # Price stats from fixed-price listings only: a running auction's current bid isn't a price.
    auctions = sum("AUCTION" in k["buying"] for k in kept)
    prices = sorted(k["price"] for k in kept if "FIXED_PRICE" in k["buying"])
    return Snapshot(cand["id"], scope, query, raw["total"], len(raw["items"]), len(kept), _pct(prices, 0.25),
                    statistics.median(prices) if prices else None, _pct(prices, 0.75),
                    round(auctions / len(kept), 3) if kept else None,
                    complete=len(raw["items"]) >= raw["total"], items=kept)


def disappearance(prev_items: list[dict], snap: Snapshot, run_day: date, prev_complete: bool) -> None:
    """Items present last run, gone now and whose scheduled end hadn't passed = probable sales.
    Only computed when both runs saw every match (otherwise 'gone' may just mean 'not fetched')."""
    if not prev_items or not (prev_complete and snap.complete):
        return
    now_ids = {i["item_id"] for i in snap.items}
    gone = [p for p in prev_items if p["item_id"] not in now_ids]
    def ended_naturally(p):
        if not p.get("end_date"):
            return False
        try:
            return datetime.fromisoformat(p["end_date"].replace("Z", "+00:00")).date() <= run_day
        except ValueError:
            return False
    probable = [p for p in gone if not ended_naturally(p)]
    snap.vanished = len(gone)
    snap.probable_sales = len(probable)
    snap.vanished_median = statistics.median([p["price"] for p in probable]) if probable else None


# ----------------------------------------------------------------------------- run

def scan(cfg: Config, candidate_ids: list[str] | None = None, scopes=("de", "eu"), client: httpx.Client | None = None,
         run_day: date | None = None) -> list[Snapshot]:
    run_day = run_day or date.today()
    cands, _, _ = research.load(cfg)
    rows = [c for c in cands.to_dict("records")
            if c.get("status") != "dropped" and (c.get("query_eu") or c.get("keywords_eu"))
            and (not candidate_ids or c["id"] in candidate_ids)]
    own_client = client is None
    client = client or httpx.Client(timeout=30)
    cid, secret = credentials(cfg) if own_client else ("test", "test")
    token = get_token(client, cid, secret)
    cache_dir = cfg.path(Path((cfg.research or {}).get("ebay", {}).get("cache_dir", "data/ebay_cache"))) / run_day.isoformat()
    out = []
    con = _con(cfg)
    try:
        for c in rows:
            query = (c.get("query_eu") or c.get("keywords_eu").split(",")[0]).strip()
            for scope in scopes:
                cats = re.sub(r"\s+", "", c.get("ebay_category_ids") or "") or None   # devices only: no parts, manuals, cases
                raw = search_all(cfg, client, token, query, scope, cache_dir, cats)
                snap = summarise(c, scope, query, raw)
                prev = con.execute("SELECT id, complete FROM ebay_snapshots WHERE candidate = ? AND scope = ? "
                                   "AND run_date < ? ORDER BY run_date DESC LIMIT 1",
                                   (c["id"], scope, run_day.isoformat())).fetchone()
                if prev:
                    prev_items = [dict(zip(("item_id", "price", "end_date"), r)) for r in con.execute(
                        "SELECT item_id, price, end_date FROM ebay_items WHERE snapshot_id = ?", (prev[0],))]
                    disappearance(prev_items, snap, run_day, bool(prev[1]))
                with con:
                    con.execute("DELETE FROM ebay_items WHERE snapshot_id IN (SELECT id FROM ebay_snapshots "
                                "WHERE candidate = ? AND scope = ? AND run_date = ?)", (c["id"], scope, run_day.isoformat()))
                    con.execute("DELETE FROM ebay_snapshots WHERE candidate = ? AND scope = ? AND run_date = ?",
                                (c["id"], scope, run_day.isoformat()))
                    cur = con.execute(
                        "INSERT INTO ebay_snapshots (candidate, run_date, scope, query, total, fetched, kept, p25, median, p75, "
                        "auction_share, vanished, probable_sales, vanished_median, complete) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (c["id"], run_day.isoformat(), scope, query, snap.total, snap.fetched, snap.kept, snap.p25,
                         snap.median, snap.p75, snap.auction_share, snap.vanished, snap.probable_sales,
                         snap.vanished_median, int(snap.complete)))
                    con.executemany("INSERT INTO ebay_items (snapshot_id, item_id, title, price, currency, buying, end_date, "
                                    "country, url) VALUES (?,?,?,?,?,?,?,?,?)",
                                    [(cur.lastrowid, i["item_id"], i["title"], i["price"], i["currency"], i["buying"],
                                      i["end_date"], i["country"], i["url"]) for i in snap.items])
                out.append(snap)
    finally:
        con.close()
        if own_client:
            client.close()
    return out


def latest_medians(cfg: Config, scope: str = "de") -> dict[str, float]:
    con = _con(cfg)
    rows = con.execute("SELECT candidate, median FROM ebay_snapshots s WHERE scope = ? AND median IS NOT NULL AND "
                       "run_date = (SELECT MAX(run_date) FROM ebay_snapshots t WHERE t.candidate = s.candidate AND t.scope = s.scope)",
                       (scope,)).fetchall()
    con.close()
    return {c: m for c, m in rows}


def demand(cfg: Config, candidate: str, days: int = 90, scope: str = "de") -> dict:
    """Probable sales in the last `days` (sum of weekly estimates), latest active count, competition ratio."""
    con = _con(cfg)
    rows = con.execute("SELECT run_date, total, kept, median, probable_sales, vanished_median FROM ebay_snapshots "
                       "WHERE candidate = ? AND scope = ? AND run_date >= date('now', ?) ORDER BY run_date",
                       (candidate, scope, f"-{days} days")).fetchall()
    con.close()
    if not rows:
        return {}
    sales = [r[4] for r in rows if r[4] is not None]
    latest = rows[-1]
    weeks = len(sales)
    est = sum(sales) * (days / 7 / weeks) if weeks else None   # scale up when we have fewer weeks than the window
    return {"runs": len(rows), "weeks_with_estimate": weeks, "active": latest[2], "asking_median": latest[3],
            "probable_sales_90d": round(est) if est is not None else None,
            "competition": round(latest[2] / est, 2) if est else None,
            "median_trend": [(r[0], r[3]) for r in rows]}
