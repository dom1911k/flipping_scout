"""Phase 0 analysis report: markdown + PNG charts."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from jinja2 import Environment, PackageLoader  # noqa: E402
from scipy.stats import fisher_exact  # noqa: E402

from .config import Config  # noqa: E402

# Reference palette (light mode) - one hue per series, fixed order; text never in series colour.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
MUTED = "#b9b8b3"

# Your own numbers from the brief, to check against the sheet.
STATED = {
    "sales": 305, "revenue": 68000, "profit": 20300, "margin": 0.30, "profit_on_cost": 0.43,
    "n3ds_sold": 145, "n3ds_buy": 188, "n3ds_sell": 269, "n3ds_profit": 81, "n3ds_days": 57, "n3ds_flaw": 0.30,
    "ipod_sold": 54, "ipod_profit": 72, "ipod_poc": 0.49, "ipod_flaw": 0.06,
}


# ----------------------------------------------------------------------------- formatting

def eur(x, nd=0):
    return "–" if x is None or pd.isna(x) else f"€{x:,.{nd}f}"


def pct(x, nd=0):
    return "–" if x is None or pd.isna(x) else f"{x * 100:.{nd}f}%"


def num(x, nd=0):
    return "–" if x is None or pd.isna(x) else f"{x:,.{nd}f}"


def md_table(df: pd.DataFrame, align: str | None = None) -> str:
    if df.empty:
        return "_none_"
    cols = list(df.columns)
    align = align or ("l" + "r" * (len(cols) - 1))
    sep = ["---:" if a == "r" else ":---" for a in align]
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(esc(c) for c in cols) + " |", "| " + " | ".join(sep) + " |"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(esc(v) for v in row) + " |")
    return "\n".join(lines)


def _style(ax, title: str, ylabel: str | None = None):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=11, color=INK, pad=10)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=INK2, labelsize=8, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=8)


def _save(fig, out: Path, name: str) -> str:
    fig.tight_layout()
    fig.savefig(out / name, dpi=150)
    plt.close(fig)
    return name


# ----------------------------------------------------------------------------- analysis

def summarise(g: pd.DataFrame) -> dict:
    closed = g[g.is_stock == 0]                 # sold + lost: both count in profit
    sold = closed[closed.lost == 0]             # real sales: prices, days, flaws
    paid, rev, prof = closed.price_paid.sum(), closed.price_sold.sum(), closed.profit_recalc.sum()
    return {
        "sold": len(sold), "stock": int((g.is_stock == 1).sum()), "lost": int(g.lost.sum()),
        "revenue": rev, "profit": prof,
        "avg_buy": sold.price_paid.mean(), "avg_sell": sold.price_sold.mean(), "avg_profit": sold.profit_recalc.mean(),
        "profit_on_cost": prof / paid if paid else np.nan,
        "margin": prof / rev if rev else np.nan,
        "median_days": sold.days_to_sell.median(),
        "flaw_rate": sold.flawed.mean() if len(sold) else np.nan,
        "major_flaw_rate": (sold.flawed.astype(bool) & ~sold.flaw_minor.astype(bool)).mean() if len(sold) else np.nan,
    }


def build_report(sales: pd.DataFrame, purchases: pd.DataFrame, issues: pd.DataFrame,
                 evidence: dict, cfg: Config) -> Path:
    as_of = pd.Timestamp(cfg.as_of)
    out = cfg.path(cfg.paths.reports) / "phase0"
    out.mkdir(parents=True, exist_ok=True)

    s = sales.copy()
    s["buy_date"] = pd.to_datetime(s["buy_date"])
    s["sell_date"] = pd.to_datetime(s["sell_date"])
    s["buy_q"] = s.buy_date.dt.to_period("Q").astype(str)
    s["sell_year"] = s.sell_date.dt.year.astype("Int64").astype(str).replace("<NA>", "")
    # bulk sales: >= 4 units of one category sold on the same day on the same platform
    key = [s.sell_date, s.platform, s.category]
    s["bulk"] = (s.is_stock == 0) & (s.groupby(key).item.transform("size") >= 4)
    sold = s[(s.is_stock == 0) & (s.lost == 0)]
    ctx: dict = {"findings": (out / "findings.md").exists(), "as_of": as_of.date(), "generated": date.today(), "workbook": cfg.paths.workbook}

    # ---- 1. check stated numbers
    tot = summarise(s)
    n3 = summarise(s[s.category == "N3DS"])
    ip = summarise(s[s.category == "iPod"])
    check = [
        ("Units sold", STATED["sales"], tot["sold"], num),
        ("Revenue", STATED["revenue"], tot["revenue"], eur),
        ("Profit", STATED["profit"], tot["profit"], eur),
        ("Margin on revenue", STATED["margin"], tot["margin"], pct),
        ("Profit on cost", STATED["profit_on_cost"], tot["profit_on_cost"], pct),
        ("N3DS sold", STATED["n3ds_sold"], n3["sold"], num),
        ("N3DS avg buy", STATED["n3ds_buy"], n3["avg_buy"], eur),
        ("N3DS avg sell", STATED["n3ds_sell"], n3["avg_sell"], eur),
        ("N3DS avg profit", STATED["n3ds_profit"], n3["avg_profit"], eur),
        ("N3DS median days to sell", STATED["n3ds_days"], n3["median_days"], num),
        ("N3DS flaw rate (any)", STATED["n3ds_flaw"], n3["flaw_rate"], pct),
        ("iPods sold", STATED["ipod_sold"], ip["sold"], num),
        ("iPod avg profit", STATED["ipod_profit"], ip["avg_profit"], eur),
        ("iPod profit on cost", STATED["ipod_poc"], ip["profit_on_cost"], pct),
        ("iPod flaw rate (any)", STATED["ipod_flaw"], ip["flaw_rate"], pct),
    ]
    ctx["check"] = md_table(pd.DataFrame([(a, f(b), f(c)) for a, b, c, f in check],
                                         columns=["Metric", "Your number", "From the sheet"]))
    ctx["lost"] = s[s.lost == 1]
    ctx["lost_loss"] = -s[s.lost == 1].profit_recalc.sum()
    ctx["tot"] = {k: (eur(v) if k in ("revenue", "profit") else v) for k, v in tot.items()}
    ctx["n3"], ctx["ip"] = n3, ip
    ctx["fmt"] = {"eur": eur, "pct": pct, "num": num}

    # ---- 2. per category
    rows = []
    for cat, g in s.groupby("category"):
        m = summarise(g)
        rows.append({"Category": cat, "Sold": m["sold"], "Stock": m["stock"], "Lost": m["lost"], "Revenue": m["revenue"],
                     "Profit": m["profit"], "Avg profit": m["avg_profit"], "Profit on cost": m["profit_on_cost"],
                     "Margin": m["margin"], "Median days": m["median_days"], "Flaw rate": m["flaw_rate"],
                     "Major flaws": m["major_flaw_rate"]})
    cat_df = pd.DataFrame(rows).sort_values("Profit", ascending=False)
    ctx["categories"] = md_table(cat_df.assign(
        Revenue=cat_df.Revenue.map(eur), Profit=cat_df.Profit.map(eur), **{
            "Avg profit": cat_df["Avg profit"].map(eur), "Profit on cost": cat_df["Profit on cost"].map(pct),
            "Margin": cat_df.Margin.map(pct), "Median days": cat_df["Median days"].map(num),
            "Flaw rate": cat_df["Flaw rate"].map(pct), "Major flaws": cat_df["Major flaws"].map(pct)}))

    fig, ax = plt.subplots(figsize=(7, 3.4))
    c = cat_df.sort_values("Profit")
    ax.barh(c.Category, c.Profit, color=SERIES[0], height=0.6)
    for y, (v, n) in enumerate(zip(c.Profit, c.Sold)):
        ax.text(v + 150, y, f"{eur(v)}  ({n} sold)", va="center", fontsize=8, color=INK2)
    _style(ax, "Total profit by category")
    ax.grid(axis="x", color=GRID); ax.grid(axis="y", visible=False)
    ax.set_xlim(0, c.Profit.max() * 1.3)
    ctx["chart_categories"] = _save(fig, out, "profit_by_category.png")

    # ---- 3. N3DS flaws over time, grade vs flaw, price impact
    n = sold[sold.category == "N3DS"].copy()
    n["half"] = n.buy_date.dt.year.astype(str) + " H" + ((n.buy_date.dt.month > 6) + 1).astype(str)
    fq = n.groupby("half").agg(units=("flawed", "size"), flawed=("flawed", "sum"),
                               major=("flaw_minor", lambda x: int(((n.loc[x.index, "flawed"] == 1) & (x == 0)).sum())))
    fq["rate"] = fq.flawed / fq.units
    fq["major_rate"] = fq.major / fq.units
    ctx["n3_flaw_time"] = md_table(fq.reset_index().assign(rate=fq.rate.map(pct).values,
                                                           major_rate=fq.major_rate.map(pct).values)
                                   .rename(columns={"half": "Bought in", "units": "Units sold", "flawed": "Flawed",
                                                    "major": "Major", "rate": "Flaw rate", "major_rate": "Major rate"}))
    fig, ax = plt.subplots(figsize=(7, 3))
    x = np.arange(len(fq))
    ax.bar(x - 0.18, fq.rate, 0.34, color=SERIES[0], label="any flaw")
    ax.bar(x + 0.18, fq.major_rate, 0.34, color=SERIES[1], label="major flaw")
    for i, (r, u) in enumerate(zip(fq.rate, fq.units)):
        ax.text(i - 0.18, r + 0.015, pct(r), ha="center", fontsize=8, color=INK2)
    ax.set_xticks(x, [f"{h}\n(n={u})" for h, u in zip(fq.index, fq.units)])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, "N3DS: share of sold units that arrived with a flaw, by purchase half-year")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ctx["chart_n3_flaw"] = _save(fig, out, "n3ds_flaw_rate.png")

    tags = n.assign(tag=n.flaw_tags.str.split(",")).explode("tag").dropna(subset=["tag"]).reset_index(drop=True)
    tag_tab = pd.crosstab(tags.tag, tags.buy_date.dt.year)
    tag_tab.columns = [str(c) for c in tag_tab.columns]
    tag_tab["total"] = tag_tab.sum(axis=1)
    ctx["n3_tags"] = md_table(tag_tab.sort_values("total", ascending=False).reset_index()
                              .rename(columns={"tag": "Flaw type"}).astype(str))

    def bucket(g):
        if pd.isna(g):
            return "no grade"
        g = int(g)
        return "≤93" if g <= 93 else "94-96" if g <= 96 else "97" if g == 97 else "98" if g == 98 else "99"
    n["grade_bucket"] = n.grade.map(bucket)
    order = ["≤93", "94-96", "97", "98", "99", "no grade"]
    n["dust_or_shell"] = n.flaw_tags.fillna("").str.contains("dust_debris|shell_serial|yellow").astype(int)
    gb = n.groupby("grade_bucket").agg(units=("flawed", "size"), flaw_rate=("flawed", "mean"),
                                        dust_shell_yellow=("dust_or_shell", "mean"),
                                        avg_paid=("price_paid", "mean"), avg_profit=("profit_recalc", "mean"))
    gb = gb.reindex([o for o in order if o in gb.index])
    ctx["grade_table"] = md_table(gb.reset_index().assign(
        flaw_rate=gb.flaw_rate.map(pct).values, dust_shell_yellow=gb.dust_shell_yellow.map(pct).values,
        avg_paid=gb.avg_paid.map(eur).values, avg_profit=gb.avg_profit.map(eur).values)
        .rename(columns={"grade_bucket": "Listed grade", "units": "Units", "flaw_rate": "Any flaw",
                         "dust_shell_yellow": "Dust/shell/yellow", "avg_paid": "Avg paid", "avg_profit": "Avg profit"}))

    def fisher(df, col):
        hi = df[df.grade >= 98][col]
        lo = df[(df.grade <= 97) & df.grade.notna()][col]
        if len(hi) == 0 or len(lo) == 0:
            return None
        table = [[int(hi.sum()), int(len(hi) - hi.sum())], [int(lo.sum()), int(len(lo) - lo.sum())]]
        odds, p = fisher_exact(table)
        return {"hi_n": len(hi), "hi_rate": hi.mean(), "lo_n": len(lo), "lo_rate": lo.mean(), "odds": odds, "p": p}

    ctx["fisher_all"] = fisher(n, "flawed")
    ctx["fisher_dust"] = fisher(n, "dust_or_shell")
    n26 = n[n.buy_date >= "2025-10-01"]
    ctx["fisher_recent"] = fisher(n26, "flawed")
    ctx["fisher_recent_dust"] = fisher(n26, "dust_or_shell")

    fig, ax = plt.subplots(figsize=(7, 3))
    ax.bar(range(len(gb)), gb.flaw_rate, color=SERIES[0], width=0.6)
    for i, (r, u) in enumerate(zip(gb.flaw_rate, gb.units)):
        ax.text(i, r + 0.015, pct(r), ha="center", fontsize=8, color=INK2)
    ax.set_xticks(range(len(gb)), [f"{b}\n(n={u})" for b, u in zip(gb.index, gb.units)])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, "N3DS: flaw rate by the grade the seller listed")
    ctx["chart_grade"] = _save(fig, out, "n3ds_grade_vs_flaw.png")

    # price impact of flaws, sold in 2026
    bulk = s[s.bulk].groupby([s.sell_date.dt.date, "platform", "category"]).agg(
        units=("item", "size"), price=("price_sold", lambda x: ", ".join(sorted({eur(v) for v in x}))),
        flawed=("flawed", "sum"), rows=("sheet_row", lambda x: ", ".join(map(str, x))))
    ctx["bulk"] = md_table(bulk.reset_index().rename(columns={"sell_date": "Sold", "platform": "Platform",
                           "category": "Category", "units": "Units", "price": "Price each",
                           "flawed": "Of which flawed", "rows": "Rows"}), "lllrrrl")
    s26 = n[(n.sell_date >= "2026-01-01") & ~n.sheet_row.isin(s[s.bulk].sheet_row)].copy()
    s26["state"] = np.where(s26.flawed == 0, "clean", np.where(s26.flaw_minor == 1, "minor flaw", "major flaw"))
    imp = s26.groupby("state").agg(units=("price_sold", "size"), avg_sold=("price_sold", "mean"),
                                   median_sold=("price_sold", "median"), avg_profit=("profit_recalc", "mean"))
    ctx["flaw_price"] = md_table(imp.reset_index().assign(
        avg_sold=imp.avg_sold.map(eur).values, median_sold=imp.median_sold.map(eur).values,
        avg_profit=imp.avg_profit.map(eur).values)
        .rename(columns={"state": "Condition", "units": "Units", "avg_sold": "Avg sold", "median_sold": "Median sold",
                         "avg_profit": "Avg profit"}))
    if {"clean", "major flaw"} <= set(imp.index):
        ctx["flaw_cost"] = imp.loc["clean", "avg_sold"] - imp.loc["major flaw", "avg_sold"]
    if {"clean", "minor flaw"} <= set(imp.index):
        ctx["flaw_cost_minor"] = imp.loc["clean", "avg_sold"] - imp.loc["minor flaw", "avg_sold"]

    # serials
    ser = s[(s.category == "N3DS") & s.serial.notna()].copy()
    ser["serial_norm"] = ser.serial.str.replace(" ", "", regex=False).str.upper()
    ser["prefix"] = ser.serial_norm.str.extract(r"^([A-Z]+)")[0]
    ctx["serial_prefix"] = md_table(ser.groupby("prefix").agg(units=("flawed", "size"), flaw_rate=("flawed", "mean"))
                                    .reset_index().assign(flaw_rate=lambda d: d.flaw_rate.map(pct))
                                    .rename(columns={"prefix": "Serial prefix", "units": "Units", "flaw_rate": "Flaw rate"}))
    dup = ser[ser.duplicated("serial_norm", keep=False)].sort_values(["serial_norm", "buy_date"])
    ctx["serial_dups"] = md_table(dup.assign(buy_date=dup.buy_date.dt.date)[
        ["serial", "sheet_row", "item", "buy_date", "seller", "comment"]].fillna("")
        .rename(columns={"serial": "Serial", "sheet_row": "Row", "item": "Item", "buy_date": "Bought",
                         "seller": "Seller", "comment": "Comment"}), "llllll")

    # ---- 4. sellers
    sold_s = sold.copy()
    sel = sold_s.groupby(sold_s.seller.fillna("(unknown)")).agg(
        units=("item", "size"), categories=("category", lambda x: ", ".join(sorted(set(x)))),
        flaw_rate=("flawed", "mean"), avg_profit=("profit_recalc", "mean"),
        poc=("profit_recalc", "sum"), paid=("price_paid", "sum"), last=("buy_date", "max"),
        confidence=("seller_confidence", lambda x: ", ".join(f"{k} {v}" for k, v in x.value_counts().items())))
    sel["poc"] = sel.poc / sel.paid
    sel = sel.sort_values("units", ascending=False)
    singles = sel[sel.units == 1]
    ctx["single_sellers"] = len(singles)
    sel = sel[sel.units > 1]
    ctx["sellers"] = md_table(sel.reset_index().assign(
        flaw_rate=sel.flaw_rate.map(pct).values, avg_profit=sel.avg_profit.map(eur).values,
        poc=sel.poc.map(pct).values, last=sel["last"].dt.date.values).drop(columns="paid")
        .rename(columns={"seller": "Seller", "units": "Sold", "categories": "Categories", "flaw_rate": "Flaw rate",
                         "avg_profit": "Avg profit", "poc": "Profit on cost", "last": "Last bought",
                         "confidence": "How identified"}), "lrlrrrll")
    n_sel = n.groupby(n.seller.fillna("(unknown)")).agg(units=("item", "size"), flaw_rate=("flawed", "mean"),
                                                        major=("flaw_minor", "size"))
    ctx["learned"] = md_table(pd.DataFrame(
        [(k.replace("storefront ", ""), v, sum(evidence["votes"][k].values())) for k, v in evidence["learned"].items()],
        columns=["Link key", "Shop", "Rows agreeing"]), "llr")
    ctx["conf_counts"] = sales.seller_confidence.value_counts().to_dict()

    # ---- 5. buy price trend & exchange rate
    trend = sold_s[sold_s.category.isin(["N3DS", "iPod", "Vita card"])]
    allq = s[s.category.isin(["N3DS", "iPod", "Vita card"])]
    bq = allq.groupby(["buy_q", "category"]).price_paid.median().unstack()
    cnt = allq.groupby(["buy_q", "category"]).size().unstack()
    ctx["buy_trend"] = md_table(pd.concat({"median paid": bq.map(eur), "n": cnt.fillna(0).astype(int)}, axis=1)
                                .reset_index().rename(columns={"buy_q": "Quarter"}).pipe(
                                    lambda d: d.set_axis([" ".join(c).strip() if isinstance(c, tuple) else c
                                                          for c in d.columns], axis=1)))
    fig, ax = plt.subplots(figsize=(7, 3.2))
    for i, cat in enumerate(["N3DS", "iPod"]):
        if cat in bq:
            y = bq[cat]
            ax.plot(range(len(y)), y.values, color=SERIES[i], linewidth=2, marker="o", markersize=4, label=cat)
            last = y.last_valid_index()
            ax.text(list(y.index).index(last) + 0.1, y[last], f"{cat} {eur(y[last])}", fontsize=8, color=INK2, va="center")
    ax.set_xticks(range(len(bq)), bq.index, fontsize=7)
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("€{x:,.0f}"))
    _style(ax, "Median landed price paid per unit, by purchase quarter")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ctx["chart_buy"] = _save(fig, out, "buy_price_trend.png")

    sell_q = sold_s[sold_s.category.isin(["N3DS", "iPod"])].assign(sq=lambda d: d.sell_date.dt.to_period("Q").astype(str))
    sq = sell_q.groupby(["sq", "category"]).price_sold.median().unstack()
    sell_q = sell_q[~sell_q.bulk]
    ctx["sell_trend"] = md_table(sq.map(eur).reset_index().rename(columns={"sq": "Sold in quarter"}))

    p = purchases[(purchases.currency == "CNY") & purchases.eff_rate.notna() & purchases.excluded.isna()].copy()
    p["month"] = pd.to_datetime(p.buy_date).dt.to_period("M")
    rate = p.dropna(subset=["month"]).groupby("month").agg(rate=("eff_rate", "median"), lines=("eff_rate", "size"))
    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.plot(range(len(rate)), rate.rate, color=SERIES[0], linewidth=2, marker="o", markersize=4)
    ax.set_xticks(range(len(rate)), [str(m) for m in rate.index], fontsize=7, rotation=45)
    _style(ax, "Effective RMB per € on your orders (median per month; higher = better for you)")
    ctx["chart_rate"] = _save(fig, out, "rmb_per_eur.png")
    ctx["rate_table"] = md_table(rate.reset_index().assign(month=lambda d: d.month.astype(str),
                                                           rate=lambda d: d.rate.map(lambda v: f"{v:.2f}"))
                                 .rename(columns={"month": "Month", "rate": "RMB per €", "lines": "Order lines"}))

    # ---- 6. stock aging
    stock = s[s.is_stock == 1].copy()
    stock["age"] = (as_of - stock.buy_date).dt.days
    stock = stock.sort_values("age", ascending=False)
    ctx["stock_value"] = stock.price_paid.sum()
    ctx["stock_n"] = len(stock)
    ctx["stock_old_n"] = int((stock.age > cfg.stock_aging_days).sum())
    ctx["stock"] = md_table(stock.assign(buy_date=stock.buy_date.dt.date, price_paid=stock.price_paid.map(eur),
                                         flag=np.where(stock.age > cfg.stock_aging_days, "slow", ""))[
        ["sheet_row", "item", "category", "buy_date", "age", "price_paid", "flag", "comment"]].fillna("")
        .rename(columns={"sheet_row": "Row", "item": "Item", "category": "Category", "buy_date": "Bought",
                         "age": "Days", "price_paid": "Paid", "flag": "", "comment": "Comment"}), "rlllrrll")

    # ---- 7. platforms & eBay caps
    plat = sold_s.groupby([sold_s.sell_year, "platform"]).agg(units=("item", "size"), revenue=("price_sold", "sum"))
    ctx["platforms"] = md_table(plat.reset_index().assign(revenue=lambda d: d.revenue.map(eur))
                                .rename(columns={"sell_year": "Year", "platform": "Platform", "units": "Units",
                                                 "revenue": "Revenue"}), "llrr")
    eb = sold_s[sold_s.platform == "eBay"].groupby(["account", "sell_year"]).agg(
        units=("item", "size"), revenue=("price_sold", "sum")).reset_index()
    eb["cap_used"] = eb.revenue / cfg.ebay_account_revenue_cap_eur
    ctx["ebay"] = md_table(eb.assign(revenue=eb.revenue.map(eur), cap_used=eb.cap_used.map(pct),
                                     flag=np.where(eb.cap_used >= 1, "⚠ over", np.where(eb.cap_used >= 0.8, "close", "")))
                           .rename(columns={"account": "Account", "sell_year": "Year", "units": "Units",
                                            "revenue": "Revenue", "cap_used": f"of €{cfg.ebay_account_revenue_cap_eur:,.0f}",
                                            "flag": ""}), "llrrrl")
    ctx["ebay_over"] = eb[eb.cap_used >= 1].to_dict("records")

    # ---- 8. days to sell
    dts = sold_s.groupby("category").days_to_sell.describe(percentiles=[0.25, 0.5, 0.75])[["count", "25%", "50%", "75%", "max"]]
    ctx["days"] = md_table(dts.reset_index().assign(**{c: dts[c].map(num).values for c in dts.columns})
                           .rename(columns={"category": "Category", "count": "Sold", "25%": "25th pct",
                                            "50%": "Median", "75%": "75th pct", "max": "Slowest"}))

    # ---- 9. inconsistencies
    inc = []
    for r in s.itertuples():
        if r.profit is not None and not pd.isna(r.profit) and not pd.isna(r.profit_recalc) and abs(r.profit - r.profit_recalc) > 0.5:
            inc.append((r.sheet_row, r.item, f"Profit {eur(r.profit, 2)} ≠ sold − paid {eur(r.profit_recalc, 2)}"))
        if r.is_stock == 0 and r.price_sold == 0 and not r.lost:
            inc.append((r.sheet_row, r.item, "Price Sold is 0 with a selling date (lost / refunded / given away?)"))
        if r.is_stock == 0 and pd.isna(r.price_sold):
            inc.append((r.sheet_row, r.item, "Selling date but no price sold"))
        if pd.notna(r.price_sold) and pd.isna(r.sell_date):
            inc.append((r.sheet_row, r.item, "Price sold but no selling date"))
        if pd.notna(r.sell_date) and pd.notna(r.buy_date) and r.sell_date < r.buy_date:
            inc.append((r.sheet_row, r.item, "Sold before it was bought"))
        if r.lost:
            continue
        if r.is_stock == 0 and pd.isna(r.profitability) and pd.notna(r.price_sold):
            inc.append((r.sheet_row, r.item, "Profitability cell empty"))
        elif (r.is_stock == 0 and pd.notna(r.profitability) and r.price_paid
              and abs(r.profitability - r.profit_recalc / r.price_paid) > 0.01):
            inc.append((r.sheet_row, r.item, f"Profitability {pct(r.profitability)} ≠ profit/paid "
                        f"{pct(r.profit_recalc / r.price_paid)}"))
        if r.is_stock == 0 and r.platform not in {"Kleinanzeigen", "eBay", "Vinted"} and r.platform:
            inc.append((r.sheet_row, r.item, f"Sold via '{r.selling_place}' (not one of your three platforms)"))
    for r in dup.itertuples():
        inc.append((r.sheet_row, r.item, f"Serial {r.serial} appears on more than one unit"))
    ctx["inconsistencies"] = md_table(pd.DataFrame(inc, columns=["Row", "Item", "What looks off"])
                                      .drop_duplicates().sort_values("Row"), "rll")

    pu = purchases[(purchases.kind == "unit") & purchases.excluded.isna()].copy()
    pu["buy_date"] = pd.to_datetime(pu.buy_date)
    first_sale_buy = s.buy_date.min()
    unmatched = pu[pu.sales_row.isna() & (pu.buy_date >= first_sale_buy)]
    ctx["unmatched_after"] = md_table(unmatched.assign(buy_date=unmatched.buy_date.dt.date,
                                                       total_eur=unmatched.total_eur.map(lambda v: eur(v, 2)))[
        ["sheet", "sheet_row", "item", "buy_date", "qty", "total_eur", "note"]].fillna("")
        .rename(columns={"sheet": "Tab", "sheet_row": "Row", "item": "Item", "buy_date": "Date", "qty": "Qty",
                         "total_eur": "Landed", "note": "Note"}), "lrllrrl")
    ctx["unmatched_n"] = len(unmatched)
    ctx["pre_sales_units"] = int((pu.buy_date < first_sale_buy).sum())
    ctx["first_sale_buy"] = first_sale_buy.date()
    matched_rows = set(purchases.sales_row.dropna().astype(int))
    for extra in purchases.attrs.get("extra_matches", {}).values():
        matched_rows.update(extra)
    ctx["sales_unmatched_n"] = int((~s.sheet_row.isin(matched_rows)).sum())
    ctx["purchases_n"] = len(purchases)
    ctx["purchase_units_n"] = len(pu)
    ctx["matched_n"] = len(matched_rows)

    # ---- 10. flaw keyword review
    ctx["keywords"] = cfg.flaw_keywords
    ctx["minor_words"] = cfg.flaw_minor_words
    ctx["minor_followups"] = cfg.flaw_minor_followups
    ctx["always_major"] = cfg.flaw_always_major
    ctx["negations"] = cfg.flaw_negations
    fl = s[s.flawed == 1]
    ctx["flagged"] = md_table(fl.assign(minor=np.where(fl.flaw_minor == 1, "minor", "major"))[
        ["sheet_row", "item", "flaw_tags", "minor", "flaw_matches", "comment"]].fillna("")
        .rename(columns={"sheet_row": "Row", "item": "Item", "flaw_tags": "Tags", "minor": "Severity",
                         "flaw_matches": "Matched", "comment": "Comment"}), "rlllll")
    nf = s[(s.flawed == 0) & s.comment.notna()]
    ctx["not_flagged"] = md_table(nf[["sheet_row", "item", "comment"]]
                                  .rename(columns={"sheet_row": "Row", "item": "Item", "comment": "Comment"}), "rll")

    ctx["issues"] = md_table(issues[["sheet", "cells", "reason", "raw"]].assign(raw=issues.raw.str[:140])
                             .rename(columns={"sheet": "Tab", "cells": "Cells", "reason": "Why", "raw": "Content"}),
                             "llll") if len(issues) else "_none_"

    env = Environment(loader=PackageLoader("resell", "templates"), trim_blocks=True, lstrip_blocks=True)
    md = env.get_template("report.md.j2").render(**ctx)
    path = out / "report.md"
    path.write_text(md, encoding="utf-8")
    return path
