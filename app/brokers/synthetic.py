"""Mercado sintético para demonstração e testes sem internet.

Gera preços com tendências e lateralizações alternadas. A cada chamada de
fetch_ohlcv um novo candle "nasce", simulando a passagem do tempo.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

from .base import Broker, Fill

_TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


class SyntheticFeed(Broker):
    name = "synthetic"

    def __init__(self, seed: int = 42, start_price: float = 100.0, advance: bool = True):
        self.seed = seed
        self.start_price = start_price
        self.advance = advance
        self._series: dict[str, pd.DataFrame] = {}

    def _rng(self, symbol: str) -> np.random.Generator:
        return np.random.default_rng(self.seed + zlib.crc32(symbol.encode()))

    def _generate(self, symbol: str, n: int, timeframe: str) -> pd.DataFrame:
        rng = self._rng(symbol)
        drift = np.zeros(n)
        i = 0
        while i < n:  # regimes de tendência alta, baixa e lateral
            length = int(rng.integers(60, 250))
            drift[i : i + length] = rng.choice([0.0015, -0.001, 0.0])
            i += length
        rets = drift + rng.normal(0, 0.012, n)
        close = self.start_price * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[self.start_price], close[:-1]])
        spread = np.abs(rng.normal(0, 0.006, n)) * close
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        step = _TF_SECONDS.get(timeframe, 3600)
        end = pd.Timestamp.now("UTC").floor("h")
        ts = pd.date_range(end=end, periods=n, freq=f"{step}s")
        return pd.DataFrame(
            {"ts": ts, "open": open_, "high": high, "low": low, "close": close,
             "volume": rng.uniform(100, 1000, n)}
        )

    def _append_bar(self, symbol: str) -> None:
        df = self._series[symbol]
        rng = np.random.default_rng(len(df) + zlib.crc32(symbol.encode()))
        last = df["close"].iloc[-1]
        c = last * float(np.exp(rng.normal(0.0002, 0.012)))
        sp = abs(rng.normal(0, 0.006)) * c
        row = {"ts": df["ts"].iloc[-1] + (df["ts"].iloc[-1] - df["ts"].iloc[-2]),
               "open": last, "high": max(last, c) + sp, "low": min(last, c) - sp,
               "close": c, "volume": float(rng.uniform(100, 1000))}
        self._series[symbol] = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        if symbol not in self._series:
            self._series[symbol] = self._generate(symbol, max(limit, 1500), timeframe)
        elif self.advance:
            self._append_bar(symbol)
        return self._series[symbol].tail(limit).reset_index(drop=True)

    def last_price(self, symbol: str) -> float:
        if symbol not in self._series:
            self.fetch_ohlcv(symbol, "1h", 1500)
        return float(self._series[symbol]["close"].iloc[-1])

    def get_cash(self) -> float:  # pragma: no cover - feed não executa
        raise NotImplementedError

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:  # pragma: no cover
        raise NotImplementedError
