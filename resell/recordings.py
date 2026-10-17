"""Phase 4d/4c: screen recordings -> listings.

video --ffmpeg (1 fps, mpdecimate drops near-duplicate frames, scaled for the API)--> frames
frames --Claude vision, strict JSON schema, a few frames per request--> cards per frame
cards --merge duplicates across overlapping frames--> listings --> flags, candidate match, score, store

The extraction for each video is cached as data/recordings/<sha256>.json, so a re-run never re-sends the
frames. Needs ANTHROPIC_API_KEY (no local OCR fallback - you chose Claude vision).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import db, research
from . import xianyu as X
from .config import Config

KINDS = ("xianyu_grid", "ebay_sold", "kleinanzeigen")

_STR = {"type": "string"}
_INT = {"type": "integer"}
_BOOL = {"type": "boolean"}

XIANYU_CARD = {
    "type": "object",
    "properties": {
        "title": _STR, "price_text": _STR, "wants": _INT, "seller": _STR,
        "badges": {"type": "array", "items": _STR}, "original_price_text": _STR, "partial": _BOOL,
        "product_type": _STR,
        "product_class": {"type": "string", "enum": ["handheld_console", "home_console", "retro_console", "portable_audio",
                                                     "headphones", "camera", "phone", "tablet", "laptop", "smart_glasses",
                                                     "accessory", "other"]},
        "likely_discontinued": _BOOL,
    },
    "required": ["title", "price_text", "wants", "seller", "badges", "original_price_text", "partial",
                 "product_type", "product_class", "likely_discontinued"],
    "additionalProperties": False,
}
SOLD_CARD = {
    "type": "object",
    "properties": {"title": _STR, "price_text": _STR, "currency": {"type": "string", "enum": ["EUR", "USD", "GBP", "CNY", ""]},
                   "date_text": _STR, "condition": _STR, "shipping_text": _STR, "partial": _BOOL},
    "required": ["title", "price_text", "currency", "date_text", "condition", "shipping_text", "partial"],
    "additionalProperties": False,
}


def _schema(card: dict) -> dict:
    return {"type": "object", "properties": {"frames": {"type": "array", "items": {
        "type": "object",
        "properties": {"frame": _STR, "page_kind": {"type": "string", "enum": list(KINDS) + ["other"]},
                       "cards": {"type": "array", "items": card}},
        "required": ["frame", "page_kind", "cards"], "additionalProperties": False}}},
        "required": ["frames"], "additionalProperties": False}


PROMPTS = {
    "xianyu_grid": (
        "These are consecutive screenshots of a Xianyu (Goofish) web page: a grid of listing cards from a search or a "
        "seller's storefront. For EVERY card in EVERY image return: title (exact Chinese text as shown, may be "
        "truncated), price_text (exactly as shown, e.g. '¥1699' or '¥1.88万'), wants (the number in 'N人想要', -1 if "
        "absent), seller (shop/user name under the card, '' if not visible), badges (small tags like '24小时内发布', "
        "'描述不符包邮退', '人气第5名'), original_price_text (a struck-through price, '' if none), partial (true if "
        "the card's title or price is cut off at the image edge), product_type (your best English model name from "
        "title AND photo, e.g. 'Sony PSP 3000', 'New Nintendo 3DS XL', 'iPhone 16 Pro Max'; read the photo when the "
        "title is a placeholder like '没了'), product_class, and likely_discontinued (is this model no longer "
        "manufactured?). Return frames in the order given, using the frame names provided. Do not invent cards."),
    "ebay_sold": (
        "These are consecutive screenshots of eBay sold-listing results (eBay 'Verkaufte Artikel' search or Terapeak). "
        "For EVERY listing return: title, price_text (the sold price exactly as shown), currency, date_text (the sold "
        "date as shown, e.g. 'Verkauft 12. Okt. 2026'), condition (e.g. 'Gebraucht'), shipping_text ('' if none), "
        "partial (true if cut off at the image edge). Return frames in the order given, with the frame names provided."),
    "kleinanzeigen": (
        "These are consecutive screenshots of Kleinanzeigen search results. For EVERY listing return: title, "
        "price_text (asking price as shown, including 'VB' if present), currency ('EUR'), date_text (as shown), "
        "condition ('' if not shown), shipping_text ('Versand möglich' etc. or ''), partial. Frames in order given."),
}


# ----------------------------------------------------------------------------- frames

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_frames(video: Path, out_dir: Path, fps: float = 1.0, max_width: int = 1568, max_frames: int = 90) -> list[Path]:
    """1 fps, drop near-identical frames (paused scrolling), scale to the API's sweet spot, JPEG."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found - install it (brew install ffmpeg / winget install ffmpeg)")
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = (f"fps={fps},mpdecimate=hi=64*24:lo=64*8:frac=0.33,setpts=N/FRAME_RATE/TB,"
          f"scale='min({max_width},iw)':-2")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", vf, "-fps_mode", "vfr", "-q:v", "3",
                    str(out_dir / "f_%03d.jpg")], check=True)
    frames = sorted(out_dir.glob("f_*.jpg"))
    if len(frames) > max_frames:
        raise RuntimeError(f"{len(frames)} frames - over the {max_frames} cap (config research.recordings.max_frames). "
                           "Record a shorter clip or raise the cap.")
    return frames


