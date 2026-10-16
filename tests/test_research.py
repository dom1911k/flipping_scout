"""Phase 4 research workspace on a throwaway database with made-up prices."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from resell import calc, research as RS
from resell.config import load_config


@pytest.fixture()
def cfg(tmp_path):
    c = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    c.paths.db = tmp_path / "t.db"
    c.report_as_of = date(2026, 10, 16)
    return c


def test_seed_is_idempotent(cfg):
    assert RS.seed(cfg) >= 5
    assert RS.seed(cfg) == 0
    cands, findings, _ = RS.load(cfg)
    assert {"vita-oled", "psp", "ipod-mini", "gba-sp-101", "minidisc"} <= set(cands.id)
    assert len(findings[findings.candidate == "vita-oled"]) >= 3


EBAY_PASTE = """Sony PS Vita 1000 OLED Konsole schwarz
Gebraucht
EUR 149,00
Verkauft 12. Okt. 2026
+EUR 5,49 Versand
PlayStation Vita PCH-1000 Wifi Crystal Black mit 16GB Karte
Gebraucht
EUR 165,50
Verkauft 3. Sep. 2026
"""
TERAPEAK_TSV = ("Sony PS Vita PCH-1000 OLED Black\t€152.30\t€4.99\t1\tOct 3, 2025\n"
                "PS Vita 1000 Wifi console 16GB\tEUR 140,00\tFree\t1\t14.07.2025\n"
                "Listing\tAvg sold price\tAvg shipping\tTotal sold\tDate last sold\n")


def test_parse_ebay_sold_paste():
    pts = RS.parse_sold_paste(EBAY_PASTE)
    assert [(p.price, p.date) for p in pts] == [(149.0, "2026-10-12"), (165.5, "2026-09-03")]
    assert pts[0].title.startswith("Sony PS Vita") and pts[0].condition == "Gebraucht"


def test_parse_terapeak_tsv():
    pts = RS.parse_sold_paste(TERAPEAK_TSV)
    assert [(p.price, p.currency, p.date) for p in pts] == [(152.3, "EUR", "2025-10-03"), (140.0, "EUR", "2025-07-14")]


@pytest.mark.parametrize("line,expect", [
    ("12.10.2026 149 €", (149.0, "2026-10-12")),
    ("2026-01-05  US $120.00", (120.0, "2026-01-05")),
    ("£89.99 sold 5 Mar 2026", (89.99, "2026-03-05")),
])
def test_parse_simple_lines(line, expect):
    p = RS.parse_sold_paste(line)[0]
    assert (p.price, p.date) == expect


def test_parse_xianyu_share_text():
    p = RS.parse_cn_text("【闲鱼】https://m.tb.cn/h.abc?tk=x 「psv1000 破解 64g 9新」 ¥680 点击链接直接打开")
    assert (p.price, p.currency, p.title) == (680.0, "CNY", "psv1000 破解 64g 9新") and p.url.startswith("https://")
    assert RS.parse_cn_text("720").price == 720


def _prices(n, start, step, days=30):
    d0 = date(2026, 10, 1)
    return [RS.PricePoint(start + i * step, "EUR", (d0 - timedelta(days=days * i)).isoformat(), f"unit {i}") for i in range(n)]


def test_assessment_profit_and_trend(cfg):
    cid = RS.add_candidate(cfg, "Test Console", china_edge="modded", testability="easy",
                           discontinued="2015", weight_kg=0.3)
    # 12 monthly sales, falling 20 € a month going back = rising over time
    RS.add_evidence(cfg, cid, "eu_sold", _prices(12, 300, -10), "terapeak")
    RS.add_evidence(cfg, cid, "cn_asking", [RS.PricePoint(700, "CNY"), RS.PricePoint(760, "CNY")], "xianyu")
    a = next(x for x in RS.assess_all(cfg) if x.cand["id"] == cid)
    assert a.trend_12m and a.trend_12m > 0.1
    assert a.criteria[1]["status"] == calc.PASS
    assert a.cn_median_rmb == 730 and a.estimate is not None
    assert a.estimate.landed.vat_on                      # new category: import VAT included
    assert a.max_rmb and a.max_rmb > 0


def test_falling_prices_fail_hype_check(cfg):
    cid = RS.add_candidate(cfg, "Hype Thing", china_edge="arbitrage", testability="easy", discontinued="2020")
    RS.add_evidence(cfg, cid, "eu_sold", _prices(12, 200, 15), "terapeak")   # older = pricier
    a = next(x for x in RS.assess_all(cfg) if x.cand["id"] == cid)
    assert a.criteria[1]["status"] == calc.FAIL and a.verdict == calc.FAIL


def test_missing_evidence_gives_next_steps_and_elektrog(cfg):
    cid = RS.add_candidate(cfg, "New Gadget", new_devices=1)
    a = next(x for x in RS.assess_all(cfg) if x.cand["id"] == cid)
    assert any("Terapeak" in s for s in a.next_steps) and any("Xianyu" in s for s in a.next_steps)
    assert any("ElektroG" in w for w in a.warnings)
    assert a.criteria[4]["status"] == calc.UNKNOWN


def test_update_validation(cfg):
    cid = RS.add_candidate(cfg, "X")
    with pytest.raises(ValueError):
        RS.update_candidate(cfg, cid, status="maybe")
    with pytest.raises(ValueError):
        RS.update_candidate(cfg, cid, colour="red")


def test_research_pages(cfg, monkeypatch):
    from fastapi.testclient import TestClient

    from resell import web
    monkeypatch.setattr(web, "load_config", lambda p: cfg)
    client = TestClient(web.create_app("unused"))
    assert client.get("/research").status_code == 200          # auto-seeds
    r = client.post("/research/vita-oled/eu", data={"source": "ebay_sold", "paste": EBAY_PASTE, "preview": "1"})
    assert "Preview: 2 prices" in r.text
    r = client.post("/research/vita-oled/eu", data={"source": "ebay_sold", "paste": EBAY_PASTE})
    assert "Saved 2" in r.text
    r = client.post("/research/vita-oled/cn", data={"paste": "¥680 psv1000\n¥720 psv1000 oled"})
    assert "Added 2" in r.text
    r = client.post("/research/new", data={"name": "Nintendo DS Lite"}, follow_redirects=False)
    assert r.headers["location"] == "/research/nintendo-ds-lite"
    assert client.get("/research/nintendo-ds-lite").status_code == 200
