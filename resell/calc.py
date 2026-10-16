"""Phase 1: landed cost, profit, reverse "max RMB" and the 5-criteria score.

All money is per unit in EUR unless a name says otherwise. Every rate, fee and threshold comes from
config.yaml; nothing here is hardcoded business knowledge.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import Config

PASS, BORDERLINE, FAIL, UNKNOWN = "PASS", "BORDERLINE", "FAIL", "UNKNOWN"


# ----------------------------------------------------------------------------- inputs / outputs

@dataclass
class Inputs:
    category: str
    rmb: float | None = None            # price per unit in RMB (None in reverse mode)
    sell_eur: float | None = None       # expected sell price; None = median of your own sales
    qty: int = 1                        # units bought together (shares the flat customs fee)
    parts_eur: float = 0.0              # repair/upgrade parts per unit
    weight_kg: float | None = None      # None = category default
    platform: str | None = None         # kleinanzeigen | ebay | vinted
    rate: float | None = None           # RMB per EUR; None = config effective rate
    import_vat: bool | None = None      # None = category profile / default
    match: str | None = None            # narrow your sales history, e.g. "white 98" or "1T"


@dataclass
class Landed:
    goods: float
    agent_fee: float
    shipping: float
    duty: float
    import_vat: float
    parts: float
    total: float
    rate: float
    vat_on: bool
    duty_note: str


@dataclass
class Selling:
    price: float
    price_source: str
    platform: str
    fee: float
    outbound_shipping: float
    returns_reserve: float
    net: float


@dataclass
class Result:
    inputs: Inputs
    landed: Landed
    selling: Selling
    profit: float
    profit_on_cost: float
    margin: float
    break_even_sell: float
    history: dict
    criteria: list[dict] = field(default_factory=list)
    verdict: str = UNKNOWN
    verdict_reason: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------------- config helpers

def profile(cfg: Config, category: str) -> dict:
    return cfg.category_profiles.get(category, {})


def platform_cfg(cfg: Config, platform: str | None) -> tuple[str, dict]:
    name = (platform or cfg.costs.get("default_platform", "kleinanzeigen")).lower()
    plats = cfg.costs.get("platforms_sell", {})
    if name not in plats:
        raise ValueError(f"unknown platform '{name}' - known: {', '.join(plats)}")
    return name, plats[name]


# ----------------------------------------------------------------------------- history from your sales

def _bulk_mask(df: pd.DataFrame) -> pd.Series:
    key = [df["sell_date"], df["platform"], df["category"]]
    return df.groupby(key)["item"].transform("size") >= 4


def load_sales(cfg: Config) -> pd.DataFrame | None:
    path = cfg.path(cfg.paths.db)
    if not path.exists():
        return None
    with sqlite3.connect(path) as con:
        try:
            df = pd.read_sql("SELECT * FROM sales", con)
        except Exception:
            return None
    for c in ("buy_date", "sell_date"):
        df[c] = pd.to_datetime(df[c])
    if "lost" not in df:
        df["lost"] = 0
    return df


def history(cfg: Config, category: str, match: str | None = None, sales: pd.DataFrame | None = None,
            today: date | None = None) -> dict:
    """Median sell price, days to sell, flaw rate and 12-month price trend from your own sales.
    Bulk sales (one price for a batch) are left out of prices."""
    sales = load_sales(cfg) if sales is None else sales
    out = {"n": 0, "window_months": int(cfg.targets.get("history_months", 12))}
    if sales is None or sales.empty:
        out["note"] = "no sales database - run `resell import` first"
        return out
    today = pd.Timestamp(today or cfg.as_of)
    df = sales[(sales.category == category) & (sales.is_stock == 0) & (sales.lost == 0)].copy()
    if match:
        df = df[df["item"].str.contains(match, case=False, regex=False)]
    if df.empty:
        out["note"] = f"no sales of {category}{' matching ' + repr(match) if match else ''}"
        return out
    df = df[~_bulk_mask(df)]
    start = today - pd.DateOffset(months=out["window_months"])
    recent = df[df.sell_date >= start]
    use = recent if len(recent) >= 3 else df
    out.update(
        n=len(use),
        window="last %d months" % out["window_months"] if use is recent else "all time",
        median_sell=float(use.price_sold.median()),
        median_sell_clean=float(use[use.flawed == 0].price_sold.median()) if (use.flawed == 0).any() else None,
        median_paid=float(use.price_paid.median()),
        median_days=float(use.days_to_sell.median()),
        flaw_rate=float(use.flawed.mean()),
    )
    # trend: least-squares slope over the window, as % change across 12 months vs the window mean
    # use clean units when there are enough, so a run of flawed units doesn't read as a falling market
    t = recent.dropna(subset=["sell_date", "price_sold"])
    if (t.flawed == 0).sum() >= 6:
        t = t[t.flawed == 0]
    span_days = (t.sell_date.max() - t.sell_date.min()).days if len(t) else 0
    if len(t) >= 6 and span_days >= 180:
        x = (t.sell_date - t.sell_date.min()).dt.days.to_numpy(dtype=float)
        slope = np.polyfit(x, t.price_sold.to_numpy(dtype=float), 1)[0]
        out["trend_12m"] = float(slope * 365 / t.price_sold.mean())
        out["trend_n"] = len(t)
        out["trend_basis"] = "clean units" if (t.flawed == 0).all() else "all units"
    return out


def recent_order_rate(cfg: Config, days: int = 60) -> float | None:
    path = cfg.path(cfg.paths.db)
    if not path.exists():
        return None
    with sqlite3.connect(path) as con:
        try:
            p = pd.read_sql("SELECT buy_date, eff_rate FROM purchases WHERE currency='CNY' AND eff_rate IS NOT NULL "
                            "AND excluded IS NULL", con)
        except Exception:
            return None
    p["buy_date"] = pd.to_datetime(p.buy_date)
    p = p[p.buy_date >= pd.Timestamp(cfg.as_of) - timedelta(days=days)]
    return float(p.eff_rate.median()) if len(p) else None


def live_rate(timeout: float = 3.0) -> float | None:
    """ECB reference EUR->CNY via frankfurter (no key). Informational only - config wins."""
    try:
        import httpx

        for url in ("https://api.frankfurter.dev/v1/latest?base=EUR&symbols=CNY",
                    "https://api.frankfurter.app/latest?from=EUR&to=CNY"):
            r = httpx.get(url, timeout=timeout)
            if r.status_code == 200:
                return float(r.json()["rates"]["CNY"])
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------------- core maths

def landed_cost(cfg: Config, inp: Inputs, rmb: float) -> Landed:
    c, prof = cfg.costs, profile(cfg, inp.category)
    rate = inp.rate or float(c["rmb_per_eur_effective"])
    goods = rmb / rate
    agent = float(c.get("agent_fee_per_item_eur", 0))
    weight = inp.weight_kg if inp.weight_kg is not None else float(prof.get("weight_kg", 0.3))
    shipping = max(float(c.get("shipping_min_eur", 0)), weight * float(c.get("shipping_eur_per_kg", 0)))
    qty = max(int(inp.qty or 1), 1)
    consignment = goods * qty
    if consignment <= float(c.get("customs_flat_threshold_eur", 150)):
        duty = float(c.get("customs_flat_per_tariff_line_eur", 0)) / qty
        duty_note = f"flat €{c.get('customs_flat_per_tariff_line_eur', 0)} per tariff line, shared by {qty}"
    else:
        duty = goods * float(prof.get("duty_rate", 0))
        duty_note = f"consignment > €{c.get('customs_flat_threshold_eur', 150)}: duty rate {prof.get('duty_rate', 0):.0%}"
    vat_on = inp.import_vat if inp.import_vat is not None else bool(prof.get("import_vat", c.get("import_vat_default", True)))
    vat = float(c.get("import_vat_rate", 0.19)) * (goods + shipping + duty) if vat_on else 0.0
    parts = float(inp.parts_eur or 0)
    total = goods + agent + shipping + duty + vat + parts
    return Landed(goods, agent, shipping, duty, vat, parts, total, rate, vat_on, duty_note)


def selling(cfg: Config, inp: Inputs, price: float, source: str) -> Selling:
    name, p = platform_cfg(cfg, inp.platform)
    fee = price * float(p.get("fee_pct", 0)) + float(p.get("fee_fixed_eur", 0))
    out = float(p.get("outbound_shipping_eur", 0))
    reserve = price * float(profile(cfg, inp.category).get("returns_reserve_pct", 0))
    return Selling(price, source, name, fee, out, reserve, price - fee - out - reserve)


def _break_even(cfg: Config, inp: Inputs, landed_total: float) -> float:
    _, p = platform_cfg(cfg, inp.platform)
    k = 1 - float(p.get("fee_pct", 0)) - float(profile(cfg, inp.category).get("returns_reserve_pct", 0))
    return (landed_total + float(p.get("fee_fixed_eur", 0)) + float(p.get("outbound_shipping_eur", 0))) / k


def _sell_price(cfg: Config, inp: Inputs, hist: dict) -> tuple[float, str]:
    if inp.sell_eur:
        return float(inp.sell_eur), "you entered it"
    if hist.get("median_sell"):
        return hist["median_sell"], f"median of your {hist['n']} sales ({hist['window']}, bulk sales excluded)"
    raise ValueError(f"no sell price given and no sales history for {inp.category}: pass a sell price")


# ----------------------------------------------------------------------------- 5 criteria

def score(cfg: Config, inp: Inputs, landed: Landed, profit: float, poc: float, hist: dict) -> list[dict]:
    t, prof = cfg.targets, profile(cfg, inp.category)
    crit = []

    d = prof.get("discontinued")
    crit.append({"name": "1. No longer manufactured",
                 "status": UNKNOWN if d is None else PASS if d else FAIL,
                 "reason": "not set in category_profiles" if d is None else
                 "discontinued, enthusiast demand" if d else "still in production"})

    if prof.get("hype"):
        crit.append({"name": "2. Not hype-driven", "status": FAIL,
                     "reason": "you left this category on purpose (hype: true)"})
    elif "trend_12m" in hist:
        tr = hist["trend_12m"]
        st = PASS if tr >= t.get("hype_trend_ok", -0.03) else FAIL if tr <= t.get("hype_trend_fail", -0.10) else BORDERLINE
        crit.append({"name": "2. Not hype-driven", "status": st,
                     "reason": f"your sell price trend over 12 months: {tr:+.0%} "
                               f"({hist['trend_n']} sales, {hist.get('trend_basis', 'all units')})"})
    else:
        crit.append({"name": "2. Not hype-driven", "status": UNKNOWN,
                     "reason": "not enough of your own sales over 6+ months - needs EU sold data (Phase 4)"})

    edge = prof.get("china_edge")
    crit.append({"name": "3. China version is better",
                 "status": {"modded": PASS, "refurbished": PASS, "arbitrage": BORDERLINE, "none": FAIL}.get(edge, UNKNOWN),
                 "reason": {"modded": "modded/upgraded units beat EU supply",
                            "refurbished": "refurbished units beat EU supply",
                            "arbitrage": "price arbitrage only (weaker)",
                            "none": "no advantage over EU supply"}.get(edge, "china_edge not set")})

    test = prof.get("testability")
    st = {"easy": PASS, "medium": BORDERLINE, "hard": FAIL}.get(test, UNKNOWN)
    reason = {"easy": "verifiable from photos, testable in minutes", "medium": "partly verifiable",
              "hard": "can't verify before buying / test quickly"}.get(test, "testability not set")
    fr = hist.get("flaw_rate")
    if st == PASS and fr is not None and fr > 0.4 and hist.get("n", 0) >= 10:
        st, reason = BORDERLINE, f"easy to test, but {fr:.0%} of your units arrived flawed - use the seller checklist"
    crit.append({"name": "4. Easy to verify and test", "status": st, "reason": reason})

    min_p, good_p, min_poc = t.get("min_profit_eur", 50), t.get("good_profit_eur", 100), t.get("min_profit_on_cost", 0.5)
    if profit >= min_p and poc >= min_poc:
        st, reason = PASS, f"€{profit:.0f} profit, {poc:.0%} on cost" + (" (great)" if profit >= good_p else "")
    elif profit >= min_p and poc >= t.get("min_profit_on_cost_borderline", 0.4):
        st, reason = BORDERLINE, f"€{profit:.0f} profit is fine, but {poc:.0%} on cost is under {min_poc:.0%}"
    elif (landed.total <= t.get("cheap_item_max_cost_eur", 40) and profit >= t.get("cheap_item_min_profit_eur", 20)
          and (hist.get("median_days") is None or hist["median_days"] <= t.get("cheap_item_max_days", 90))):
        st, reason = BORDERLINE, f"only €{profit:.0f} profit, but a cheap, low-risk item ({poc:.0%} on cost)"
    else:
        misses = []
        if profit < min_p:
            misses.append(f"€{profit:.0f} profit < €{min_p:.0f}")
        if poc < min_poc:
            misses.append(f"{poc:.0%} on cost < {min_poc:.0%}")
        st, reason = FAIL, ", ".join(misses)
    crit.append({"name": "5. Profit target", "status": st, "reason": reason})
    return crit


def verdict(crit: list[dict]) -> tuple[str, str]:
    fails = [c for c in crit if c["status"] == FAIL]
    soft = [c for c in crit if c["status"] in (BORDERLINE, UNKNOWN)]
    if fails:
        return FAIL, "; ".join(f"{c['name'][3:]}: {c['reason']}" for c in fails)
    if soft:
        return BORDERLINE, "; ".join(f"{c['name'][3:]}: {c['reason']}" for c in soft)
    return PASS, "meets all five criteria"


# ----------------------------------------------------------------------------- entry points

def calculate(cfg: Config, inp: Inputs, sales: pd.DataFrame | None = None) -> Result:
    if inp.rmb is None:
        raise ValueError("rmb price required")
    hist = history(cfg, inp.category, inp.match, sales)
    landed = landed_cost(cfg, inp, inp.rmb)
    price, source = _sell_price(cfg, inp, hist)
    sell = selling(cfg, inp, price, source)
    profit = sell.net - landed.total
    poc = profit / landed.total if landed.total else 0.0
    res = Result(inp, landed, sell, profit, poc, profit / price if price else 0.0,
                 _break_even(cfg, inp, landed.total), hist)
    res.criteria = score(cfg, inp, landed, profit, poc, hist)
    res.verdict, res.verdict_reason = verdict(res.criteria)
    return res


@dataclass
class MaxBuy:
    max_rmb: float
    binding: str              # which target sets the limit
    at_max: Result
    min_profit_eur: float
    min_profit_on_cost: float


def max_rmb(cfg: Config, inp: Inputs, min_profit: float | None = None, min_poc: float | None = None,
            sales: pd.DataFrame | None = None) -> MaxBuy:
    """Highest RMB price per unit that still meets both targets at the expected sell price."""
    t = cfg.targets
    min_profit = t.get("min_profit_eur", 50) if min_profit is None else min_profit
    min_poc = t.get("min_profit_on_cost", 0.5) if min_poc is None else min_poc
    hist = history(cfg, inp.category, inp.match, sales)
    price, source = _sell_price(cfg, inp, hist)
    sell = selling(cfg, inp, price, source)

    def ok(rmb: float) -> bool:
        lt = landed_cost(cfg, inp, rmb).total
        prof = sell.net - lt
        return prof >= min_profit and prof / lt >= min_poc

    lo, hi = 0.0, price * float(inp.rate or cfg.costs["rmb_per_eur_effective"]) * 2
    if not ok(lo):
        lo_res = calculate(cfg, Inputs(**{**asdict(inp), "rmb": 0.0, "sell_eur": price}), sales)
        return MaxBuy(0.0, "unreachable: even at ¥0 the fixed costs miss the targets", lo_res, min_profit, min_poc)
    for _ in range(60):  # cost rises with RMB (bar a few-euro customs step at the €150 threshold)
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    best = np.floor(lo)
    res = calculate(cfg, Inputs(**{**asdict(inp), "rmb": float(best), "sell_eur": price}), sales)
    res.selling.price_source = source
    # which target binds: whichever is closer to its limit at the max price
    by_profit = res.profit - min_profit
    by_poc = (res.profit_on_cost - min_poc) * res.landed.total
    binding = (f"€{min_profit:.0f} minimum profit" if by_profit <= by_poc
               else f"{min_poc:.0%} profit on cost")
    return MaxBuy(float(best), binding, res, min_profit, min_poc)
