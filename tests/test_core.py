import json
from pathlib import Path

import pytest

from app import backtest, evolution, research, risk
from app.brokers import PaperBroker, SyntheticFeed
from app.config import settings
from app.engine import Engine
from app.strategies import STRATEGIES

FIX = Path(__file__).resolve().parents[1] / "extras" / "trader-skill" / "tests" / "fixtures" / "trader-copycat"


def data(n=1500):
    return SyntheticFeed(advance=False).fetch_ohlcv("BTC/USDT", "1h", n)


def test_all_strategies_run():
    df = data()
    for name, sd in STRATEGIES.items():
        p = sd.constrain({k: (lo + hi) / 2 for k, (lo, hi, _) in sd.space.items()})
        r = backtest.run(df, name, p)
        assert r.max_drawdown >= 0 and r.trades >= 0


def test_backtest_charges_costs():
    df = data()
    p = {"fast": 5, "slow": 20, "trend_filter": 0}
    cheap = backtest.run(df, "sma_cross", p, fee=0, slippage=0)
    dear = backtest.run(df, "sma_cross", p, fee=0.01, slippage=0)
    assert dear.total_return < cheap.total_return


def test_evolution_validates_out_of_sample():
    res = evolution.evolve(data(), seed=7, generations=4, population=20)
    if res["best"]:
        b = res["best"]
        assert b["valid"] and b["oos"]["max_drawdown"] <= 0.15


def test_position_size_respects_limits(fresh_db):
    fresh_db.set_setting("risk_per_trade", 0.01)
    fresh_db.set_setting("max_position_pct", 0.25)
    q = risk.position_size(10_000, 10_000, 100, 95)   # risco 1% = 100 -> 20 unid.; teto 25% = 25 unid.
    assert q == pytest.approx(20)
    q = risk.position_size(10_000, 10_000, 100, 99.9)  # stop curto: limitado a 25% do patrimônio
    assert q == pytest.approx(25)
    q = risk.position_size(10_000, 500, 100, 95)       # sem caixa
    assert q <= 5


def test_kill_switch_on_drawdown(fresh_db):
    fresh_db.execute("INSERT INTO equity(ts,equity,cash) VALUES(?,?,?)", (fresh_db.now(), 10_000, 0))
    risk.update_guards(8_000)
    assert fresh_db.get_bool("kill_switch")
    assert not risk.can_open("X", 8_000, True).ok


def _engine():
    return Engine(settings, PaperBroker(SyntheticFeed(), 10_000, 0.001, 0.0005))


def test_manual_mode_waits_for_approval(fresh_db):
    e = _engine()
    s = e.create_signal("BTC/USDT", "buy", 1, 100, 95, 115, "teste", "teste")
    assert s["status"] == "pending" and not fresh_db.query("SELECT * FROM trades")
    assert "executada" in e.approve(s["id"])
    assert fresh_db.one("SELECT * FROM positions WHERE symbol='BTC/USDT'")


def test_auto_mode_and_stop(fresh_db):
    fresh_db.set_setting("execution_mode", "auto")
    e = _engine()
    for _ in range(250):
        e.cycle()
    eq, cash = e.equity()
    assert eq > 0
    for t in fresh_db.query("SELECT * FROM trades WHERE side='sell'"):
        assert t["reason"]


def test_reject_and_expire(fresh_db):
    e = _engine()
    s = e.create_signal("ETH/USDT", "buy", 1, 100, 95, 115, "t", "t")
    e.reject(s["id"])
    assert fresh_db.one("SELECT status FROM signals WHERE id=?", (s["id"],))["status"] == "rejected"
    s2 = e.create_signal("ETH/USDT", "buy", 1, 100, 95, 115, "t", "t")
    fresh_db.execute("UPDATE signals SET expires_at='2000-01-01' WHERE id=?", (s2["id"],))
    assert "expirou" in e.approve(s2["id"])


class StubUS(SyntheticFeed):
    P = {"AAPL": 340, "AXP": 300, "GOOGL": 330, "GOOG": 330, "KO": 70, "BAC": 50}

    def last_price(self, s):
        return self.P.get(s, 100)

    def is_market_open(self, s):
        return True

    def normalize_qty(self, s, q, p):
        return round(q, 4)


