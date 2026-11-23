"""Módulo "Pesquisa EUA": as 11 ferramentas do trader-skill integradas ao robô.

As ferramentas originais (MIT, Jake Schincariol) ficam em app/research/tools e
trabalham com arquivos JSON na pasta data/trader/. Este módulo:

- gera data/trader/config.json a partir das configurações do painel;
- mantém data/trader/portfolio.json com cada compra/venda nos EUA (lotes p/ imposto);
- transforma as ordens propostas (copycat, rebalance, tax) em SINAIS do robô,
  sempre passando pelo trader-risk; só ordens com PASS viram sinais;
- roda os vigias (notícias, Congresso, alertas de preço) no ciclo 24h e
  manda novidades para o painel e Telegram.

Ordens dos EUA seguem o modo do robô: no manual você aprova cada uma;
no automático, só as que passaram no trader-risk são executadas.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db
from ..config import settings

TOOLS = Path(__file__).parent / "tools"
SCRIPTS = {
    "alerts": "trader-alerts/alerts.py", "analyst": "trader-analyst/facts.py", "broker": "trader-broker/ticket.py",
    "budget": "trader-budget/size.py", "copycat": "trader-copycat/edgar13f.py", "news": "trader-news/news.py",
    "pelosi": "trader-pelosi/ptr.py", "rebalance": "trader-rebalance/rebalance.py", "risk": "trader-risk/check.py",
    "screener": "trader-screener/screen.py", "tax": "trader-tax/lots.py",
}


def workdir() -> Path:
    """Pasta onde as ferramentas leem/escrevem trader/*.json (fica ao lado do banco)."""
    base = Path(settings.db_path).resolve().parent
    (base / "trader" / "state").mkdir(parents=True, exist_ok=True)
    return base


def tpath(name: str) -> Path:
    return workdir() / "trader" / name


def run_tool(tool: str, *args: str, want_json: bool = True, timeout: int = 180) -> dict:
    """Executa uma ferramenta e devolve {code, data, text, error}."""
    cmd = [sys.executable, "-I", str(TOOLS / SCRIPTS[tool]), *args]
    if want_json and "--json" not in args:
        cmd.append("--json")
    env = dict(os.environ)
    if settings.sec_user_agent:
        env["SEC_USER_AGENT"] = settings.sec_user_agent
    p = subprocess.run(cmd, cwd=workdir(), capture_output=True, text=True, timeout=timeout, env=env)
    out = {"code": p.returncode, "text": p.stdout, "error": p.stderr.strip(), "data": None}
    if want_json and p.stdout.strip():
        try:
            out["data"] = json.loads(p.stdout)
        except json.JSONDecodeError:
            pass
    return out


def _read(name: str, default=None):
    p = tpath(name)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return default
    return default


def _write(name: str, data) -> None:
    tpath(name).write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------- relógio NYSE
_clock_cache: dict = {"t": 0, "v": {}}


def nyse_clock() -> dict:
    if time.time() - _clock_cache["t"] < 120:
        return _clock_cache["v"]
    r = run_tool("alerts", "clock")
    _clock_cache.update(t=time.time(), v=r["data"] or {})
    return _clock_cache["v"]


# -------------------------------------------------------------- configuração
def write_config(us_cash: float | None = None) -> dict:
    blocked = [t.strip().upper() for t in db.get_setting("us_blocked").split(",") if t.strip()]
    cfg = {
        "budget": round(us_cash if us_cash is not None else settings.us_initial_capital, 2),
        "cash_buffer_pct": db.get_float("us_cash_buffer_pct"),
        "max_position_pct": db.get_float("us_max_position_pct"),
        "max_order_usd": db.get_float("us_max_order_usd"),
        "min_order_usd": db.get_float("us_min_order_usd"),
        "max_orders_per_day": int(db.get_float("us_max_orders_per_day")),
        "fractional": True,
        "broker": "alpaca",
        "paper": not (settings.is_live and settings.alpaca_key and not settings.alpaca_paper),
        "blocked": blocked,
        "allow_options": False, "allow_margin": False, "allow_short": False,
        "sec_user_agent": settings.sec_user_agent or "",
    }
    _write("config.json", cfg)
    return cfg


def sync_portfolio(engine) -> dict:
    """Atualiza o caixa do portfolio.json com o saldo real da corretora dos EUA e os preços."""
    b = engine.brokers.get("us")
    cash = b.get_cash() if b else settings.us_initial_capital
    pf = _read("portfolio.json") or {"cash": cash, "positions": []}
    pf["cash"] = round(cash, 2)
    _write("portfolio.json", pf)
    held = [p["ticker"] for p in pf.get("positions", [])]
    value = sum(p["shares"] * engine.prices.get(p["ticker"], 0) for p in pf.get("positions", []))
    write_config(cash + value)
    prices = {"prices": {}}
    for t in held:
        try:
            prices["prices"][t] = engine.price(t, "us")
        except Exception:
            pass
    old = _read("prices.json") or {"prices": {}}
    old.setdefault("prices", {}).update(prices["prices"])
    old["as_of"] = db.now()
    _write("prices.json", old)
    return pf


def record_fill(symbol: str, side: str, qty: float, price: float) -> None:
    """Registra a execução como lote de imposto (trader-broker fill)."""
    if not tpath("config.json").exists():
        write_config()
    r = run_tool("broker", "fill", "--symbol", symbol, "--side", side, "--qty", f"{qty:.6f}",
                 "--price", f"{price:.4f}", want_json=False)
    if r["code"] != 0:
        db.log(f"Pesquisa EUA: não registrou lote de {symbol}: {r['error']}", "WARN")


def refresh_prices(tickers: list[str], engine=None) -> dict:
    found = {}
    for t in tickers:
        try:
            found[t] = engine.price(t, "us") if engine else None
        except Exception:
            pass
    found = {k: v for k, v in found.items() if v}
    missing = [t for t in tickers if t not in found]
    if missing:
        r = run_tool("alerts", "quote", *missing)
        for q in ((r["data"] or {}).get("quotes") or []):
            if q.get("price"):
                found[q.get("symbol", q.get("ticker"))] = q["price"]
    old = _read("prices.json") or {"prices": {}}
    old.setdefault("prices", {}).update(found)
    old["as_of"] = db.now()
    _write("prices.json", old)
    return found


# ------------------------------------------------------- ordens -> sinais
def orders_to_signals(engine, source: str) -> dict:
    """Passa trader/orders.json pelo trader-risk e transforma cada PASS em sinal do robô."""
    odoc = _read("orders.json") or {}
    orders = odoc.get("orders", [])
    if not orders:
        return {"created": 0, "blocked": [], "message": "nenhuma ordem proposta"}
    refresh_prices(sorted({o["symbol"] for o in orders}), engine)
    risk = run_tool("risk")
    results = (risk["data"] or {}).get("results", [])
    created, blocked, dups = [], [], []
    for o, v in zip(orders, results):
        if v.get("verdict") != "PASS":
            blocked.append({"symbol": o["symbol"], "reasons": v.get("reasons")})
            continue
        try:
            px = engine.price(o["symbol"], "us")
        except Exception:
            px = (_read("prices.json") or {}).get("prices", {}).get(o["symbol"], 0) or 0
        qty = float(o["qty"]) if o.get("qty") else 0.0
        notional = float(o["notional"]) if o.get("notional") else 0.0
        if not qty and notional and px:
            qty = notional / px
        sig = engine.create_signal(o["symbol"], o["side"], qty, px, 0, 0, source,
                                   (o.get("why") or source)[:400], market="us", notional=notional)
        if sig:
            created.append(sig["id"])
        else:
            dups.append(o["symbol"])
    if blocked:
        db.log(f"Pesquisa EUA ({source}): {len(blocked)} ordem(ns) bloqueada(s) pelo trader-risk: "
               + "; ".join(f"{b['symbol']}: {', '.join(b['reasons'] or [])}" for b in blocked), "WARN")
    msg = f"{len(created)} sinal(is) criado(s), {len(blocked)} bloqueado(s) pelo risco"
    if dups:
        msg += f", {len(dups)} já pendente(s): {', '.join(dups)}"
    return {"created": len(created), "signal_ids": created, "blocked": blocked, "duplicates": dups, "message": msg}


# ------------------------------------------------------------- ferramentas
def copycat_find(query: str) -> dict:
    return run_tool("copycat", "find", *query.split())


def copycat(engine, cik: str, top: int = 5, budget: float | None = None) -> dict:
    """Copia a carteira de um investidor (13F) -> orçamento -> risco -> sinais."""
    sync_portfolio(engine)
    r = run_tool("copycat", "copy", cik, "--top", str(top), "--out", "trader/targets.json")
    if r["code"] != 0:
        return {"error": r["error"] or r["text"]}
    args = ["--write"]
    if budget:
        args += ["--budget", str(budget)]
    size = run_tool("budget", *args)
    if size["code"] != 0:
        return {"error": size["error"], "copy": r["data"]}
    out = orders_to_signals(engine, f"copycat:{cik}")
    out.update(copy=r["data"], sizing=size["data"])
    return out


def rebalance(engine, band: float = 5.0) -> dict:
    sync_portfolio(engine)
    r = run_tool("rebalance", "--band", str(band), "--write")
    if r["code"] != 0:
        return {"error": r["error"] or r["text"]}
    out = orders_to_signals(engine, "rebalance")
    out["plan"] = r["data"]
    return out


def tax_report(engine) -> dict:
    sync_portfolio(engine)
    return run_tool("tax", "report", "--lots")


def tax_harvest(engine, min_loss: float = 100) -> dict:
    sync_portfolio(engine)
    r = run_tool("tax", "harvest", "--min-loss", str(min_loss), "--write", "trader/orders.json")
    if r["code"] != 0:
        return {"error": r["error"] or r["text"]}
    out = orders_to_signals(engine, "tax-harvest")
    out["plan"] = r["data"]
    return out


def analyst(tickers: list[str]) -> dict:
    return run_tool("analyst", *tickers, "--metrics", "trader/metrics.json", timeout=600)


def screener(rules: dict | None = None) -> dict:
    if rules:
        _write("rules.json", rules)
    rules_path = "trader/rules.json" if tpath("rules.json").exists() else str(TOOLS / "trader-screener/rules.example.json")
    if not tpath("metrics.json").exists():
        return {"error": "rode o Analista primeiro (gera trader/metrics.json)"}
    args = ["trader/metrics.json", "--rules", rules_path, "--out", "trader/shortlist.json"]
    if tpath("prices.json").exists():
        args += ["--prices", "trader/prices.json"]
    return run_tool("screener", *args)


def add_alert(symbol: str, kind: str, value: float) -> dict:
    flag = {"above": "--above", "below": "--below", "stop": "--stop", "pct_move": "--pct"}[kind]
    return run_tool("alerts", "add", "--symbol", symbol.upper(), flag, str(value), want_json=False)


def pelosi_index(member: str = "") -> dict:
    args = ["index"] + (["--member", member] if member else [])
    return run_tool("pelosi", *args)


# ------------------------------------------------------------------ vigias
def _due(name: str, minutes: float) -> bool:
    key = f"research_last:{name}"
    last = db.get_setting(key)
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(minutes=minutes):
        return False
    db.set_setting(key, db.now())
    return True


def _store_alert(source: str, title: str, body: str = "", url: str = "") -> None:
    db.execute("INSERT INTO alerts(ts,source,title,body,url) VALUES(?,?,?,?,?)", (db.now(), source, title, body, url))


def watch_news(engine) -> int:
    if not (tpath("portfolio.json").exists() or tpath("targets.json").exists()):
        return 0
    r = run_tool("news", "--hours", "24")
    items = [i for i in ((r["data"] or {}).get("items") or []) if i.get("new")]
    for i in items[:20]:
        _store_alert("news", f"{i.get('ticker', '')}: {i.get('title', '')}",
                     " ".join(i.get("tags", []) or []), i.get("link", ""))
    if items:
        engine.notify("📰 Notícias nos seus ativos dos EUA:\n" + "\n".join(
            f"• {i.get('ticker', '')} {i.get('title', '')}" for i in items[:8]))
    return len(items)


def watch_pelosi(engine) -> int:
    r = run_tool("pelosi", "new")
    fresh = ((r["data"] or {}).get("new") or []) if isinstance(r["data"], dict) else []
    for f in fresh[:20]:
        name = f.get("name") or f"{f.get('first', '')} {f.get('last', '')}".strip()
        _store_alert("pelosi", f"Nova declaração de trade: {name} ({f.get('filed', f.get('date', ''))})",
                     json.dumps(f, ensure_ascii=False)[:1000], f.get("url", ""))
    if fresh:
        engine.notify(f"🏛️ {len(fresh)} nova(s) declaração(ões) de trades do Congresso dos EUA. Veja no painel.")
    return len(fresh)


def watch_alerts(engine) -> int:
    if not tpath("alerts.json").exists():
        return 0
    r = run_tool("alerts", "check")
    rings = (r["data"] or {}).get("rings") or []
    for x in rings:
        _store_alert("alerts", x.get("message") or x.get("text") or json.dumps(x, ensure_ascii=False))
    if rings:
        engine.notify("🔔 Alertas de preço:\n" + "\n".join(
            f"• {x.get('message') or x.get('id')}" for x in rings))
    return len(rings)


def research_hook(engine) -> None:
    """Chamado a cada ciclo do motor; cada vigia respeita seu próprio intervalo."""
    if not settings.us_enabled:
        return
    jobs = [("news", watch_news), ("pelosi", watch_pelosi), ("alerts", watch_alerts)]
    for name, fn in jobs:
        if db.get_bool(f"us_watch_{name}") and _due(name, db.get_float(f"us_{name}_minutes")):
            try:
                fn(engine)
            except Exception as e:
                db.log(f"Vigia {name} falhou: {e}", "WARN")


def attach(engine) -> None:
    """Liga o módulo ao motor: vigias no ciclo e lotes de imposto a cada execução nos EUA."""
    engine.hooks.append(research_hook)
    engine.fill_hooks.append(lambda sig, fill: record_fill(fill.symbol, fill.side, fill.qty, fill.price)
                             if (sig.get("market") or "main") == "us" else None)
    write_config()
