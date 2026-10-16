"""Local web page for the Phase 1 calculator: FastAPI + HTMX, one page, no accounts, binds to 127.0.0.1.

Works without JavaScript too: the forms post normally and the server returns the whole page.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from . import calc
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
        ctx = {"categories": categories(c), "platforms": list(c.costs.get("platforms_sell", {})),
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
