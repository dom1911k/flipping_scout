"""Phase 4d (recordings) and 4b (eBay Browse API) on a throwaway DB. The recording fixture is your
17 Oct sample (seller 狼黑杂货铺), read by hand into the same JSON shape Claude vision returns."""
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from resell import ebay as EB
from resell import recordings as REC
from resell import research as RS
from resell import xianyu as X
from resell.config import load_config

FIX = Path(__file__).parent / "fixtures" / "xianyu_sample_frames.json"


@pytest.fixture()
def cfg(tmp_path):
    c = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
    c.paths.db = tmp_path / "t.db"
    c.paths.reports = tmp_path / "reports"
    c.research["recordings"]["cache_dir"] = str(tmp_path / "rec")
    c.research["ebay"]["cache_dir"] = str(tmp_path / "ebay")
    RS.seed(c)
    return c


# ----------------------------------------------------------------------------- 4d pure logic

@pytest.mark.parametrize("text,val", [("¥1699", 1699), ("¥1.88万", 18800), ("¥3,700", 3700), ("2w", 20000), ("", None)])
def test_parse_price(text, val):
    assert X.parse_price(text) == val


@pytest.mark.parametrize("title,flag", [
    ("收 PSP3000 高价回收", "wanted_ad"),
    ("求购 ipod classic", "wanted_ad"),
    ("PSP3000 寄改IPS屏 改装费", "service"),
    ("ipod classic 改装 64g/128g/256g 价格不一", "multi_sku"),
    ("ipod 16到128g 都有", "multi_sku"),
    ("psp3000 原装充电器", "accessory"),
    ("没了", "no_title"),
])
def test_trap_flags(title, flag):
    assert flag in X.trap_flags(X.Listing(title=title, price_rmb=100))


def test_generation_is_not_a_service():
    # spec lists 代 as a service word, but '3代' / '二代' mean "generation" - only 代购/代改/... count
    assert "service" not in X.trap_flags(X.Listing(title="联想X1 Carbon 3代超级本", price_rmb=599))


def test_dedupe_merges_overlapping_frames():
    d = json.loads(FIX.read_text(encoding="utf-8"))
    ls = X.dedupe([(f["frame"], f["cards"]) for f in d["frames"]])
    assert len(ls) == 30                                         # 44 cards seen, 30 unique, 1 partial dropped
    psp = [l for l in ls if l.title.startswith("PSP3000白色")][0]
    assert psp.frames == ["f_010", "f_011"] and psp.price_rmb == 525


def test_quick_mode_formulas(cfg):
    cfg.research["quick"]["rate"] = 7.9
    assert X.quick_landed(cfg, 790) == pytest.approx(110)
    # sell 300: min(300-50, 300/1.5=200) = 200; (200-10)*7.9 = 1501
    assert X.quick_max_rmb(cfg, 300) == pytest.approx(1501)


# ----------------------------------------------------------------------------- 4d pipeline

def test_recording_pipeline_on_sample(cfg, tmp_path):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"not really a video")
    res = REC.process(cfg, video, label="狼黑杂货铺", extraction=json.loads(FIX.read_text(encoding="utf-8")))
    by_cand = {}
    for l in res.listings:
        if l.candidate:
            by_cand.setdefault(l.candidate, []).append(l)
    assert sorted(l.price_rmb for l in by_cand["psp"]) == [488, 525]
    assert [l.price_rmb for l in by_cand["n3ds"]] == [1350]
    n3 = by_cand["n3ds"][0]
    assert "variant_unclear" in n3.flags and "IPS" in n3.follow_up_zh   # title doesn't say IPS -> ask
    assert {d["name"] for d in res.discovered} == {"New Nintendo 3DS", "Nintendo DS Lite"}
    cands, _, _ = RS.load(cfg)
    assert {"new-nintendo-3ds", "nintendo-ds-lite"} <= set(cands.id)
    s = res.sellers[0]
    assert s.seller == "狼黑杂货铺" and s.watch and not s.supplier
    md = REC.report_md(res)
    assert "Ask the seller" in md and "New on the radar" in md
    # same video again: from the cache, no new candidates
    res2 = REC.process(cfg, video, label="狼黑杂货铺", extraction=json.loads(FIX.read_text(encoding="utf-8")))
    assert res2.recording_id == res.recording_id and res2.discovered == []


def test_price_drop_detection(cfg, tmp_path):
    d = json.loads(FIX.read_text(encoding="utf-8"))
    v1, v2 = tmp_path / "a.mp4", tmp_path / "b.mp4"
    v1.write_bytes(b"1")
    v2.write_bytes(b"2")
    REC.process(cfg, v1, extraction=d)
    d["frames"][0]["cards"][3]["price_text"] = "¥450"                      # PSP3000 墨绿 488 -> 450
    d["frames"][0]["cards"].append({**d["frames"][0]["cards"][3], "title": "PSP GO 黑色 全原装", "price_text": "¥599"})
    res = REC.process(cfg, v2, extraction=d)
    assert any(l.title.startswith("PSP GO") for l in res.changes["new"])
    assert any(old == 488 and l.price_rmb == 450 for l, old in res.changes["price_drops"])


