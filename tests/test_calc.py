"""Calculator maths on made-up numbers."""
from pathlib import Path

import pandas as pd
import pytest

from resell import calc
from resell.config import load_config

CFG = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
EMPTY = pd.DataFrame(columns=["category", "is_stock", "lost", "item", "sell_date", "platform", "price_sold"])


def _cfg(**costs):
    c = CFG.model_copy(deep=True)
    c.costs.update(costs)
    return c


def test_landed_cost_breakdown():
    c = _cfg(rmb_per_eur_effective=8.0, agent_fee_per_item_eur=0.5, shipping_eur_per_kg=20.0, shipping_min_eur=1.0)
    inp = calc.Inputs(category="N3DS", rmb=1600, import_vat=True)
    L = calc.landed_cost(c, inp, 1600)
    assert L.goods == 200
    assert L.shipping == pytest.approx(0.40 * 20)
    assert L.duty == 0                                   # > EUR 150: console duty rate 0
    assert L.import_vat == pytest.approx(0.19 * (200 + 8))
    assert L.total == pytest.approx(200 + 0.5 + 8 + 0.19 * 208)


def test_flat_customs_below_threshold_is_shared():
    c = _cfg(rmb_per_eur_effective=8.0)
    one = calc.landed_cost(c, calc.Inputs(category="Vita card", rmb=240, qty=1), 240)
    four = calc.landed_cost(c, calc.Inputs(category="Vita card", rmb=240, qty=4), 240)
    assert one.duty == 3.0 and four.duty == 0.75


def test_vat_defaults_on_for_unknown_category():
    L = calc.landed_cost(CFG, calc.Inputs(category="Minidisc", rmb=800), 800)
    assert L.vat_on and L.import_vat > 0
    assert not calc.landed_cost(CFG, calc.Inputs(category="N3DS", rmb=800), 800).vat_on


def test_profit_and_break_even():
    r = calc.calculate(CFG, calc.Inputs(category="N3DS", rmb=1520, sell_eur=300), sales=EMPTY)
    assert r.profit == pytest.approx(r.selling.net - r.landed.total)
    at_be = calc.calculate(CFG, calc.Inputs(category="N3DS", rmb=1520, sell_eur=r.break_even_sell), sales=EMPTY)
    assert at_be.profit == pytest.approx(0, abs=0.01)


def test_max_rmb_meets_targets_and_is_tight():
    inp = calc.Inputs(category="N3DS", sell_eur=320)
    m = calc.max_rmb(CFG, inp, min_profit=80, min_poc=0.5, sales=EMPTY)
    r = m.at_max
    assert r.profit >= 80 and r.profit_on_cost >= 0.5
    over = calc.calculate(CFG, calc.Inputs(category="N3DS", rmb=m.max_rmb + 5, sell_eur=320), sales=EMPTY)
    assert over.profit < 80 or over.profit_on_cost < 0.5


def test_max_rmb_unreachable():
    m = calc.max_rmb(CFG, calc.Inputs(category="Vita card", sell_eur=10), sales=EMPTY)
    assert m.max_rmb == 0 and "unreachable" in m.binding


def test_verdicts():
    good = calc.calculate(CFG, calc.Inputs(category="iPod", rmb=700, sell_eur=260), sales=EMPTY)
    assert good.criteria[4]["status"] == calc.PASS
    hype = calc.calculate(CFG, calc.Inputs(category="DDR5", rmb=1000, sell_eur=300), sales=EMPTY)
    assert hype.verdict == calc.FAIL and "hype" in hype.verdict_reason
    thin = calc.calculate(CFG, calc.Inputs(category="N3DS", rmb=1600, sell_eur=300), sales=EMPTY)
    assert thin.verdict in (calc.FAIL, calc.BORDERLINE)


def test_history_trend_and_bulk_exclusion():
    days = pd.date_range("2026-01-01", periods=10, freq="30D")
    rows = [{"category": "iPod", "is_stock": 0, "lost": 0, "item": "Ipod 7 1T", "sell_date": d, "platform": "K",
             "price_sold": 250 + i * 5, "price_paid": 170, "days_to_sell": 40, "flawed": 0} for i, d in enumerate(days)]
    rows += [{"category": "iPod", "is_stock": 0, "lost": 0, "item": "Ipod 7 1T", "sell_date": days[-1], "platform": "K",
              "price_sold": 100, "price_paid": 170, "days_to_sell": 40, "flawed": 1}] * 4  # one bulk dump
    h = calc.history(CFG, "iPod", sales=pd.DataFrame(rows), today=days[-1].date())
    assert h["n"] == 9 and h["median_sell"] > 250        # the bulk day (5 units) is left out entirely
    assert h["trend_12m"] > 0.1


def test_price_parsing():
    from resell.web import _num
    assert [_num(x) for x in ["¥1,580", "1580元", "1.580", "1580.5", "12,5"]] == [1580, 1580, 1580, 1580.5, 12.5]


def test_web_endpoints():
    from fastapi.testclient import TestClient

    from resell.web import create_app
    client = TestClient(create_app(Path(__file__).resolve().parents[1] / "config.yaml"))
    assert client.get("/").status_code == 200
    r = client.post("/go", data={"mode": "max", "category": "N3DS", "sell": "320"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Pay at most" in r.text
    r = client.post("/go", data={"mode": "calc", "category": "N3DS", "rmb": "1500", "sell": "320"})
    assert r.status_code == 200 and "<html" in r.text and "Landed cost" in r.text
    for page in ("/messages?category=iPod", "/inspect?category=Vita%20card", "/sellers"):
        assert client.get(page).status_code == 200
