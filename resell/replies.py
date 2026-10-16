"""Phase 3: Kleinanzeigen reply helper.

Paste a buyer message -> intents, offer, scam flags -> a negotiation decision computed from your fact
sheet (never below the floor) -> a short German draft in your tone. The decision is always rule-based;
the optional LLM (ANTHROPIC_API_KEY) only words the reply, and its draft is rejected if it mentions a
price the rules didn't allow.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from .config import Config

PRONOUN = {"die": ("sie", "die"), "der": ("ihn", "der"), "das": ("es", "das")}
UNIT_AFTER = r"(?:gb|tb|mb|mah|uhr|h\b|std|stunden|min|%|x\b|mm|cm|g\b|kg|jahre|tage|stück|gen\b|th\b|er\b)"


# ----------------------------------------------------------------------------- content & local files

def _content(name: str) -> dict:
    return yaml.safe_load(resources.files("resell").joinpath("content", name).read_text(encoding="utf-8"))


def rules() -> dict:
    return _content("replies.yaml")


def tone_examples() -> list[dict]:
    return _content("tone_examples.yaml")["examples"]


def _local(cfg: Config, key: str, example: str) -> tuple[dict, bool]:
    """(data, is_example). Your real files live in data/ (gitignored); the examples ship with the code."""
    path = cfg.path(Path(cfg.replies.get(key, f"data/{example.replace('.example', '')}")))
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, False
    return _content(example), True


def factsheets(cfg: Config) -> tuple[dict, bool]:
    data, ex = _local(cfg, "factsheets", "factsheets.example.yaml")
    return data.get("listings", {}), ex


def profile(cfg: Config) -> tuple[dict, bool]:
    return _local(cfg, "profile", "seller_profile.example.yaml")


def init_local_files(cfg: Config) -> list[Path]:
    """Copy the example fact sheets and profile into data/ for editing (never overwrites)."""
    out = []
    for key, example in (("factsheets", "factsheets.example.yaml"), ("profile", "seller_profile.example.yaml")):
        dest = cfg.path(Path(cfg.replies.get(key, f"data/{example.replace('.example', '')}")))
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            with resources.as_file(resources.files("resell").joinpath("content", example)) as src:
                shutil.copy(src, dest)
            out.append(dest)
    return out


# ----------------------------------------------------------------------------- analysis

@dataclass
class Analysis:
    text: str
    intents: list[str]
    offer: float | None
    mode: str | None                      # pickup | shipping | None
    time: str | None                      # e.g. "morgen gegen 12 Uhr"
    name: str | None
    flags: list[dict] = field(default_factory=list)
    faq_answers: list[str] = field(default_factory=list)

    @property
    def scam(self) -> bool:
        return any(f["severity"] == "scam" for f in self.flags)


def _has(text: str, words: list[str]) -> list[str]:
    """Keywords found as whole words ('bar' must not match 'machbar'); spaces in keywords are literal."""
    out = []
    for w in words:
        k = w.strip().lower()
        pat = (r"(?<!\w)" if k[:1].isalnum() else "") + re.escape(k) + (r"(?!\w)" if k[-1:].isalnum() else "")
        if re.search(pat, text):
            out.append(w)
    return out


def find_offer(text: str, asking: float | None = None) -> float | None:
    """A euro amount the buyer proposes. Ignores storage sizes, mAh, times, dates and model numbers."""
    low = text.lower()
    cands = []
    for m in re.finditer(r"(?<![\w.,])(\d{2,4})(?:[.,](\d{1,2}))?(?![\w.,]?\d)", low):
        val = float(m.group(1) + ("." + m.group(2) if m.group(2) else ""))
        after = low[m.end():m.end() + 8]
        before = low[max(0, m.start() - 25):m.start()]
        if re.match(r"\s*" + UNIT_AFTER, after) or re.search(r"(?:um|gegen|ab|bis|zwischen)\s*$", before):
            continue
        if re.match(r"\s*[:.]\d", after):  # 19:30, 13.10.
            continue
        euro = bool(re.match(r"\s*(?:€|eur\b|euro|,-)", after)) or bool(re.search(r"€\s*$", before))
        pricey = bool(re.search(r"(biete|wären|wäre|würde|zahle|für|bei|gehen|angebot|preis|machbar|mach|nehmen|geben)\s*(?:\w+\s*){0,3}$", before))
        if euro or pricey:
            cands.append((euro, val))
    if not cands:
        return None
    if asking:
        cands = [c for c in cands if 0.2 * asking <= c[1] <= 1.5 * asking] or cands
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return cands[0][1]


def find_time(text: str) -> str | None:
    low = text.lower()
    m = re.search(r"((?:heute|morgen|übermorgen|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)?"
                  r"\s*(?:abend|nachmittag|vormittag|früh)?\s*(?:um|gegen|ab|zwischen)\s+\d{1,2}(?:[:.]\d{2})?"
                  r"(?:\s*(?:und|-|bis)\s*\d{1,2}(?:[:.]\d{2})?)?\s*uhr)", low)
    if m:
        return re.sub(r"\buhr\b", "Uhr", m.group(1).strip())
    m = re.search(r"\b(heute|morgen|übermorgen|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b", low)
    return m.group(1) if m else None


def find_name(text: str) -> str | None:
    """The buyer's sign-off: 'LG Pinar', 'Gruß Niclas', 'Viele Grüße, Michi', 'Liebe Grüße\\nFlorian'."""
    m = re.search(r"(?:^|\n|\s)(?:lg|vg|mfg|gruß|grüße|gruss|grüsse|viele grüße|liebe grüße|beste grüße|cheers|best)"
                  r"[,!.]?\s*\n?\s*([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)\s*[!.]?\s*$", text.strip(), re.I)
    if m and m.group(1).lower() not in {"und", "dir", "euch", "ihnen", "aus", "von"}:
        return m.group(1)
    m = re.search(r"(?:ich bin|mein name ist|hier ist)\s+([A-ZÄÖÜ][a-zäöüß]+)", text)
    return m.group(1) if m else None


