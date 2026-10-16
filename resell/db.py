"""SQLite helpers."""
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

import pandas as pd

TABLES = ("sales", "purchases", "parse_issues")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def reset(con: sqlite3.Connection) -> None:
    for t in TABLES:
        con.execute(f"DROP TABLE IF EXISTS {t}")
    con.executescript(resources.files("resell").joinpath("schema.sql").read_text(encoding="utf-8"))


def write(con: sqlite3.Connection, table: str, df: pd.DataFrame) -> None:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    df = df[[c for c in df.columns if c in cols]]
    df.to_sql(table, con, if_exists="append", index=False)


def read(con: sqlite3.Connection, table: str) -> pd.DataFrame:
    return pd.read_sql(f"SELECT * FROM {table}", con)
