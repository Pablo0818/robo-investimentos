"""Configuração fixa (arquivo .env) do Robô de Investimentos.

Configurações de infraestrutura e chaves ficam no .env.
Configurações operacionais (modo manual/automático, risco etc.) ficam no
banco e podem ser alteradas pelo painel.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    v = _env(name, str(default)).lower()
    return v in ("1", "true", "sim", "yes", "on")


@dataclass
class Settings:
    # paper = simulação com dinheiro fictício | live = dinheiro real
    trading_mode: str = field(default_factory=lambda: _env("TRADING_MODE", "paper").lower())
    # Fonte de dados / corretora: synthetic | crypto | mt5
    broker: str = field(default_factory=lambda: _env("BROKER", "synthetic").lower())
    symbols: list[str] = field(
        default_factory=lambda: [s.strip() for s in _env("SYMBOLS", "BTC/USDT,ETH/USDT").split(",") if s.strip()]
    )
    timeframe: str = field(default_factory=lambda: _env("TIMEFRAME", "1h"))
    loop_seconds: int = field(default_factory=lambda: int(_env("LOOP_SECONDS", "60")))
    initial_capital: float = field(default_factory=lambda: float(_env("INITIAL_CAPITAL", "10000")))
    quote_currency: str = field(default_factory=lambda: _env("QUOTE_CURRENCY", "USDT"))
    fee_rate: float = field(default_factory=lambda: float(_env("FEE_RATE", "0.001")))
    slippage: float = field(default_factory=lambda: float(_env("SLIPPAGE", "0.0005")))
    history_bars: int = field(default_factory=lambda: int(_env("HISTORY_BARS", "1000")))

    # Cripto (ccxt)
    exchange_id: str = field(default_factory=lambda: _env("EXCHANGE_ID", "binance"))
    exchange_api_key: str = field(default_factory=lambda: _env("EXCHANGE_API_KEY"))
    exchange_secret: str = field(default_factory=lambda: _env("EXCHANGE_SECRET"))
    exchange_testnet: bool = field(default_factory=lambda: _bool("EXCHANGE_TESTNET", True))

    # MetaTrader 5 (B3)
    mt5_login: str = field(default_factory=lambda: _env("MT5_LOGIN"))
    mt5_password: str = field(default_factory=lambda: _env("MT5_PASSWORD"))
    mt5_server: str = field(default_factory=lambda: _env("MT5_SERVER"))
    mt5_path: str = field(default_factory=lambda: _env("MT5_PATH"))

    # Bolsa dos EUA / Pesquisa (módulo trader-skill)
    us_enabled: bool = field(default_factory=lambda: _bool("US_ENABLED", True))
    us_initial_capital: float = field(default_factory=lambda: float(_env("US_INITIAL_CAPITAL", "5000")))
    alpaca_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY"))
    alpaca_secret: str = field(default_factory=lambda: _env("ALPACA_SECRET"))
    alpaca_paper: bool = field(default_factory=lambda: _bool("ALPACA_PAPER", True))
    sec_user_agent: str = field(default_factory=lambda: _env("SEC_USER_AGENT"))

    # B3 em simulação com preços reais (Yahoo, ~15 min de atraso). Dinheiro real exige MT5.
    b3_enabled: bool = field(default_factory=lambda: _bool("B3_ENABLED", True))
    b3_symbols: list[str] = field(
        default_factory=lambda: [s.strip().upper() for s in _env("B3_SYMBOLS", "PETR4,VALE3,ITUB4,BBAS3,WEGE3").split(",") if s.strip()]
    )
    b3_initial_capital: float = field(default_factory=lambda: float(_env("B3_INITIAL_CAPITAL", "10000")))

    # Telegram
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # Painel (login por e-mail e senha; a senha inicial só vale até o 1º acesso)
    admin_email: str = field(default_factory=lambda: _env("ADMIN_EMAIL", "admin@local"))
    admin_initial_password: str = field(default_factory=lambda: _env("ADMIN_INITIAL_PASSWORD"))
    trust_proxy: bool = field(default_factory=lambda: _bool("TRUST_PROXY", False))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))

    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(Path("data") / "robo.db")))

    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"


# Configurações operacionais padrão (editáveis no painel; salvas no banco)
DEFAULT_RUNTIME = {
    "execution_mode": "manual",      # manual = você aprova | auto = robô executa sozinho
    "auto_evolve": "false",          # promover automaticamente estratégias melhores
    "reinvest_profits": "true",      # usar o lucro para aumentar o tamanho das posições
    "risk_per_trade": "0.01",        # 1% do patrimônio arriscado por operação
    "max_position_pct": "0.25",      # no máximo 25% do patrimônio em um único ativo
    "max_positions": "4",
    "max_daily_loss_pct": "0.03",    # perde 3% no dia -> para de abrir posições até amanhã
    "max_drawdown_pct": "0.15",      # cai 15% do topo -> trava total (kill switch)
    "atr_stop_mult": "2.0",          # stop = preço - 2 x ATR
    "take_profit_r": "3.0",          # alvo = 3x o risco
    "trailing_stop": "true",
    "signal_ttl_minutes": "60",      # sinal pendente expira em 60 min
    "evolve_every_hours": "24",
    "kill_switch": "false",
    # Perfil de operação: normal | metralhadora (vazio = usa o .env)
    "profile": "normal",
    "strategy_style": "baixa",   # baixa = comprar na queda e vender na alta | tendencia | todas
    "timeframe": "",
    "crypto_symbols": "",
    # Pesquisa EUA (trader-skill)
    "us_cash_buffer_pct": "10",
    "us_max_position_pct": "25",
    "us_max_order_usd": "2000",
    "us_min_order_usd": "5",
    "us_max_orders_per_day": "10",
    "us_blocked": "",
    "us_watch_news": "true",
    "us_watch_pelosi": "true",
    "us_watch_alerts": "true",
    "us_news_minutes": "60",
    "us_pelosi_minutes": "30",
    "us_alerts_minutes": "5",
    "paused": "false",
}

settings = Settings()


# Perfis prontos (aplicados pelo painel). A evolução continua exigindo lucro já
# descontadas as taxas, então mais operações só acontecem se compensarem.
PROFILES = {
    "normal": {
        "timeframe": "", "crypto_symbols": "", "max_positions": "4", "risk_per_trade": "0.01",
        "max_position_pct": "0.25", "atr_stop_mult": "2.0", "take_profit_r": "3.0",
        "signal_ttl_minutes": "60", "evolve_every_hours": "24",
    },
    "metralhadora": {
        "timeframe": "15m",
        "crypto_symbols": "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,ADA/USDT,AVAX/USDT,"
                          "LINK/USDT,TRX/USDT,LTC/USDT,DOT/USDT",
        "max_positions": "8", "risk_per_trade": "0.01", "max_position_pct": "0.15",
        "atr_stop_mult": "1.5", "take_profit_r": "2.0", "signal_ttl_minutes": "15",
        "evolve_every_hours": "6", "auto_evolve": "true", "reinvest_profits": "true",
        "strategy_style": "baixa",
    },
}