def analyse(text: str, listing: dict | None = None) -> Analysis:
    r = rules()
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    intents = [k for k, words in r["intents"].items() if _has(low, words)]
    asking = (listing or {}).get("price")
    offer = find_offer(text, asking)
    if offer is not None and "price" not in intents:
        intents.append("price")
    mode = None
    pickup, shipping = _has(low, r["mode_words"]["pickup"]), _has(low, r["mode_words"]["shipping"])
    if pickup and not shipping:
        mode = "pickup"
    elif shipping and not pickup:
        mode = "shipping"
    flags = []
    for key, f in r["flags"].items():
        hit = _has(low, f["patterns"])
        if hit:
            flags.append({"key": key, "severity": f["severity"], "note": f["note"], "matched": hit})
    faq = []
    for entry in (listing or {}).get("faq") or []:
        if _has(low, [k.lower() for k in entry["keywords"]]):
            faq.append(entry["answer"])
    if faq and "specs" not in intents and "condition" not in intents:
        intents.append("specs")
    if not intents:
        intents = ["availability"]  # a bare "Hallo" / "Interesse" is effectively "is it still there?"
    return Analysis(text, intents, offer, mode, find_time(text), find_name(text), flags, faq)


# ----------------------------------------------------------------------------- negotiation

@dataclass
class Decision:
    action: str                 # accept | counter | final | lowball | fixed | none
    price: float | None
    mode: str | None
    reason: str


def _round(x: float, step: float) -> float:
    return float(round(x / step) * step)


def floor_for(listing: dict, mode: str | None) -> float:
    pick = float(listing.get("floor_pickup") or listing.get("price"))
    ship = listing.get("shipping") or {}
    shipped = listing.get("floor_shipped")
    if shipped is None:
        shipped = pick + (ship.get("cost", 0) if ship.get("included") else 0)
    if mode == "shipping":
        return float(shipped)
    if mode == "pickup" or not ship.get("offered"):
        return pick
    return max(pick, float(shipped))  # unknown yet: protect the higher floor


