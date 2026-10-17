"""Command line entry point: `resell import`, `resell report`."""
from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd
import typer

from . import db, enrich
from .config import load_config
from .importers.order_sheets import read_orders
from .importers.sales_sheet import read_sales

app = typer.Typer(add_completion=False, help="resell-toolkit: local tools for the reselling business")

ConfigOpt = typer.Option(Path("config.yaml"), "--config", "-c", help="Path to config.yaml")


def _load(cfg):
    wb = openpyxl.load_workbook(cfg.path(cfg.paths.workbook), data_only=True, read_only=False)
    sales, issues = read_sales(wb, cfg.sales_sheet)
    purchases, p_issues = read_orders(wb, cfg.order_sheet_prefix)
    sales = enrich.enrich_sales(sales, cfg)
    purchases = enrich.enrich_purchases(purchases, cfg)
    purchases = enrich.match_purchases(sales, purchases, cfg)
    sales, evidence = enrich.resolve_sellers(sales, purchases, cfg)
    return sales, purchases, pd.DataFrame(issues + p_issues, columns=["sheet", "cells", "raw", "reason"]), evidence


def _store(cfg, sales, purchases, issues, evidence):
    con = db.connect(cfg.path(cfg.paths.db))
    with con:
        db.reset(con)
        db.write(con, "sales", sales)
        db.write(con, "purchases", purchases)
        db.write(con, "parse_issues", issues.astype(str))
        sf = pd.DataFrame([{"key": k, "seller": v, "rows": sum(evidence["votes"].get(k, {}).values())}
                           for k, v in evidence["learned"].items()], columns=["key", "seller", "rows"])
        db.write(con, "storefronts", sf)
    con.close()


@app.command("import")
def import_cmd(config: Path = ConfigOpt):
    """Read the workbook (read-only) and rebuild the local SQLite database."""
    cfg = load_config(config)
    sales, purchases, issues, evidence = _load(cfg)
    _store(cfg, sales, purchases, issues, evidence)
    typer.echo(f"sales: {len(sales)} rows ({int((sales.is_stock == 0).sum())} sold, {int(sales.is_stock.sum())} stock)")
    typer.echo(f"purchases: {len(purchases)} order lines, {int(purchases.sales_row.notna().sum())} matched to sales rows")
    typer.echo(f"parse issues logged: {len(issues)}  ->  {cfg.path(cfg.paths.db)}")
    typer.echo("inspections and seller notes are kept (never touched by import)")


@app.command()
def report(config: Path = ConfigOpt):
    """Import, then write the Phase 0 analysis report (markdown + charts)."""
    from .report import build_report

    cfg = load_config(config)
    sales, purchases, issues, evidence = _load(cfg)
    _store(cfg, sales, purchases, issues, evidence)
    path = build_report(sales, purchases, issues, evidence, cfg)
    typer.echo(f"report written to {path}")


# ----------------------------------------------------------------------------- Phase 1

def _money(v):
    return f"€{v:,.2f}"


def _print_result(r, rate_hint: str = ""):
    from .calc import FAIL, PASS

    L, S = r.landed, r.selling
    typer.echo(f"\n{r.inputs.category}: ¥{r.inputs.rmb:,.0f} at {L.rate:.2f} RMB/€{rate_hint}")
    rows = [("Goods", L.goods), ("Agent fee", L.agent_fee), ("Intl shipping", L.shipping),
            (f"Customs ({L.duty_note})", L.duty),
            ("Import VAT" + ("" if L.vat_on else " (off for this category)"), L.import_vat),
            ("Parts", L.parts)]
    for k, v in rows:
        if v or k.startswith(("Import VAT", "Customs")):
            typer.echo(f"  {k:<52} {_money(v):>10}")
    typer.echo(f"  {'Landed cost':<52} {_money(L.total):>10}")
    typer.echo(f"\n  Sell on {S.platform} at {_money(S.price)} ({S.price_source})")
    for k, v in (("Platform fee", S.fee), ("Shipping to buyer", S.outbound_shipping), ("Returns reserve", S.returns_reserve)):
        if v:
            typer.echo(f"  {k:<52} {_money(-v):>10}")
    typer.echo(f"  {'Profit':<52} {_money(r.profit):>10}")
    typer.echo(f"  Profit on cost {r.profit_on_cost:.0%} · margin {r.margin:.0%} · break-even sell {_money(r.break_even_sell)}")
    h = r.history
    if h.get("n"):
        typer.echo(f"  Your history: {h['n']} sold, median {_money(h['median_sell'])}, "
                   f"{h['median_days']:.0f} days, {h['flaw_rate']:.0%} flawed")
    typer.echo("")
    for c in r.criteria:
        mark = {PASS: "✓", FAIL: "✗"}.get(c["status"], "~")
        typer.echo(f"  {mark} {c['name']:<30} {c['status']:<10} {c['reason']}")
    typer.echo(f"\n  ==> {r.verdict}: {r.verdict_reason}\n")


