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
