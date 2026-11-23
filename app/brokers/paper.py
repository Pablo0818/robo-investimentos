"""Corretora de simulação (paper trading): usa dados reais (ou sintéticos)
e executa ordens com dinheiro fictício, cobrando taxa e derrapagem."""
from __future__ import annotations

import pandas as pd

from .. import db
from .base import Broker, Fill


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, feed: Broker, initial_capital: float, fee_rate: float, slippage: float,
                 cash_key: str = "paper_cash", label: str = "simulação"):
        self.feed = feed
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.cash_key = cash_key
        if db.one("SELECT value FROM settings WHERE key=?", (cash_key,)) is None:
            db.set_setting(cash_key, initial_capital)
            db.execute("INSERT INTO cash_flows(ts,amount,note) VALUES(?,?,?)",
                       (db.now(), initial_capital, f"Capital inicial ({label})"))

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return self.feed.fetch_ohlcv(symbol, timeframe, limit)

    def last_price(self, symbol: str) -> float:
        return self.feed.last_price(symbol)

    def get_cash(self) -> float:
        return float(db.get_setting(self.cash_key))

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        return self.feed.normalize_qty(symbol, qty, price)

    def is_market_open(self, symbol: str) -> bool:
        return self.feed.is_market_open(symbol)

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        px = self.last_price(symbol)
        px = px * (1 + self.slippage) if side == "buy" else px * (1 - self.slippage)
        fee = px * qty * self.fee_rate
        cash = self.get_cash()
        if side == "buy":
            cost = px * qty + fee
            if cost > cash + 1e-9:
                raise RuntimeError(f"Saldo insuficiente: precisa {cost:.2f}, tem {cash:.2f}")
            cash -= cost
        else:
            cash += px * qty - fee
        db.set_setting(self.cash_key, round(cash, 8))
        return Fill(symbol, side, qty, px, fee, order_id="paper")
