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
        self.locked = {}
        self.orders = []       # execuções (símbolo, lado, qtd, preço, tipo)
        self.open = {}         # ordens limite no livro
        self.px_override = {}
        self.fill_limits = True  # False = ninguém executa nossas ordens passivas

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

    def price_to_precision(self, s, p):
        return f"{float(p):.6f}"

    def fetch_order_book(self, s, limit=5):
        px = self.fetch_ticker(s)["last"]
        return {"bids": [[px * 0.999, 1]], "asks": [[px * 1.001, 1]]}

    def _exec(self, s, side, q, px, maker):
        fee = 0.9975 if maker else 0.995
        base = s.split("/")[0]
        if side == "buy":
            self.bal[base] = self.bal.get(base, 0) + q * fee
        else:
            self.bal["BRL"] += q * px * fee
        self.orders.append((s, side, q, px, "limit" if maker else "market"))

    def fetch_order(self, oid, s):
        o = self.open[oid]
        if o["status"] == "open" and self.fill_limits:
            px = self.fetch_ticker(s)["last"]
            hit = px <= o["price"] * 1.002 if o["side"] == "buy" else px >= o["price"]
            if hit:
                o["status"], o["filled"] = "closed", o["amount"]
                if o["side"] == "sell":
                    self.locked[s.split("/")[0]] -= o["amount"]
                self._exec(s, o["side"], o["amount"], o["price"], True)
        return {"id": oid, "status": o["status"], "filled": o["filled"], "price": o["price"], "average": None}

    def cancel_order(self, oid, s):
        o = self.open[oid]
        if o["status"] == "open":
            o["status"] = "canceled"
            base = s.split("/")[0]
            if o["side"] == "buy":
                self.bal["BRL"] += o["amount"] * o["price"]
            else:
                self.locked[base] -= o["amount"]
                self.bal[base] += o["amount"]
        return {"id": oid}

    def fetch_open_orders(self, s):
        return [{"id": k} for k, o in self.open.items() if o["status"] == "open" and o["symbol"] == s]

    def create_order(self, s, t, side, q, price=None, params=None):
        q = float(q)
        base = s.split("/")[0]
        if t == "limit":
            price = float(price)
            assert q * price >= 10, "abaixo da ordem mínima"
            if side == "buy":
                assert self.bal["BRL"] >= q * price - 1e-9, "saldo insuficiente"
                self.bal["BRL"] -= q * price
            else:
                assert self.bal.get(base, 0) + 1e-12 >= q, "vendendo mais do que tem"
                self.bal[base] -= q
                self.locked[base] = self.locked.get(base, 0) + q
            oid = f"L{len(self.open) + 1}"
            self.open[oid] = {"symbol": s, "side": side, "amount": q, "price": price, "status": "open", "filled": 0}
            return {"id": oid}
        px = self.fetch_ticker(s)["last"]
        if side == "buy":
            assert q * px >= 10, "abaixo da ordem mínima"
            assert self.bal["BRL"] >= q * px, "saldo insuficiente"
            self.bal["BRL"] -= q * px
        else:
            assert self.bal.get(base, 0) + 1e-12 >= q, "vendendo mais do que tem"
            self.bal[base] -= q
        self._exec(s, side, q, px, False)  # taxa cobrada na moeda (compra) ou em reais (venda)
        return {"id": f"M{len(self.orders)}"}  # a Foxbit responde só com o id


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
    assert ("SOL/BRL", "buy", "limit") == (ex.orders[0][0], ex.orders[0][1], ex.orders[0][4]), \
        f"não comprou na oportunidade com ordem passiva: {e.state}"
    pos = db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")
    assert pos["strategy"] == "oportunidade" and pos["take"] > pos["avg_price"] > pos["stop"] > 0
    assert (pos["take"] / pos["avg_price"] - 1) > 2 * 2 * e.fee_for("real")  # alvo paga as taxas
    assert pos["take_order"], "a venda no alvo não ficou no livro"
    assert e.state[("real", "BTC/BRL")]["status"] != "erro", "candles não lidos (plano B falhou)"
    ex.px_override["SOL/BRL"] = pos["take"] * 1.001  # preço bate o alvo
    e.cycle()
    assert ex.orders[-1][:2] == ("SOL/BRL", "sell") and ex.orders[-1][4] == "limit", "não vendeu no alvo"
    sell = db.one("SELECT * FROM trades WHERE symbol='SOL/BRL' AND side='sell'")
    assert sell["pnl"] > 0 and sell["fee"] > 0  # lucro depois das taxas e taxa registrada
    assert not db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")


def test_dinheiro_real_stop_e_saldo(real_engine):
    e, ex, db = real_engine
    e.cycle()
    pos = db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")
    ex.px_override["SOL/BRL"] = pos["stop"] * 0.999  # preço fura o stop
    e.cycle()
    assert ex.orders[-1][:2] == ("SOL/BRL", "sell") and ex.orders[-1][4] == "market"
    assert not ex.fetch_open_orders("SOL/BRL"), "a venda do alvo ficou presa no livro"
    assert ex.locked.get("SOL", 0) == 0 and ex.bal.get("SOL", 0) * pos["avg_price"] < 0.01  # só poeira
    assert db.one("SELECT pnl FROM trades WHERE side='sell'")["pnl"] < 0
    leq, cash = e.live_equity()
    held = sum(p["qty"] * e.prices.get(p["symbol"], p["avg_price"]) for p in db.query("SELECT * FROM positions"))
    assert 95 < leq < 100 and abs(leq - (cash + held)) < 0.01  # perdeu pouco; conta fecha com reais + posições
    assert not db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")


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


def test_ordem_passiva_que_nao_executa_nao_vira_erro(real_engine):
    e, ex, db = real_engine
    ex.fill_limits = False  # ninguém aceita o nosso preço
    import app.brokers.crypto as cb
    import time as _t
    orig = cb.CryptoBroker.maker_buy
    cb.CryptoBroker.maker_buy = lambda self, s, q, wait_s=45: orig(self, s, q, wait_s=0)
    try:
        e.cycle()
    finally:
        cb.CryptoBroker.maker_buy = orig
    assert not db.one("SELECT * FROM positions WHERE symbol='SOL/BRL'")
    assert not ex.fetch_open_orders("SOL/BRL"), "a ordem que não executou não foi cancelada"
    assert abs(ex.bal["BRL"] - 100) < 1e-6, "o dinheiro ficou preso"
    assert db.one("SELECT status FROM signals WHERE symbol='SOL/BRL'")["status"] == "expired"
    assert not db.query("SELECT * FROM logs WHERE level='ERROR'")


def test_autoteste_acusa_chave_invalida(real_engine, monkeypatch):
    from app import selftest

    e, ex, db = real_engine

    def bad():
        raise Exception("401 Unauthorized: invalid api key")

    monkeypatch.setattr(ex, "fetch_balance", bad)
    r = selftest.run(e)
    item = next(i for i in r["items"] if i["item"] == "Conexão e saldo")
    assert item["ok"] is False and "401" in item["detail"] and not r["ok"]
