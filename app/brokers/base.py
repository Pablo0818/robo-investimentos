from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class Fill:
    symbol: str
    side: str          # buy | sell
    qty: float
    price: float
    fee: float
    order_id: str = ""


class Broker(ABC):
    """Interface comum: dados de mercado + execução de ordens."""

    name = "base"

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Retorna DataFrame com colunas ts, open, high, low, close, volume (somente candles FECHADOS)."""

    def last_price(self, symbol: str) -> float:
        return float(self.fetch_ohlcv(symbol, "1m", 2)["close"].iloc[-1])

    @abstractmethod
    def get_cash(self) -> float:
        """Saldo livre na moeda de cotação."""

    @abstractmethod
    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        ...

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        return max(0.0, float(qty))

    def is_market_open(self, symbol: str) -> bool:
        return True

    def close(self) -> None:
        pass
