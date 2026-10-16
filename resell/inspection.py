"""Phase 2: seller messages, arrival inspections and the seller scorecard."""
from __future__ import annotations

import json
import re
from datetime import datetime
from importlib import resources

import pandas as pd
import yaml

from . import db
from .config import Config
from .enrich import parse_source

RESULTS = ("ok", "minor", "flaw", "na")
PRECHECK = {"answered": "Sent, fully answered", "partial": "Sent, partly answered",
            "ignored": "Sent, no answer / refused", "not_sent": "Not sent"}
STATUSES = ("preferred", "ok", "watch", "avoid")


# ----------------------------------------------------------------------------- content

def _content(name: str) -> dict:
    return yaml.safe_load(resources.files("resell").joinpath("content", name).read_text(encoding="utf-8"))


def messages() -> dict:
    return _content("messages.yaml")


def checklists() -> dict:
    return _content("checklists.yaml")


def template_for(category: str) -> tuple[str, dict]:
    t = messages()["templates"]
    key = category if category in t else "generic"
    return key, t[key]


def checklist_for(category: str) -> list[dict]:
    c = checklists()["checklists"]
    return c.get(category) or c["generic"]


def norm_serial(s: str | None) -> str:
    return re.sub(r"\s+", "", s).upper() if isinstance(s, str) else ""


# ----------------------------------------------------------------------------- inspections

def evaluate(category: str, results: dict[str, str], cfg: Config) -> tuple[int, int, str | None]:
    """flawed, flaw_minor, tags from check results. Groups in flaw_always_major never count as minor."""
    checks = {c["key"]: c for c in checklist_for(category)}
    tags, minor_only = [], True
    for key, res in results.items():
        if res not in ("minor", "flaw") or key not in checks:
            continue
        group = checks[key].get("flaw", "function")
        if group not in tags:
            tags.append(group)
        is_minor = res == "minor" and checks[key].get("minor_ok") and group not in cfg.flaw_always_major
        minor_only = minor_only and bool(is_minor)
    return int(bool(tags)), int(bool(tags) and minor_only), ",".join(tags) or None


def serial_warnings(cfg: Config, serial: str | None, exclude_id: int | None = None) -> list[str]:
    s = norm_serial(serial)
    if not s:
        return []
    out = []
    if s in {norm_serial(x) for x in checklists().get("known_fake_serials", [])}:
        out.append(f"{serial} is on the known-fake list (seen on several reshelled units)")
    con = db.connect(cfg.path(cfg.paths.db))
    sales = db.read(con, "sales")
    insp = db.read(con, "inspections")
    con.close()
    if not sales.empty and "serial" in sales:
        hits = sales[sales.serial.map(norm_serial) == s]
        if len(hits):
            rows = ", ".join(str(r) for r in hits.sheet_row)
            out.append(f"serial already on Sales Sheet row(s) {rows} - same unit, or a copied shell label?")
    if not insp.empty:
        hits = insp[(insp.serial.map(norm_serial) == s) & (insp.id != (exclude_id or -1))]
        if len(hits):
            out.append(f"serial already used in inspection #{', #'.join(map(str, hits.id))}")
    return out


def resolve_seller(cfg: Config, seller: str | None, listing: str | None) -> tuple[str | None, str | None]:
    """Typed seller name wins; otherwise map the link's storefront/user code to a known shop."""
    p = parse_source(listing)
    storefront = p["storefront"]
    if seller and seller.strip():
        return seller.strip(), storefront
    con = db.connect(cfg.path(cfg.paths.db))
    sf = db.read(con, "storefronts")
    con.close()
    lookup = dict(zip(sf.key, sf.seller)) if not sf.empty else {}
    for key in (f"storefront {storefront}" if storefront else None, f"user {p['user_id']}" if p["user_id"] else None):
        if key and key in lookup:
            return lookup[key], storefront
    if storefront:
        return f"storefront {storefront}", storefront
    if p["user_id"]:
        return f"user {p['user_id']}", None
    return None, storefront


def save_inspection(cfg: Config, *, category: str, results: dict[str, str], item: str | None = None,
                    seller: str | None = None, listing: str | None = None, buy_date: str | None = None,
                    price_paid: float | None = None, serial: str | None = None, precheck: str | None = None,
                    check_notes: dict[str, str] | None = None, comment: str | None = None) -> int:
    bad = {k: v for k, v in results.items() if v not in RESULTS}
    if bad:
        raise ValueError(f"results must be one of {RESULTS}: {bad}")
    if precheck and precheck not in PRECHECK:
        raise ValueError(f"precheck must be one of {list(PRECHECK)}")
    seller, storefront = resolve_seller(cfg, seller, listing)
    flawed, minor, tags = evaluate(category, results, cfg)
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        cur = con.execute(
            "INSERT INTO inspections (created, category, item, seller, listing, storefront, buy_date, price_paid, "
            "serial, precheck, results, check_notes, comment, flawed, flaw_minor, flaw_tags) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), category, item, seller, listing, storefront, buy_date,
             price_paid, serial, precheck, json.dumps(results), json.dumps(check_notes or {}, ensure_ascii=False),
             comment, flawed, minor, tags))
    con.close()
    return cur.lastrowid


def list_inspections(cfg: Config, limit: int = 50) -> pd.DataFrame:
    con = db.connect(cfg.path(cfg.paths.db))
    df = pd.read_sql("SELECT * FROM inspections ORDER BY id DESC LIMIT ?", con, params=(limit,))
    con.close()
    return df


