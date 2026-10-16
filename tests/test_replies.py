"""Phase 3 reply helper, using the buyer messages from your real chats (names kept generic)."""
from pathlib import Path

import pytest
import yaml

from resell import replies as R
from resell.config import load_config

CFG = load_config(Path(__file__).resolve().parents[1] / "config.yaml")
CFG.replies["factsheets"] = "does/not/exist.yaml"   # always use the shipped examples in tests
CFG.replies["profile"] = "does/not/exist.yaml"
SHEETS, _ = R.factsheets(CFG)


@pytest.mark.parametrize("text,offer", [
    ("Wären 190 € bei spontaner Abholung heute auch noch machbar?", 190),
    ("würdest du 270 inkl Versand machen", 270),
    ("Ich biete 250", 250),
    ("Wie viele Stunden schafft der 1800 mAh Akku?", None),
    ("Hat er 64 GB Speicher?", None),
    ("bist du heute zwischen 19 und 20 uhr da", None),
    ("Passt es morgen gegen 12 Uhr?", None),
    ("Ist die 3DS noch zu haben?", None),
])
def test_find_offer(text, offer):
    assert R.find_offer(text, 300) == offer


def test_names_and_times():
    assert R.find_name("Hallöchen\nIst es noch zu haben?\nLG\nPinar") == "Pinar"
    assert R.find_name("Moin,\nam Preis nichts machbar?\n\nGruß Niclas") == "Niclas"
    assert R.find_name("Ist sie noch verfügbar?\nViele Grüße, Michi") == "Michi"
    assert R.find_name("Hallo, noch da?") is None
    assert R.find_time("Passt es morgen gegen 12 uhr?") == "morgen gegen 12 Uhr"


def test_machbar_is_not_bar():
    a = R.analyse("am Preis ist definitiv nichts mehr machbar?", SHEETS["n3ds-dual-ips"])
    assert a.mode is None and "price" in a.intents and "payment" not in a.intents


def test_negotiation_rules():
    ipod = SHEETS["ipod-classic-512"]          # asking 230, floor pickup 200
    a = R.analyse("Was wäre dein letzter Preis bei Abholung?", ipod)
    assert R.negotiate(ipod, a, CFG).action == "counter"
    d = R.negotiate(ipod, R.analyse("Wären 190 € bei Abholung machbar?", ipod), CFG, my_last=210)
    assert (d.action, d.price) == ("final", 200)      # floor, "das wäre wirklich das Letzte"
    d = R.negotiate(ipod, R.analyse("Ich biete 120 € bei Abholung", ipod), CFG)
    assert (d.action, d.price) == ("lowball", 200)
    d = R.negotiate(ipod, R.analyse("Wären 225 € bei Abholung ok?", ipod), CFG)
    assert d.action == "accept"
    thor = SHEETS["ayn-thor"]
    assert R.negotiate(thor, R.analyse("was geht preislich?", thor), CFG).action == "fixed"


@pytest.mark.parametrize("listing,text", [
    ("n3ds-dual-ips", "Ich biete 150"), ("n3ds-dual-ips", "Ich biete 265 inkl Versand"),
    ("ipod-classic-512", "190 bei Abholung?"), ("ipod-classic-512", "letzter Preis?"),
])
def test_never_below_floor(listing, text):
    sheet = SHEETS[listing]
    for last in (None, 205, 280):
        a = R.analyse(text, sheet)
        d = R.negotiate(sheet, a, CFG, my_last=last)
        if d.price is not None and d.action != "accept":
            assert d.price >= R.floor_for(sheet, a.mode)


def test_scam_patterns_decline_without_details():
    r = R.reply(CFG, "n3ds-dual-ips", "Hallo, ich zahle 300, mein Kurier holt es ab. Schreib mir auf WhatsApp", use_llm=False)
    keys = {f["key"] for f in r.analysis.flags}
    assert {"courier", "off_platform"} <= keys and r.analysis.scam
    assert "Kleinanzeigen" in r.draft and "PayPal Adresse" not in r.draft and "Adresse ist" not in r.draft
    r = R.reply(CFG, "n3ds-dual-ips", "Bitte bestätige die Zahlung über https://kleinanzeigen-sicher.example", use_llm=False)
    assert r.analysis.scam


