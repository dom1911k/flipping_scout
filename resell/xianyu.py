"""Phase 4d: turn extracted Xianyu cards into scored listings.

Pure logic, no I/O except where noted: price parsing, trap flags, candidate matching, quick landed cost /
max price, seller (supplier) signals, the Chinese follow-up question, and change detection vs. the
previous recording of the same seller (the Distill replacement).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .config import Config

# ----------------------------------------------------------------------------- trap patterns (spec 4d)

MULTI_SKU = re.compile(
    r"价格不一|多款|都有|可选|价格私聊|私聊价格|价格私信|多色|多种|各种型号|型号齐全|"
    r"\d+\s*(?:到|至|~|-|—)\s*\d+\s*(?:g|gb|t|tb)\b|"           # 16到128g, 64-512g
    r"\d+\s*(?:g|gb)\s*[/、,，]\s*\d+\s*(?:g|gb)",                 # 64g/128g
    re.I)
WANTED = re.compile(r"^\s*(?:收|回收|求购|求)|高价回收|求购|回收|收一台|收个|收台")
SERVICE = re.compile(r"寄改|改装费|刷机服务|代刷|代改|代购|代拍|代发|安装服务|维修服务|上门|教程|服务费|工费")
ACCESSORY = re.compile(r"充电器|充电线|数据线|保护套|保护壳|收纳包|贴膜|钢化膜|保护膜|屏幕膜|摇杆帽|按键帽|"
                       r"\bcase\b|\bcharger\b|\bcable\b")
PLACEHOLDER = re.compile(r"^\s*(?:没了|没有了|无|没开|已售|卖了|售出)\s*[。.!！]*\s*$")
SUPPLIER = re.compile(r"新到了?一批|到了一批|一批|批发|多台|现货|大量|可批|量大|改装|定制|可改|专业改|工作室|店铺|回收价")

DISCOVERY_CLASSES_DEFAULT = ("handheld_console", "portable_audio", "headphones", "camera", "retro_console")


def parse_price(text: str | None) -> float | None:
    """'¥1,699' -> 1699, '¥1.88万' -> 18800, '3699' -> 3699."""
    if not text:
        return None
    t = str(text).replace(",", "").replace("，", "").replace(" ", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万|w|W)?", t)
    if not m:
        return None
    v = float(m.group(1))
    return v * 10000 if m.group(2) else v


def parse_wants(text) -> int | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", str(text))
    if not m:
        return None
    return int(float(m.group(1)) * (10000 if m.group(2) else 1))


def norm_title(t: str | None) -> str:
    return re.sub(r"[\s，,。.、!！~·\-]+", "", (t or "").lower())


# ----------------------------------------------------------------------------- listing

@dataclass
class Listing:
    title: str
    price_rmb: float | None
    price_text: str | None = None
    wants: int | None = None
    seller: str | None = None
    badges: list[str] = field(default_factory=list)
    original_price_rmb: float | None = None
    product_type: str | None = None
    product_class: str | None = None
    likely_discontinued: bool | None = None
    frames: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    candidate: str | None = None
    # scoring
    landed_eur: float | None = None
    sell_eur: float | None = None
    sell_basis: str | None = None
    max_rmb: float | None = None
    spread_eur: float | None = None
    verdict: str | None = None
    follow_up_zh: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def from_card(card: dict, frame: str | None = None) -> Listing:
    return Listing(
        title=(card.get("title") or "").strip(),
        price_rmb=parse_price(card.get("price_text") or card.get("price")),
        price_text=card.get("price_text") or (str(card["price"]) if card.get("price") is not None else None),
        wants=parse_wants(card.get("wants")),
        seller=(card.get("seller") or "").strip() or None,
        badges=[b for b in (card.get("badges") or []) if b],
        original_price_rmb=parse_price(card.get("original_price_text")),
        product_type=(card.get("product_type") or "").strip() or None,
        product_class=card.get("product_class"),
        likely_discontinued=card.get("likely_discontinued"),
        frames=[frame] if frame else [],
    )


def dedupe(cards_by_frame: list[tuple[str, list[dict]]]) -> list[Listing]:
    """Merge the same card seen in several overlapping frames. Key: seller + normalised title + price.
    Placeholder titles ('没了', '无') are keyed by price + product type instead. Partial cards are dropped
    when a complete copy exists."""
    merged: dict[tuple, Listing] = {}
    for frame, cards in cards_by_frame:
        for card in cards:
            if card.get("partial") and not (card.get("title") and (card.get("price_text") or card.get("price"))):
                continue
            l = from_card(card, frame)
            if l.price_rmb is None:
                continue
            nt = norm_title(l.title)
            key_title = nt if nt and not PLACEHOLDER.match(l.title) else f"~{(l.product_type or '').lower()}"
            key = (l.seller or "", key_title, l.price_rmb)
            if key in merged:
                m = merged[key]
                m.frames += [f for f in l.frames if f not in m.frames]
                m.wants = max(filter(None, [m.wants, l.wants]), default=None)
                m.badges = list(dict.fromkeys(m.badges + l.badges))
                if len(l.title) > len(m.title):
                    m.title = l.title
            else:
                merged[key] = l
    return list(merged.values())


# ----------------------------------------------------------------------------- flags & matching

def trap_flags(l: Listing) -> list[str]:
    t = l.title or ""
    flags = []
    if WANTED.search(t):
        flags.append("wanted_ad")
    if SERVICE.search(t) or any(SERVICE.search(b) for b in l.badges):
        flags.append("service")
    if MULTI_SKU.search(t):
        flags.append("multi_sku")
    if l.product_class == "accessory" or (ACCESSORY.search(t) and l.product_class in (None, "accessory", "other")):
        flags.append("accessory")
    if PLACEHOLDER.match(t):
        flags.append("no_title")    # seller hid the title: the photo decides what it is
    return flags


def match_candidate(l: Listing, candidates: list[dict]) -> str | None:
    """First candidate whose match_terms regex hits the title (or the product type for blank titles)."""
    text = f"{l.title} {l.product_type or ''}"
    for c in candidates:
        pat = c.get("match_terms")
        if isinstance(pat, str) and pat.strip():
            try:
                if re.search(pat, text, re.I):
                    return c["id"]
            except re.error:
                continue
    return None


# ----------------------------------------------------------------------------- scoring (spec 4e quick mode)

def quick_landed(cfg: Config, rmb: float) -> float:
    q = (cfg.research or {}).get("quick", {})
    rate = float(q.get("rate") or cfg.costs.get("rmb_per_eur_effective", 7.9))
    return rmb / rate + float(q.get("fixed_eur", 10))


def quick_max_rmb(cfg: Config, sell: float) -> float:
    """(min(sell - target_profit, sell / (1 + target_markup)) - fixed) x rate."""
    q = (cfg.research or {}).get("quick", {})
    rate = float(q.get("rate") or cfg.costs.get("rmb_per_eur_effective", 7.9))
    profit = float(q.get("target_profit_eur", 50))
    markup = float(q.get("target_markup", 0.5))
    return max(0.0, (min(sell - profit, sell / (1 + markup)) - float(q.get("fixed_eur", 10))) * rate)


FOLLOW_UP_MULTI = ("你好，请问这款具体有哪些型号/容量？每一款分别是多少钱？标价{price}是哪一款的价格？"
                   "另外成色和功能都正常吗？谢谢！")


def variant_check(l: Listing, cand: dict | None) -> None:
    """If the candidate is a specific variant (dual IPS, modded storage...) and the title doesn't say so,
    flag it and prepare the question - the price you'd pay only makes sense for that variant."""
    if not cand:
        return
    pat = cand.get("variant_terms")
    if isinstance(pat, str) and pat.strip():
        try:
            ok = re.search(pat, l.title or "", re.I)
        except re.error:
            return
        if not ok:
            l.flags.append("variant_unclear")
            l.follow_up_zh = cand.get("variant_question_zh") or "你好，请问具体是什么版本/配置？谢谢！"


