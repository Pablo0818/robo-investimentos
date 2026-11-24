"""Criptomoedas via ccxt (Binance e outras ~100 exchanges). Mercado 24h/7d."""
from __future__ import annotations

import pandas as pd

from .base import Broker, Fill


class CryptoBroker(Broker):
    name = "crypto"

    # Exchanges de reserva só para LER preços (sem conta) quando a principal bloqueia o país do servidor
    DATA_FALLBACKS = ["binanceus", "kraken", "okx", "bybit"]

    def __init__(self, exchange_id: str = "binance", api_key: str = "", secret: str = "",
                 testnet: bool = False, quote: str = "USDT"):
        import ccxt  # import tardio

        self._data_only = not api_key
        self._fallbacks = list(self.DATA_FALLBACKS) if self._data_only else []
        cls = getattr(ccxt, exchange_id)
        params = {"enableRateLimit": True, "options": {"defaultType": "spot"}}
        if api_key:
            params.update({"apiKey": api_key, "secret": secret})
        self.ex = cls(params)
        if testnet:
            self.ex.set_sandbox_mode(True)
        self.quote = quote
        self.paper = bool(testnet)
        self._markets = None

    @property
    def markets(self):
        if self._markets is None:
            self._markets = self.ex.load_markets()
        return self._markets

    def _switch_source(self, err: Exception) -> bool:
        """Se a exchange bloquear o servidor (ex.: Binance em servidor dos EUA), troca a fonte de preços."""
        import ccxt

        msg = str(err).lower()
        blocked = any(k in msg for k in ("451", "restricted location", "unavailable from a restricted",
                                          "403 forbidden", "eligibility", "cloudfront"))
        if not (self._data_only and blocked and self._fallbacks):
            return False
        new = self._fallbacks.pop(0)
        from .. import db

        db.log(f"{self.ex.id} bloqueou este servidor; lendo preços de cripto pela {new}", "WARN")
        self.ex = getattr(ccxt, new)({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        self._markets = None
        return True

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        import time

        n = min(limit + 1, 720)
        tf_ms = self.ex.parse_timeframe(timeframe) * 1000
        while True:
            try:
                # pede a partir de "agora - n candles": algumas corretoras (ex.: Foxbit) sem isso
                # devolvem os candles mais antigos, não os de hoje
                since = int(time.time() * 1000) - n * tf_ms
                raw = self.ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=n)
                break
            except Exception as e:
                if self._switch_source(e):
                    continue
                if n > 300:  # algumas corretoras (ex.: Foxbit) aceitam menos candles por pedido
                    n = 500 if n > 500 else 300
                    continue
                raise
        df = pd.DataFrame([r[:6] for r in raw], columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.astype(float).sort_values("ts").drop_duplicates("ts")  # algumas devolvem do mais novo ao mais velho
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        if self.keep_forming:  # dinheiro real: usa o candle em formação (preço de agora) para decidir a cada minuto
            return df.reset_index(drop=True)
        return df.iloc[:-1].reset_index(drop=True)  # remove o candle ainda em formação

    def last_price(self, symbol: str) -> float:
        try:
            t = self.ex.fetch_ticker(symbol)
        except Exception as e:
            if not self._switch_source(e):
                raise
            t = self.ex.fetch_ticker(symbol)
        return float(t.get("last") or t.get("close"))

    def get_cash(self) -> float:
        bal = self.ex.fetch_balance()
        return float(bal.get("free", {}).get(self.quote, 0.0) or 0.0)

    def normalize_qty(self, symbol: str, qty: float, price: float) -> float:
        try:
            m = self.markets.get(symbol, {})
        except Exception:
            return round(qty, 6)
        q = float(self.ex.amount_to_precision(symbol, qty)) if m else qty
        lim = m.get("limits", {}) if m else {}
        min_amt = (lim.get("amount") or {}).get("min") or 0
        min_cost = (lim.get("cost") or {}).get("min") or 0
        if q < min_amt or q * price < min_cost:
            return 0.0
        return q

    keep_forming = False
    fee_rate = 0.0  # taxa estimada quando a corretora não informa a taxa na resposta da ordem

    def market_order(self, symbol: str, side: str, qty: float) -> Fill:
        if side == "sell" and not self.paper:
            # a taxa da compra pode ter sido cobrada na própria moeda: vende o que existe de verdade
            try:
                base = symbol.split("/")[0]
                free = float(self.ex.fetch_balance().get("free", {}).get(base, 0) or 0)
                if 0 < free < qty:
                    qty = float(self.ex.amount_to_precision(symbol, free))
            except Exception:
                pass
        o = self.ex.create_order(symbol, "market", side, qty)
        filled = float(o.get("filled") or qty)
        price = float(o.get("average") or o.get("price") or self.last_price(symbol))
        fee = 0.0
        for f in o.get("fees") or ([o["fee"]] if o.get("fee") else []):
            if f and f.get("cost"):
                cost = float(f["cost"])
                cur = f.get("currency")
                fee += cost if cur == self.quote else cost * price
        if not fee and self.fee_rate:
            fee = filled * price * self.fee_rate
        return Fill(symbol, side, filled, price, fee, order_id=str(o.get("id", "")))
