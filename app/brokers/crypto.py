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
        self._alt: dict = {}
        self._warned: dict = {}

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

    # fonte reserva de candles em reais (só leitura, sem conta) quando a corretora real falha
    BRL_DATA_FALLBACKS = ["mercado", "foxbit"]

    def _frame(self, raw) -> pd.DataFrame:
        df = pd.DataFrame([r[:6] for r in raw], columns=["ts", "open", "high", "low", "close", "volume"])
        df = df.astype(float).sort_values("ts").drop_duplicates("ts")  # algumas devolvem do mais novo ao mais velho
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.reset_index(drop=True)

    def _try_candles(self, ex, symbol: str, timeframe: str, n: int, errors: list) -> pd.DataFrame | None:
        """Tenta várias formas de pedir os candles e só aceita se vierem candles RECENTES."""
        import time

        tf_ms = ex.parse_timeframe(timeframe) * 1000
        now = int(time.time() * 1000)
        tries = [dict(since=now - n * tf_ms, limit=n), dict(limit=n), dict(limit=min(n, 300)),
                 dict(since=now - 300 * tf_ms, limit=300)]
        for kw in tries:
            try:
                raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, **kw)
            except Exception as e:
                if self._switch_source(e):
                    return None  # trocou a fonte principal; o chamador tenta de novo
                errors.append(f"{ex.id}: {str(e)[:120]}")
                continue
            if not raw:
                errors.append(f"{ex.id}: nenhum candle")
                continue
            df = self._frame(raw)
            age = now - int(df["ts"].iloc[-1].timestamp() * 1000)
            if len(df) >= 60 and age <= 3 * tf_ms:
                return df
            errors.append(f"{ex.id}: {len(df)} candles, último de {age // 60000} min atrás")
        return None

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        import ccxt

        n = min(limit + 1, 500)
        errors: list[str] = []
        df = None
        for _ in range(len(self.DATA_FALLBACKS) + 1):
            df = self._try_candles(self.ex, symbol, timeframe, n, errors)
            if df is not None or not errors or self._data_only is False:
                break
        if df is None and symbol.endswith("/BRL"):
            for alt in self.BRL_DATA_FALLBACKS:
                if alt == self.ex.id:
                    continue
                try:
                    if alt not in self._alt:
                        self._alt[alt] = getattr(ccxt, alt)({"enableRateLimit": True})
                    df = self._try_candles(self._alt[alt], symbol, timeframe, n, errors)
                except Exception as e:
                    errors.append(f"{alt}: {str(e)[:120]}")
                if df is not None:
                    if not self._warned.get(symbol):
                        from .. import db

                        db.log(f"{symbol}: candles da {self.ex.id} indisponíveis ({'; '.join(errors[:2])}); "
                               f"usando os da {alt} para analisar (as ordens continuam na {self.ex.id})", "WARN")
                        self._warned[symbol] = True
                    break
        if df is None:
            raise RuntimeError("não consegui ler os candles: " + " | ".join(errors[:4]))
        if self.keep_forming:  # dinheiro real: usa o candle em formação (preço de agora) para decidir a cada minuto
            return df
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
