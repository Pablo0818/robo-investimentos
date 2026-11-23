"""Backtest candle a candle, com as mesmas regras do robô ao vivo:
entrada no fechamento do sinal, stop por ATR, alvo em múltiplos do risco,
stop móvel, taxas e derrapagem."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import strategies as st

BARS_PER_YEAR = {"1m": 525_600, "5m": 105_120, "15m": 35_040, "1h": 8_760, "4h": 2_190, "1d": 365}


@dataclass
class Result:
    total_return: float
    sharpe: float
    max_drawdown: float
    trades: int
    win_rate: float
    profit_factor: float
    exposure: float

    def dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def run(df: pd.DataFrame, name: str, params: dict, *, fee: float = 0.001, slippage: float = 0.0005,
        atr_mult: float = 2.0, take_r: float = 3.0, trailing: bool = True, timeframe: str = "1h",
        start: int = 0) -> Result:
    pos_signal = st.positions(name, df, params)
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    open_ = df["open"].to_numpy()
    a = st.atr(df).to_numpy()
    cost = fee + slippage

    equity = np.ones(len(df))
    eq = 1.0
    in_pos = False
    entry = stop = take = 0.0
    trade_rets: list[float] = []
    bars_in = 0
    for i in range(1, len(df)):
        if in_pos:
            bars_in += 1
            exit_px = None
            if low[i] <= stop:
                exit_px = min(stop, open_[i])  # gap abaixo do stop sai na abertura
            elif take_r > 0 and high[i] >= take:
                exit_px = take
            elif pos_signal[i] == 0:
                exit_px = close[i]
            mark = exit_px if exit_px is not None else close[i]
            eq_bar = eq * (mark / close[i - 1])
            if exit_px is not None:
                eq_bar *= (1 - cost)
                trade_rets.append((exit_px * (1 - cost)) / (entry * (1 + cost)) - 1)
                in_pos = False
            else:
                if trailing and not np.isnan(a[i]):
                    stop = max(stop, close[i] - atr_mult * a[i])
            eq = eq_bar
        elif i >= start and pos_signal[i] == 1 and pos_signal[i - 1] == 0 and not np.isnan(a[i]) and a[i] > 0:
            in_pos = True
            entry = close[i]
            stop = entry - atr_mult * a[i]
            take = entry + take_r * (entry - stop)
            eq *= (1 - cost)
        equity[i] = eq

    equity = equity[start:] / equity[start] if start > 0 else equity
    rets = np.diff(equity) / equity[:-1]
    bpy = BARS_PER_YEAR.get(timeframe, 8760)
    sd = rets.std()
    sharpe = float(rets.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0
    peak = np.maximum.accumulate(equity)
    mdd = float(((peak - equity) / peak).max())
    wins = [r for r in trade_rets if r > 0]
    losses = [-r for r in trade_rets if r <= 0]
    pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (float(len(wins)) if wins else 0.0)
    return Result(
        total_return=float(equity[-1] - 1), sharpe=sharpe, max_drawdown=mdd, trades=len(trade_rets),
        win_rate=(len(wins) / len(trade_rets)) if trade_rets else 0.0, profit_factor=float(pf),
        exposure=bars_in / max(1, len(df) - start),
    )
