"""Cérebro do robô: um conselho de agentes que decide se uma compra vale a pena.

Cada agente olha uma coisa diferente e pode vetar ou ajustar o tamanho da compra:

1. Agente de regime   — o mercado está em alta, de lado ou em queda? (média de 200 + inclinação
                         da média de 50). Em queda não compra e sai das posições (Paul Tudor Jones,
                         modelos de mudança de regime). De lado não compra rompimento.
2. Comitê de estratégias — as 3 melhores estratégias aprovadas na prova votam, com peso pela nota
                         fora da amostra (ensembles do FinRL). Sem maioria, não compra; com mais
                         votos, compra maior (ideia do meta-labeling de López de Prado).
3. Agente de custos   — o lucro esperado precisa pagar as taxas com folga. No Alpha Arena as IAs
                         perderam porque operaram demais e as taxas comeram os ganhos pequenos.
4. Agente de pausa    — depois de sair de um ativo, espera alguns candles antes de entrar de novo.
5. Agente de reflexão — se a estratégia campeã perde 5 vezes seguidas de verdade, é aposentada e
                         o robô reaprende (agente reflexivo do TradingAgents).
O agente de notícias (news_agent) e o gerente de risco (risk) continuam valendo por cima disso.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import db
from . import strategies as st

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


# ---------------- 1. regime ----------------
def regime(df: pd.DataFrame) -> str:
    c = df["close"]
    if len(c) < 210:
        return "indefinido"
    e200, e50 = st.ema(c, 200), st.ema(c, 50)
    slope = (e50.iloc[-1] / e50.iloc[-10] - 1) if e50.iloc[-10] else 0.0
    px = c.iloc[-1]
    if px < e200.iloc[-1] and slope < 0:
        return "queda"
    if px > e200.iloc[-1] and slope > 0:
        return "alta"
    return "lateral"


# ---------------- 2. comitê ----------------
def save_committee(symbol: str, members: list[dict]) -> None:
    db.set_setting(f"committee:{symbol}", json.dumps(members))


def committee(symbol: str) -> list[dict]:
    raw = db.get_setting(f"committee:{symbol}")
    try:
        return json.loads(raw) if raw else []
    except Exception:
        return []


def vote(symbol: str, df: pd.DataFrame, champ: dict) -> tuple[float, int]:
    """Fração ponderada do comitê que quer estar comprada agora (0 a 1) e nº de membros."""
    members = committee(symbol)
    if not any(m["name"] == champ["name"] and m["params"] == champ["params"] for m in members):
        members = [{"name": champ["name"], "params": champ["params"], "sharpe": champ.get("oos_sharpe") or 1.0}] + members
    members = members[:3]
    ws = [math.exp(max(-3.0, min(3.0, float(m.get("sharpe") or 0)))) for m in members]
    yes = 0.0
    for m, w in zip(members, ws):
        try:
            if st.positions(m["name"], df, m["params"])[-1] == 1:
                yes += w
        except Exception:
            pass
    return yes / sum(ws), len(members)


# ---------------- 3/4. custos e pausa ----------------
def pays_fees(price: float, stop: float, take: float, cost_roundtrip: float) -> bool:
    gain = ((take - price) if take else 3 * (price - stop)) / price  # tendência: mira 3x o risco
    return gain > 2 * cost_roundtrip


def in_cooldown(symbol: str, timeframe: str, bars: int = 3) -> bool:
    r = db.one("SELECT ts FROM trades WHERE symbol=? AND side='sell' ORDER BY id DESC LIMIT 1", (symbol,))
    if not r:
        return False
    try:
        last = datetime.fromisoformat(r["ts"])
    except Exception:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last < timedelta(minutes=TF_MIN.get(timeframe, 60) * bars)


# ---------------- 5. reflexão ----------------
def reflect(symbol: str, champ: dict) -> bool:
    """Aposenta a campeã que perdeu 5 vezes seguidas desde que foi criada. Retorna True se aposentou."""
    rows = db.query("SELECT pnl FROM trades WHERE symbol=? AND side='sell' AND ts>=? ORDER BY id DESC LIMIT 5",
                    (symbol, champ.get("created") or ""))
    if len(rows) == 5 and all((r["pnl"] or 0) < 0 for r in rows):
        db.execute("UPDATE strategies SET status='retired' WHERE symbol=? AND status='champion'", (symbol,))
        db.set_setting(f"committee:{symbol}", "")
        db.log(f"🧠 {symbol}: a estratégia {champ['name']} perdeu 5 vezes seguidas; aposentada, o robô vai reaprender")
        return True
    return False


# ---------------- decisão ----------------
def decide_entry(symbol: str, df: pd.DataFrame, champ: dict, price: float, stop: float, take: float,
                 timeframe: str, cost_roundtrip: float) -> tuple[bool, float, str]:
    """Retorna (pode comprar?, multiplicador do tamanho, explicação)."""
    if not db.get_bool("brain_enabled"):
        return True, 1.0, ""
    reg = regime(df)
    if reg == "queda":
        return False, 0.0, "agente de regime: mercado em queda (abaixo da média de 200), fico de fora"
    if reg == "lateral" and champ["name"] in st.RUNNERS:
        return False, 0.0, "agente de regime: mercado de lado, rompimento costuma falhar"
    if in_cooldown(symbol, timeframe):
        return False, 0.0, "agente de pausa: saí deste ativo há pouco, espero alguns candles"
    if not pays_fees(price, stop, take, cost_roundtrip):
        return False, 0.0, "agente de custos: o lucro esperado não paga as taxas com folga"
    conf, n = vote(symbol, df, champ)
    if n >= 2 and conf < 0.5:
        return False, 0.0, f"comitê: só {conf:.0%} das estratégias concordam com a compra"
    mult = 0.5 + conf  # 0,5x a 1,5x conforme a concordância
    if reg == "alta":
        mult *= 1.1
    return True, float(np.clip(mult, 0.5, 1.5)), f"regime {reg}, comitê {conf:.0%} a favor ({n} estratégias)"


def wants_exit(df: pd.DataFrame) -> str | None:
    if db.get_bool("brain_enabled") and regime(df) == "queda":
        return "agente de regime: o mercado virou para queda"
    return None
