"""API e painel web (http://localhost:8000)."""
from __future__ import annotations

import json
import secrets
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, db, readiness, research, risk
from .config import DEFAULT_RUNTIME, settings
from .engine import Engine

WEB = Path(__file__).parent / "web"
OPEN_WHEN_MUST_CHANGE = {"/api/me", "/api/password", "/api/logout"}


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or (
        settings.trust_proxy and request.headers.get("x-forwarded-proto") == "https")


def require_user(request: Request) -> dict:
    u = auth.user_for(request.cookies.get(auth.COOKIE))
    if not u:
        raise HTTPException(401, "faça login")
    if u["must_change"] and request.url.path not in OPEN_WHEN_MUST_CHANGE:
        raise HTTPException(403, "troque a senha para continuar")
    return u


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    old: str
    new: str


class SettingsIn(BaseModel):
    values: dict[str, str | float | bool | int]
    confirm: str = ""


class CashFlowIn(BaseModel):
    amount: float
    note: str = ""


class CopycatIn(BaseModel):
    cik: str
    top: int = 5
    budget: float | None = None


class AlertIn(BaseModel):
    symbol: str
    kind: str
    value: float


class TickersIn(BaseModel):
    tickers: list[str]


class RulesIn(BaseModel):
    rules: dict | None = None