def test_vision_request_shape(cfg, tmp_path):
    """The request sent to Claude: images + schema + fallbacks; cards come back per frame."""
    sent = {}

    class Resp:
        stop_reason = "end_turn"

        class usage:
            input_tokens, output_tokens = 6000, 900

        def __init__(self, text):
            self.content = [type("B", (), {"type": "text", "text": text})()]

    class Fake:
        def __init__(self):
            self.beta = self
            self.messages = self

        def create(self, **kw):
            sent.update(kw)
            frames = [b["text"].split()[1].rstrip(":") for b in kw["messages"][0]["content"] if b["type"] == "text"
                      and b["text"].startswith("Frame")]
            return Resp(json.dumps({"frames": [{"frame": f, "page_kind": "xianyu_grid", "cards": []} for f in frames]}))

    imgs = []
    for i in range(5):
        p = tmp_path / f"f_{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8fake")
        imgs.append(p)
    ex = REC.vision_extract(cfg, imgs, "xianyu_grid", client=Fake())
    assert len(ex.frames) == 5 and ex.cost_usd > 0
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert sent["output_config"]["format"]["type"] == "json_schema"


# ----------------------------------------------------------------------------- 4b eBay Browse API

def _item(i, price, buying="FIXED_PRICE", end=None, title="Sony PSP 3000 Konsole"):
    return {"itemId": f"v1|{i}|0", "title": title, "price": {"value": str(price), "currency": "EUR"},
            "buyingOptions": [buying], "itemEndDate": end, "itemLocation": {"country": "DE"},
            "itemWebUrl": f"https://www.ebay.de/itm/{i}"}


def _client(pages_by_week):
    calls = {"n": 0}

    def handler(req: httpx.Request):
        if req.url.path.endswith("/oauth2/token"):
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "T", "expires_in": 7200})
        assert req.headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_DE" and req.headers["Authorization"] == "Bearer T"
        f = req.url.params["filter"]
        assert "buyingOptions:{FIXED_PRICE|AUCTION}" in f and "conditionIds:{" in f and "7000" not in f
        calls["n"] += 1
        items = pages_by_week["items"]
        return httpx.Response(200, json={"total": len(items), "itemSummaries": items})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_browse_snapshot_and_probable_sales(cfg):
    week = {"items": [_item(1, 80), _item(2, 95), _item(3, 120, "AUCTION", "2026-10-20T10:00:00Z"),
                      _item(4, 999, title="PSP 3000 defekt Ersatzteil"), _item(5, 110),
                      _item(7, 15, title="Netzteil für Sony PSP 1000/2000/3000")]}
    client, calls = _client(week)
    s1 = EB.scan(cfg, ["psp"], ("de",), client=client, run_day=date(2026, 10, 10))[0]
    # 'defekt' excluded, the charger fails the match regex, the auction's bid is not a price
    assert s1.kept == 4 and s1.median == 95 and s1.auction_share == 0.25
    assert s1.probable_sales is None                                                         # first run
    # a week later: #2 vanished early (probable sale), #3 auction ended on schedule (not a sale), #6 is new
    week["items"] = [_item(1, 80), _item(5, 105), _item(6, 90)]
    s2 = EB.scan(cfg, ["psp"], ("de",), client=client, run_day=date(2026, 10, 25))[0]
    assert s2.vanished == 2 and s2.probable_sales == 1 and s2.vanished_median == 95
    assert EB.latest_medians(cfg)["psp"] == 90


def test_browse_cache_avoids_repeat_calls(cfg):
    client, calls = _client({"items": [_item(1, 80)]})
    EB.scan(cfg, ["psp"], ("de",), client=client, run_day=date(2026, 10, 10))
    n = calls["n"]
    EB.scan(cfg, ["psp"], ("de",), client=client, run_day=date(2026, 10, 10))
    assert calls["n"] == n


def test_browse_sell_price_fallback(cfg):
    client, _ = _client({"items": [_item(1, 100), _item(2, 120)]})
    EB.scan(cfg, ["psp"], ("de",), client=client, run_day=date(2026, 10, 10))
    sells = REC.sell_prices(cfg)
    eur, basis = sells["psp"]
    assert eur == pytest.approx(110 * 0.85) and "estimate" in basis


# ----------------------------------------------------------------------------- updater

def test_updater_pulls_and_keeps_local_edits(tmp_path):
    import subprocess

    from resell import updater

    def git(cwd, *a):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)

    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t"); git(origin, "config", "user.name", "t")
    (origin / "config.yaml").write_text("a: 1\nb: 2\n")
    git(origin, "add", "."); git(origin, "commit", "-qm", "first")
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "config", "user.email", "t@t"); git(clone, "config", "user.name", "t")
    assert updater.check(clone).behind == 0
    (origin / "new.py").write_text("x = 1\n")
    git(origin, "add", "."); git(origin, "commit", "-qm", "Add new module")
    (clone / "config.yaml").write_text("a: 1\nb: 3\n")          # a local edit to a tracked file
    st = updater.check(clone)
    assert st.ok and st.behind == 1 and st.incoming == ["Add new module"]
    res = updater.update(clone, reinstall=False)
    assert res.ok and res.updated and (clone / "new.py").exists()
    assert (clone / "config.yaml").read_text() == "a: 1\nb: 3\n"  # kept
    assert updater.update(clone, reinstall=False).updated is False


def test_updater_outside_git(tmp_path):
    from resell import updater
    st = updater.check(tmp_path)
    assert not st.ok and "git" in st.error


def test_config_local_overrides(tmp_path):
    import shutil

    from resell.config import load_config
    shutil.copy(Path(__file__).resolve().parent.parent / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "config.local.yaml").write_text("costs:\n  shipping_eur_per_kg: 30\n", encoding="utf-8-sig")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.costs["shipping_eur_per_kg"] == 30 and "rmb_per_eur_effective" in cfg.costs
