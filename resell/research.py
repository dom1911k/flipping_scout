"""Phase 4 (research first): a workspace to evaluate new product categories on your five criteria.

Each candidate collects findings (text + source per criterion) and price evidence (EU sold prices
pasted from Terapeak / eBay sold searches, China asking prices from Xianyu). From that it computes the
12-month EU price trend (hype check), the profit at median prices via the Phase 1 calculator, a
PASS / BORDERLINE / FAIL per criterion, and the next step that would fill the biggest gap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import resources

import numpy as np
import pandas as pd
import yaml

from . import calc, db
from .config import Config

STATUSES = ("new", "watching", "shortlist", "active", "dropped")
OLD_STATUS = {"idea": "new", "researching": "watching", "test_buy": "shortlist"}
EDGES = ("modded", "refurbished", "arbitrage", "none")
TESTABILITY = ("easy", "medium", "hard")
SOURCES = ("terapeak", "ebay_sold", "pricecharting", "kleinanzeigen", "xianyu", "manual")
CRITERIA = {1: "No longer manufactured", 2: "Not hype-driven", 3: "China version is better",
            4: "Easy to verify and test", 5: "Profit target", 0: "General"}

MONTHS = {"jan": 1, "januar": 1, "feb": 2, "februar": 2, "mär": 3, "märz": 3, "mar": 3, "march": 3, "apr": 4,
          "april": 4, "mai": 5, "may": 5, "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7,
          "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "okt": 10, "oct": 10, "oktober": 10,
          "october": 10, "nov": 11, "november": 11, "dez": 12, "dec": 12, "dezember": 12, "december": 12}


_NO_SALES = pd.DataFrame(columns=["category", "is_stock", "lost", "item", "sell_date", "platform", "price_sold"])


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _con(cfg: Config):
    return db.connect(cfg.path(cfg.paths.db))


# ----------------------------------------------------------------------------- candidates & findings

def migrate(cfg: Config) -> None:
    """Old status names -> new ones; fill match/query fields from the seed for seeded candidates."""
    data = yaml.safe_load(resources.files("resell").joinpath("content", "candidates_seed.yaml").read_text(encoding="utf-8"))
    con = _con(cfg)
    with con:
        for old, new in OLD_STATUS.items():
            con.execute("UPDATE candidates SET status = ? WHERE status = ?", (new, old))
        for c in data["candidates"]:
            for col in ("match_terms", "query_eu", "exclude_terms", "variant_terms", "variant_question_zh",
                        "ebay_category_ids"):
                if c.get(col):
                    con.execute(f"UPDATE candidates SET {col} = ? WHERE id = ? AND ({col} IS NULL OR {col} = '')",
                                (c[col], c["id"]))
    con.close()


def seed(cfg: Config) -> int:
    """Insert the starting candidates + findings once (existing ids are left alone)."""
    migrate(cfg)
    data = yaml.safe_load(resources.files("resell").joinpath("content", "candidates_seed.yaml").read_text(encoding="utf-8"))
    con, n = _con(cfg), 0
    with con:
        for c in data["candidates"]:
            if con.execute("SELECT 1 FROM candidates WHERE id = ?", (c["id"],)).fetchone():
                continue
            con.execute(
                "INSERT INTO candidates (id, name, calc_category, own_sales_match, match_terms, query_eu, exclude_terms, "
                "status, discontinued, china_edge, testability, hype, new_devices, weight_kg, keywords_zh, keywords_eu, "
                "notes, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (c["id"], c["name"], c.get("calc_category"), c.get("own_sales_match"), c.get("match_terms"),
                 c.get("query_eu"), c.get("exclude_terms"), OLD_STATUS.get(c.get("status"), c.get("status", "new")), c.get("discontinued"),
                 c.get("china_edge"), c.get("testability"), int(c.get("hype", 0)), int(c.get("new_devices", 0)),
                 c.get("weight_kg"), c.get("keywords_zh"), c.get("keywords_eu"), c.get("notes"), _now()))
            for f in c.get("findings", []):
                con.execute("INSERT INTO findings (candidate, criterion, text, source, added) VALUES (?,?,?,?,?)",
                            (c["id"], f["criterion"], f["text"], f.get("source"), _now()))
            n += 1
    con.close()
    migrate(cfg)   # fill match/variant fields for the rows just inserted
    return n


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "candidate"


def add_candidate(cfg: Config, name: str, **fields) -> str:
    cid = fields.pop("id", None) or slug(name)
    con = _con(cfg)
    with con:
        if con.execute("SELECT 1 FROM candidates WHERE id = ?", (cid,)).fetchone():
            raise ValueError(f"candidate '{cid}' already exists")
        con.execute("INSERT INTO candidates (id, name, status, updated) VALUES (?,?,?,?)",
                    (cid, name, fields.pop("status", "new"), _now()))
    con.close()
    if fields:
        update_candidate(cfg, cid, **fields)
    return cid


def update_candidate(cfg: Config, cid: str, **fields) -> None:
    allowed = {"name", "calc_category", "own_sales_match", "match_terms", "query_eu", "exclude_terms", "ebay_category_ids",
               "variant_terms", "variant_question_zh", "status", "discontinued", "china_edge", "testability", "hype",
               "new_devices", "weight_kg", "keywords_zh", "keywords_eu", "notes"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown fields {bad}")
    checks = {"status": STATUSES, "china_edge": EDGES + ("",), "testability": TESTABILITY + ("",)}
    for k, opts in checks.items():
        if fields.get(k) not in (None, *opts):
            raise ValueError(f"{k} must be one of {opts}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields) + ", updated = ?"
    con = _con(cfg)
    with con:
        con.execute(f"UPDATE candidates SET {sets} WHERE id = ?", (*fields.values(), _now(), cid))
    con.close()


def add_finding(cfg: Config, cid: str, criterion: int, text: str, source: str | None = None) -> None:
    if criterion not in CRITERIA:
        raise ValueError("criterion must be 0-5")
    con = _con(cfg)
    with con:
        con.execute("INSERT INTO findings (candidate, criterion, text, source, added) VALUES (?,?,?,?,?)",
                    (cid, criterion, text.strip(), (source or "").strip() or None, _now()))
    con.close()


def delete_row(cfg: Config, table: str, row_id: int) -> None:
    if table not in ("findings", "evidence"):
        raise ValueError(table)
    con = _con(cfg)
    with con:
        con.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    con.close()


def load(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    con = _con(cfg)
    out = []
    for t in ("candidates", "findings", "evidence"):
        df = db.read(con, t)
        out.append(df.astype(object).where(df.notna(), None) if t != "evidence" else df)  # NaN -> None for forms
    con.close()
    return tuple(out)


# ----------------------------------------------------------------------------- price parsing

@dataclass
class PricePoint:
    price: float
    currency: str
    date: str | None = None
    title: str | None = None
    condition: str | None = None
    url: str | None = None


_MONEY = re.compile(
    r"(?P<pre>EUR|€|US\s?\$|USD|\$|GBP|£|CNY|RMB|¥|￥)?\s*(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?P<post>EUR|€|USD|\$|GBP|£|元|CNY|RMB)?", re.I)
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b"), "mdy"),
    (re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zäÄ]{3,9})\.?\s+(\d{4})\b"), "d_mon_y"),
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b"), "mon_d_y"),
]
_CONDITION = re.compile(r"\b(gebraucht|neu|new|used|pre-owned|defekt|for parts|ersatzteil|sehr gut|gut|refurbished|generalüberholt)\b", re.I)
_NOISE = re.compile(r"^(verkauft|sold|ended|beendet|versand|shipping|kostenlos|free|lieferung|delivery|top-rated|sofort-kaufen|"
                    r"buy it now|preisvorschlag|best offer|auction|auktion|gebote?|bids?|\+|\d+ gebote)", re.I)


def _currency(pre: str | None, post: str | None) -> str | None:
    t = (pre or post or "").upper().replace(" ", "")
    if t in ("EUR", "€"):
        return "EUR"
    if t in ("USD", "US$", "$"):
        return "USD"
    if t in ("GBP", "£"):
        return "GBP"
    if t in ("CNY", "RMB", "¥", "￥", "元"):
        return "CNY"
    return None


def _num(s: str) -> float:
    s = s.replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def find_date(line: str) -> str | None:
    for pat, kind in _DATE_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        try:
            if kind == "ymd":
                y, mo, d = int(m[1]), int(m[2]), int(m[3])
            elif kind == "dmy":
                d, mo, y = int(m[1]), int(m[2]), int(m[3])
            elif kind == "mdy":
                mo, d, y = int(m[1]), int(m[2]), int(m[3])
            elif kind == "d_mon_y":
                d, mo, y = int(m[1]), MONTHS[m[2].lower()[:3] if m[2].lower()[:3] in MONTHS else m[2].lower()], int(m[3])
            else:
                mo, d, y = MONTHS[m[1].lower()[:3]], int(m[2]), int(m[3])
            y = y + 2000 if y < 100 else y
            return date(y, mo, d).isoformat()
        except (KeyError, ValueError):
            continue
    return None


def find_prices(line: str, default_currency: str | None = None) -> list[tuple[float, str]]:
    out = []
    for pat, _ in _DATE_PATTERNS:          # dates are not prices
        line = pat.sub(" ", line)
    for m in _MONEY.finditer(line):
        cur = _currency(m["pre"], m["post"]) or default_currency
        if not cur:
            continue
        try:
            v = _num(m["num"])
        except ValueError:
            continue
        if 1 <= v <= 100000:
            out.append((v, cur))
    return out


def parse_sold_paste(text: str, default_currency: str = "EUR") -> list[PricePoint]:
    """Parse a copy-paste of sold results (Terapeak table, eBay 'Verkaufte Artikel', a list of
    'date price' lines). Tab-separated rows are read row by row; otherwise lines are grouped into
    listings: a title line, then price and date lines. Rows without a price are skipped."""
    lines = [l.rstrip() for l in text.replace("\r", "").split("\n")]
    points: list[PricePoint] = []
    if sum("\t" in l for l in lines) >= max(2, len([l for l in lines if l.strip()]) // 2):
        for l in lines:
            cells = [c.strip() for c in l.split("\t") if c.strip()]
            if not cells:
                continue
            money = [p for c in cells for p in find_prices(c) if _currency_hint(c)]
            if not money:
                continue
            title = max((c for c in cells if re.search(r"[A-Za-z]{3}", c) and not find_prices(c)), key=len, default=None)
            dt = next((d for c in cells if (d := find_date(c))), None)
            cond = next((m.group(1) for c in cells if (m := _CONDITION.search(c))), None)
            points.append(PricePoint(money[0][0], money[0][1], dt, title, cond))
        return points

    cur: dict = {}

    def flush():
        if cur.get("price"):
            points.append(PricePoint(cur["price"], cur["currency"], cur.get("date"), cur.get("title"), cur.get("condition")))
        cur.clear()

    for raw in lines:
        l = raw.strip()
        if not l:
            continue
        dt = find_date(l)
        money = [p for p in find_prices(l) if _currency_hint(l)]
        is_title = (len(l) >= 15 and re.search(r"[A-Za-zÄÖÜäöü]{3}", l) and not money and not dt
                    and not _NOISE.match(l))
        if is_title:
            if cur.get("price"):
                flush()
            cur["title"] = l
            continue
        if money and "price" not in cur and not re.search(r"versand|shipping|lieferung|delivery|\+", l, re.I):
            cur["price"], cur["currency"] = money[0]
        if dt:
            if cur.get("date") and cur.get("price"):
                flush()
            cur["date"] = dt
        if (m := _CONDITION.search(l)) and "condition" not in cur:
            cur["condition"] = m.group(1)
        if not money and not dt and re.fullmatch(r"\d+([.,]\d{1,2})?", l) and "price" not in cur and default_currency:
            cur["price"], cur["currency"] = _num(l), default_currency
    flush()
    return points


def _currency_hint(s: str) -> bool:
    return bool(re.search(r"EUR|€|US\s?\$|USD|\$|GBP|£|CNY|RMB|¥|￥|元", s, re.I))


def parse_cn_text(text: str) -> PricePoint | None:
    """A Xianyu share text / copied listing: first ¥ (or 元) price, the link, the 「title」 if present."""
    prices = find_prices(text, None)
    cny = [p for p in prices if p[1] == "CNY"]
    if not cny:
        nums = re.findall(r"(?<!\d)(\d{2,5}(?:\.\d{1,2})?)(?!\d)", text)
        if not nums:
            return None
        cny = [(float(nums[0]), "CNY")]
    url = (re.search(r"https?://\S+", text) or [None])[0]
    title = (re.search(r"「([^」]+)」", text) or re.search(r"【([^】]+)】", text))
    title = title.group(1) if title else re.sub(r"https?://\S+|¥\s*\d[\d.,]*|\d+元", "", text).strip()[:120] or None
    return PricePoint(cny[0][0], "CNY", date.today().isoformat(), title, None, url)


def add_evidence(cfg: Config, cid: str, kind: str, points: list[PricePoint], source: str) -> int:
    if kind not in ("eu_sold", "eu_asking", "cn_asking", "cn_sold"):
        raise ValueError(kind)
    con = _con(cfg)
    with con:
        for p in points:
            con.execute("INSERT INTO evidence (candidate, kind, source, date, price, currency, title, condition, url, added) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (cid, kind, source, p.date, p.price, p.currency, p.title, p.condition, p.url, _now()))
    con.close()
    return len(points)


# ----------------------------------------------------------------------------- analysis

def _to_eur(cfg: Config, price: float, currency: str) -> float | None:
    if currency == "EUR":
        return price
    if currency == "CNY":
        return price / float(cfg.costs.get("rmb_per_eur_effective", 7.6))
    fx = (cfg.research or {}).get("fx_to_eur", {})
    return price * float(fx[currency]) if currency in fx else None


@dataclass
class Assessment:
    cand: dict
    findings: dict[int, list[dict]]
    eu: pd.DataFrame
    cn: pd.DataFrame
    eu_median: float | None = None
    eu_median_90d: float | None = None
    eu_n: int = 0
    eu_span_days: int = 0
    trend_12m: float | None = None
    eu_by_quarter: list[dict] = field(default_factory=list)
    cn_median_rmb: float | None = None
    cn_n: int = 0
    estimate: calc.Result | None = None
    max_rmb: float | None = None
    criteria: list[dict] = field(default_factory=list)
    verdict: str = calc.UNKNOWN
    verdict_reason: str = ""
    own: dict | None = None             # your own sales, when the candidate matches items you've sold
    next_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _eu_frame(cfg: Config, cand: dict, ev: pd.DataFrame) -> pd.DataFrame:
    """EU sold evidence in EUR, plus your own sales when the candidate maps to a category you sell."""
    rows = []
    if not ev.empty:
        e = ev[(ev.candidate == cand["id"]) & (ev.kind == "eu_sold")]
        for r in e.itertuples():
            eur = _to_eur(cfg, r.price, r.currency)
            if eur is not None:
                rows.append({"id": r.id, "date": r.date, "eur": eur, "source": r.source, "title": r.title,
                             "condition": r.condition, "price": r.price, "currency": r.currency})
    if isinstance(cand.get("own_sales_match"), str) and cand["own_sales_match"].strip():
        sales = calc.load_sales(cfg)
        if sales is not None and not sales.empty:
            match = sales["item"].str.contains(cand["own_sales_match"], case=False, regex=True, na=False)
            s = sales[match & (sales.is_stock == 0) & (sales.lost == 0)]
            s = s[~calc._bulk_mask(s)]
            for r in s.itertuples():
                rows.append({"id": None, "date": r.sell_date.date().isoformat() if pd.notna(r.sell_date) else None,
                             "eur": r.price_sold, "source": "own sales", "title": r.item, "condition": None,
                             "price": r.price_sold, "currency": "EUR"})
    df = pd.DataFrame(rows, columns=["id", "date", "eur", "source", "title", "condition", "price", "currency"])
    df["date"] = pd.to_datetime(df.date, errors="coerce")
    return df


def assess(cfg: Config, cand: dict, findings: pd.DataFrame, ev: pd.DataFrame, sales_cache=None) -> Assessment:
    t = cfg.targets
    f_by = {}
    if not findings.empty:
        for r in findings[findings.candidate == cand["id"]].sort_values("id").to_dict("records"):
            f_by.setdefault(int(r["criterion"]), []).append(r)
    eu = _eu_frame(cfg, cand, ev)
    cn = ev[(ev.candidate == cand["id"]) & ev.kind.isin(["cn_asking", "cn_sold"])] if not ev.empty else pd.DataFrame()
    a = Assessment(cand, f_by, eu, cn)
    today = pd.Timestamp(cfg.as_of)

    # EU prices
    if len(eu):
        a.eu_n = len(eu)
        recent = eu[eu.date >= today - pd.DateOffset(months=12)]
        base = recent if len(recent) >= 3 else eu
        a.eu_median = float(base.eur.median())
        r90 = eu[eu.date >= today - pd.Timedelta(days=90)]
        a.eu_median_90d = float(r90.eur.median()) if len(r90) >= 3 else None
        dated = eu.dropna(subset=["date"])
        if len(dated):
            a.eu_span_days = int((dated.date.max() - dated.date.min()).days)
            q = dated.assign(q=dated.date.dt.to_period("Q").astype(str)).groupby("q").eur.agg(["median", "size"])
            a.eu_by_quarter = [{"quarter": k, "median": float(v["median"]), "n": int(v["size"])} for k, v in q.iterrows()]
        tr = dated[dated.date >= today - pd.DateOffset(months=12)]
        if len(tr) >= 6 and (tr.date.max() - tr.date.min()).days >= 180:
            x = (tr.date - tr.date.min()).dt.days.to_numpy(dtype=float)
            slope = np.polyfit(x, tr.eur.to_numpy(dtype=float), 1)[0]
            a.trend_12m = float(slope * 365 / tr.eur.mean())
        elif len(dated) >= 6 and a.eu_span_days >= 300:
            # older data only: compare the latest 12 months' median with the 12 months before
            last = dated[dated.date >= dated.date.max() - pd.DateOffset(months=12)]
            prev = dated[(dated.date < dated.date.max() - pd.DateOffset(months=12)) &
                         (dated.date >= dated.date.max() - pd.DateOffset(months=24))]
            if len(last) >= 3 and len(prev) >= 3:
                a.trend_12m = float(last.eur.median() / prev.eur.median() - 1)

    # China prices
    if len(cn):
        rmb = cn[cn.currency == "CNY"].price
        if len(rmb):
            a.cn_median_rmb, a.cn_n = float(rmb.median()), int(len(rmb))

    # profit via the Phase 1 calculator, with this candidate as a temporary category profile
    c2 = cfg.model_copy(deep=True)
    key = cand.get("calc_category") or f"cand:{cand['id']}"
    prof = dict(c2.category_profiles.get(key, {}))
    prof.update({k: v for k, v in {
        "weight_kg": cand.get("weight_kg"), "discontinued": bool(cand.get("discontinued")) or None,
        "china_edge": cand.get("china_edge") or None, "testability": cand.get("testability") or None,
        "hype": bool(cand.get("hype")) or None}.items() if v is not None})
    if not cand.get("calc_category"):
        prof.setdefault("import_vat", True)     # new category: never rosier than reality
        prof.setdefault("returns_reserve_pct", 0.05)
    c2.category_profiles[key] = prof
    hist = {"n": a.eu_n}
    if a.trend_12m is not None:
        hist.update(trend_12m=a.trend_12m, trend_n=a.eu_n, trend_basis="EU sold evidence")
    sell = a.eu_median_90d or a.eu_median
    if a.cn_median_rmb and sell:
        inp = calc.Inputs(category=key, rmb=a.cn_median_rmb, sell_eur=sell)
        res = calc.calculate(c2, inp, sales=_NO_SALES)
        res.criteria = calc.score(c2, inp, res.landed, res.profit, res.profit_on_cost, hist)
        res.verdict, res.verdict_reason = calc.verdict(res.criteria)
        a.estimate = res
        a.criteria = res.criteria
        try:
            a.max_rmb = calc.max_rmb(c2, calc.Inputs(category=key, sell_eur=sell),
                                     sales=_NO_SALES).max_rmb
        except ValueError:
            a.max_rmb = None
    else:
        dummy = calc.Landed(0, 0, 0, 0, 0, 0, 0, 0, False, "")
        a.criteria = calc.score(c2, calc.Inputs(category=key), dummy, 0, 0, hist)
        a.criteria[4] = {"name": "5. Profit target", "status": calc.UNKNOWN,
                         "reason": "needs " + " and ".join(x for x, ok in (("EU sold prices", sell), ("Xianyu prices", a.cn_median_rmb)) if not ok)}
    if a.criteria[1]["status"] == calc.UNKNOWN:
        a.criteria[1]["reason"] = (f"needs ≥6 dated EU sales over 6+ months (have {a.eu_n}) - "
                                   "paste Terapeak / eBay sold results")
    # your own results beat estimates: use them for criterion 5 when there is no Xianyu evidence yet
    own = eu[eu.source == "own sales"] if len(eu) else eu
    if len(own) and isinstance(cand.get("own_sales_match"), str):
        sales = calc.load_sales(cfg)
        m = sales["item"].str.contains(cand["own_sales_match"], case=False, regex=True, na=False)
        s = sales[m & (sales.is_stock == 0) & (sales.lost == 0) & (sales.sell_date >= today - pd.DateOffset(months=12))]
        if len(s) >= 3:
            a.own = {"profit": float((s.price_sold - s.price_paid).median()),
                     "poc": float((s.price_sold - s.price_paid).sum() / s.price_paid.sum()), "n": int(len(s))}
            if a.estimate is None:
                ok = a.own["profit"] >= t.get("min_profit_eur", 50)
                poc_ok = a.own["poc"] >= t.get("min_profit_on_cost", 0.5)
                border = a.own["poc"] >= t.get("min_profit_on_cost_borderline", 0.4)
                a.criteria[4] = {"name": "5. Profit target",
                                 "status": calc.PASS if ok and poc_ok else calc.BORDERLINE if ok and border else calc.FAIL,
                                 "reason": f"your last 12 months: €{a.own['profit']:.0f} median profit, "
                                           f"{a.own['poc']:.0%} on cost ({a.own['n']} sales)"}
    a.verdict, a.verdict_reason = calc.verdict(a.criteria)

    # next steps, most useful first
    if a.eu_n < 10 or a.eu_span_days < 365:
        kw = cand.get("keywords_eu") or cand["name"]
        a.next_steps.append(f"Paste Terapeak sold results for '{kw.split(',')[0].strip()}' (Sold, last 3 years) - "
                            f"need ≥10 sales over 12+ months for the hype check (have {a.eu_n}).")
    if a.cn_n < 3 and not a.own:
        kw = cand.get("keywords_zh") or cand["name"]
        a.next_steps.append(f"Add 3-5 Xianyu asking prices for '{kw.split(',')[0].strip()}' (share text or screenshot).")
    for crit, field_ in ((1, "discontinued"), (3, "china_edge"), (4, "testability")):
        if not cand.get(field_):
            a.next_steps.append(f"Set '{field_}' (criterion {crit}).")
    if a.verdict == calc.PASS and cand.get("status") in ("new", "watching"):
        a.next_steps.append("All five criteria pass: send the seller checklist and do a 1-3 unit test buy.")
    if cand.get("new_devices"):
        a.warnings.append("ElektroG: importing NEW electrical devices may make you a 'Hersteller' needing stiftung ear "
                          "registration before selling. Check with stiftung ear / IHK first.")
    if cand.get("calc_category") is None and a.estimate and a.estimate.landed.vat_on:
        a.warnings.append("Import VAT is included in the estimate (new category default).")
    return a


def assess_all(cfg: Config) -> list[Assessment]:
    cands, findings, ev = load(cfg)
    if cands.empty:
        return []
    out = [assess(cfg, c, findings, ev) for c in cands.to_dict("records")]
    order = {"active": 0, "shortlist": 1, "watching": 2, "new": 3, "dropped": 4}
    rank = {calc.PASS: 0, calc.BORDERLINE: 1, calc.UNKNOWN: 2, calc.FAIL: 3}
    return sorted(out, key=lambda a: (order.get(a.cand.get("status"), 5), rank.get(a.verdict, 2), a.cand["name"]))
