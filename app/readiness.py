"""Checklist "Pronto para dinheiro real?".

Confere automaticamente, com os dados da simulação, se já há evidência
suficiente para começar com dinheiro real (sempre com valor pequeno).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from . import db

MIN_DAYS = 60
MIN_TRADES = 50
MARKET_DROP = 0.15   # o mercado precisa ter caído pelo menos 15% em algum momento do período
MIN_PROFIT_FACTOR = 1.1


def _first_ts() -> datetime | None:
    r = db.one("SELECT MIN(ts) t FROM equity")
    return datetime.fromisoformat(r["t"]) if r and r["t"] else None


def _equity_drawdown() -> float:
    rows = db.query("SELECT equity FROM equity ORDER BY ts")
    if not rows:
        return 0.0
    s = pd.Series([r["equity"] for r in rows])
    peak = s.cummax()
    return float(((peak - s) / peak).max())


def _market_drop(engine, since: datetime | None) -> tuple[float, str]:
    worst, sym_w = 0.0, ""
    for sym in engine.cfg.symbols:
        try:
            df = engine.broker("main").fetch_ohlcv(sym, engine.cfg.timeframe, engine.cfg.history_bars)
            if since is not None:
                df = df[pd.to_datetime(df["ts"], utc=True) >= pd.Timestamp(since)]
            if len(df) < 2:
                continue
            c = df["close"]
            dd = float(((c.cummax() - c) / c.cummax()).max())
            if dd > worst:
                worst, sym_w = dd, sym
        except Exception:
            continue
    return worst, sym_w


def check(engine) -> dict:
    first = _first_ts()
    days = (datetime.now(timezone.utc) - first).days if first else 0
    sells = db.query("SELECT pnl FROM trades WHERE side='sell' AND market='main'")
    n = len(sells)
    gains = sum(t["pnl"] for t in sells if t["pnl"] > 0)
    losses = -sum(t["pnl"] for t in sells if t["pnl"] <= 0)
    pf = gains / losses if losses > 0 else (float("inf") if gains > 0 else 0.0)
    net = gains - losses
    dd = _equity_drawdown()
    dd_limit = db.get_float("max_drawdown_pct")
    tripped = db.one("SELECT 1 FROM logs WHERE msg LIKE 'TRAVA DE SEGURANÇA ATIVADA%'") is not None
    mdrop, msym = _market_drop(engine, first)

    items = [
        {"key": "days", "ok": days >= MIN_DAYS, "label": f"Pelo menos {MIN_DAYS} dias de simulação",
         "value": f"{days} dia(s)"},
        {"key": "trades", "ok": n >= MIN_TRADES, "label": f"Pelo menos {MIN_TRADES} operações fechadas",
         "value": f"{n} operação(ões)"},
        {"key": "market_drop", "ok": mdrop >= MARKET_DROP,
         "label": f"O mercado teve uma queda forte (≥ {MARKET_DROP:.0%}) no período",
         "value": f"maior queda {mdrop:.1%}" + (f" em {msym}" if msym else "")},
        {"key": "drawdown", "ok": dd <= dd_limit and not tripped,
         "label": "O robô se protegeu: queda do patrimônio dentro do limite e trava nunca disparou",
         "value": f"queda máx. {dd:.1%} (limite {dd_limit:.0%})" + (" · trava disparou" if tripped else "")},
        {"key": "results", "ok": net > 0 and pf >= MIN_PROFIT_FACTOR,
         "label": f"Resultado ao vivo positivo e consistente (fator de lucro ≥ {MIN_PROFIT_FACTOR})",
         "value": f"lucro {net:,.2f} · fator {pf:.2f}" if pf != float("inf") else f"lucro {net:,.2f}"},
    ]
    ready = all(i["ok"] for i in items)
    return {"ready": ready, "items": items,
            "advice": ("Tudo verde. Comece com 5 a 10% do valor pretendido, no modo MANUAL, e aumente aos poucos."
                       if ready else "Continue na simulação até todos os itens ficarem verdes.")}
