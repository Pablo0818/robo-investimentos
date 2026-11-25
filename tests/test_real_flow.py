"""Fluxo COMPLETO do dinheiro real contra uma Foxbit falsa, rodado em toda atualização do servidor.

A Foxbit falsa imita os comportamentos reais que já deram problema: candles do mais novo para o mais
velho, recusa do pedido com data, taxa da compra cobrada na própria moeda e ordem mínima de R$ 10.
"""
import json
import time
from datetime import datetime, timezone

import numpy as np
import pytest


class FakeFoxbit:
    id = "foxbit"

    def __init__(self, params=None):
        self.bal = {"BRL": 100.0}
        self.orders = []
        self.px_override = {}

    def parse_timeframe(self, tf):
        return {"15m": 900, "1h": 3600}[tf]

    def set_sandbox_mode(self, v):
        pass

    def load_markets(self):
        return {f"{c}/BRL": {"symbol": f"{c}/BRL", "limits": {"amount": {"min": 1e-8}, "cost": {"min": 10}}}
                for c in ("BTC", "ETH", "SOL")}

    def _rows(self, symbol, tf, limit):
        r = np.random.default_rng(sum(map(ord, symbol)) + len(tf))
        c = 100 * np.exp(np.cumsum(r.normal(0.0006, 0.01, limit)))
        if tf == "15m" and symbol == "SOL/BRL":  # queda rápida nas últimas 4 velas e começando a reagir
            for k, m in zip(range(-5, -1), (0.985, 0.97, 0.955, 0.95)):
                c[k] = c[-6] * m
            c[-1] = c[-2] * 1.004
        step = self.parse_timeframe(tf) * 1000
        now = int(time.time() * 1000) // step * step
        rows = []
        for i in range(limit):
            o = c[i - 1] if i else c[0]
            if tf == "15m" and symbol == "SOL/BRL" and i == limit - 1:
                o = c[i - 1] * 0.999
            rows.append([now - (limit - 1 - i) * step, o, max(o, c[i]) * 1.001, min(o, c[i]) * 0.999, c[i], 10.0])
        return rows[::-1]  # como a Foxbit pode mandar: do mais novo para o mais velho

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=500):
        if since is not None:
            raise Exception("400 Bad Request: end_time is required")  # obriga a usar o plano B
        return self._rows(symbol, timeframe, min(limit, 500))

    def fetch_ticker(self, s):
        if s in self.px_override:
            return {"last": self.px_override[s]}
        return {"last": self._rows(s, "15m", 50)[0][4]}

    def fetch_tickers(self):
        return {k: {"symbol": k, "quoteVolume": 1000} for k in self.load_markets()}

    def fetch_balance(self):
        return {"free": dict(self.bal)}

    def amount_to_precision(self, s, q):
        return f"{float(q):.8f}"

    def create_order(self, s, t, side, q):
        q = float(q)
        px = self.fetch_ticker(s)["last"]
        base = s.split("/")[0]
        if side == "buy":
            assert q * px >= 10, "abaixo da ordem mínima"
            assert self.bal["BRL"] >= q * px, "saldo insuficiente"
            self.bal["BRL"] -= q * px
            self.bal[base] = self.bal.get(base, 0) + q * 0.995  # taxa cobrada na moeda
        else:
            assert self.bal.get(base, 0) + 1e-12 >= q, "vendendo mais do que tem"
            self.bal[base] -= q
            self.bal["BRL"] += q * px * 0.995
        self.orders.append((s, side, q, px))
        return {"id": len(self.orders)}  # a Foxbit responde só com o id


@pytest.fixture()
def real_engine(fresh_db, monkeypatch):
    import ccxt

    from app.brokers import build_real_broker
    from app.brokers.paper import PaperBroker
    from app.brokers.synthetic import SyntheticFeed
    from app.config import settings
    from app.engine import Engine

    monkeypatch.setattr(ccxt, "foxbit", FakeFoxbit)
    monkeypatch.setattr(settings, "real_exchange", "foxbit")
    monkeypatch.setattr(settings, "real_api_key", "k")
    monkeypatch.setattr(settings, "real_secret", "s")
    b, fee = build_real_broker(settings)
    e = Engine(settings, PaperBroker(SyntheticFeed(), 10_000, 0.001, 0.0005))
    e.add_strategy_market("real", b, ["SOL/BRL", "BTC/BRL", "ETH/BRL"])
    e.market_fees["real"] = fee
    fresh_db.set_setting("execution_mode", "auto")
    fresh_db.set_setting("opp_rules", json.dumps({"drop": 0.015, "rsi": 35, "take": 0.03, "stop": 0.02,
                                                  "trades": 10, "avg": 0.004}))
    fresh_db.set_setting("opp_calibrated", datetime.now(timezone.utc).isoformat())
    return e, b.ex, fresh_db


def test_dinheiro_real_compra_e_vende_por_oportunidade(real_engine):
    e, ex, db = real_engine
    e.cycle()
    assert e.live_markets() == ["real"]
    assert ("SOL/BRL", "buy") == ex.orders[0][:2], f"não comprou na oportunidade: {e.state}"
    pos = db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")
    assert pos["strategy"] == "oportunidade" and pos["take"] > pos["avg_price"] > pos["stop"] > 0
    assert (pos["take"] / pos["avg_price"] - 1) > 2 * 2 * e.fee_for("real")  # alvo paga as taxas
    assert e.state[("real", "BTC/BRL")]["status"] != "erro", "candles não lidos (plano B falhou)"
    ex.px_override["SOL/BRL"] = pos["take"] * 1.001  # preço bate o alvo
    e.cycle()
    assert ex.orders[-1][:2] == ("SOL/BRL", "sell"), "não vendeu no alvo"
    sell = db.one("SELECT * FROM trades WHERE symbol='SOL/BRL' AND side='sell'")
    assert sell["pnl"] > 0 and sell["fee"] > 0  # lucro depois das taxas e taxa registrada
    assert not db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")


def test_dinheiro_real_stop_e_saldo(real_engine):
    e, ex, db = real_engine
    e.cycle()
    pos = db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")
    ex.px_override["SOL/BRL"] = pos["stop"] * 0.999  # preço fura o stop
    e.cycle()
    assert ex.orders[-1][:2] == ("SOL/BRL", "sell")
    assert db.one("SELECT pnl FROM trades WHERE side='sell'")["pnl"] < 0
    leq, cash = e.live_equity()
    assert 95 < leq < 100 and cash == leq  # perdeu pouco, tudo de volta em reais


def test_autoteste_e_ordem_de_teste(real_engine):
    from app import selftest

    e, ex, db = real_engine
    e.cycle()
    r = selftest.run(e)
    bad = [i for i in r["items"] if i["ok"] is False]
    assert not bad, bad
    t = selftest.order_test(e)
    assert t["ok"], t
    assert [o[1] for o in ex.orders[-2:]] == ["buy", "sell"]


def test_autoteste_acusa_chave_invalida(real_engine, monkeypatch):
    from app import selftest

    e, ex, db = real_engine

    def bad():
        raise Exception("401 Unauthorized: invalid api key")

    monkeypatch.setattr(ex, "fetch_balance", bad)
    r = selftest.run(e)
    item = next(i for i in r["items"] if i["item"] == "Conexão e saldo")
    assert item["ok"] is False and "401" in item["detail"] and not r["ok"]
