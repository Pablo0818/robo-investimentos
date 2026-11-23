from __future__ import annotations

from ..config import Settings
from .base import Broker, Fill
from .paper import PaperBroker
from .synthetic import SyntheticFeed


def build_broker(s: Settings) -> Broker:
    """Monta a corretora conforme o .env.

    - TRADING_MODE=paper: dados reais/sintéticos + execução simulada.
    - TRADING_MODE=live : execução real na corretora escolhida.
    """
    if s.broker == "synthetic":
        feed: Broker = SyntheticFeed()
        if s.is_live:
            raise ValueError("BROKER=synthetic só funciona em TRADING_MODE=paper")
    elif s.broker == "crypto":
        from .crypto import CryptoBroker

        if s.is_live:
            if not s.exchange_api_key:
                raise ValueError("Modo live com cripto exige EXCHANGE_API_KEY e EXCHANGE_SECRET")
            return CryptoBroker(s.exchange_id, s.exchange_api_key, s.exchange_secret,
                                testnet=s.exchange_testnet, quote=s.quote_currency)
        feed = CryptoBroker(s.exchange_id, quote=s.quote_currency)  # só dados públicos
    elif s.broker == "mt5":
        from .mt5 import MT5Broker

        b = MT5Broker(s.mt5_login, s.mt5_password, s.mt5_server, s.mt5_path)
        if s.is_live:
            return b
        feed = b
    else:
        raise ValueError(f"BROKER desconhecido: {s.broker}")
    return PaperBroker(feed, s.initial_capital, s.fee_rate, s.slippage)


def build_us_broker(s: Settings) -> Broker | None:
    """Corretora dos EUA: Alpaca se houver chaves, senão simulação com dados do Yahoo."""
    if not s.us_enabled:
        return None
    from .us import AlpacaBroker, YahooFeed

    if s.alpaca_key:
        return AlpacaBroker(s.alpaca_key, s.alpaca_secret, paper=s.alpaca_paper or not s.is_live)
    return PaperBroker(YahooFeed(), s.us_initial_capital, 0.0, 0.0005, cash_key="paper_cash_us",
                       label="simulação EUA")


__all__ = ["Broker", "Fill", "build_broker", "PaperBroker", "SyntheticFeed"]
