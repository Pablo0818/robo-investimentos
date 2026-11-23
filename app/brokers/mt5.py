"""B3 (ações, mini índice, mini dólar) via MetaTrader 5.

Requer Windows com o terminal MetaTrader 5 da sua corretora instalado e logado,
e o pacote `MetaTrader5` (pip install MetaTrader5).
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from .base import Broker, Fill

_TZ = ZoneInfo("America/Sao_Paulo")


class MT5Broker(Broker):
    name = "mt5"

    def __init__(self, login: str = "", password: str = "", server: str = "", path: str = ""):
        import MetaTrader5 as mt5  # disponível apenas no Windows

        self.mt5 = mt5
        kwargs = {}
        if path:
            kwargs["path"] = path
        if login:
            kwargs.update(login=int(login), password=password, server=server)
        if not mt5.initialize(**kwargs):
            raise RuntimeError(f"Falha ao conectar no MetaTrader 5: {mt5.last_error()}")
        self._tf = {
            "1m": mt5.TIMEFRAME_M1, "5m": mt5.TIMEFRAME_M5, "15m": mt5.TIMEFRAME_M15,
            "1h": mt5.TIMEFRAME_H1, "4h": mt5.TIMEFRAME_H4, "1d": mt5.TIMEFRAME_D1,
        }

    def _ensure(self, symbol: str):
        if not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"Ativo {symbol} não encontrado no MT5")
        return self.mt5.symbol_info(symbol)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        self._ensure(symbol)
        # posição 1 = ignora o candle atual (em formação)
        rates = self.mt5.copy_rates_from_pos(symbol, self._tf[timeframe], 1, limit)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Sem dados para {symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.rename(columns={"tick_volume": "volume"})
        return df[["ts", "open", "high", "low", "close", "volume"]]

    def last_price(self, symbol: str) -> float:
        self._ensure(symbol)
        t = self.mt5.symbol_info_tick(symbol)
        return float(t.last or t.bid)

    def get_cash(self) -> float:
        info = self.mt5.account_info()
        return float(info.margin_free)

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        info = self._ensure(symbol)
        step = info.volume_step or 1
        q = (qty // step) * step
        return float(q) if q >= info.volume_min else 0.0

    def is_market_open(self, symbol: str) -> bool:
        n = datetime.now(_TZ)
        if n.weekday() >= 5:
            return False
        return time(10, 0) <= n.time() <= time(16, 50)

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        mt5 = self.mt5
        info = self._ensure(symbol)
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if side == "buy" else tick.bid
        filling = mt5.ORDER_FILLING_RETURN
        if info.filling_mode & 1:
            filling = mt5.ORDER_FILLING_FOK
        elif info.filling_mode & 2:
            filling = mt5.ORDER_FILLING_IOC
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(qty),
            "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
            "price": price, "deviation": 20, "magic": 20261122,
            "comment": "robo-investimentos", "type_time": mt5.ORDER_TIME_DAY,
            "type_filling": filling,
        }
        r = mt5.order_send(req)
        if r is None or r.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"Ordem recusada: {getattr(r, 'comment', mt5.last_error())}")
        return Fill(symbol, side, float(r.volume), float(r.price or price), 0.0, order_id=str(r.order))

    def close(self) -> None:
        self.mt5.shutdown()