def _rate_hint(cfg) -> str:
    from .calc import live_rate, recent_order_rate

    bits = []
    if (o := recent_order_rate(cfg)) is not None:
        bits.append(f"your orders, last 60 days {o:.2f}")
    if (lv := live_rate()) is not None:
        bits.append(f"ECB today {lv:.2f}")
    return f"  ({'; '.join(bits)})" if bits else ""


@app.command()
def calc(
    rmb: float = typer.Argument(..., help="Price per unit in RMB"),
    category: str = typer.Option(..., "--category", "-k", help="N3DS, iPod, Vita card, ..."),
    sell: float = typer.Option(None, "--sell", "-s", help="Expected sell price; default = median of your sales"),
    match: str = typer.Option(None, "--match", "-m", help="Narrow your sales history, e.g. '98' or '1T'"),
    qty: int = typer.Option(1, "--qty", "-q"),
    parts: float = typer.Option(0.0, "--parts", help="Repair/upgrade parts per unit, EUR"),
    weight: float = typer.Option(None, "--weight", help="kg per unit; default from category"),
    platform: str = typer.Option(None, "--platform", "-p", help="kleinanzeigen | ebay | vinted"),
    rate: float = typer.Option(None, "--rate", help="Override RMB per EUR"),
    vat: bool = typer.Option(None, "--vat/--no-vat", help="Force import VAT on/off"),
    config: Path = ConfigOpt,
):
    """Landed cost, profit and the 5-criteria verdict for one listing."""
    from .calc import Inputs, calculate

    cfg = load_config(config)
    r = calculate(cfg, Inputs(category=category, rmb=rmb, sell_eur=sell, qty=qty, parts_eur=parts,
                              weight_kg=weight, platform=platform, rate=rate, import_vat=vat, match=match))
    _print_result(r, _rate_hint(cfg))


@app.command()
def maxbuy(
    category: str = typer.Option(..., "--category", "-k"),
    sell: float = typer.Option(None, "--sell", "-s", help="Target sell price; default = median of your sales"),
    profit: float = typer.Option(None, "--profit", help="Minimum profit in EUR (default from config)"),
    poc: float = typer.Option(None, "--poc", help="Minimum profit on cost, e.g. 0.5"),
    match: str = typer.Option(None, "--match", "-m"),
    qty: int = typer.Option(1, "--qty", "-q"),
    parts: float = typer.Option(0.0, "--parts"),
    platform: str = typer.Option(None, "--platform", "-p"),
    rate: float = typer.Option(None, "--rate"),
    vat: bool = typer.Option(None, "--vat/--no-vat"),
    config: Path = ConfigOpt,
):
    """Reverse mode: the most RMB you should pay for one unit."""
    from .calc import Inputs, max_rmb

    cfg = load_config(config)
    m = max_rmb(cfg, Inputs(category=category, sell_eur=sell, qty=qty, parts_eur=parts, platform=platform,
                            rate=rate, import_vat=vat, match=match), profit, poc)
    r = m.at_max
    typer.echo(f"\nMax price for {category}{' (' + match + ')' if match else ''}: ¥{m.max_rmb:,.0f}"
               f"  (≈ {_money(r.landed.total)} landed)")
    typer.echo(f"  sells at {_money(r.selling.price)} ({r.selling.price_source})")
    typer.echo(f"  targets: ≥ €{m.min_profit_eur:.0f} profit and ≥ {m.min_profit_on_cost:.0%} on cost "
               f"- the limit is set by {m.binding}")
    typer.echo(f"  at that price: profit {_money(r.profit)}, {r.profit_on_cost:.0%} on cost\n")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, config: Path = ConfigOpt):
    """Local web page for the calculator (http://127.0.0.1:8765)."""
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(config), host=host, port=port)