def delete_inspection(cfg: Config, inspection_id: int) -> None:
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        con.execute("DELETE FROM inspections WHERE id = ?", (inspection_id,))
    con.close()


# ----------------------------------------------------------------------------- seller notes

def set_seller_note(cfg: Config, seller: str, status: str | None = None, notes: str | None = None) -> None:
    if status and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        row = con.execute("SELECT status, notes FROM seller_notes WHERE seller = ?", (seller,)).fetchone()
        status = status or (row[0] if row else None)
        notes = notes if notes is not None else (row[1] if row else None)
        con.execute("INSERT OR REPLACE INTO seller_notes (seller, status, notes, updated) VALUES (?,?,?,?)",
                    (seller, status, notes, datetime.now().isoformat(timespec="seconds")))
    con.close()


# ----------------------------------------------------------------------------- scorecard

def _link(sales: pd.DataFrame, insp: pd.DataFrame) -> pd.Series:
    """Map inspection id -> sales sheet_row: by serial if unique, else category + buy date + price."""
    out = {}
    if sales.empty or insp.empty:
        return pd.Series(out, dtype="Int64")
    s = sales.assign(_serial=sales.serial.map(norm_serial))
    for r in insp.itertuples():
        hit = s[(s._serial == norm_serial(r.serial)) & (s._serial != "")] if r.serial else s.iloc[0:0]
        if len(hit) != 1 and r.buy_date:
            hit = s[(s.category == r.category) & (s.buy_date.astype(str).str[:10] == str(r.buy_date)[:10])]
            if r.price_paid is not None and not pd.isna(r.price_paid):
                hit = hit[(hit.price_paid - r.price_paid).abs() <= 2.5]
        if len(hit) == 1:
            out[r.id] = int(hit.sheet_row.iloc[0])
    return pd.Series(out, dtype="Int64")


def scorecard(cfg: Config, category: str | None = None) -> pd.DataFrame:
    con = db.connect(cfg.path(cfg.paths.db))
    sales, insp = db.read(con, "sales"), db.read(con, "inspections")
    notes, sf = db.read(con, "seller_notes"), db.read(con, "storefronts")
    con.close()
    cols = ["seller", "category", "bought", "flawed", "flaw_minor", "profit", "price_paid", "buy_date",
            "sold", "inspected", "precheck"]
    units = pd.DataFrame(columns=cols)
    if not sales.empty:
        s = (sales[sales["lost"].fillna(0) == 0] if "lost" in sales else sales).copy()
        s["sold"] = (s.is_stock == 0).astype(int)
        s["profit"] = s.profit_recalc.where(s.sold == 1)
        s["inspected"], s["precheck"] = 0, None
        linked = _link(s, insp)
        if len(linked):
            by_row = insp.set_index("id").loc[linked.index]
            by_row["sheet_row"] = linked.values
            for r in by_row.reset_index().itertuples():
                m = s.sheet_row == r.sheet_row
                # the inspection is the better record of what arrived
                s.loc[m, ["flawed", "flaw_minor", "inspected", "precheck"]] = [r.flawed, r.flaw_minor, 1, r.precheck]
                if r.seller and (s.loc[m, "seller"].isna().all() or s.loc[m, "seller_confidence"].isin(["inferred", "unknown"]).all()):
                    s.loc[m, "seller"] = r.seller
            insp = insp[~insp.id.isin(linked.index)]
        s["bought"] = 1
        units = s[cols]
    if not insp.empty:
        extra = insp.assign(bought=1, sold=0, profit=None, inspected=1)[cols]
        units = pd.concat([units, extra], ignore_index=True) if len(units) else extra
    if units.empty:
        return pd.DataFrame()
    if category:
        units = units[units.category == category]
    units = units.dropna(subset=["seller"])
    units["buy_date"] = pd.to_datetime(units.buy_date, errors="coerce")
    for c in ("flawed", "flaw_minor", "sold", "inspected", "profit", "price_paid"):
        units[c] = pd.to_numeric(units[c], errors="coerce")
    units["major"] = ((units.flawed == 1) & (units.flaw_minor != 1)).astype(int)
    units["answered"] = units.precheck.isin(["answered", "partial"]).astype(int)
    units["asked"] = units.precheck.isin(["answered", "partial", "ignored"]).astype(int)
    g = units.groupby("seller")
    card = pd.DataFrame({
        "bought": g.size(),
        "sold": g.sold.sum(),
        "categories": g.category.agg(lambda x: ", ".join(sorted(set(map(str, x))))),
        "flaw_rate": g.flawed.mean(),
        "major_flaw_rate": g.major.mean(),
        "avg_profit": g.profit.mean(),
        "profit_on_cost": g.apply(lambda d: d.profit.sum() / d.price_paid[d.sold == 1].sum()
                                  if d.price_paid[d.sold == 1].sum() else float("nan")),
        "last_bought": g.buy_date.max(),
        "inspected": g.inspected.sum(),
        "checklist_asked": g.asked.sum(),
        "checklist_answered": g.answered.sum(),
    })
    if not sf.empty:
        keys = sf.groupby("seller").key.agg(lambda x: ", ".join(k.replace("storefront ", "") for k in x))
        card["storefronts"] = keys.reindex(card.index)
    else:
        card["storefronts"] = None
    if not notes.empty:
        n = notes.set_index("seller")
        card["status"] = n.status.reindex(card.index)
        card["notes"] = n.notes.reindex(card.index)
    else:
        card["status"], card["notes"] = None, None
    return card.sort_values(["bought", "last_bought"], ascending=False).reset_index()