def score(cfg: Config, l: Listing, sell_by_candidate: dict[str, tuple[float, str]]) -> None:
    """Fill landed / max / verdict. sell_by_candidate: id -> (EUR price, 'sold median' | 'Browse median -x%')."""
    if l.price_rmb is None:
        return
    l.landed_eur = round(quick_landed(cfg, l.price_rmb), 2)
    if "wanted_ad" in l.flags or "service" in l.flags:
        l.verdict = "skip"
        return
    if l.candidate and l.candidate in sell_by_candidate:
        sell, basis = sell_by_candidate[l.candidate]
        l.sell_eur, l.sell_basis = sell, basis
        l.max_rmb = round(quick_max_rmb(cfg, sell))
        l.spread_eur = round(sell - l.landed_eur, 2)
        if "multi_sku" in l.flags:
            l.verdict = "ask"
        elif "variant_unclear" in l.flags:
            l.verdict = "ask" if l.price_rmb <= l.max_rmb * 1.1 else "too high"
        elif l.price_rmb <= l.max_rmb:
            l.verdict = "buy-check"
        elif l.price_rmb <= l.max_rmb * 1.1:
            l.verdict = "close"
        else:
            l.verdict = "too high"
    elif l.candidate:
        l.verdict = "no EU price yet"
    if "multi_sku" in l.flags:
        l.follow_up_zh = FOLLOW_UP_MULTI.format(price=f"¥{l.price_rmb:,.0f}")
    elif "variant_unclear" in l.flags and l.verdict == "too high":
        l.follow_up_zh = None   # not worth asking