def negotiate(listing: dict, a: Analysis, cfg: Config, my_last: float | None = None) -> Decision:
    n = cfg.replies.get("negotiation", {})
    step = float(n.get("round_to", 5))
    asking = float(listing["price"])
    mode = a.mode
    floor = floor_for(listing, mode)
    if "price" not in a.intents:
        return Decision("none", None, mode, "no price question")
    if listing.get("fixed_price"):
        return Decision("fixed", asking, mode, "listing is fixed price")
    first = max(floor, _round(asking * (1 - float(n.get("first_step_pct", 0.07))), step))
    anchor = my_last if my_last else (first if a.offer is None else asking)
    if a.offer is None:
        if my_last:
            return Decision("final", max(floor, _round(my_last, step)), mode, "asked again after your counter")
        return Decision("final" if first <= floor else "counter", first, mode,
                        f"first concession {n.get('first_step_pct', 0.07):.0%} off asking")
    offer = a.offer
    if offer >= asking or (my_last and offer >= my_last):
        return Decision("accept", offer, mode, "offer meets your price")
    if offer < floor * float(n.get("lowball_pct", 0.8)):
        return Decision("lowball", floor, mode, f"offer €{offer:.0f} is far below your floor €{floor:.0f}")
    if offer < floor:
        return Decision("final", floor, mode, f"offer €{offer:.0f} below floor - counter at floor")
    counter = max(floor, _round((offer + anchor) / 2, step))
    if counter - offer <= float(n.get("accept_within", 5)):
        return Decision("accept", offer, mode, f"offer within €{n.get('accept_within', 5)} of a fair counter")
    return Decision("final" if counter <= floor else "counter", counter, mode, "meet in the middle")


# ----------------------------------------------------------------------------- template draft

def _fmt_price(p: float) -> str:
    return f"{p:.0f}" if p == int(p) else f"{p:.2f}".replace(".", ",")


