"""Biblioteca de estratégias (somente compra / "long only").

Cada estratégia recebe o histórico de candles e um dicionário de parâmetros e
devolve, para cada candle fechado, 1 (quero estar comprado) ou 0 (fora).
O módulo de evolução combina estratégias e parâmetros para achar a melhor
para cada ativo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd


# ---------------- indicadores ----------------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _trend_ok(df: pd.DataFrame, p: dict) -> pd.Series:
    n = int(p.get("trend_filter", 0))
    if n <= 0:
        return pd.Series(True, index=df.index)
    return df["close"] > ema(df["close"], n)


def _hold(entry: pd.Series, exit_: pd.Series) -> np.ndarray:
    """Converte eventos de entrada/saída em posição 0/1."""
    pos = np.zeros(len(entry))
    state = 0
    e, x = entry.to_numpy(), exit_.to_numpy()
    for i in range(len(pos)):
        if state == 0 and e[i]:
            state = 1
        elif state == 1 and x[i]:
            state = 0
        pos[i] = state
    return pos


# ---------------- estratégias ----------------
def sma_cross(df: pd.DataFrame, p: dict) -> np.ndarray:
    f, s = sma(df["close"], int(p["fast"])), sma(df["close"], int(p["slow"]))
    return ((f > s) & _trend_ok(df, p)).to_numpy().astype(float)


def ema_trend(df: pd.DataFrame, p: dict) -> np.ndarray:
    f, s = ema(df["close"], int(p["fast"])), ema(df["close"], int(p["slow"]))
    return ((f > s) & (df["close"] > f) & _trend_ok(df, p)).to_numpy().astype(float)


def rsi_reversion(df: pd.DataFrame, p: dict) -> np.ndarray:
    r = rsi(df["close"], int(p["period"]))
    entry = (r < p["buy_below"]) & _trend_ok(df, p)
    return _hold(entry, r > p["sell_above"])


def donchian_breakout(df: pd.DataFrame, p: dict) -> np.ndarray:
    hi = df["high"].rolling(int(p["entry"])).max().shift()
    lo = df["low"].rolling(int(p["exit"])).min().shift()
    entry = (df["close"] > hi) & _trend_ok(df, p)
    return _hold(entry, df["close"] < lo)


def bollinger_reversion(df: pd.DataFrame, p: dict) -> np.ndarray:
    n, k = int(p["period"]), float(p["k"])
    m, sd = sma(df["close"], n), df["close"].rolling(n).std()
    entry = (df["close"] < m - k * sd) & _trend_ok(df, p)
    return _hold(entry, df["close"] > m)


def macd_momentum(df: pd.DataFrame, p: dict) -> np.ndarray:
    m = ema(df["close"], int(p["fast"])) - ema(df["close"], int(p["slow"]))
    sig = ema(m, int(p["signal"]))
    return ((m > sig) & (m > 0) & _trend_ok(df, p)).to_numpy().astype(float)


def dip_buyer(df: pd.DataFrame, p: dict) -> np.ndarray:
    """Compra na queda: IFR curtíssimo muito baixo (pânico de curto prazo), vende no repique."""
    r = rsi(df["close"], int(p["period"]))
    entry = (r < p["buy_below"]) & _trend_ok(df, p)
    return _hold(entry, df["close"] > sma(df["close"], int(p["exit_ma"])))


def pct_dip(df: pd.DataFrame, p: dict) -> np.ndarray:
    """Compra quando o preço cai X% abaixo da média; vende quando volta para a média + Y%."""
    m = sma(df["close"], int(p["period"]))
    entry = (df["close"] < m * (1 - p["drop_pct"] / 100)) & _trend_ok(df, p)
    return _hold(entry, df["close"] > m * (1 + p["gain_pct"] / 100))


@dataclass
class StrategyDef:
    name: str
    fn: Callable[[pd.DataFrame, dict], np.ndarray]
    space: dict  # parâmetro -> (min, max, tipo)
    description: str

    def constrain(self, p: dict) -> dict:
        p = dict(p)
        for k, (lo, hi, kind) in self.space.items():
            v = min(max(p.get(k, lo), lo), hi)
            p[k] = int(round(v)) if kind == "int" else round(float(v), 3)
        if "fast" in p and "slow" in p and p["fast"] >= p["slow"]:
            p["slow"] = min(self.space["slow"][1], p["fast"] + 5)
        if "buy_below" in p and "sell_above" in p and p["sell_above"] <= p["buy_below"] + 5:
            p["sell_above"] = p["buy_below"] + 20
        return p


TREND = (0, 200, "int")
STRATEGIES: dict[str, StrategyDef] = {
    "sma_cross": StrategyDef("sma_cross", sma_cross,
                             {"fast": (5, 60, "int"), "slow": (20, 250, "int"), "trend_filter": TREND},
                             "Cruzamento de médias móveis simples"),
    "ema_trend": StrategyDef("ema_trend", ema_trend,
                             {"fast": (5, 50, "int"), "slow": (20, 200, "int"), "trend_filter": TREND},
                             "Seguidor de tendência com médias exponenciais"),
    "rsi_reversion": StrategyDef("rsi_reversion", rsi_reversion,
                                 {"period": (2, 30, "int"), "buy_below": (10, 40, "float"),
                                  "sell_above": (50, 85, "float"), "trend_filter": TREND},
                                 "Compra em sobrevenda (IFR) e vende na recuperação"),
    "donchian_breakout": StrategyDef("donchian_breakout", donchian_breakout,
                                     {"entry": (10, 100, "int"), "exit": (5, 50, "int"), "trend_filter": TREND},
                                     "Rompimento de máximas (canal de Donchian)"),
    "bollinger_reversion": StrategyDef("bollinger_reversion", bollinger_reversion,
                                       {"period": (10, 60, "int"), "k": (1.0, 3.0, "float"), "trend_filter": TREND},
                                       "Compra abaixo da banda de Bollinger, vende na média"),
    "dip_buyer": StrategyDef("dip_buyer", dip_buyer,
                             {"period": (2, 6, "int"), "buy_below": (5, 30, "float"), "exit_ma": (3, 12, "int"),
                              "trend_filter": TREND},
                             "Compra na queda forte de curto prazo e vende no repique"),
    "pct_dip": StrategyDef("pct_dip", pct_dip,
                           {"period": (10, 80, "int"), "drop_pct": (0.5, 6.0, "float"), "gain_pct": (0.0, 3.0, "float"),
                            "trend_filter": TREND},
                           "Compra X% abaixo da média e vende acima dela"),
    "macd_momentum": StrategyDef("macd_momentum", macd_momentum,
                                 {"fast": (5, 20, "int"), "slow": (20, 60, "int"), "signal": (5, 20, "int"),
                                  "trend_filter": TREND},
                                 "Momento pelo MACD"),
}


def positions(name: str, df: pd.DataFrame, params: dict) -> np.ndarray:
    return STRATEGIES[name].fn(df, params)


def describe(name: str, params: dict) -> str:
    d = STRATEGIES[name].description
    ps = ", ".join(f"{k}={v}" for k, v in params.items())
    return f"{d} ({ps})"


# Estilos: "baixa" = só estratégias de comprar na queda e vender na alta
STYLES = {
    "baixa": ["rsi_reversion", "bollinger_reversion", "dip_buyer", "pct_dip"],
    "tendencia": ["sma_cross", "ema_trend", "donchian_breakout", "macd_momentum"],
}


def allowed(style: str) -> list[str]:
    return STYLES.get(style) or list(STRATEGIES)