@pytest.mark.skipif(not FIX.exists(), reason="fixtures do trader-skill ausentes")
def test_trader_skill_pipeline(fresh_db):
    """13F (offline) -> orçamento -> trader-risk -> sinais -> execução -> lotes de imposto."""
    us = PaperBroker(StubUS(), 5000, 0, 0, cash_key="paper_cash_us")
    e = Engine(settings, PaperBroker(SyntheticFeed(), 10_000, 0.001, 0.0005), us_broker=us)
    research.attach(e)
    research.sync_portfolio(e)
    r = research.run_tool("copycat", "copy", "1067983", "--top", "5", "--offline", str(FIX),
                          "--map", "/nonexistent.json", "--out", "trader/targets.json")
    assert r["code"] == 0, r["error"]
    assert research.run_tool("budget", "--write")["code"] == 0
    out = research.orders_to_signals(e, "copycat:1067983")
    assert out["created"] == 5
    sid = out["signal_ids"][0]
    assert "executada" in e.approve(sid)
    pf = json.loads(research.tpath("portfolio.json").read_text())
    assert pf["positions"][0]["lots"]
    assert research.tax_report(e)["code"] == 0
    assert research.nyse_clock().get("calendar_known") is not None


def test_readiness_not_ready_at_start(fresh_db):
    from app import readiness

    r = readiness.check(_engine())
    assert r["ready"] is False and len(r["items"]) == 5


def test_b3_market_in_cycle(fresh_db):
    """Um segundo mercado (B3) roda no mesmo ciclo, com caixa e posições separados."""
    e = _engine()
    b3 = PaperBroker(SyntheticFeed(seed=7), 10_000, 0.0003, 0.0005, cash_key="paper_cash_b3")
    e.add_strategy_market("b3", b3, ["PETR4"])
    fresh_db.set_setting("execution_mode", "auto")
    for _ in range(150):
        e.cycle()
    eq_b3, cash_b3 = e.market_equity("b3")
    assert eq_b3 > 0
    for p in fresh_db.query("SELECT * FROM positions"):
        assert p["market"] == ("b3" if p["symbol"] == "PETR4" else "main")


def test_deposit_is_not_profit(fresh_db):
    fresh_db.execute("INSERT INTO cash_flows(ts,amount,note) VALUES(?,?,?)", (fresh_db.now(), 10_000, "x"))
    fresh_db.execute("INSERT INTO equity(ts,equity,cash,flows) VALUES(?,?,?,?)", (fresh_db.now(), 10_000, 0, 10_000))
    fresh_db.execute("INSERT INTO cash_flows(ts,amount,note) VALUES(?,?,?)", (fresh_db.now(), 5_000, "aporte"))
    assert risk.day_start_equity(15_000) == 15_000          # aporte não vira "lucro do dia"
    fresh_db.execute("INSERT INTO cash_flows(ts,amount,note) VALUES(?,?,?)", (fresh_db.now(), -8_000, "saque"))
    risk.update_guards(7_000)                               # saque não dispara a trava
    assert not fresh_db.get_bool("kill_switch")


def test_profile_metralhadora(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "broker", "crypto")
    e = _engine()
    e.apply_profile("metralhadora")
    assert e.timeframe == "15m" and len(e.symbols_for("main")) == 12
    assert fresh_db.get_setting("max_positions") == "8"
    e.apply_profile("normal")
    assert e.symbols_for("main") == list(settings.symbols)


def test_performance_weight(fresh_db):
    assert risk.performance_weight("X") == 1.0
    for pnl in (5, 4, 3, 6, -1):
        fresh_db.execute("INSERT INTO trades(ts,symbol,side,qty,price,fee,pnl) VALUES(?,?,?,?,?,?,?)",
                         (fresh_db.now(), "X", "sell", 1, 1, 0, pnl))
    assert risk.performance_weight("X") > 1.0
    for _ in range(10):
        fresh_db.execute("INSERT INTO trades(ts,symbol,side,qty,price,fee,pnl) VALUES(?,?,?,?,?,?,?)",
                         (fresh_db.now(), "Y", "sell", 1, 1, 0, -2))
    assert risk.performance_weight("Y") < 1.0