def template_reply(listing: dict, a: Analysis, d: Decision, prof: dict, alt: dict | None = None) -> tuple[str, list[str]]:
    """(draft, todo notes for you)."""
    s = rules()["snippets"]
    todo: list[str] = []
    art = PRONOUN.get(listing.get("article", "die"), PRONOUN["die"])
    ship = listing.get("shipping") or {}
    mode_txt = {"pickup": " bei Abholung",
                "shipping": " inkl Versand" if ship.get("included") else ""}.get(d.mode or "", "")
    v = {"name": a.name or "", "price": _fmt_price(d.price) if d.price else "", "mode": mode_txt,
         "title": listing.get("title", ""), "area": prof.get("area", ""), "address": prof.get("address", ""),
         "paypal": prof.get("paypal", ""), "ship_cost": _fmt_price(float(ship.get("cost", 0))),
         "time": (a.time or "")[:1].upper() + (a.time or "")[1:], "condition": listing.get("condition", ""),
         "included": ", ".join(listing.get("included") or []), "tested": ", ".join(listing.get("tested") or []),
         "sign": prof.get("sign", ""), "it": art[0], "it_nom": art[1].capitalize(),
         "alt_title": (alt or {}).get("title", ""), "alt_pitch": (alt or {}).get("pitch", "")}
    f = lambda key: s[key].format(**v)
    lines: list[str] = []

    def cap(sentence: str) -> str:
        return sentence[:1].upper() + sentence[1:]

    if a.scam:
        body = [f("scam_decline")]
        if any(fl["key"] == "off_platform" for fl in a.flags):
            body.append(f("scam_stay_here"))
        greet = f("greeting_named") if a.name else f("greeting")
        return "\n".join([greet] + body + [f("closing")]), ["Scam pattern - don't share address, phone or PayPal."]

    sold = listing.get("sold")
    if sold:
        lines.append(f("sold_alternative") if alt else f("sold"))
    elif "availability" in a.intents and len(a.intents) == 1:
        return f"{f('greeting_short')} {f('available')}\n{f('closing')}", todo  # "Hallo, ja ist noch zu haben"

    if "trade" in a.intents:
        # the buyer's message describes THEIR item - answer only the swap question
        a.intents, a.faq_answers = ["trade"], []
        lines.append(f("trade"))
    if not sold and "price" in a.intents:
        key = {"accept": "price_accept", "counter": "price_counter", "final": "price_final",
               "lowball": "price_lowball", "fixed": "price_fixed_shipping" if ship.get("offered") else "price_fixed"}.get(d.action)
        if key:
            lines.append(f(key))
    if not sold:
        for ans in a.faq_answers:
            lines.append(ans)
        if "specs" in a.intents and not a.faq_answers:
            lines.append(s["unknown_answer"])
            todo.append("Spec question not covered by the fact sheet - fill in [ANTWORT ERGÄNZEN] (and add it to the faq).")
        if "condition" in a.intents and listing.get("condition"):
            lines.append(f("condition"))
            if listing.get("tested"):
                lines.append(f("tested"))
        if "included" in a.intents and listing.get("included"):
            lines.append(f("included"))
        price_says_shipping = d.action in ("accept", "counter", "final", "lowball") and d.mode == "shipping" and ship.get("included")
        if "shipping" in a.intents and d.mode != "pickup" and not price_says_shipping:
            lines.append(f("shipping_included") if ship.get("included") else
                         f("shipping_cost") if ship.get("offered") else f("shipping_none"))
        if "pickup" in a.intents:
            asks_where = bool(re.search(r"\bwo\b|wohnst|welche[rm]? (bezirk|ecke|gegend)|where", a.text.lower()))
            if (asks_where or not lines) and prof.get("area"):
                lines.append(f("pickup"))
            if a.time and not re.search(r"\d", a.time):
                lines.append(f("pickup_day"))
            elif a.time:
                lines.append(f("pickup_time"))
                todo.append(f"Check your calendar: the buyer suggested '{a.time}'.")
                if prof.get("address"):
                    lines.append(f("pickup_address"))
            else:
                lines.append(f("pickup_ask_time"))
        if "payment" in a.intents:
            low = a.text.lower()
            if d.mode == "shipping" or "waren" in low or "w&d" in low:
                if "paypal" in low and prof.get("paypal"):
                    lines.append(f("payment_paypal_ok"))
                    lines.append(f("payment_need_address"))
                else:
                    lines.append(f("payment_shipping"))
            else:
                lines.append(f("payment_pickup"))
    for fl in a.flags:
        todo.append(f"{fl['key']}: {fl['note']}")
    if not lines:
        lines.append(f("available"))
    lines = [cap(lines[0])] + lines[1:]
    named = bool(a.name)
    greet = f("greeting_named") if named else f("greeting")
    close = f("closing_signed") if named and prof.get("sign") else f("closing")
    return "\n".join([greet] + lines + [close]), todo


# ----------------------------------------------------------------------------- optional LLM wording

def llm_available() -> bool:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _allowed_numbers(listing: dict, d: Decision) -> set[str]:
    nums = {_fmt_price(float(listing["price"]))}
    if d.price:
        nums.add(_fmt_price(d.price))
    ship = (listing.get("shipping") or {}).get("cost")
    if ship:
        nums.add(_fmt_price(float(ship)))
    return nums


def validate_draft(draft: str, listing: dict, d: Decision, a: Analysis) -> list[str]:
    """Reasons to reject an LLM draft. Prices must come from the decision, never invented."""
    problems = []
    allowed = _allowed_numbers(listing, d)
    if a.offer is not None:
        allowed.add(_fmt_price(a.offer))
    for m in re.finditer(r"(?<![\w.,])(\d{2,4})(?:[.,]\d{1,2})?\s*(?:€|euro|eur\b)?", draft.lower()):
        tail = draft.lower()[m.end():m.end() + 6]
        if re.match(r"\s*" + UNIT_AFTER, tail):
            continue
        num = m.group(0).strip().rstrip("€").replace("euro", "").replace("eur", "").strip()
        num = num.replace(".", ",") if "," not in num else num
        if num not in allowed and num.split(",")[0] not in allowed:
            problems.append(f"mentions {m.group(0).strip()} - not a price the rules allowed")
    if len(draft) > 700:
        problems.append("too long")
    return problems