# ----------------------------------------------------------------------------- vision

@dataclass
class Extraction:
    kind: str
    frames: list[dict]                      # [{frame, page_kind, cards: [...]}]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)


def vision_extract(cfg: Config, frames: list[Path], kind: str, client=None) -> Extraction:
    import anthropic

    rc = (cfg.research or {}).get("recordings", {})
    client = client or anthropic.Anthropic()
    batch = int(rc.get("frames_per_request", 4))
    price = rc.get("price_per_mtok_usd", {"input": 4.0, "output": 20.0})
    card = XIANYU_CARD if kind == "xianyu_grid" else SOLD_CARD
    ex = Extraction(kind, [])
    for i in range(0, len(frames), batch):
        chunk = frames[i:i + batch]
        content = []
        for p in chunk:
            content.append({"type": "text", "text": f"Frame {p.stem}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                        "data": base64.standard_b64encode(p.read_bytes()).decode()}})
        content.append({"type": "text", "text": PROMPTS[kind]})
        try:
            resp = client.beta.messages.create(
                model=rc.get("model", "claude-opus-5-5"),
                max_tokens=16000,
                output_config={"effort": rc.get("effort", "medium"),
                               "format": {"type": "json_schema", "schema": _schema(card)}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.APIStatusError as e:
            ex.notes.append(f"frames {chunk[0].stem}-{chunk[-1].stem}: API error {e.status_code} - skipped")
            continue
        except anthropic.APIConnectionError:
            ex.notes.append(f"frames {chunk[0].stem}-{chunk[-1].stem}: network error - skipped")
            continue
        ex.input_tokens += resp.usage.input_tokens
        ex.output_tokens += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            ex.notes.append(f"frames {chunk[0].stem}-{chunk[-1].stem}: declined - skipped")
            continue
        if resp.stop_reason == "max_tokens":
            ex.notes.append(f"frames {chunk[0].stem}-{chunk[-1].stem}: output cut off - lower frames_per_request")
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            ex.frames += json.loads(text)["frames"]
        except (json.JSONDecodeError, KeyError):
            ex.notes.append(f"frames {chunk[0].stem}-{chunk[-1].stem}: unreadable answer - skipped")
    ex.cost_usd = round(ex.input_tokens / 1e6 * price["input"] + ex.output_tokens / 1e6 * price["output"], 3)
    return ex


# ----------------------------------------------------------------------------- processing

@dataclass
class RecordingResult:
    recording_id: int
    kind: str
    label: str | None
    frames: int
    listings: list[X.Listing] = field(default_factory=list)
    sellers: list[X.SellerSignal] = field(default_factory=list)
    changes: dict = field(default_factory=dict)
    discovered: list[dict] = field(default_factory=list)
    sold_saved: dict = field(default_factory=dict)       # candidate -> n (ebay_sold / kleinanzeigen)
    unmatched_sold: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    notes: list[str] = field(default_factory=list)


def _cache_dir(cfg: Config) -> Path:
    return cfg.path(Path((cfg.research or {}).get("recordings", {}).get("cache_dir", "data/recordings")))


def process(cfg: Config, video: Path, kind: str = "xianyu_grid", label: str | None = None,
            extraction: dict | None = None, client=None, force_candidate: str | None = None) -> RecordingResult:
    """Full pipeline. `extraction` (a cached/hand-made {frames: [...]}) skips ffmpeg + vision."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    digest = sha256(video) if video.exists() else hashlib.sha256(str(video).encode()).hexdigest()
    cache = _cache_dir(cfg) / f"{digest}.json"
    con = db.connect(cfg.path(cfg.paths.db))
    row = con.execute("SELECT id FROM recordings WHERE sha256 = ?", (digest,)).fetchone()
    con.close()
    if row and extraction is None and not cache.exists():
        raise ValueError("this video was already processed (same content)")
    notes, cost, cached = [], 0.0, False
    if extraction is not None:
        cached = True
    elif cache.exists():
        extraction, cached = json.loads(cache.read_text(encoding="utf-8")), True
    if extraction is None:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) and client is None:
            raise RuntimeError("reading recordings needs ANTHROPIC_API_KEY (Claude vision)")
        rc = (cfg.research or {}).get("recordings", {})
        with tempfile.TemporaryDirectory() as tmp:
            frames = extract_frames(video, Path(tmp), max_frames=int(rc.get("max_frames", 90)))
            ex = vision_extract(cfg, frames, kind, client=client)
        extraction = {"kind": kind, "frames": ex.frames, "input_tokens": ex.input_tokens,
                      "output_tokens": ex.output_tokens, "cost_usd": ex.cost_usd, "notes": ex.notes}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(extraction, ensure_ascii=False, indent=1), encoding="utf-8")
        notes += ex.notes
        cost = ex.cost_usd
    frames_data = extraction["frames"]

    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        if row:
            rec_id = row[0]
            con.execute("DELETE FROM xianyu_listings WHERE recording_id = ?", (rec_id,))
        else:
            cur = con.execute(
                "INSERT INTO recordings (file, sha256, kind, label, processed, frames, input_tokens, output_tokens, cost_usd) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (video.name, digest, kind, label, datetime.now().isoformat(timespec="seconds"), len(frames_data),
                 extraction.get("input_tokens", 0), extraction.get("output_tokens", 0), extraction.get("cost_usd", 0)))
            rec_id = cur.lastrowid
    con.close()
    res = RecordingResult(rec_id, kind, label, len(frames_data), cost_usd=cost, cached=cached, notes=notes)
    if kind == "xianyu_grid":
        _process_xianyu(cfg, res, frames_data)
    else:
        _process_sold(cfg, res, frames_data, kind, force_candidate)
    return res


def sell_prices(cfg: Config) -> dict[str, tuple[float, str]]:
    """Per candidate: (EUR, basis) for scoring. Sold median first (last 90 days, else 12 months);
    Browse API asking median minus the configured discount once that exists."""
    out = {}
    for a in research.assess_all(cfg):
        if a.eu_median_90d:
            out[a.cand["id"]] = (a.eu_median_90d, f"sold median, 90 days (n={a.eu_n})")
        elif a.eu_median:
            out[a.cand["id"]] = (a.eu_median, f"sold median (n={a.eu_n})")
    try:
        from . import ebay
        disc = float((cfg.research or {}).get("browse_discount", 0.15))
        for cid, med in ebay.latest_medians(cfg).items():
            out.setdefault(cid, (med * (1 - disc), f"eBay asking median −{disc:.0%} (estimate)"))
    except Exception:
        pass
    return out


def _process_xianyu(cfg: Config, res: RecordingResult, frames_data: list[dict]) -> None:
    listings = X.dedupe([(f["frame"], [_clean_card(c) for c in f.get("cards", [])]) for f in frames_data
                         if f.get("page_kind", "xianyu_grid") in ("xianyu_grid", "other")])
    cands, _, _ = research.load(cfg)
    cand_rows = cands.to_dict("records") if not cands.empty else []
    sells = sell_prices(cfg)
    for l in listings:
        l.flags = X.trap_flags(l)
        l.candidate = None if "wanted_ad" in l.flags else X.match_candidate(l, cand_rows)
        X.variant_check(l, next((c for c in cand_rows if c["id"] == l.candidate), None))
        X.score(cfg, l, sells)
    classes = tuple((cfg.research or {}).get("discovery_classes", X.DISCOVERY_CLASSES_DEFAULT))
    res.listings = listings
    res.sellers = X.seller_signals(listings, classes)
    # changes vs. the previous recording of the same sellers (Distill replacement)
    sellers = {l.seller for l in listings if l.seller}
    con = db.connect(cfg.path(cfg.paths.db))
    prev = []
    for s in sellers:
        last = con.execute("SELECT MAX(recording_id) FROM xianyu_listings WHERE seller = ? AND recording_id < ?",
                           (s, res.recording_id)).fetchone()[0]
        if last:
            prev += [dict(zip(("seller", "title", "price_rmb"), r)) for r in con.execute(
                "SELECT seller, title, price_rmb FROM xianyu_listings WHERE recording_id = ? AND seller = ?", (last, s))]
    with con:
        for l in listings:
            con.execute(
                "INSERT INTO xianyu_listings (recording_id, seller, title, price_rmb, price_text, wants, badges, "
                "original_price_rmb, product_type, product_class, likely_discontinued, flags, candidate, landed_eur, "
                "max_rmb, sell_eur, sell_basis, verdict, follow_up_zh, frames, seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (res.recording_id, l.seller, l.title, l.price_rmb, l.price_text, l.wants, json.dumps(l.badges, ensure_ascii=False),
                 l.original_price_rmb, l.product_type, l.product_class,
                 None if l.likely_discontinued is None else int(l.likely_discontinued), ",".join(l.flags), l.candidate,
                 l.landed_eur, l.max_rmb, l.sell_eur, l.sell_basis, l.verdict, l.follow_up_zh, ",".join(l.frames),
                 date.today().isoformat()))
    con.close()
    res.changes = X.changes_vs_previous(listings, prev) if prev else {}
    # discovery: new candidates from product types worth a look
    known = {c["id"] for c in cand_rows}
    names = {str(c["name"]).lower() for c in cand_rows}
    for d in X.discovery(listings, known, names, classes):
        ex = d["examples"][0]
        cid = research.add_candidate(cfg, d["name"], id=d["id"], status="new",
                                     notes=f"Found in recording #{res.recording_id}: {ex['seller']} ¥{ex['price_rmb']:.0f} "
                                           f"'{ex['title'][:40]}'")
        res.discovered.append({**d, "id": cid})


def _clean_card(c: dict) -> dict:
    """Vision output uses '' / -1 for 'none' (strict schemas have no nulls)."""
    c = dict(c)
    for k in ("original_price_text", "seller", "product_type"):
        if c.get(k) == "":
            c[k] = None
    if isinstance(c.get("wants"), (int, float)) and c["wants"] < 0:
        c["wants"] = None
    return c


def _process_sold(cfg: Config, res: RecordingResult, frames_data: list[dict], kind: str, force: str | None) -> None:
    seen, points_by = set(), {}
    cands, _, _ = research.load(cfg)
    cand_rows = cands.to_dict("records") if not cands.empty else []
    for f in frames_data:
        for c in f.get("cards", []):
            if c.get("partial"):
                continue
            prices = research.find_prices(c.get("price_text", ""), c.get("currency") or "EUR")
            if not prices:
                continue
            key = (X.norm_title(c.get("title")), prices[0][0], c.get("date_text"))
            if key in seen:
                continue
            seen.add(key)
            cid = force or X.match_candidate(X.Listing(title=c.get("title", ""), price_rmb=None), cand_rows)
            if not cid:
                res.unmatched_sold += 1
                continue
            points_by.setdefault(cid, []).append(research.PricePoint(
                prices[0][0], prices[0][1], research.find_date(c.get("date_text", "")), c.get("title"),
                c.get("condition") or None))
    evidence_kind = "eu_sold" if kind == "ebay_sold" else "eu_asking"
    for cid, pts in points_by.items():
        res.sold_saved[cid] = research.add_evidence(cfg, cid, evidence_kind, pts, f"recording:{kind}")


# ----------------------------------------------------------------------------- report

def report_md(res: RecordingResult) -> str:
    L = []
    when = date.today().isoformat()
    L.append(f"# Recording #{res.recording_id} · {res.kind}{' · ' + res.label if res.label else ''} · {when}\n")
    L.append(f"{res.frames} frames read{' (from cache, no API cost)' if res.cached else f', API cost ≈ ${res.cost_usd:.2f}'}.")
    for n in res.notes:
        L.append(f"\n> ⚠ {n}")
    if res.kind != "xianyu_grid":
        L.append(f"\nSaved: " + (", ".join(f"{n} → {cid}" for cid, n in res.sold_saved.items()) or "nothing matched a candidate")
                 + (f". {res.unmatched_sold} listings matched no candidate (add match terms, or use --candidate)." if res.unmatched_sold else "."))
        return "\n".join(L) + "\n"
    ls = res.listings
    rel = [l for l in ls if l.candidate]
    L.append(f"{len(ls)} listings, {len(rel)} match a candidate, "
             f"{sum('wanted_ad' in l.flags for l in ls)} want-ads, {sum('multi_sku' in l.flags for l in ls)} multi-variant.\n")
    order = {"buy-check": 0, "close": 1, "ask": 2, "no EU price yet": 3, "too high": 4, "skip": 5, None: 6}
    if rel:
        L.append("## Your candidates in this recording\n")
        L.append("| Verdict | Candidate | Price | Landed | Max | Spread | Wants | Flags | Title |")
        L.append("|:---|:---|---:|---:|---:|---:|---:|:---|:---|")
        for l in sorted(rel, key=lambda l: (order.get(l.verdict, 6), l.candidate)):
            L.append(f"| **{l.verdict or '–'}** | {l.candidate} | ¥{l.price_rmb:,.0f} | €{l.landed_eur:,.0f} | "
                     f"{('¥%.0f' % l.max_rmb) if l.max_rmb is not None else '–'} | "
                     f"{('€%.0f' % l.spread_eur) if l.spread_eur is not None else '–'} | {l.wants or ''} | "
                     f"{', '.join(l.flags)} | {l.title[:40]} |")
        bases = sorted({f"{l.candidate}: {l.sell_basis}" for l in rel if l.sell_basis})
        if bases:
            L.append("\nSell price used: " + "; ".join(bases))
        L.append("\nLanded = quick mode (RMB ÷ rate + €10). Use `resell calc` for the full breakdown before buying.")
    asks = [l for l in ls if l.follow_up_zh and l.candidate]
    if asks:
        L.append("\n## Ask the seller before deciding\n")
        for l in asks:
            L.append(f"- **{l.title[:40]}** ({l.seller}):\n  `{l.follow_up_zh}`")
    if res.changes:
        new, drops = res.changes.get("new", []), res.changes.get("price_drops", [])
        L.append("\n## Changes since this seller's last recording\n")
        L.append(f"- {len(new)} new listings" + (": " + "; ".join(f"{l.title[:30]} ¥{l.price_rmb:,.0f}" for l in new[:10]) if new else ""))
        for l, old in drops:
            L.append(f"- Price drop: {l.title[:30]} ¥{old:,.0f} → ¥{l.price_rmb:,.0f}")
    if res.discovered:
        L.append("\n## New on the radar (added as `new` candidates)\n")
        for d in res.discovered:
            ex = d["examples"][0]
            L.append(f"- **{d['name']}** — {ex['seller']} ¥{ex['price_rmb']:,.0f} ({ex['title'][:40]})")
    L.append("\n## Sellers\n")
    for s in res.sellers:
        tag = "🟢 likely supplier/modder" if s.supplier else ("👀 has your kind of stock" if s.watch else "one-off / not your stock")
        L.append(f"- **{s.seller}**: {s.listings} listings, {s.relevant} relevant ({s.relevant_share:.0%}) - {tag}"
                 + (f"; repeats: {', '.join(s.repeat_products)}" if s.repeat_products else "")
                 + (f"; wording: {', '.join(s.supplier_words)}" if s.supplier_words else ""))
    skipped = [l for l in ls if "wanted_ad" in l.flags or "service" in l.flags]
    if skipped:
        L.append("\n<details><summary>Skipped (want-ads / services)</summary>\n")
        for l in skipped:
            L.append(f"- {', '.join(l.flags)}: {l.title[:50]} ¥{l.price_rmb:,.0f}")
        L.append("\n</details>")
    return "\n".join(L) + "\n"


def write_report(cfg: Config, res: RecordingResult) -> Path:
    out = cfg.path(cfg.paths.reports) / "recordings"
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in (res.label or res.kind) if ch.isalnum() or ch in "-_")[:30] or res.kind
    p = out / f"{date.today().isoformat()}_{res.recording_id}_{safe}.md"
    p.write_text(report_md(res), encoding="utf-8")
    return p
