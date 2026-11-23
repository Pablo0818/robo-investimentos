"""Gestão de risco e de capital.

Regras (todas ajustáveis no painel):
- Tamanho da posição: arrisca no máximo `risk_per_trade` do patrimônio entre a entrada e o stop.
- Nenhum ativo passa de `max_position_pct` do patrimônio.
- No máximo `max_positions` posições abertas.
- Perda diária >= `max_daily_loss_pct`: não abre novas posições até o dia seguinte.
- Queda desde o topo >= `max_drawdown_pct`: TRAVA TOTAL (kill switch), só você destrava.
- Reinvestimento: com `reinvest_profits` ligado, o lucro aumenta o tamanho das próximas
  posições (juros compostos). Desligado, o tamanho fica limitado ao capital aportado.
- Saídas (vendas e stops) nunca são bloqueadas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import db


@dataclass
class Check:
    ok: bool
    reason: str = ""


def net_deposits() -> float:
    r = db.one("SELECT COALESCE(SUM(amount),0) s FROM cash_flows")
    return float(r["s"]) if r else 0.0


# Aportes e retiradas NÃO contam como lucro/prejuízo: comparamos "patrimônio - aportes".
def day_start_equity(current: float) -> float:
    """Patrimônio do início do dia, ajustado pelos aportes/retiradas feitos desde então."""
    today = datetime.now(timezone.utc).date().isoformat()
    r = db.one("SELECT equity, flows FROM equity WHERE ts >= ? ORDER BY ts ASC LIMIT 1", (today,))
    if not r:
        return current
    return float(r["equity"]) + (net_deposits() - float(r["flows"] or 0))


def peak_equity(current: float) -> float:
    """Maior patrimônio já atingido, ajustado para a base atual de aportes."""
    r = db.one("SELECT MAX(equity - COALESCE(flows,0)) m FROM equity")
    if not r or r["m"] is None:
        return current
    return max(current, float(r["m"]) + net_deposits())


def sizing_capital(equity: float) -> float:
    if db.get_bool("reinvest_profits"):
        return equity
    base = net_deposits()
    return min(equity, base) if base > 0 else equity


def performance_weight(symbol: str | None) -> float:
    """Peso do ativo (0,5x a 1,5x): mais dinheiro para quem está dando lucro.

    Combina a nota da estratégia no teste (fora da amostra) com o resultado real
    das últimas 10 operações fechadas desse ativo.
    """
    if not symbol:
        return 1.0
    w = 1.0
    s = db.one("SELECT oos_sharpe FROM strategies WHERE symbol=? AND status='champion'", (symbol,))
    if s and s["oos_sharpe"] is not None:
        w += max(-0.25, min(0.25, (float(s["oos_sharpe"]) - 1.5) / 6))
    rows = db.query("SELECT pnl FROM trades WHERE symbol=? AND side='sell' ORDER BY id DESC LIMIT 10", (symbol,))
    if len(rows) >= 3:
        wins = sum(1 for r in rows if r["pnl"] > 0) / len(rows)
        net = sum(r["pnl"] for r in rows)
        w += (wins - 0.5) * 0.5 + (0.1 if net > 0 else -0.1)
    return max(0.5, min(1.5, w))


def position_size(equity: float, cash: float, price: float, stop: float, symbol: str | None = None,
                  mult: float = 1.0) -> float:
    """Quantidade a comprar (ajustada pelo desempenho do ativo)."""
    if price <= 0 or stop >= price:
        return 0.0
    cap = sizing_capital(equity)
    w = performance_weight(symbol) * mult
    risk_amt = cap * db.get_float("risk_per_trade") * w
    qty_risk = risk_amt / (price - stop)
    qty_cap = cap * min(0.35, db.get_float("max_position_pct") * w) / price
    qty_cash = cash * 0.98 / price  # folga para taxas
    return max(0.0, min(qty_risk, qty_cap, qty_cash))


def update_guards(equity: float, notify=None) -> None:
    """Liga a trava total se a queda desde o topo passar do limite."""
    peak = peak_equity(equity)
    dd = (peak - equity) / peak if peak > 0 else 0
    if dd >= db.get_float("max_drawdown_pct") and not db.get_bool("kill_switch"):
        db.set_setting("kill_switch", True)
        msg = (f"TRAVA DE SEGURANÇA ATIVADA: patrimônio caiu {dd:.1%} desde o topo. "
               "Nenhuma compra nova até você destravar no painel.")
        db.log(msg, "ERROR")
        if notify:
            notify(msg)


def can_open(symbol: str, equity: float, market_open: bool, market: str = "main") -> Check:
    if db.get_bool("kill_switch"):
        return Check(False, "trava de segurança ligada")
    if db.get_bool("paused"):
        return Check(False, "robô pausado")
    if not market_open:
        return Check(False, "mercado fechado")
    last = db.one("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1")
    if last:  # perda diária medida no patrimônio total
        total = float(last["equity"])
        start = day_start_equity(total)
        if start > 0 and (start - total) / start >= db.get_float("max_daily_loss_pct"):
            return Check(False, "limite de perda diária atingido")
    n = db.one("SELECT COUNT(*) c FROM positions WHERE market=?", (market,))["c"]
    if n >= int(db.get_float("max_positions")):
        return Check(False, "máximo de posições abertas")
    if db.one("SELECT 1 FROM positions WHERE symbol=?", (symbol,)):
        return Check(False, "já existe posição neste ativo")
    return Check(True)