# ----------------------------------------------------------------------------- Phase 2

@app.command()
def msg(
    category: str = typer.Argument("N3DS", help="N3DS, iPod, Vita card, generic"),
    snippet: str = typer.Option(None, "--snippet", "-s", help="video, serial_unclear, white_screen, discount, ..."),
    zh_only: bool = typer.Option(False, "--zh", help="Only the Chinese text (for piping to the clipboard)"),
):
    """Pre-purchase message for a Xianyu seller (Chinese + German)."""
    from . import inspection as I

    if snippet:
        snips = I.messages()["snippets"]
        if snippet not in snips:
            raise typer.BadParameter(f"unknown snippet - known: {', '.join(snips)}")
        zh, de, title, why = snips[snippet]["zh"], snips[snippet]["de"], snippet, []
    else:
        key, t = I.template_for(category)
        zh, de, title, why = t["zh"], t["de"], t["title"], t.get("why", [])
    if zh_only:
        typer.echo(zh.strip())
        return
    typer.echo(f"\n── {title} ── (send this)\n")
    typer.echo(zh.strip())
    typer.echo("\n── Deutsch ──\n")
    typer.echo(de.strip())
    for w in why:
        typer.echo(f"  · {w}")
    typer.echo("\nTip: `resell msg N3DS --zh | pbcopy` (macOS) or `| clip` (Windows) copies it.\n")


@app.command()
def inspect(
    category: str = typer.Option(..., "--category", "-k"),
    item: str = typer.Option(None, "--item", help="Name as in your sheet, e.g. 'N3DSLL dual IPS white 98'"),
    seller: str = typer.Option(None, "--seller"),
    listing: str = typer.Option(None, "--listing", help="Goofish link (seller is looked up from it)"),
    buy_date: str = typer.Option(None, "--bought", help="YYYY-MM-DD"),
    price: float = typer.Option(None, "--paid", help="Landed price paid, EUR"),
    serial: str = typer.Option(None, "--serial"),
    precheck: str = typer.Option(None, "--precheck", help="answered | partial | ignored | not_sent"),
    config: Path = ConfigOpt,
):
    """Arrival inspection, check by check, saved to the local DB."""
    from . import inspection as I

    cfg = load_config(config)
    if serial:
        for w in I.serial_warnings(cfg, serial):
            typer.secho(f"⚠ {w}", fg="red")
    typer.echo(f"\n{category} arrival check - answer o(k) / m(inor) / f(law) / n(ot applicable)\n")
    keys = {"o": "ok", "m": "minor", "f": "flaw", "n": "na", "": "ok"}
    results, notes = {}, {}
    for c in I.checklist_for(category):
        while True:
            a = typer.prompt(f"  {c['label']} - {c['how']} [o/m/f/n]", default="o", show_default=False).strip().lower()[:1]
            if a in keys and (a != "m" or c.get("minor_ok")):
                break
            typer.echo("    (o, f or n" + (", m" if c.get("minor_ok") else "") + ")")
        results[c["key"]] = keys[a]
        if keys[a] in ("minor", "flaw"):
            notes[c["key"]] = typer.prompt("    note", default="", show_default=False)
    if precheck is None:
        precheck = typer.prompt("Seller checklist (answered/partial/ignored/not_sent)", default="not_sent")
    comment = typer.prompt("Comment (optional)", default="", show_default=False) or None
    iid = I.save_inspection(cfg, category=category, results=results, item=item, seller=seller, listing=listing,
                            buy_date=buy_date, price_paid=price, serial=serial, precheck=precheck,
                            check_notes=notes, comment=comment)
    flawed, minor, tags = I.evaluate(category, results, cfg)
    state = "clean" if not flawed else ("minor flaw" if minor else "FLAWED")
    typer.echo(f"\nSaved inspection #{iid}: {state}{' (' + tags + ')' if tags else ''}")


