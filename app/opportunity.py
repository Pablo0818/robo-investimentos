"""Agente de oportunidade: compra e vende por OPORTUNIDADE, não por hora.

A cada minuto olha o gráfico de 15 min (com o candle em formação = preço de agora) de cada moeda
e compra quando aparece uma queda rápida com sinal de repique. Vende no alvo, no stop ou depois de
um tempo máximo. As regras (tamanho da queda, IFR, alvo e stop) são escolhidas e PROVADAS a cada
30 minutos nos dados recentes de todas as moedas juntas, já descontando as taxas da corretora.
Se nenhuma combinação der lucro na prova, o agente não opera.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import db
from . import strategies as st

TF = "15m"
LOOKBACK = 16        # 4 horas de candles de 15 min para medir a queda
MAX_BARS = 48        # tempo máximo segurando: 12 horas
GRID = {
    "drop": [0.015, 0.025, 0.035],   # queda mínima desde a máxima das últimas 4h
    "rsi": [25, 35],                 # IFR(7) abaixo disso
    "take": [0.025, 0.035, 0.05],    # alvo de lucro
    "stop": [0.02, 0.03],            # stop de proteção
}


def _features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    f["hi"] = df["high"].rolling(LOOKBACK).max()
    f["drop"] = 1 - df["close"] / f["hi"]
    f["rsi"] = st.rsi(df["close"], 7)
    f["green"] = df["close"] > df["open"]  # candle verde = começou a reagir
    return f


def _entries(df: pd.DataFrame, f: pd.DataFrame, p: dict) -> np.ndarray:
    return ((f["drop"] >= p["drop"]) & (f["rsi"] < p["rsi"]) & f["green"]).to_numpy()


def _simulate(df: pd.DataFrame, p: dict, cost: float) -> list[float]:
    """Resultado líquido (fração) de cada operação que as regras teriam feito."""
    f = _features(df)
    ent = _entries(df, f, p)
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    out, i, n = [], LOOKBACK, len(df)
    while i < n - 1:
        if not ent[i]:
            i += 1
            continue
        px = cl[i]
        tk, sp = px * (1 + p["take"]), px * (1 - p["stop"])
        res, j = None, i + 1
        while j < n and j <= i + MAX_BARS:
            if lo[j] <= sp:  # conservador: se tocou os dois no mesmo candle, conta o stop
                res = -p["stop"]
                break
            if hi[j] >= tk:
                res = p["take"]
                break
            j += 1
        if res is None:
            if j >= n:
                break  # operação ainda aberta no fim dos dados: não conta
            res = cl[min(j, n - 1)] / px - 1
        out.append(res - cost)
        i = j + 1
    return out


def calibrate(frames: dict[str, pd.DataFrame], cost: float) -> dict | None:
    """Escolhe as regras que mais lucraram, somando todas as moedas. None se nada deu lucro."""
    best = None
    for drop, rsi_, take, stop in itertools.product(*GRID.values()):
        if take < 2.2 * cost:  # o alvo precisa pagar as taxas com folga
            continue
        p = {"drop": drop, "rsi": rsi_, "take": take, "stop": stop}
        res = [r for df in frames.values() for r in _simulate(df, p, cost)]
        if len(res) < 8:
            continue
        wins, losses = sum(r for r in res if r > 0), -sum(r for r in res if r < 0)
        pf = wins / losses if losses else 9.9
        avg = float(np.mean(res))
        if avg <= 0 or pf < 1.2:
            continue
        score = avg * np.sqrt(len(res))
        if best is None or score > best["score"]:
            best = {**p, "score": score, "trades": len(res), "avg": avg, "pf": pf,
                    "hit": sum(1 for r in res if r > 0) / len(res)}
    return best


def rules() -> dict | None:
    raw = db.get_setting("opp_rules")
    try:
        return json.loads(raw) if raw else None
    except Exception:
        return None


def needs_calibration() -> bool:
    last = db.get_setting("opp_calibrated")
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(minutes=30)


def save(best: dict | None) -> None:
    db.set_setting("opp_calibrated", datetime.now(timezone.utc).isoformat())
    db.set_setting("opp_rules", json.dumps(best) if best else "")
    if best:
        db.log(f"🎯 Agente de oportunidade calibrado: compra quedas de {best['drop']:.1%} com IFR<{best['rsi']}, "
               f"alvo +{best['take']:.1%}, stop -{best['stop']:.1%} · prova: {best['trades']} operações, "
               f"acerto {best['hit']:.0%}, média {best['avg']:+.2%} por operação já com taxas")
    else:
        db.log("🎯 Agente de oportunidade: nenhuma regra deu lucro nos dados recentes depois das taxas; "
               "fico sem operar por oportunidade até a próxima prova (30 min)")


def signal(df: pd.DataFrame, p: dict) -> bool:
    """Oportunidade AGORA (último candle, ainda em formação)."""
    if len(df) < LOOKBACK + 10:
        return False
    f = _features(df)
    return bool(_entries(df, f, p)[-1])
