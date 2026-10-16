"""Parse the "Sales Sheet" tab into one row per unit."""
from __future__ import annotations

from datetime import date, datetime

import openpyxl
import pandas as pd

COLUMNS = {
    "Console Name": "item",
    "Source": "source",
    "Buying Date": "buy_date",
    "Selling Date": "sell_date",
    "Selling Place": "selling_place",
    "Price Paid": "price_paid",
    "Price Sold": "price_sold",
    "Profit": "profit",
    "Serial Number": "serial",
    "Tracking Number": "tracking",
    "Comment": "comment",
    "Profitability": "profitability",
}


def _date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(v):
    if v is None:
        return None
    s = " ".join(str(v).split())  # collapses newlines and doubled spaces
    return s or None


def read_sales(wb: openpyxl.Workbook, sheet: str) -> tuple[pd.DataFrame, list[dict]]:
    """Return (sales rows, parse issues). Only the first occurrence of each header is used;
    the summary tables to the right of the sales columns are ignored."""
    ws = wb[sheet]
    header = [c.value for c in ws[1]]
    idx = {}
    for i, h in enumerate(header):
        if h in COLUMNS and COLUMNS[h] not in idx:
            idx[COLUMNS[h]] = i
    missing = set(COLUMNS.values()) - set(idx)
    if missing:
        raise ValueError(f"{sheet}: missing columns {sorted(missing)}")

    rows, issues = [], []
    for r, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        get = lambda k: values[idx[k]] if idx[k] < len(values) else None
        item = _text(get("item"))
        if not item:
            if any(get(k) is not None for k in ("price_paid", "price_sold", "buy_date")):
                issues.append({"sheet": sheet, "cells": f"row {r}", "raw": repr(values[:12]),
                               "reason": "values but no item name"})
            continue
        row = {
            "sheet_row": r,
            "item": item,
            "source": _text(get("source")),
            "buy_date": _date(get("buy_date")),
            "sell_date": _date(get("sell_date")),
            "selling_place": _text(get("selling_place")),
            "price_paid": _num(get("price_paid")),
            "price_sold": _num(get("price_sold")),
            "profit": _num(get("profit")),
            "serial": _text(get("serial")),
            "tracking": _text(get("tracking")),
            "comment": _text(get("comment")),
            "profitability": _num(get("profitability")),
        }
        if row["tracking"] == "-":
            row["tracking"] = None
        if row["buy_date"] is None:
            issues.append({"sheet": sheet, "cells": f"row {r}", "raw": item, "reason": "no buying date"})
        if row["price_paid"] is None:
            issues.append({"sheet": sheet, "cells": f"row {r}", "raw": item, "reason": "no price paid"})
        rows.append(row)
    return pd.DataFrame(rows), issues