def test_paypal_friends_is_caution_not_scam():
    r = R.reply(CFG, "n3ds-dual-ips", "Bin heute um 19 Uhr da, geht PayPal Freunde?", use_llm=False)
    assert [f["severity"] for f in r.analysis.flags] == ["caution"] and not r.analysis.scam


def test_drafts_in_your_tone():
    r = R.reply(CFG, "n3ds-dual-ips", "Hallöchen\nIst es noch zu haben?\nLG\nPinar", use_llm=False)
    assert r.draft.startswith("Hallo, ja") and r.draft.endswith("Viele Grüße")
    r = R.reply(CFG, "n3ds-dual-ips", "Moin,\nam Preis ist definitiv nichts mehr machbar?\n\nGruß Niclas", use_llm=False)
    assert r.draft.startswith("Hi Niclas,\nWäre bereit auf 280")
    r = R.reply(CFG, "ayn-thor", "was preislich noch möglich wäre & für was der Farbton Rainbow steht?", use_llm=False)
    assert "Festpreis" in r.draft and "Rainbow" in r.draft
    r = R.reply(CFG, "n3ds-dual-ips", "hätten Sie Interesse ihre 3ds zu tauschen gegen eine Switch mit Spiele", use_llm=False)
    assert "tauschen möchte ich lieber nicht" in r.draft and "vorinstalliert" not in r.draft
    r = R.reply(CFG, "n3ds-dual-ips", "Womit ist sie gemoddet? Homebrew?", use_llm=False)
    assert "Luma" in r.draft
    r = R.reply(CFG, "n3ds-dual-ips", "Welche Region hat die Konsole? Und welcher Akku?", use_llm=False)
    assert "[ANTWORT ERGÄNZEN]" in r.draft or "japanisch" in r.draft.lower()


def test_address_only_with_a_clock_time():
    r = R.reply(CFG, "n3ds-dual-ips", "Passt es morgen gegen 12 Uhr vorbeizukommen?", use_llm=False)
    assert "Adresse ist" in r.draft
    r = R.reply(CFG, "n3ds-dual-ips", "Kann ich morgen vorbeikommen?", use_llm=False)
    assert "Adresse ist" not in r.draft and "um wie viel Uhr" in r.draft


def test_validate_draft_rejects_invented_prices():
    sheet = SHEETS["n3ds-dual-ips"]
    a = R.analyse("letzter Preis?", sheet)
    d = R.negotiate(sheet, a, CFG)
    ok = f"Hey!\nWäre bereit auf {d.price:.0f} zu gehen.\nViele Grüße"
    assert R.validate_draft(ok, sheet, d, a) == []
    assert R.validate_draft("Hey! Für 240 € gehört sie dir.", sheet, d, a)
    assert R.validate_draft("Hat 64GB und 1800 mAh.", sheet, d, a) == []


def test_llm_path_falls_back_on_bad_draft(monkeypatch):
    anthropic = pytest.importorskip("anthropic")

    class Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class Resp:
        stop_reason = "end_turn"

        def __init__(self, text):
            self.content = [Block(text)]

    class Fake:
        reply_text = "Hey! 199 € und sie gehört dir."

        def __init__(self, *a, **k):
            self.beta = self
            self.messages = self

        def create(self, **kwargs):
            assert kwargs["model"] == "claude-opus-5-5" and kwargs["fallbacks"] == "default"
            return Resp(Fake.reply_text)

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    r = R.reply(CFG, "n3ds-dual-ips", "letzter Preis?", use_llm=True)
    assert "rejected" in r.source and r.draft == r.template
    Fake.reply_text = "Hey! Wäre bereit auf 280 zu gehen :)\nViele Grüße"
    r = R.reply(CFG, "n3ds-dual-ips", "letzter Preis?", use_llm=True)
    assert r.source == "worded by Claude" and ":)" in r.draft


def test_espanso_export_is_valid_yaml():
    data = yaml.safe_load(R.espanso_yaml(CFG))
    triggers = {m["trigger"] for m in data["matches"]}
    assert {":ka-verf", ":ka-preis", ":ka-scam"} <= triggers