# ----------------------------------------------------------------------------- sellers

@dataclass
class SellerSignal:
    seller: str
    listings: int
    relevant: int
    relevant_share: float
    repeat_products: list[str]
    supplier_words: list[str]
    supplier: bool
    watch: bool
    candidates: list[str]


def seller_signals(listings: list[Listing], discovery_classes=DISCOVERY_CLASSES_DEFAULT) -> list[SellerSignal]:
    by = defaultdict(list)
    for l in listings:
        by[l.seller or "(unknown)"].append(l)
    out = []
    for seller, ls in by.items():
        real = [l for l in ls if "wanted_ad" not in l.flags]
        relevant = [l for l in real if l.candidate or
                    (l.product_class in discovery_classes and l.likely_discontinued is not False)]
        types = Counter((l.candidate or l.product_type or "").lower() for l in relevant if (l.candidate or l.product_type))
        repeats = sorted(t for t, n in types.items() if n >= 2)
        words = sorted({m.group(0) for l in ls for m in SUPPLIER.finditer(l.title)})
        share = len(relevant) / max(len(real), 1)
        # a repeatable supplier/modder: several units of the same thing and mostly your kind of stock,
        # or explicit batch/mod wording. A general shop with some relevant stock is worth watching.
        supplier = (bool(repeats) and len(relevant) >= 3 and share >= 0.3) or (bool(words) and len(relevant) >= 2)
        watch = not supplier and len(relevant) >= 2
        out.append(SellerSignal(seller, len(ls), len(relevant), round(share, 2), repeats, words, supplier, watch,
                                sorted({l.candidate for l in ls if l.candidate})))
    return sorted(out, key=lambda s: (-s.relevant, -s.listings))


def changes_vs_previous(current: list[Listing], previous: list[dict]) -> dict[str, list]:
    """New items and price drops vs. the last recording of the same seller(s). previous = stored rows."""
    prev_by_title: dict[tuple, float] = {}
    for p in previous:
        prev_by_title[(p.get("seller") or "", norm_title(p.get("title")))] = p.get("price_rmb")
    new, drops = [], []
    for l in current:
        key = (l.seller or "", norm_title(l.title))
        if not norm_title(l.title) or PLACEHOLDER.match(l.title):
            continue
        if key not in prev_by_title:
            new.append(l)
        elif prev_by_title[key] and l.price_rmb and l.price_rmb < prev_by_title[key]:
            drops.append((l, prev_by_title[key]))
    return {"new": new, "price_drops": drops}


def discovery(listings: list[Listing], known_ids: set[str], existing_names: set[str],
              classes=DISCOVERY_CLASSES_DEFAULT) -> list[dict]:
    """Product types worth adding as `new` candidates: not matched, in an interesting class, likely
    discontinued, and not a want-ad / service / accessory."""
    found: dict[str, dict] = {}
    for l in listings:
        if l.candidate or not l.product_type or set(l.flags) & {"wanted_ad", "service", "accessory"}:
            continue
        if l.product_class not in classes or l.likely_discontinued is False:
            continue
        key = re.sub(r"[^a-z0-9]+", "-", l.product_type.lower()).strip("-")
        if key in known_ids or l.product_type.lower() in existing_names:
            continue
        f = found.setdefault(key, {"id": key, "name": l.product_type, "examples": []})
        f["examples"].append({"seller": l.seller, "price_rmb": l.price_rmb, "title": l.title})
    return list(found.values())
