"""SQLite helpers."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

import pandas as pd

TABLES = ("sales", "purchases", "parse_issues", "storefronts")  # rebuilt on every import


def connect(path: Path) -> sqlite3.Connection:
    """Open the DB and make sure the hand-filled tables exist (they are never dropped)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(resources.files("resell").joinpath("schema_persistent.sql").read_text(encoding="utf-8"))
    _add_missing_columns(con)
    return con


# columns added after a table was first created (CREATE TABLE IF NOT EXISTS won't add them)
_LATER_COLUMNS = {"candidates": {"own_sales_match": "TEXT"}}


def _add_missing_columns(con: sqlite3.Connection) -> None:
    for table, cols in _LATER_COLUMNS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if have and col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def reset(con: sqlite3.Connection) -> None:
    for t in TABLES:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(resources.files("resell").joinpath("schema.sql").read_text(encoding="utf-8"))


def write(con: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    df = df[[c for c in df.columns if c in cols]]
    df.to_sql(table, con, if_exists="append", index=False)


def read(con: sqlite3.Connection, table: str) -> pd.DataFrame:
    try:
        return pd.read_sql(f"SELECT * FROM {table}", con)
    except Exception:  # table not created yet (no import run)
        return pd.DataFrame()
