"""Load and validate config.yaml."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class Paths(BaseModel):
    workbook: Path
    db: Path
    reports: Path


class CategoryRule(BaseModel):
    name: str
    pattern: str


class Config(BaseModel):
    paths: Paths
    sales_sheet: str = "Sales Sheet"
    order_sheet_prefix: str = "Order #"
    stock_aging_days: int = 120
    report_as_of: date | None = None
    categories: list[CategoryRule]
    grade_min: int = 80
    grade_max: int = 99
    flaw_keywords: dict[str, list[str]]
    flaw_minor_words: list[str] = Field(default_factory=list)
    flaw_minor_followups: list[str] = Field(default_factory=list)
    flaw_negations: list[str] = Field(default_factory=list)
    flaw_overrides: dict[int, bool] = Field(default_factory=dict)
    flaw_always_major: list[str] = Field(default_factory=list)
    lost_rows: list[int] = Field(default_factory=list)
    main_seller: str | None = None
    dif_seller_markers: list[str] = Field(default_factory=list)
    non_seller_sources: list[str] = Field(default_factory=list)
    dif_seller_convention_since: date | None = None
    platforms: dict[str, dict[str, str]] = Field(default_factory=dict)
    ebay_account_revenue_cap_eur: float = 2000
    costs: dict[str, Any] = Field(default_factory=dict)

    root: Path = Path(".")

    def path(self, p: Path) -> Path:
        return p if p.is_absolute() else self.root / p

    @property
    def as_of(self) -> date:
        return self.report_as_of or date.today()


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = Config(**raw)
    cfg.root = path.resolve().parent
    return cfg
