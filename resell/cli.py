"""Command line entry point: `resell import`, `resell report`."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd
import typer

from . import db, enrich
from .config import load_config
from .importers.order_sheets import read_orders
from .importers.sales_sheet import read_sales

app = typer.Typer(add_completion=False, help="resell-toolkit: local tools for the reselling business")

ConfigOpt = typer.Option(Path("config.yaml"), "--config", "-c", help="Path to config.yaml")


def _load(cfg):
    wb = openpyxl.load_workbook(cfg.path(cfg.paths.workbook), data_only=True, read_only=False)
    sales, issues = read_sales(wb, cfg.sales_sheet)
    purchases, p_issues = read_orders(wb, cfg.order_sheet_prefix)
    sales = enrich.enrich_sales(sales, cfg)
    purchases = enrich.enrich_purchases(purchases, cfg)
    purchases = enrich.match_purchases(sales, purchases, cfg)
    sales, evidence = enrich.resolve_sellers(sales, purchases, cfg)
    return sales, purchases, pd.DataFrame(issues + p_issues, columns=["sheet", "cells", "raw", "reason"]), evidence


@app.command("import")
def import_cmd(config: Path = ConfigOpt):
    """Read the workbook (read-only) and rebuild the local SQLite database."""
    cfg = load_config(config)
    sales, purchases, issues, _ = _load(cfg)
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        db.reset(con)
        db.write(con, "sales", sales)
        db.write(con, "purchases", purchases)
        db.write(con, "parse_issues", issues.astype(str))
    typer.echo(f"sales: {len(sales)} rows ({int((sales.is_stock == 0).sum())} sold, {int(sales.is_stock.sum())} stock)")
    typer.echo(f"purchases: {len(purchases)} order lines, {int(purchases.sales_row.notna().sum())} matched to sales rows")
    typer.echo(f"parse issues logged: {len(issues)}  ->  {cfg.path(cfg.paths.db)}")


@app.command()
def report(config: Path = ConfigOpt):
    """Import, then write the Phase 0 analysis report (markdown + charts)."""
    from .report import build_report

    cfg = load_config(config)
    sales, purchases, issues, evidence = _load(cfg)
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        db.reset(con)
        db.write(con, "sales", sales)
        db.write(con, "purchases", purchases)
        db.write(con, "parse_issues", issues.astype(str))
    path = build_report(sales, purchases, issues, evidence, cfg)
    typer.echo(f"report written to {path}")


if __name__ == "__main__":
    app()


# ----------------------------------------------------------------------------- Phase 1

def _money(v):
    return f"€{v:,.2f}"


def _print_result(r, rate_hint: str = ""):
    from .calc import FAIL, PASS

    L, S = r.landed, r.selling
    typer.echo(f"\n{r.inputs.category}: ¥{r.inputs.rmb:,.0f} at {L.rate:.2f} RMB/€{rate_hint}")
    rows = [("Goods", L.goods), ("Agent fee", L.agent_fee), ("Intl shipping", L.shipping),
            (f"Customs ({L.duty_note})", L.duty),
            ("Import VAT" + ("" if L.vat_on else " (off for this category)"), L.import_vat),
            ("Parts", L.parts)]
    for k, v in rows:
        if v or k.startswith(("Import VAT", "Customs")):
            typer.echo(f"  {k:<52} {_money(v):>10}")
    typer.echo(f"  {'Landed cost':<52} {_money(L.total):>10}")
    typer.echo(f"\n  Sell on {S.platform} at {_money(S.price)} ({S.price_source})")
    for k, v in (("Platform fee", S.fee), ("Shipping to buyer", S.outbound_shipping), ("Returns reserve", S.returns_reserve)):
        if v:
            typer.echo(f"  {k:<52} {_money(-v):>10}")
    typer.echo(f"  {'Profit':<52} {_money(r.profit):>10}")
    typer.echo(f"  Profit on cost {r.profit_on_cost:.0%} · margin {r.margin:.0%} · break-even sell {_money(r.break_even_sell)}")
    h = r.history
    if h.get("n"):
        typer.echo(f"  Your history: {h['n']} sold, median {_money(h['median_sell'])}, "
                   f"{h['median_days']:.0f} days, {h['flaw_rate']:.0%} flawed")
    typer.echo("")
    for c in r.criteria:
        mark = {PASS: "✓", FAIL: "✗"}.get(c["status"], "~")
        typer.echo(f"  {mark} {c['name']:<30} {c['status']:<10} {c['reason']}")
    typer.echo(f"\n  ==> {r.verdict}: {r.verdict_reason}\n")


def _rate_hint(cfg) -> str:
    from .calc import live_rate, recent_order_rate

    bits = []
    if (o := recent_order_rate(cfg)) is not None:
        bits.append(f"your orders, last 60 days {o:.2f}")
    if (lv := live_rate()) is not None:
        bits.append(f"ECB today {lv:.2f}")
    return f"  ({'; '.join(bits)})" if bits else ""


@app.command()
def calc(
    rmb: float = typer.Argument(..., help="Price per unit in RMB"),
    category: str = typer.Option(..., "--category", "-k", help="N3DS, iPod, Vita card, ..."),
    sell: float = typer.Option(None, "--sell", "-s", help="Expected sell price; default = median of your sales"),
    match: str = typer.Option(None, "--match", "-m", help="Narrow your sales history, e.g. '98' or '1T'"),
    qty: int = typer.Option(1, "--qty", "-q"),
    parts: float = typer.Option(0.0, "--parts", help="Repair/upgrade parts per unit, EUR"),
    weight: float = typer.Option(None, "--weight", help="kg per unit; default from category"),
    platform: str = typer.Option(None, "--platform", "-p", help="kleinanzeigen | ebay | vinted"),
    rate: float = typer.Option(None, "--rate", help="Override RMB per EUR"),
    vat: bool = typer.Option(None, "--vat/--no-vat", help="Force import VAT on/off"),
    config: Path = ConfigOpt,
):
    """Landed cost, profit and the 5-criteria verdict for one listing."""
    from .calc import Inputs, calculate

    cfg = load_config(config)
    r = calculate(cfg, Inputs(category=category, rmb=rmb, sell_eur=sell, qty=qty, parts_eur=parts,
                              weight_kg=weight, platform=platform, rate=rate, import_vat=vat, match=match))
    _print_result(r, _rate_hint(cfg))


@app.command()
def maxbuy(
    category: str = typer.Option(..., "--category", "-k"),
    sell: float = typer.Option(None, "--sell", "-s", help="Target sell price; default = median of your sales"),
    profit: float = typer.Option(None, "--profit", help="Minimum profit in EUR (default from config)"),
    poc: float = typer.Option(None, "--poc", help="Minimum profit on cost, e.g. 0.5"),
    match: str = typer.Option(None, "--match", "-m"),
    qty: int = typer.Option(1, "--qty", "-q"),
    parts: float = typer.Option(0.0, "--parts"),
    platform: str = typer.Option(None, "--platform", "-p"),
    rate: float = typer.Option(None, "--rate"),
    vat: bool = typer.Option(None, "--vat/--no-vat"),
    config: Path = ConfigOpt,
):
    """Reverse mode: the most RMB you should pay for one unit."""
    from .calc import Inputs, max_rmb

    cfg = load_config(config)
    m = max_rmb(cfg, Inputs(category=category, sell_eur=sell, qty=qty, parts_eur=parts, platform=platform,
                            rate=rate, import_vat=vat, match=match), profit, poc)
    r = m.at_max
    typer.echo(f"\nMax price for {category}{' (' + match + ')' if match else ''}: ¥{m.max_rmb:,.0f}"
               f"  (≈ {_money(r.landed.total)} landed)")
    typer.echo(f"  sells at {_money(r.selling.price)} ({r.selling.price_source})")
    typer.echo(f"  targets: ≥ €{m.min_profit_eur:.0f} profit and ≥ {m.min_profit_on_cost:.0%} on cost "
               f"- the limit is set by {m.binding}")
    typer.echo(f"  at that price: profit {_money(r.profit)}, {r.profit_on_cost:.0%} on cost\n")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, config: Path = ConfigOpt):
    """Local web page for the calculator (http://127.0.0.1:8765)."""
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(config), host=host, port=port)
