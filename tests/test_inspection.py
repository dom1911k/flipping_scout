"""Phase 2 on a throwaway database with made-up rows."""
from pathlib import Path

import pandas as pd
import pytest

from resell import db
from resell import inspection as I
from resell.config import load_config


@pytest.fixture()
def cfg(tmp_path):
    c = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    c.paths.db = tmp_path / "t.db"
    con = db.connect(c.paths.db)
    with con:
        db.reset(con)
        db.write(con, "sales", pd.DataFrame([
            {"sheet_row": 2, "item": "N3DSLL dual IPS white 98", "category": "N3DS", "buy_date": "2026-01-05",
             "price_paid": 200.0, "price_sold": 300.0, "profit_recalc": 100.0, "serial": "QJF 111 1", "flawed": 0,
             "flaw_minor": 0, "is_stock": 0, "lost": 0, "seller": "Shop A", "seller_confidence": "explicit"},
            {"sheet_row": 3, "item": "N3DSLL dual IPS grey 97", "category": "N3DS", "buy_date": "2026-02-01",
             "price_paid": 210.0, "price_sold": None, "profit_recalc": None, "serial": None, "flawed": 0,
             "flaw_minor": 0, "is_stock": 1, "lost": 0, "seller": "Shop A", "seller_confidence": "inferred"},
        ]))
        db.write(con, "storefronts", pd.DataFrame([{"key": "storefront 5849", "seller": "Shop A", "rows": 3}]))
    con.close()
    return c


def test_messages_and_checklists_load():
    for cat in ("N3DS", "iPod", "Vita card"):
        key, t = I.template_for(cat)
        assert key == cat and t["zh"].strip() and t["de"].strip()
        assert I.checklist_for(cat)
    assert I.template_for("Minidisc")[0] == "generic"
    keys = [c["key"] for c in I.checklist_for("N3DS")]
    assert len(keys) == len(set(keys))


def test_evaluate_minor_rules(cfg):
    assert I.evaluate("N3DS", {"shell": "minor"}, cfg) == (1, 1, "shell_serial")
    # yellow is always a real flaw, even if marked minor
    assert I.evaluate("N3DS", {"top_white": "minor"}, cfg) == (1, 0, "yellow")
    assert I.evaluate("N3DS", {"wifi": "ok", "sd": "na"}, cfg) == (0, 0, None)


def test_serial_warnings(cfg):
    ws = I.serial_warnings(cfg, "qw130304658")
    assert any("known-fake" in w for w in ws)
    assert any("row(s) 2" in w for w in I.serial_warnings(cfg, "QJF1111"))
    assert I.serial_warnings(cfg, "") == []


def test_seller_from_storefront(cfg):
    link = "https://www.goofish.com/item?spm=a21ybx.personal.feeds.2.58496ac2Zx&id=9"
    assert I.resolve_seller(cfg, None, link) == ("Shop A", "5849")
    assert I.resolve_seller(cfg, "Typed", link)[0] == "Typed"
    assert I.resolve_seller(cfg, None, "https://www.goofish.com/item?spm=a21ybx.personal.feeds.2.ffff6ac2&id=9")[0] == "storefront ffff"


def test_inspection_overrides_sales_flag_and_feeds_scorecard(cfg):
    I.save_inspection(cfg, category="N3DS", results={"top_dust": "flaw"}, serial="QJF1111", precheck="answered")
    I.save_inspection(cfg, category="N3DS", results={}, seller="Shop B", buy_date="2026-03-01", precheck="ignored")
    card = I.scorecard(cfg, "N3DS").set_index("seller")
    a = card.loc["Shop A"]
    assert a.bought == 2 and a.flaw_rate == 0.5 and a.checklist_answered == 1 and a.checklist_asked == 1
    b = card.loc["Shop B"]
    assert b.bought == 1 and b.checklist_asked == 1 and b.checklist_answered == 0


def test_notes_survive_reimport(cfg):
    I.set_seller_note(cfg, "Shop A", "preferred", "good")
    I.save_inspection(cfg, category="N3DS", results={}, seller="Shop A")
    con = db.connect(cfg.paths.db)
    with con:
        db.reset(con)  # what `resell import` does
    con.close()
    assert len(I.list_inspections(cfg)) == 1
    con = db.connect(cfg.paths.db)
    assert con.execute("SELECT status FROM seller_notes").fetchone()[0] == "preferred"
    con.close()


def test_bad_values_rejected(cfg):
    with pytest.raises(ValueError):
        I.save_inspection(cfg, category="N3DS", results={"wifi": "broken"})
    with pytest.raises(ValueError):
        I.set_seller_note(cfg, "x", "great")
