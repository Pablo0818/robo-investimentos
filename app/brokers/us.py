"""Bolsa dos EUA: dados do Yahoo (gratuito, com atraso) e execução pela Alpaca.

- YahooFeed: cotações/candles gratuitos para simulação (TRADING_MODE=paper).
- AlpacaBroker: corretora americana com API oficial, conta paper gratuita e
  frações de ações. Usada pelo módulo "Pesquisa EUA" (ex-trader-skill).
"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from .base import Broker, Fill

_YF_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "4h": "60m", "1d": "1d"}
_YF_RANGE = {"1m": "5d", "5m": "30d", "15m": "30d", "1h": "180d", "4h": "180d", "1d": "5y"}
_UA = {"User-Agent": "Mozilla/5.0 (compatible; robo-investimentos/1.0)"}


def _nyse_open() -> bool:
    """Usa o calendário oficial da NYSE (feriados e meio-pregões) do trader-alerts."""
    from ..research import nyse_clock

    return bool(nyse_clock().get("open"))


class YahooFeed(Broker):
    name = "yahoo"

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = httpx.get(url, params={"interval": _YF_INTERVAL.get(timeframe, "60m"),
                                   "range": _YF_RANGE.get(timeframe, "180d")}, headers=_UA, timeout=30)
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        df = pd.DataFrame({"ts": pd.to_datetime(res["timestamp"], unit="s", utc=True), "open": q["open"],
                           "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"]})
        return df.dropna().iloc[:-1].tail(limit).reset_index(drop=True)

    def last_price(self, symbol: str) -> float:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        r = httpx.get(url, params={"interval": "1d", "range": "1d"}, headers=_UA, timeout=30)
        r.raise_for_status()
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        q = round(qty, 4)  # frações de ações
        return q if q * price >= 1 else 0.0

    def is_market_open(self, symbol: str) -> bool:
        return _nyse_open()

    def get_cash(self) -> float:  # pragma: no cover
        raise NotImplementedError

    def market_order(self, symbol, side, qty):  # pragma: no cover
        raise NotImplementedError


class AlpacaBroker(Broker):
    name = "alpaca"

    def __init__(self, key: str, secret: str, paper: bool = True):
        self.paper = paper
        self.base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.data = "https://data.alpaca.markets"
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def _get(self, url: str, **params):
        r = httpx.get(url, headers=self.h, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        tf = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}[timeframe]
        j = self._get(f"{self.data}/v2/stocks/{symbol}/bars", timeframe=tf, limit=limit, feed="iex")
        bars = j.get("bars") or []
        df = pd.DataFrame(bars).rename(columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close",
                                                "v": "volume"})
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        return df[["ts", "open", "high", "low", "close", "volume"]]

    def last_price(self, symbol: str) -> float:
        j = self._get(f"{self.data}/v2/stocks/{symbol}/trades/latest", feed="iex")
        return float(j["trade"]["p"])

    def get_cash(self) -> float:
        return float(self._get(f"{self.base}/v2/account")["cash"])

    def is_market_open(self, symbol: str) -> bool:
        return bool(self._get(f"{self.base}/v2/clock")["is_open"])

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        q = round(qty, 4)
        return q if q * price >= 1 else 0.0

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        body = {"symbol": symbol, "side": side, "type": "market", "time_in_force": "day", "qty": str(qty)}
        r = httpx.post(f"{self.base}/v2/orders", headers=self.h, json=body, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"Alpaca recusou: {r.text}")
        o = r.json()
        for _ in range(20):  # aguarda o preenchimento
            if o.get("status") == "filled":
                break
            time.sleep(1)
            o = self._get(f"{self.base}/v2/orders/{o['id']}")
        if o.get("status") != "filled":
            raise RuntimeError(f"Ordem {o.get('id')} enviada, status {o.get('status')} (confira na Alpaca)")
        return Fill(symbol, side, float(o["filled_qty"]), float(o["filled_avg_price"]), 0.0, order_id=o["id"])


class B3YahooFeed(YahooFeed):
    """Ações da B3 pelo Yahoo (PETR4 -> PETR4.SA). Pregão: 10h às 16h55, dias úteis."""
    name = "b3"

    @staticmethod
    def _y(symbol: str) -> str:
        return symbol if symbol.endswith(".SA") or symbol.startswith("^") else f"{symbol}.SA"

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return super().fetch_ohlcv(self._y(symbol), timeframe, limit)

    def last_price(self, symbol: str) -> float:
        return super().last_price(self._y(symbol))

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        return float(int(qty))  # mercado fracionário: 1 ação no mínimo

    def is_market_open(self, symbol: str) -> bool:
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo

        n = datetime.now(ZoneInfo("America/Sao_Paulo"))
        return n.weekday() < 5 and dtime(10, 0) <= n.time() <= dtime(16, 50)
