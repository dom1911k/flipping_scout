"""Local web page for the Phase 1 calculator: FastAPI + HTMX, one page, no accounts, binds to 127.0.0.1.

Works without JavaScript too: the forms post normally and the server returns the whole page.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from . import calc
from . import inspection as insp
from .config import load_config

env = Environment(loader=PackageLoader("resell", "templates"), autoescape=select_autoescape(["html"]))


def _num(v: str | None) -> float | None:
    """Accept '¥1,580', '1580元', '1.580' (German thousands), '1580.5'."""
    if v is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(v))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.search(r",\d{3}$", s) else s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def _vat(v: str | None) -> bool | None:
    return {"on": True, "off": False}.get(v or "auto")


def create_app(config_path: str | Path = "config.yaml") -> FastAPI:
    app = FastAPI(title="resell-toolkit", docs_url=None, redoc_url=None)

    def cfg():
        return load_config(config_path)  # re-read each request, so config edits apply without restart

    def categories(c):
        names = [p for p in c.category_profiles] + [r.name for r in c.categories]
        return list(dict.fromkeys(names))

    def page(request: Request, form: dict, result=None, maxbuy=None, error=None, mode="max"):
        c = cfg()
        ctx = {"active": "/", "categories": categories(c), "platforms": list(c.costs.get("platforms_sell", {})),
               "default_platform": c.costs.get("default_platform"), "form": form, "result": result,
               "maxbuy": maxbuy, "error": error, "mode": mode, "rate": c.costs.get("rmb_per_eur_effective"),
               "targets": c.targets, "order_rate": calc.recent_order_rate(c)}
        partial = request.headers.get("HX-Request") == "true"
        tpl = env.get_template("_result.html" if partial else "calc.html")
        return HTMLResponse(tpl.render(**ctx))

    def inputs(form: dict) -> calc.Inputs:
        return calc.Inputs(
            category=form.get("category") or "N3DS",
            rmb=_num(form.get("rmb")),
            sell_eur=_num(form.get("sell")),
            qty=int(_num(form.get("qty")) or 1),
            parts_eur=_num(form.get("parts")) or 0.0,
            platform=form.get("platform") or None,
            rate=_num(form.get("rate")),
            import_vat=_vat(form.get("vat")),
            match=(form.get("match") or "").strip() or None,
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, {"category": "N3DS", "qty": "1"})

    @app.post("/max", response_class=HTMLResponse)
    async def post_max(request: Request):
        form = dict(await request.form())
        try:
            m = calc.max_rmb(cfg(), inputs(form), _num(form.get("min_profit")), _pct(form.get("min_poc")))
            return page(request, form, maxbuy=m, mode="max")
        except ValueError as e:
            return page(request, form, error=str(e), mode="max")

    @app.post("/calc", response_class=HTMLResponse)
    async def post_calc(request: Request):
        form = dict(await request.form())
        inp = inputs(form)
        if inp.rmb is None:
            return page(request, form, mode="calc")  # shows the "enter a price" hint
        try:
            return page(request, form, result=calc.calculate(cfg(), inp), mode="calc")
        except ValueError as e:
            return page(request, form, error=str(e), mode="calc")

    # ------------------------------------------------------------------ Phase 2 pages

    @app.get("/messages", response_class=HTMLResponse)
    def messages_page(category: str = "N3DS"):
        m = insp.messages()
        key, t = insp.template_for(category)
        return HTMLResponse(env.get_template("messages.html").render(
            active="/messages", templates=m["templates"], key=key, t=t, snippets=m["snippets"]))

    def inspect_page(category: str, form: dict | None = None, saved=None, warnings=(), error=None):
        c = cfg()
        recent = insp.list_inspections(c, 30).to_dict("records")
        card = insp.scorecard(c)
        names = sorted(card.seller.astype(str)) if not card.empty else []
        return HTMLResponse(env.get_template("inspect.html").render(
            active="/inspect", categories=list(insp.checklists()["checklists"]), category=category,
            checks=insp.checklist_for(category), form=form or {}, saved=saved, warnings=warnings, error=error,
            recent=recent, seller_names=names, precheck=insp.PRECHECK))

    @app.get("/inspect", response_class=HTMLResponse)
    def inspect_get(category: str = "N3DS"):
        return inspect_page(category)

    @app.post("/inspect", response_class=HTMLResponse)
    async def inspect_post(request: Request):
        form = dict(await request.form())
        category = form.get("category") or "N3DS"
        checks = insp.checklist_for(category)
        results = {ch["key"]: form.get(f"r_{ch['key']}", "ok") for ch in checks}
        notes = {ch["key"]: form[f"note_{ch['key']}"] for ch in checks if form.get(f"note_{ch['key']}")}
        c = cfg()
        warnings = insp.serial_warnings(c, form.get("serial"))
        try:
            iid = insp.save_inspection(
                c, category=category, results=results, item=form.get("item") or None,
                seller=form.get("seller") or None, listing=form.get("listing") or None,
                buy_date=form.get("buy_date") or None, price_paid=_num(form.get("price_paid")),
                serial=form.get("serial") or None, precheck=form.get("precheck") or None,
                check_notes=notes, comment=form.get("comment") or None)
        except ValueError as e:
            return inspect_page(category, form, error=str(e))
        flawed, minor, tags = insp.evaluate(category, results, c)
        state = "clean" if not flawed else (f"minor flaw ({tags})" if minor else f"flawed ({tags})")
        return inspect_page(category, None, saved={"id": iid, "state": state}, warnings=warnings)

    @app.post("/inspect/serial", response_class=HTMLResponse)
    async def inspect_serial(request: Request):
        form = dict(await request.form())
        ws = insp.serial_warnings(cfg(), form.get("serial"))
        return HTMLResponse("".join(f'<div class="warn-msg">⚠ {w}</div>' for w in ws))

    @app.post("/inspect/{iid}/delete")
    def inspect_delete(iid: int):
        insp.delete_inspection(cfg(), iid)
        return RedirectResponse("/inspect", status_code=303)

    @app.get("/sellers", response_class=HTMLResponse)
    def sellers_page(request: Request, category: str = "", min_units: int = 2):
        c = cfg()
        card = insp.scorecard(c, category or None)
        rows = []
        if not card.empty:
            card = card[card.bought >= min_units]
            for r in card.to_dict("records"):
                r["last_bought"] = r["last_bought"].date().isoformat() if r["last_bought"] == r["last_bought"] and r["last_bought"] is not None else ""
                r["status"] = r["status"] if isinstance(r["status"], str) else ""
                r["notes"] = r["notes"] if isinstance(r["notes"], str) else ""
                r["storefronts"] = r["storefronts"] if isinstance(r["storefronts"], str) else ""
                rows.append(r)
        pct = lambda v: "–" if v is None or v != v else f"{v * 100:.0f}%"
        eur = lambda v: "–" if v is None or v != v else f"€{v:,.0f}"
        return HTMLResponse(env.get_template("sellers.html").render(
            active="/sellers", rows=rows, category=category, categories=categories(c), min_units=min_units,
            statuses=insp.STATUSES, pct=pct, eur=eur, query=str(request.url.query)))

    @app.post("/sellers/note")
    async def sellers_note(request: Request):
        form = dict(await request.form())
        insp.set_seller_note(cfg(), form["seller"], form.get("status") or None, form.get("notes", ""))
        return RedirectResponse("/sellers" + (f"?{form['back']}" if form.get("back") else ""), status_code=303)

    @app.post("/go", response_class=HTMLResponse)
    async def go(request: Request):
        form = dict(await request.form())
        if form.get("mode") == "calc":
            return await post_calc(request)
        return await post_max(request)

    return app


def _pct(v: str | None) -> float | None:
    n = _num(v)
    if n is None:
        return None
    return n / 100 if n > 1 else n