def llm_reply(cfg: Config, listing: dict, a: Analysis, d: Decision, prof: dict, template: str) -> tuple[str | None, str]:
    """(draft or None, note). Words the reply with Claude; falls back to the template on any problem."""
    import anthropic

    examples = "\n\n".join(
        f"Käufer: {e['buyer']}\nIch: {e['me'].format(name='Max', area=prof.get('area', 'X'), address='[Adresse]', paypal='[PayPal]', sign=prof.get('sign', ''))}"
        for e in tone_examples())
    facts = yaml.safe_dump({k: listing.get(k) for k in ("title", "condition", "tested", "included", "facts", "faq", "shipping")},
                           allow_unicode=True, sort_keys=False)
    price_rule = {
        "accept": f"Nimm das Angebot von {_fmt_price(d.price)} € an.",
        "counter": f"Biete genau {_fmt_price(d.price)} € an (nicht weniger).",
        "final": f"Biete genau {_fmt_price(d.price)} € als letztes Angebot an.",
        "lowball": f"Lehne freundlich ab; das niedrigste mögliche ist {_fmt_price(d.price)} €.",
        "fixed": "Der Preis ist Festpreis. Biete höchstens an, den Versand zu übernehmen, falls Versand angeboten wird.",
        "none": "Sprich keinen Preis an.",
    }[d.action]
    mode = {"pickup": "bei Abholung", "shipping": "inkl. Versand" if (listing.get("shipping") or {}).get("included") else "plus Versand"}.get(d.mode or "", "")
    system = (
        "Du schreibst Antworten auf Kleinanzeigen-Nachrichten für einen privaten Verkäufer in Berlin. "
        "Schreib kurz, freundlich, natürlich, per du, genau im Stil der Beispiele (Begrüßung wie 'Hey!' oder 'Hi Name,', "
        "Schluss 'Viele Grüße'). Nutze nur Fakten aus dem Datenblatt; wenn etwas fehlt, schreib [ANTWORT ERGÄNZEN]. "
        "Nenne keine anderen Preise als die erlaubten. Gib keine Adresse, Telefonnummer oder E-Mail heraus, außer sie steht im Vorschlag. "
        "Antworte nur mit dem Nachrichtentext."
    )
    user = (
        f"Beispiele meiner bisherigen Antworten:\n\n{examples}\n\n"
        f"Datenblatt des Artikels:\n{facts}\n"
        f"Preisregel: {price_rule} {('(' + mode + ')') if mode else ''}\n"
        f"Erkannte Themen: {', '.join(a.intents)}\n"
        f"Regelbasierter Vorschlag (Inhalt beibehalten, gern natürlicher formulieren):\n{template}\n\n"
        f"Nachricht des Käufers:\n{a.text}"
    )
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=cfg.replies.get("llm_model", "claude-opus-5-5"),
            max_tokens=2000,
            output_config={"effort": cfg.replies.get("llm_effort", "low")},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIConnectionError:
        return None, "LLM unreachable - template used"
    except anthropic.APIStatusError as e:
        return None, f"LLM error {e.status_code} - template used"
    if response.stop_reason == "refusal":
        return None, "LLM declined - template used"
    draft = "".join(b.text for b in response.content if b.type == "text").strip()
    problems = validate_draft(draft, listing, d, a)
    if problems or not draft:
        return None, "LLM draft rejected (" + "; ".join(problems or ["empty"]) + ") - template used"
    return draft, "worded by Claude"


# ----------------------------------------------------------------------------- one call for CLI and web