@app.command()
def sellers(category: str = typer.Option(None, "--category", "-k"), min_units: int = 2, config: Path = ConfigOpt):
    """Seller scorecard from your sales history and inspections."""
    from . import inspection as I

    card = I.scorecard(load_config(config), category)
    if card.empty:
        typer.echo("no data - run `resell import` first")
        return
    card = card[card.bought >= min_units]
    typer.echo(f"\n{'Seller':<28}{'Bought':>7}{'Flawed':>8}{'Major':>7}{'Profit':>8}{'On cost':>9}  {'Last':<11}{'Checklist':>10}  Status")
    for r in card.itertuples():
        chk = f"{r.checklist_answered}/{r.checklist_asked}" if r.checklist_asked else "-"
        profit = f"€{r.avg_profit:.0f}" if pd.notna(r.avg_profit) else "-"
        poc = f"{r.profit_on_cost:.0%}" if pd.notna(r.profit_on_cost) else "-"
        last = r.last_bought.date().isoformat() if pd.notna(r.last_bought) else "-"
        typer.echo(f"{str(r.seller)[:27]:<28}{r.bought:>7}{r.flaw_rate:>8.0%}{r.major_flaw_rate:>7.0%}{profit:>8}{poc:>9}  "
                   f"{last:<11}{chk:>10}  {r.status if isinstance(r.status, str) else ''}"
                   f"{' - ' + r.notes if isinstance(r.notes, str) else ''}")
    typer.echo("")


@app.command("seller-note")
def seller_note(seller: str, status: str = typer.Option(None, help="preferred | ok | watch | avoid"),
                note: str = typer.Option(None), config: Path = ConfigOpt):
    """Set a seller's status and notes (kept across imports)."""
    from . import inspection as I

    I.set_seller_note(load_config(config), seller, status, note)
    typer.echo(f"saved: {seller}")


# ----------------------------------------------------------------------------- Phase 3

@app.command("reply")
def reply_cmd(
    message: str = typer.Argument("-", help="Buyer message ('-' = read from stdin)"),
    listing: str = typer.Option(..., "--listing", "-l", help="Fact sheet id, e.g. n3ds-dual-ips"),
    last: float = typer.Option(None, "--last", help="Your last counter-offer in this chat, EUR"),
    mode: str = typer.Option(None, "--mode", help="pickup | shipping (default: detected)"),
    name: str = typer.Option(None, "--name", help="Buyer's name (default: from their sign-off)"),
    llm: bool = typer.Option(None, "--llm/--no-llm", help="Word it with Claude (needs ANTHROPIC_API_KEY)"),
    config: Path = ConfigOpt,
):
    """Draft a Kleinanzeigen reply in your tone."""
    import sys

    from . import replies as R

    text = sys.stdin.read() if message == "-" else message
    r = R.reply(load_config(config), listing, text, my_last=last, mode=mode, name=name, use_llm=llm)
    a, d = r.analysis, r.decision
    typer.echo(f"\nintents: {', '.join(a.intents)}" + (f" · offer €{a.offer:.0f}" if a.offer else "")
               + (f" · {a.mode}" if a.mode else "") + (f" · time: {a.time}" if a.time else ""))
    if d.action != "none":
        typer.echo(f"price: {d.action} {('€%.0f' % d.price) if d.price else ''} ({d.reason})")
    for fl in a.flags:
        typer.secho(f"{'⛔' if fl['severity'] == 'scam' else '⚠'} {fl['key']}: {fl['note']}",
                    fg="red" if fl["severity"] == "scam" else "yellow")
    typer.echo(f"\n{r.draft}\n")
    for t in r.todo:
        if not any(t.startswith(fl["key"]) for fl in a.flags):
            typer.secho(f"→ {t}", fg="cyan")
    typer.echo(f"({r.source}{'; example fact sheets - run `resell replies-init`' if r.using_examples else ''})")


