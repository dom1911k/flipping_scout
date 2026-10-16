"""Derive category, grade, flaws, platform and seller for imported rows."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from urllib.parse import unquote

import pandas as pd

from .config import Config

URL_RE = re.compile(r"https?://", re.I)
LISTING_TITLE_RE = re.compile(r"闲鱼|xianyu|idle fish", re.I)
STOREFRONT_RE = re.compile(r"spm=a21ybx\.personal\.feeds\.\d+\.([0-9a-f]{4})", re.I)
GOOFISH_ITEM_RE = re.compile(r"goofish\.com/item\?.*?\bid=(\d+)", re.I)
GOOFISH_USER_RE = re.compile(r"goofish\.com/personal\?.*?\buserId=(\d+)", re.I)
TAOBAO_ITEM_RE = re.compile(r"taobao\.com/item\.htm\?.*?\bid=(\d+)", re.I)
PART_RE = re.compile(
    r"screen|shell|button|battery|speaker|cable|\bcase\b|hinge|joystick|c-stick|lens|membrane|pads?\b|"
    r"power switch|chip|digitizer|antenna|\bmod\b|charger|sd cards?\b|micro sd|trophy|cartridge|"
    r"backlight|nipple|replacement|usbc|sound\b|tools|ips\b(?!.*(n3ds|3ds|ipod|gba|gbc|gbp|sp\b))",
    re.I,
)
# Names that are always a sellable unit, even if a part word appears ("N3DSXL with upper IPS screen")
UNIT_RE = re.compile(r"n3ds\s*(ll|xl)|3ds\s*(ll|xl)|ipod|ugreen|grip|olympus|ddr5|\bayn\b|psv \d+g|vita (sd )?(\d+gb|card)|ps vita black",
                     re.I)


# ----------------------------------------------------------------------------- small helpers

def categorize(name: str, cfg: Config) -> str:
    for rule in cfg.categories:
        if re.search(rule.pattern, name or "", re.I):
            return rule.name
    return "Other"


def grade_of(name: str, cfg: Config) -> int | None:
    for m in re.finditer(r"(?<![\d.x+])(\d{2})(?:%|(?![\d%])(?!\s*(?:g|gb|t|tb|mah)\b))", name or "", re.I):
        g = int(m.group(1))
        if cfg.grade_min <= g <= cfg.grade_max:
            return g
    return None


def is_dif_seller(name: str, cfg: Config) -> bool:
    low = (name or "").lower()
    return any(m in low for m in cfg.dif_seller_markers)


_CLAUSE_SPLIT = re.compile(r"[;/+()\n]|\s-\s|,|\.\s|\band\b|\balso\b", re.I)


def detect_flaws(text: str, cfg: Config) -> tuple[list[str], list[str], bool]:
    """Return (groups, matched words, all_minor)."""
    low = (text or "").lower()
    for neg in cfg.flaw_negations:
        low = low.replace(neg.lower(), " ")
    groups, words, minors = [], [], []
    clauses = [c for c in _CLAUSE_SPLIT.split(low) if c and c.strip()]
    for i, clause in enumerate(clauses):
        hit = False
        for group, kws in cfg.flaw_keywords.items():
            for kw in kws:
                if kw.lower() in clause:
                    hit = True
                    if group not in groups:
                        groups.append(group)
                    words.append(kw)
                    break
        if hit:
            # a qualifier can sit in the neighbouring clause: "Yellow corners (not too bad - wont mention)"
            near = " ".join(clauses[i:i + 2])
            minors.append(any(w in clause for w in cfg.flaw_minor_words)
                          or any(w in near for w in cfg.flaw_minor_followups))
    return groups, words, bool(minors) and all(minors)


def parse_source(raw: str | None) -> dict:
    """Classify a Source / link cell: shop name, Goofish link, Taobao link or pasted listing title."""
    s = raw.strip() if isinstance(raw, str) else ""
    out = {"kind": "none", "name": None, "listing_id": None, "user_id": None, "storefront": None}
    if not s:
        return out
    if URL_RE.search(s):
        u = unquote(unquote(s))
        if m := GOOFISH_ITEM_RE.search(u):
            out.update(kind="goofish_item", listing_id=f"goofish:{m.group(1)}")
            if sf := STOREFRONT_RE.search(u):
                out["storefront"] = sf.group(1).lower()
        elif m := GOOFISH_USER_RE.search(u):
            out.update(kind="goofish_user", user_id=m.group(1))
        elif m := TAOBAO_ITEM_RE.search(u):
            out.update(kind="taobao_item", listing_id=f"taobao:{m.group(1)}")
        else:
            out["kind"] = "url"
        return out
    if LISTING_TITLE_RE.search(s):
        out["kind"] = "listing_title"
        return out
    out.update(kind="name", name=s.split(" - ")[0].strip())
    return out


def _norm(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\(?(dif(ferent)? (seller|vendor)[^)]*)\)?", " ", s)
    return " ".join(s.split())


# ----------------------------------------------------------------------------- sales

def enrich_sales(sales: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = sales.copy()
    df["category"] = df["item"].map(lambda n: categorize(n, cfg))
    df["grade"] = df["item"].map(lambda n: grade_of(n, cfg)).astype("Int64")
    df["dif_seller"] = df["item"].map(lambda n: int(is_dif_seller(n, cfg)))

    flaws = (df["item"].fillna("") + " ; " + df["comment"].fillna("")).map(lambda t: detect_flaws(t, cfg))
    df["flaw_tags"] = flaws.map(lambda f: ",".join(f[0]) or None)
    df["flaw_matches"] = flaws.map(lambda f: ", ".join(f[1]) or None)
    df["flawed"] = flaws.map(lambda f: int(bool(f[0])))
    df["flaw_minor"] = flaws.map(lambda f: int(f[2]))
    for row, val in cfg.flaw_overrides.items():
        mask = df["sheet_row"] == row
        df.loc[mask, "flawed"] = int(val)
        if not val:
            df.loc[mask, ["flaw_minor"]] = 0

    place = df["selling_place"].fillna("")
    df["platform"] = place.map(lambda p: cfg.platforms.get(p, {}).get("platform") or (p or None))
    df["account"] = place.map(lambda p: cfg.platforms.get(p, {}).get("account"))
    df["is_stock"] = (df["price_sold"].isna() & df["sell_date"].isna()).astype(int)
    bd = pd.to_datetime(df["buy_date"])
    sd = pd.to_datetime(df["sell_date"])
    df["days_to_sell"] = (sd - bd).dt.days.astype("Int64")
    df["profit_recalc"] = df["price_sold"] - df["price_paid"]
    return df


# ----------------------------------------------------------------------------- purchases

def enrich_purchases(purchases: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    df = purchases.copy()
    df["category"] = df["item"].map(lambda n: categorize(n, cfg))
    df["kind"] = df["item"].map(_kind)
    df["storefront"] = df["link"].map(lambda s: parse_source(s)["storefront"])
    return df


def _kind(name: str) -> str:
    """unit = something sold as-is; part = repair/mod parts. Text in brackets describes condition
    ("N3DS (upper screen broken)") and is ignored for this decision."""
    bare = re.sub(r"\([^)]*\)", " ", name)
    if UNIT_RE.search(bare):
        return "unit"
    return "part" if PART_RE.search(bare) else "unit"


PRICE_TOLERANCE_EUR = 2.5


def match_purchases(sales: pd.DataFrame, purchases: pd.DataFrame, cfg: Config | None = None) -> pd.DataFrame:
    """Link order lines to sales rows: same category, landed price within PRICE_TOLERANCE_EUR per unit, close
    dates and similar names. Each sales row is claimed at most once."""
    purchases = purchases.copy()
    purchases["sales_row"] = pd.NA
    claimed: set[int] = set()
    s = sales.assign(_bd=pd.to_datetime(sales["buy_date"]), _n=sales["item"].map(_norm))
    p_order = purchases[(purchases["kind"] == "unit") & purchases["excluded"].isna() & purchases["total_eur"].notna()]
    for pass_days, min_ratio in ((3, 0.55), (120, 0.8)):
        for idx, p in p_order.iterrows():
            if pd.notna(purchases.at[idx, "sales_row"]):
                continue
            qty = int(p["qty"] or 1)
            unit_total = p["total_eur"] / max(qty, 1)
            pd_date = pd.to_datetime(p["buy_date"]) if p["buy_date"] is not None else None
            same_cat = (s["category"] == p["category"]) if p["category"] != "Other" else True
            cand = s[same_cat & ~s["sheet_row"].isin(claimed)
                     & ((s["price_paid"] - unit_total).abs() <= PRICE_TOLERANCE_EUR)]
            if pd_date is not None:
                cand = cand[(cand["_bd"] - pd_date).abs().dt.days <= pass_days]
            elif pass_days < 100:
                continue
            if cfg and cfg.main_seller and is_dif_seller(p["item"], cfg):
                # an order line marked "dif seller" can't be a unit the sheet credits to the main seller
                cand = cand[cand["source"].fillna("").str.strip() != cfg.main_seller]
            if cand.empty:
                continue
            pn = _norm(p["item"])
            ratios = cand["_n"].map(lambda n: SequenceMatcher(None, n, pn).ratio())
            cand = cand.assign(_r=ratios)
            cand = cand[cand["_r"] >= min_ratio].sort_values(["_r", "sheet_row"], ascending=[False, True])
            picked = cand["sheet_row"].head(qty).tolist()
            if picked:
                purchases.at[idx, "sales_row"] = picked[0]
                claimed.update(picked)
                for extra in picked[1:]:
                    # qty > 1: remember the extra rows on the same purchase via a side map
                    purchases.attrs.setdefault("extra_matches", {}).setdefault(idx, []).append(extra)
    return purchases


# ----------------------------------------------------------------------------- sellers

def resolve_sellers(sales: pd.DataFrame, purchases: pd.DataFrame, cfg: Config) -> tuple[pd.DataFrame, dict]:
    """Fill sales.seller / seller_confidence / listing_id. Returns (sales, evidence) where evidence
    holds the learned storefront -> shop name map for the report."""
    df = sales.copy()
    # links attached to each sales row: its own Source cell plus any matched order line
    links: dict[int, list[str]] = defaultdict(list)
    for idx, p in purchases.iterrows():
        rows = []
        if pd.notna(p.get("sales_row")):
            rows.append(int(p["sales_row"]))
        rows += purchases.attrs.get("extra_matches", {}).get(idx, [])
        for r in rows:
            if isinstance(p["link"], str) and p["link"]:
                links[r].append(p["link"])

    parsed = {r.sheet_row: [parse_source(r.source)] + [parse_source(l) for l in links.get(r.sheet_row, [])]
              for r in df.itertuples()}
    non_sellers = {x.lower() for x in cfg.non_seller_sources}

    def explicit_name(ps):
        for p in ps:
            if p["kind"] == "name" and p["name"].lower() not in non_sellers:
                return p["name"]
        return None

    # learn storefront/user/listing -> shop name from rows that have both
    votes: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for row, ps in parsed.items():
        name = explicit_name(ps)
        if not name:
            continue
        for p in ps:
            for key in (("storefront", p["storefront"]), ("user", p["user_id"]), ("listing", p["listing_id"])):
                if key[1]:
                    votes[key][name] += 1
    learned = {}
    for key, c in votes.items():
        name, n = c.most_common(1)[0]
        if n / sum(c.values()) >= 0.8:
            learned[key] = name

    since = pd.Timestamp(cfg.dif_seller_convention_since) if cfg.dif_seller_convention_since else None
    seller, conf, listing = [], [], []
    for r in df.itertuples():
        ps = parsed[r.sheet_row]
        lid = next((p["listing_id"] for p in ps if p["listing_id"]), None)
        listing.append(lid)
        name = explicit_name(ps)
        if name:
            seller.append(name); conf.append("explicit"); continue
        found = None
        for p in ps:
            for key, label in ((("user", p["user_id"]), "link"), (("storefront", p["storefront"]), "fingerprint"),
                               (("listing", p["listing_id"]), "link")):
                if key[1] and key in learned:
                    found = (learned[key], label)
                    break
            if found:
                break
        if not found:
            p = next((p for p in ps if p["user_id"] or p["storefront"]), None)
            if p and p["user_id"]:
                found = (f"user {p['user_id']}", "link")
            elif p and p["storefront"]:
                found = (f"storefront {p['storefront']}", "fingerprint")
        if not found and (r.category == "N3DS" and not r.dif_seller and since is not None
                          and r.buy_date is not None and pd.Timestamp(r.buy_date) >= since and cfg.main_seller):
            found = (cfg.main_seller, "inferred")
        if not found and lid:
            found = (f"listing {lid}", "link")
        if not found:
            found = ("other (dif seller)" if r.dif_seller else None, "unknown")
        seller.append(found[0]); conf.append(found[1])
    df["seller"], df["seller_confidence"], df["listing_id"] = seller, conf, listing
    evidence = {"learned": {f"{k[0]} {k[1]}": v for k, v in learned.items() if k[0] != "listing"},
                "votes": {f"{k[0]} {k[1]}": dict(c) for k, c in votes.items() if k[0] != "listing"}}
    return df, evidence