@dataclass
class Reply:
    listing_id: str
    analysis: Analysis
    decision: Decision
    draft: str
    template: str
    todo: list[str]
    source: str
    using_examples: bool

    def as_dict(self) -> dict:
        return asdict(self)


def reply(cfg: Config, listing_id: str, text: str, *, my_last: float | None = None, mode: str | None = None,
          name: str | None = None, use_llm: bool | None = None) -> Reply:
    sheets, ex1 = factsheets(cfg)
    prof, ex2 = profile(cfg)
    if listing_id not in sheets:
        raise ValueError(f"unknown listing '{listing_id}' - known: {', '.join(sheets)}")
    listing = sheets[listing_id]
    a = analyse(text, listing)
    if mode:
        a.mode = mode
    if name:
        a.name = name
    d = negotiate(listing, a, cfg, my_last)
    alt = None
    if listing.get("sold") and listing.get("alternative") in sheets:
        o = sheets[listing["alternative"]]
        alt = {"title": o.get("title"), "pitch": o.get("pitch") or ""}
    template, todo = template_reply(listing, a, d, prof, alt)
    draft, source = template, "templates"
    if use_llm is None:
        use_llm = llm_available() and cfg.replies.get("use_llm", True)
    if use_llm and not a.scam:
        if llm_available():
            out, source = llm_reply(cfg, listing, a, d, prof, template)
            draft = out or template
        else:
            source = "templates (no ANTHROPIC_API_KEY or anthropic package)"
    return Reply(listing_id, a, d, draft, template, todo, source, ex1 or ex2)


# ----------------------------------------------------------------------------- Espanso export

def espanso_yaml(cfg: Config) -> str:
    """Text-expander snippets for your phone/desktop: type ':ka-verf' -> 'Hallo, ja ist noch zu haben...'."""
    s = rules()["snippets"]
    prof, _ = profile(cfg)
    fill = {"name": "", "price": "$|$", "mode": "", "area": prof.get("area", ""), "address": prof.get("address", ""),
            "paypal": prof.get("paypal", ""), "sign": prof.get("sign", ""), "it": "sie", "it_nom": "Die",
            "time": "", "ship_cost": "", "condition": "", "included": "", "tested": "", "title": "",
            "alt_title": "", "alt_pitch": "", "floor": "", "answer": ""}
    picks = {
        ":ka-verf": ["greeting_short+available", "closing"],
        ":ka-preis": ["greeting", "price_counter", "closing"],
        ":ka-letzt": ["greeting", "price_final", "closing"],
        ":ka-fest": ["greeting", "price_fixed_shipping", "closing"],
        ":ka-zuwenig": ["greeting", "price_lowball", "closing"],
        ":ka-abhol": ["greeting", "pickup", "pickup_ask_time", "closing"],
        ":ka-adr": ["pickup_address"],
        ":ka-pp": ["payment_paypal_ok", "payment_need_address", "closing"],
        ":ka-tausch": ["greeting", "trade", "closing"],
        ":ka-scam": ["greeting", "scam_decline", "scam_stay_here", "closing"],
        ":ka-weg": ["greeting", "sold", "closing"],
    }
    matches = []
    for trig, keys in picks.items():
        parts = [" ".join(s[x].format(**fill) for x in k.split("+")) for k in keys]
        if len(parts) > 1 and keys[0].startswith("greeting"):
            parts[1] = parts[1][0].upper() + parts[1][1:]
        matches.append({"trigger": trig, "replace": "\n".join(parts)})
    class _Literal(yaml.SafeDumper):
        pass

    _Literal.add_representer(str, lambda d, v: d.represent_scalar(
        "tag:yaml.org,2002:str", v, style="|" if "\n" in v else None))
    header = "# Generated by `resell espanso`. Copy to ~/.config/espanso/match/kleinanzeigen.yml\n"
    return header + yaml.dump({"matches": matches}, Dumper=_Literal, allow_unicode=True, sort_keys=False, width=1000)