@app.command("replies-init")
def replies_init(config: Path = ConfigOpt):
    """Copy the example fact sheets and seller profile to data/ (gitignored) for editing."""
    from . import replies as R

    made = R.init_local_files(load_config(config))
    for p in made:
        typer.echo(f"created {p} - edit it")
    if not made:
        typer.echo("data/factsheets.yaml and data/seller_profile.yaml already exist")


@app.command()
def espanso(config: Path = ConfigOpt):
    """Print reply snippets as an Espanso match file (pipe into ~/.config/espanso/match/kleinanzeigen.yml)."""
    from . import replies as R

    typer.echo(R.espanso_yaml(load_config(config)))


# ----------------------------------------------------------------------------- Phase 4: research

@app.command()
def research(candidate: str = typer.Argument(None, help="Candidate id for details; empty = compare all"),
             config: Path = ConfigOpt):
    """Compare candidate categories on your five criteria (or show one in detail)."""
    from . import research as RS

    cfg = load_config(config)
    if RS.load(cfg)[0].empty:
        typer.echo(f"seeded {RS.seed(cfg)} candidates")
    rows = RS.assess_all(cfg)
    if candidate:
        a = next((x for x in rows if x.cand["id"] == candidate), None)
        if not a:
            raise typer.BadParameter(f"unknown candidate - known: {', '.join(x.cand['id'] for x in rows)}")
        typer.echo(f"\n{a.cand['name']}  ==> {a.verdict}: {a.verdict_reason}\n")
        for i, c in enumerate(a.criteria, 1):
            typer.echo(f"  {c['status']:<10} {c['name'][3:]}: {c['reason']}")
            for f in a.findings.get(i, []):
                typer.echo(f"             • {f['text']}" + (f"  [{f['source']}]" if f.get("source") else ""))
        for f in a.findings.get(0, []):
            typer.echo(f"  • {f['text']}")
        if a.estimate:
            r = a.estimate
            typer.echo(f"\n  At median prices: buy ¥{r.inputs.rmb:.0f} → landed €{r.landed.total:.0f}, sell €{r.selling.price:.0f}"
                       f" → €{r.profit:.0f} profit ({r.profit_on_cost:.0%} on cost); pay at most ¥{a.max_rmb or 0:.0f}")
        for w in a.warnings:
            typer.secho(f"  ⚠ {w}", fg="yellow")
        for s in a.next_steps:
            typer.secho(f"  → {s}", fg="cyan")
        typer.echo("")
        return
    typer.echo(f"\n{'Candidate':<44}{'Verdict':<12}{'1 2 3 4 5':<11}{'EU med':>8}{'CN med':>8}{'Profit':>8}  Next step")
    for a in rows:
        marks = " ".join({"PASS": "✓", "FAIL": "✗", "BORDERLINE": "~"}.get(c["status"], "?") for c in a.criteria)
        prof = (f"€{a.estimate.profit:.0f}" if a.estimate else f"€{a.own['profit']:.0f}*" if a.own else "-")
        typer.echo(f"{a.cand['name'][:43]:<44}{a.verdict:<12}{marks:<11}"
                   f"{('€%.0f' % a.eu_median) if a.eu_median else '-':>8}{('¥%.0f' % a.cn_median_rmb) if a.cn_median_rmb else '-':>8}"
                   f"{prof:>8}  {(a.next_steps[0] if a.next_steps else '')[:70]}")
    typer.echo("\n✓ pass  ~ borderline  ✗ fail  ? unknown   * = from your own sales.  Details: resell research <id>\n")


@app.command("research-add")
def research_add(name: str, config: Path = ConfigOpt):
    """Add a new candidate category."""
    from . import research as RS

    typer.echo(f"added: {RS.add_candidate(load_config(config), name)}")