def create_app(engine: Engine) -> FastAPI:
    auth.init()
    app = FastAPI(title="Robô de Investimentos", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

    @app.get("/")
    def index(request: Request):
        u = auth.user_for(request.cookies.get(auth.COOKIE))
        if not u:
            return RedirectResponse("/login")
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/login")
    def login_page():
        return FileResponse(WEB / "login.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(WEB / "static" / "manifest.webmanifest", media_type="application/manifest+json")

    @app.post("/api/login")
    def do_login(body: LoginIn, request: Request, response: Response):
        try:
            token = auth.login(body.email, body.password, client_ip(request))
        except PermissionError as e:
            raise HTTPException(429, str(e))
        if not token:
            raise HTTPException(401, "E-mail ou senha incorretos")
        response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
                            secure=is_https(request), samesite="lax")
        u = auth.user_for(token)
        return {"ok": True, "must_change": bool(u["must_change"])}

    protected = [Depends(require_user)]

    @app.get("/api/me", dependencies=protected)
    def me(request: Request):
        u = auth.user_for(request.cookies.get(auth.COOKIE))
        return {"email": u["email"], "must_change": bool(u["must_change"])}

    @app.post("/api/logout")
    def do_logout(request: Request, response: Response):
        auth.logout(request.cookies.get(auth.COOKIE, ""))
        response.delete_cookie(auth.COOKIE)
        return {"ok": True}

    @app.post("/api/password", dependencies=protected)
    def password(body: PasswordIn, request: Request):
        token = request.cookies.get(auth.COOKIE)
        u = auth.user_for(token)
        try:
            auth.change_password(u["email"], body.old, body.new, keep_token=token)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.get("/api/status", dependencies=protected)
    def status():
        eq, cash = engine.equity()
        peak = risk.peak_equity(eq)
        start = risk.day_start_equity(eq)
        deposits = risk.net_deposits()
        positions = db.query("SELECT * FROM positions")
        for p in positions:
            px = engine.prices.get(p["symbol"], p["avg_price"])
            p["price"] = px
            p["pnl"] = (px - p["avg_price"]) * p["qty"]
            p["pnl_pct"] = px / p["avg_price"] - 1 if p["avg_price"] else 0
        realized = db.one("SELECT COALESCE(SUM(pnl),0) s, COALESCE(SUM(fee),0) f FROM trades")
        return {
            "trading_mode": settings.trading_mode, "broker": settings.broker, "symbols": settings.symbols,
            "timeframe": settings.timeframe, "equity": eq, "cash": cash, "deposits": deposits,
            "profit": eq - deposits, "day_pnl": eq - start, "drawdown": (peak - eq) / peak if peak else 0,
            "realized_pnl": realized["s"], "fees": realized["f"], "positions": positions,
            "settings": db.all_settings(), "last_cycle": engine.last_cycle, "last_error": engine.last_error,
            "markets": {k: b.name for k, b in engine.brokers.items()},
            "pending": db.one("SELECT COUNT(*) c FROM signals WHERE status='pending'")["c"],
            "us_enabled": "us" in engine.brokers, "sec_ready": bool(settings.sec_user_agent),
        }

    @app.get("/api/signals", dependencies=protected)
    def signals(status: str = "", limit: int = 100):
        if status:
            return db.query("SELECT * FROM signals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        return db.query("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))

    @app.post("/api/signals/{sid}/approve", dependencies=protected)
    def approve(sid: int):
        return {"message": engine.approve(sid)}

    @app.post("/api/signals/{sid}/reject", dependencies=protected)
    def reject(sid: int):
        return {"message": engine.reject(sid)}

    @app.get("/api/trades", dependencies=protected)
    def trades(limit: int = 200):
        return db.query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/api/equity", dependencies=protected)
    def equity(limit: int = 2000):
        rows = db.query("SELECT ts, equity, cash FROM equity ORDER BY ts DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    @app.get("/api/strategies", dependencies=protected)
    def strategies():
        rows = db.query("SELECT * FROM strategies ORDER BY id DESC LIMIT 100")
        for r in rows:
            r["params"] = json.loads(r["params"])
        return rows

    @app.post("/api/strategies/{sid}/promote", dependencies=protected)
    def promote(sid: int):
        return {"message": engine.promote(sid)}

    @app.post("/api/evolve", dependencies=protected)
    def evolve():
        with engine.lock:
            return {"results": engine.evolve_all()}

    @app.post("/api/settings", dependencies=protected)
    def save_settings(body: SettingsIn):
        v = {k: str(val).lower() if isinstance(val, bool) else str(val) for k, val in body.values.items()}
        unknown = [k for k in v if k not in DEFAULT_RUNTIME]
        if unknown:
            raise HTTPException(400, f"configuração desconhecida: {unknown}")
        if v.get("execution_mode") == "auto" and db.get_setting("execution_mode") != "auto":
            if body.confirm.strip().upper() != "CONFIRMO":
                raise HTTPException(400, "Para ligar o modo automático digite CONFIRMO")
        if v.get("kill_switch") == "false" and db.get_bool("kill_switch"):
            if body.confirm.strip().upper() != "CONFIRMO":
                raise HTTPException(400, "Para destravar digite CONFIRMO")
            eq, _ = engine.equity()
            db.execute("DELETE FROM equity")  # zera o topo de referência da queda
            db.execute("INSERT INTO equity(ts,equity,cash) VALUES(?,?,?)", (db.now(), eq, 0))
        for k, val in v.items():
            db.set_setting(k, val)
        db.log(f"Configurações alteradas: {v}")
        if any(k.startswith("us_") for k in v):
            research.write_config()
        return {"settings": db.all_settings()}

    @app.post("/api/positions/{symbol:path}/close", dependencies=protected)
    def close(symbol: str):
        return {"message": engine.close_position(symbol)}

    @app.post("/api/cashflow", dependencies=protected)
    def cashflow(body: CashFlowIn):
        db.execute("INSERT INTO cash_flows(ts,amount,note) VALUES(?,?,?)", (db.now(), body.amount, body.note))
        if settings.trading_mode == "paper" and "paper_cash" in db.all_settings():
            db.set_setting("paper_cash", float(db.get_setting("paper_cash")) + body.amount)
        return {"ok": True}

    @app.get("/api/cashflows", dependencies=protected)
    def cashflows():
        return db.query("SELECT * FROM cash_flows ORDER BY id DESC")

    @app.get("/api/report/monthly", dependencies=protected)
    def monthly():
        out: dict = defaultdict(lambda: {"realized_pnl": 0.0, "sales": 0.0, "fees": 0.0, "trades": 0})
        for t in db.query("SELECT * FROM trades"):
            m = out[(t["ts"][:7], t["market"] or "main")]
            m["fees"] += t["fee"] or 0
            m["trades"] += 1
            if t["side"] == "sell":
                m["realized_pnl"] += t["pnl"] or 0
                m["sales"] += t["qty"] * t["price"]
        return [{"month": k[0], "market": k[1], **v} for k, v in sorted(out.items(), reverse=True)]

    @app.get("/api/readiness", dependencies=protected)
    def ready():
        return readiness.check(engine)

    @app.get("/api/logs", dependencies=protected)
    def logs(limit: int = 200):
        return db.query("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------ Pesquisa EUA (trader-skill integrado)
    @app.get("/api/us/alerts", dependencies=protected)
    def us_alerts(limit: int = 100):
        return db.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/api/us/find", dependencies=protected)
    def us_find(q: str):
        return research.copycat_find(q)

    @app.post("/api/us/copycat", dependencies=protected)
    def us_copycat(body: CopycatIn):
        with engine.lock:
            return research.copycat(engine, body.cik, body.top, body.budget)

    @app.post("/api/us/rebalance", dependencies=protected)
    def us_rebalance(band: float = 5.0):
        with engine.lock:
            return research.rebalance(engine, band)

    @app.get("/api/us/tax", dependencies=protected)
    def us_tax():
        return research.tax_report(engine)

    @app.post("/api/us/tax/harvest", dependencies=protected)
    def us_harvest(min_loss: float = 100):
        with engine.lock:
            return research.tax_harvest(engine, min_loss)

    @app.post("/api/us/analyst", dependencies=protected)
    def us_analyst(body: TickersIn):
        return research.analyst([t.upper() for t in body.tickers])

    @app.post("/api/us/screener", dependencies=protected)
    def us_screener(body: RulesIn):
        return research.screener(body.rules)

    @app.post("/api/us/alert", dependencies=protected)
    def us_add_alert(body: AlertIn):
        return research.add_alert(body.symbol, body.kind, body.value)

    @app.post("/api/us/watch/{name}", dependencies=protected)
    def us_watch(name: str):
        fn = {"news": research.watch_news, "pelosi": research.watch_pelosi, "alerts": research.watch_alerts}[name]
        return {"new": fn(engine)}

    @app.get("/api/us/pelosi", dependencies=protected)
    def us_pelosi(member: str = ""):
        return research.pelosi_index(member)

    @app.get("/api/us/clock", dependencies=protected)
    def us_clock():
        return research.nyse_clock()

    return app
