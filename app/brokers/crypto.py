"""Criptomoedas via ccxt (Binance e outras ~100 exchanges). Mercado 24h/7d."""
from __future__ import annotations

import pandas as pd

from .base import Broker, Fill


class CryptoBroker(Broker):
    name = "crypto"

    def __init__(self, exchange_id: str = "binance", api_key: str = "", secret: str = "",
                 testnet: bool = False, quote: str = "USDT"):
        import ccxt  # import tardio

        cls = getattr(ccxt, exchange_id)
        params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if api_key:
            params.update({"apiKey": api_key, "secret": secret})
        self.ex = cls(params)
        if testnet:
            self.ex.set_sandbox_mode(True)
        self.quote = quote
        self._markets = None

    @property
    def markets(self):
        if self._markets is None:
            self._markets = self.ex.load_markets()
        return self._markets

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        raw = self.ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=min(limit + 1, 1000))
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.iloc[:-1].reset_index(drop=True)  # remove o candle ainda em formação

    def last_price(self, symbol: str) -> float:
        t = self.ex.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close"))

    def get_cash(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal.get("free", {}).get(self.quote, 0.0) or 0.0)

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        m = self.markets.get(symbol, {})
        q = float(self.ex.amount_to_precision(symbol, qty)) if m else qty
        lim = m.get("limits", {}) if m else {}
        min_amt = (lim.get("amount") or {}).get("min") or 0
        min_cost = (lim.get("cost") or {}).get("min") or 0
        if q < min_amt or q * price < min_cost:
            return 0.0
        return q

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        o = self.ex.create_order(symbol, "market", side, qty)
        filled = float(o.get("filled") or qty)
        price = float(o.get("average") or o.get("price") or self.last_price(symbol))
        fee = 0.0
        for f in o.get("fees") or ([o["fee"]] if o.get("fee") else []):
            if f and f.get("cost"):
                cost = float(f["cost"])
                cur = f.get("currency")
                fee += cost if cur == self.quote else cost * price
        return Fill(symbol, side, filled, price, fee, order_id=str(o.get("id", "")))