@app.command("research-paste")
def research_paste(candidate: str, source: str = typer.Option("terapeak", help="terapeak | ebay_sold | pricecharting | manual"),
                   dry_run: bool = typer.Option(False, "--dry-run", help="Only show what was read"),
                   config: Path = ConfigOpt):
    """Read EU sold prices from stdin (copy-paste of Terapeak / eBay sold results)."""
    import sys

    from . import research as RS

    pts = RS.parse_sold_paste(sys.stdin.read())
    for p in pts[:15]:
        typer.echo(f"  {p.date or '-':<11} {p.currency} {p.price:>8.2f}  {(p.title or '')[:60]}")
    if len(pts) > 15:
        typer.echo(f"  ... {len(pts) - 15} more")
    if not pts:
        typer.echo("no prices found (they need a currency: €, EUR, $, £)")
    elif not dry_run:
        typer.echo(f"saved {RS.add_evidence(load_config(config), candidate, 'eu_sold', pts, source)} prices")


@app.command("research-cn")
def research_cn(candidate: str, text: str = typer.Argument(..., help="Xianyu share text or a price"), config: Path = ConfigOpt):
    """Add a Xianyu asking price (share text, '¥680 title', or just 680)."""
    from . import research as RS

    p = RS.parse_cn_text(text)
    if not p:
        raise typer.BadParameter("no price found")
    RS.add_evidence(load_config(config), candidate, "cn_asking", [p], "xianyu")
    typer.echo(f"added ¥{p.price:.0f} {p.title or ''}")


# ----------------------------------------------------------------------------- Phase 4d: recordings

@app.command()
def record(
    video: Path = typer.Argument(..., help="Screen recording (.mp4/.mov)"),
    kind: str = typer.Option("xianyu_grid", "--kind", "-k", help="xianyu_grid | ebay_sold | kleinanzeigen"),
    label: str = typer.Option(None, "--label", "-l", help="e.g. the seller or search term"),
    candidate: str = typer.Option(None, "--candidate", "-c", help="ebay_sold/kleinanzeigen: assign all rows to this candidate"),
    extraction: Path = typer.Option(None, "--from-json", help="Use an existing extraction JSON instead of Claude vision"),
    config: Path = ConfigOpt,
):
    """Read a screen recording (Xianyu grid, eBay sold, Kleinanzeigen) into listings and a report."""
    import json as _json

    from . import recordings as REC

    cfg = load_config(config)
    ext = _json.loads(extraction.read_text(encoding="utf-8")) if extraction else None
    res = REC.process(cfg, video, kind=kind, label=label, extraction=ext, force_candidate=candidate)
    path = REC.write_report(cfg, res)
    typer.echo(REC.report_md(res))
    typer.echo(f"report: {path}")


# ----------------------------------------------------------------------------- Phase 4b: eBay Browse API

@app.command("ebay-scan")
def ebay_scan(candidate: list[str] = typer.Option(None, "--candidate", "-c", help="Only these candidate ids"),
              de_only: bool = typer.Option(False, "--de-only", help="Skip the EU-wide context run"),
              config: Path = ConfigOpt):
    """Weekly eBay snapshot per candidate (official Browse API): listings, prices, probable sales."""
    from . import ebay as EB

    snaps = EB.scan(load_config(config), candidate or None, ("de",) if de_only else ("de", "eu"))
    typer.echo(f"\n{'Candidate':<16}{'Scope':<6}{'Active':>7}{'p25':>7}{'Median':>8}{'p75':>7}{'Auct.':>7}{'Prob. sales':>12}")
    for s in snaps:
        f = lambda v: f"€{v:.0f}" if v is not None else "-"
        typer.echo(f"{s.candidate:<16}{s.scope:<6}{s.kept:>7}{f(s.p25):>7}{f(s.median):>8}{f(s.p75):>7}"
                   f"{(f'{s.auction_share:.0%}' if s.auction_share is not None else '-'):>7}"
                   f"{(str(s.probable_sales) if s.probable_sales is not None else 'first run' if s.complete else 'incomplete'):>12}")
    typer.echo("\nProbable sales = listings that vanished before their end date since the last run (an estimate, not sold data).")
