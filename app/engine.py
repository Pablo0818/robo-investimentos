"""Motor do robô: roda em ciclo contínuo (24h), lê o mercado, gera sinais,
pede sua aprovação (modo manual) ou executa sozinho (modo automático),
protege as posições com stop e evolui as estratégias periodicamente."""
from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import db, evolution, risk
from . import brain
from . import strategies as st
from .brokers import Broker
from .config import Settings


class Engine:
    def __init__(self, cfg: Settings, broker: Broker, notify: Callable[[str], None] | None = None,
                 on_signal: Callable[[dict], None] | None = None, us_broker: Broker | None = None):
        self.cfg = cfg
        self.brokers: dict[str, Broker] = {"main": broker}
        if us_broker is not None:
            self.brokers["us"] = us_broker
        # mercados operados pelas estratégias automáticas: mercado -> ativos
        self.strategy_markets: dict[str, list[str]] = {"main": list(cfg.symbols)}
        self.market_fees: dict[str, float] = {}  # taxa por mercado (corretoras reais cobram mais)
        self.notify = notify or (lambda m: None)
        self.on_signal = on_signal or (lambda s: None)
        self.lock = threading.RLock()
        self.prices: dict[str, float] = {}
        self.hooks: list[Callable[["Engine"], None]] = []
        self.fill_hooks: list[Callable[[dict, object], None]] = []
        self.last_cycle: str | None = None
        # gráficos do painel: últimos candles de cada ativo e índices de referência
        self.candles: dict[tuple[str, str], "object"] = {}
        self.indices: list[tuple[str, str, str]] = []  # (mercado, símbolo, nome)
        self.index_data: dict[str, dict] = {}
        self.state: dict[tuple[str, str], dict] = {}
        self._evolved_this_cycle = 0  # o que o robô está fazendo em cada ativo
        self.last_error: str | None = None

    # ------------------------------------------------------------------ util
    def broker(self, market: str = "main") -> Broker:
        if market not in self.brokers:
            raise RuntimeError(f"Mercado '{market}' não configurado")
        return self.brokers[market]

    def price(self, symbol: str, market: str = "main") -> float:
        try:
            p = float(self.broker(market).last_price(symbol))
            self.prices[symbol] = p
            return p
        except Exception:
            if symbol in self.prices:
                return self.prices[symbol]
            raise

    def add_strategy_market(self, market: str, broker: Broker, symbols: list[str]) -> None:
        self.brokers[market] = broker
        self.strategy_markets[market] = list(symbols)

    @property
    def timeframe(self) -> str:
        return db.get_setting("timeframe") or self.cfg.timeframe

    def symbols_for(self, market: str) -> list[str]:
        if market == "main":
            custom = [x.strip() for x in db.get_setting("crypto_symbols").split(",") if x.strip()]
            if custom and self.cfg.broker == "crypto":
                return custom
        if market == "b3":
            custom = [x.strip().upper() for x in db.get_setting("b3_symbols").split(",") if x.strip()]
            if custom and market in self.strategy_markets:
                return custom
        return self.strategy_markets.get(market, [])

    def apply_profile(self, name: str) -> str:
        """Aplica um perfil pronto e força as estratégias a reaprenderem no novo ritmo."""
        from .config import PROFILES

        prof = PROFILES[name]
        with self.lock:
            for k, v in prof.items():
                db.set_setting(k, v)
            db.set_setting("profile", name)
            db.execute("UPDATE strategies SET status='retired' WHERE status IN ('champion','proposed')")
            db.execute("DELETE FROM settings WHERE key LIKE 'evolve_try:%'")
            db.set_setting("last_evolve", db.now())
            self.candles.clear()
        db.log(f"Perfil '{name}' ativado: gráfico {self.timeframe}, {len(self.symbols_for('main'))} criptos, "
               f"até {prof['max_positions']} posições por mercado. Estratégias vão reaprender.")
        return f"Perfil {name} ativado. O robô vai reaprender as estratégias nos próximos minutos."

    def money_mode(self, market: str) -> str:
        """'paper' = dinheiro fictício (simulação/testnet/conta demo) | 'live' = dinheiro real."""
        b = self.brokers.get(market)
        if b is None or b.name == "paper" or getattr(b, "paper", False):
            return "paper"
        return "live"

    def sim_paused(self, market: str) -> bool:
        """Com dinheiro real ligado, os mercados fictícios só coletam dados (não compram nem evoluem)."""
        return (self.money_mode(market) == "paper" and bool(self.live_markets())
                and not db.get_bool("simulate_with_live"))

    def live_markets(self) -> list[str]:
        return [m for m in self.brokers if self.money_mode(m) == "live"]

    def live_equity(self) -> tuple[float, float]:
        eq = cash = 0.0
        for m in self.live_markets():
            e, c = self.market_equity(m)
            eq, cash = eq + e, cash + c
        return eq, cash

    def fee_for(self, market: str) -> float:
        return self.market_fees.get(market, self.cfg.fee_rate)

    def market_equity(self, market: str) -> tuple[float, float]:
        """Patrimônio e caixa de UM mercado (cada mercado tem sua moeda e seu caixa)."""
        try:
            cash = self.broker(market).get_cash()
        except Exception:
            cash = 0.0
        value = sum(p["qty"] * self.prices.get(p["symbol"], p["avg_price"])
                    for p in db.query("SELECT * FROM positions WHERE market=?", (market,)))
        return cash + value, cash

    def equity(self) -> tuple[float, float]:
        cash = 0.0
        for b in self.brokers.values():
            try:
                cash += b.get_cash()
            except Exception:
                pass
        value = 0.0
        for p in db.query("SELECT * FROM positions"):
            px = self.prices.get(p["symbol"], p["avg_price"])
            value += p["qty"] * px
        return cash + value, cash

    def champion(self, symbol: str) -> dict | None:
        r = db.one("SELECT * FROM strategies WHERE symbol=? AND status='champion'", (symbol,))
        if r:
            r["params"] = json.loads(r["params"])
        return r

    # --------------------------------------------------------------- ciclo
    def run_forever(self, stop: threading.Event) -> None:
        db.log(f"Robô iniciado | modo {self.cfg.trading_mode.upper()} | corretora {self.cfg.broker} | "
               f"ativos {', '.join(self.cfg.symbols)}")
        while not stop.is_set():
            self.cycle()
            stop.wait(self.cfg.loop_seconds)

    def cycle(self) -> None:
        self._evolved_this_cycle = 0
        with self.lock:
            try:
                self._expire_signals()
                for market in self.strategy_markets:
                    for sym in self.symbols_for(market):
                        try:
                            self.process_symbol(sym, market)
                        except Exception as e:
                            db.log(f"{sym}: erro no ciclo: {e}", "ERROR")
                for p in db.query("SELECT symbol, market FROM positions WHERE market='us'"):
                    try:
                        self.price(p["symbol"], p["market"])
                    except Exception:
                        pass
                eq, cash = self.equity()
                last = db.one("SELECT ts FROM equity ORDER BY ts DESC LIMIT 1")
                if not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last["ts"])).total_seconds() >= 55:
                    db.execute("INSERT INTO equity(ts,equity,cash,flows) VALUES(?,?,?,?)",
                               (db.now(), eq, cash, risk.net_deposits()))
                    if self.live_markets():
                        leq, lcash = self.live_equity()
                        db.execute("INSERT INTO equity_live(ts,equity,cash) VALUES(?,?,?)", (db.now(), leq, lcash))
                risk.update_guards(eq, self.notify)
                self._maybe_evolve()
                self._update_indices()
                for h in self.hooks:
                    try:
                        h(self)
                    except Exception as e:
                        db.log(f"Módulo extra falhou: {e}", "ERROR")
                self.last_cycle = db.now()
                self.last_error = None
            except Exception as e:
                self.last_error = str(e)
                db.log(f"Erro no ciclo: {e}\n{traceback.format_exc()}", "ERROR")

    def _st(self, market: str, sym: str, status: str, detail: str = "", strategy: str | None = None) -> None:
        self.state[(market, sym)] = {"market": market, "symbol": sym, "status": status, "detail": detail,
                                     "strategy": strategy, "ts": db.now()}

    def process_symbol(self, sym: str, market: str = "main") -> None:
        b = self.broker(market)
        df = b.fetch_ohlcv(sym, self.timeframe, self.cfg.history_bars)
        if len(df) < 100:
            db.log(f"{sym}: histórico insuficiente ({len(df)} candles)", "WARN")
            return
        self.prices[sym] = float(df["close"].iloc[-1])
        self.candles[(market, sym)] = df.tail(400).reset_index(drop=True)
        sim_off = self.sim_paused(market)
        if sim_off and not db.one("SELECT 1 FROM positions WHERE symbol=?", (sym,)):
            self._st(market, sym, "coletando dados", "simulação parada: só lendo preços (dinheiro real ligado)")
            return
        champ = self.champion(sym)
        if champ is None and sim_off:
            return
        if champ is None:
            key = f"evolve_try:{sym}"
            last = db.get_setting(key)
            # revezamento justo: cada ativo sem estratégia volta à fila depois que os outros foram testados
            sem = sum(1 for m in self.strategy_markets if not self.sim_paused(m)
                      for x in self.symbols_for(m) if self.champion(x) is None)
            per = max(1, int(db.get_float("evolve_max_per_cycle")))
            wait = timedelta(minutes=max(db.get_float("evolve_retry_minutes"), sem / per) - 0.1)
            if (last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < wait) or \
                    self._evolved_this_cycle >= int(db.get_float("evolve_max_per_cycle")):
                self._st(market, sym, "sem estratégia", "testando estratégias a cada minuto")
                return
            self._evolved_this_cycle += 1
            db.set_setting(key, db.now())
            db.log(f"{sym}: sem estratégia aprovada ainda, rodando evolução...")
            evolution.run_for_symbol(sym, df, self.cfg, self.notify, self.timeframe, self.fee_for(market))
            champ = self.champion(sym)
            if champ is None:
                self._st(market, sym, "sem estratégia", "nenhuma estratégia passou no teste ainda; testa de novo em instantes")
                return
        pos_arr = st.positions(champ["name"], df, champ["params"])
        want, prev = pos_arr[-1], pos_arr[-2]
        a = float(st.atr(df).iloc[-1])
        position = db.one("SELECT * FROM positions WHERE symbol=?", (sym,))
        live_px = self.price(sym, market)

        if position:
            hi = max(position["highest"] or position["avg_price"], live_px)
            stop = position["stop"]
            if db.get_bool("trailing_stop") and stop:
                stop = max(stop, live_px - db.get_float("atr_stop_mult") * a)
            db.execute("UPDATE positions SET highest=?, stop=? WHERE symbol=?", (hi, stop, sym))
            self._st(market, sym, "comprado", f"resultado {live_px / position['avg_price'] - 1:+.2%} · stop "
                     f"{stop:.6g} · alvo {position['take'] or 0:.6g}", champ["name"])
            if stop and live_px <= stop:
                self._exit_now(sym, "stop de proteção atingido")
            elif position["take"] and live_px >= position["take"]:
                self._exit_now(sym, "alvo de lucro atingido")
            elif brain.wants_exit(df):
                self._exit_now(sym, brain.wants_exit(df))
            elif want == 0:
                self.create_signal(sym, "sell", position["qty"], live_px, 0, 0, champ["name"],
                                   f"estratégia {champ['name']} mandou sair", market=market)
            return

        if brain.reflect(sym, champ):
            self._st(market, sym, "sem estratégia", "estratégia aposentada depois de 5 perdas seguidas; reaprendendo")
            return
        if not (want == 1 and prev == 0):
            self._st(market, sym, "aguardando", "esperando o preço cair até o ponto de compra da estratégia"
                     if champ["name"] in st.STYLES["baixa"] else "esperando o sinal de entrada", champ["name"])
            return
        if want == 1 and prev == 0:
            eq, cash = self.market_equity(market)
            chk = risk.can_open(sym, eq, b.is_market_open(sym), market)
            if chk.ok and self.money_mode(market) == "live":
                chk = risk.live_guard(market, eq, self.notify)
            if not chk.ok:
                db.log(f"{sym}: sinal de compra ignorado ({chk.reason})")
                self._st(market, sym, "bloqueado", chk.reason, champ["name"])
                return
            from . import news_agent
            veto = news_agent.blocks_buy(sym) if market in ("main", "real") else None
            if veto:
                db.log(f"{sym}: compra vetada pelo agente de notícias ({veto})")
                self._st(market, sym, "bloqueado", veto, champ["name"])
                return
            stop = live_px - db.get_float("atr_stop_mult") * a
            take = 0.0 if champ["name"] in st.RUNNERS else live_px + db.get_float("take_profit_r") * (live_px - stop)
            ok, mult, why = brain.decide_entry(sym, df, champ, live_px, stop, take, self.timeframe,
                                               2 * (self.fee_for(market) + self.cfg.slippage))
            if not ok:
                db.log(f"🧠 {sym}: compra vetada — {why}")
                self._st(market, sym, "bloqueado", why, champ["name"])
                return
            qty = b.normalize_qty(sym, risk.position_size(eq, cash, live_px, stop, sym, mult), live_px)
            if qty <= 0:
                db.log(f"{sym}: sinal de compra, mas o tamanho calculado é menor que o mínimo do ativo", "WARN")
                return
            self.create_signal(sym, "buy", qty, live_px, stop, take, champ["name"],
                               st.describe(champ["name"], champ["params"]) + (f" · 🧠 {why}" if why else ""),
                               market=market)
            self._st(market, sym, "comprando", "sinal de compra gerado agora", champ["name"])

    def _update_indices(self) -> None:
        """Índices de referência (ex.: Ibovespa) para o painel, no máximo a cada 5 min."""
        for market, sym, name in self.indices:
            cur = self.index_data.get(sym)
            if cur and time.time() - cur["t"] < 300:
                continue
            try:
                df = self.broker(market).fetch_ohlcv(sym, "1d", 120)
                self.index_data[sym] = {"t": time.time(), "name": name, "market": market, "df": df}
            except Exception as e:
                db.log(f"Índice {name} indisponível: {e}", "WARN")

    # -------------------------------------------------------------- sinais
    def create_signal(self, sym: str, side: str, qty: float, price: float, stop: float, take: float,
                      strategy: str, reason: str, market: str = "main", notional: float = 0.0,
                      force_manual: bool = False) -> dict | None:
        if side == "buy" and self.sim_paused(market):
            return None  # com dinheiro real ligado, a simulação não compra mais
        dup = db.one("SELECT id FROM signals WHERE symbol=? AND side=? AND status='pending'", (sym, side))
        if dup:
            return None
        ttl = int(db.get_float("signal_ttl_minutes"))
        exp = (datetime.now(timezone.utc) + timedelta(minutes=ttl)).isoformat(timespec="seconds")
        sid = db.execute(
            "INSERT INTO signals(ts,symbol,side,qty,price,stop,take,strategy,reason,status,expires_at,market,notional)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now(), sym, side, qty, price, stop, take, strategy, reason, "pending", exp, market, notional))
        sig = db.one("SELECT * FROM signals WHERE id=?", (sid,))
        if db.get_setting("execution_mode") == "auto" and not force_manual:
            self.execute_signal(sig)
        else:
            db.log(f"Sinal #{sid} aguardando sua aprovação: {side} {sym}")
            self.on_signal(sig)
        return db.one("SELECT * FROM signals WHERE id=?", (sid,))

    def _expire_signals(self) -> None:
        n = db.execute("UPDATE signals SET status='expired' WHERE status='pending' AND expires_at < ?", (db.now(),))
        return n

    def approve(self, sid: int) -> str:
        with self.lock:
            sig = db.one("SELECT * FROM signals WHERE id=?", (sid,))
            if not sig:
                return f"Sinal #{sid} não existe"
            if sig["status"] != "pending":
                return f"Sinal #{sid} já está '{sig['status']}'"
            if sig["expires_at"] < db.now():
                db.execute("UPDATE signals SET status='expired' WHERE id=?", (sid,))
                return f"Sinal #{sid} expirou"
            return self.execute_signal(sig)

    def reject(self, sid: int) -> str:
        with self.lock:
            db.execute("UPDATE signals SET status='rejected' WHERE id=? AND status='pending'", (sid,))
            db.log(f"Sinal #{sid} recusado por você")
            return f"Sinal #{sid} recusado"

    def execute_signal(self, sig: dict) -> str:
        sym, side, market = sig["symbol"], sig["side"], sig.get("market") or "main"
        b = self.broker(market)
        try:
            px = self.price(sym, market)
            if side == "buy":
                eq, cash = self.market_equity(market)
                if market in self.strategy_markets:
                    chk = risk.can_open(sym, eq, b.is_market_open(sym), market)
                    if chk.ok and self.money_mode(market) == "live":
                        chk = risk.live_guard(market, eq, self.notify)
                    if not chk.ok:
                        raise RuntimeError(chk.reason)
                    dist = sig["price"] - sig["stop"]
                    stop = px - dist
                    take = px + db.get_float("take_profit_r") * dist if sig["take"] else 0
                    qty = b.normalize_qty(sym, risk.position_size(eq, b.get_cash(), px, stop, sym), px)
                else:
                    if not b.is_market_open(sym):
                        raise RuntimeError("mercado fechado")
                    stop, take = sig["stop"] or 0, sig["take"] or 0
                    qty = sig["qty"] or (sig["notional"] / px)
                    qty = b.normalize_qty(sym, min(qty, b.get_cash() * 0.99 / px), px)
                if qty <= 0:
                    raise RuntimeError("quantidade abaixo do mínimo / sem saldo")
                fill = b.market_order(sym, "buy", qty)
                cost = fill.price * fill.qty + fill.fee
                pos = db.one("SELECT * FROM positions WHERE symbol=?", (sym,))
                if pos:
                    nq = pos["qty"] + fill.qty
                    avg = (pos["avg_price"] * pos["qty"] + cost) / nq
                    db.execute("UPDATE positions SET qty=?, avg_price=? WHERE symbol=?", (nq, avg, sym))
                else:
                    db.execute("INSERT INTO positions(symbol,qty,avg_price,stop,take,opened_ts,strategy,highest,market,mode)"
                               " VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (sym, fill.qty, cost / fill.qty, stop, take, db.now(), sig["strategy"], fill.price, market,
                                self.money_mode(market)))
                pnl = 0.0
            else:
                pos = db.one("SELECT * FROM positions WHERE symbol=?", (sym,))
                if not pos:
                    raise RuntimeError("não há posição para vender")
                qty = min(sig["qty"] or pos["qty"], pos["qty"])
                fill = b.market_order(sym, "sell", qty)
                pnl = (fill.price - pos["avg_price"]) * fill.qty - fill.fee
                left = pos["qty"] - fill.qty
                if left <= pos["qty"] * 1e-6:
                    db.execute("DELETE FROM positions WHERE symbol=?", (sym,))
                else:
                    db.execute("UPDATE positions SET qty=? WHERE symbol=?", (left, sym))
            db.execute("INSERT INTO trades(ts,symbol,side,qty,price,fee,pnl,signal_id,strategy,reason,market,mode)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (db.now(), sym, side, fill.qty, fill.price, fill.fee, pnl, sig["id"], sig["strategy"],
                        sig["reason"], market, self.money_mode(market)))
            db.execute("UPDATE signals SET status='executed' WHERE id=?", (sig["id"],))
            for h in self.fill_hooks:
                try:
                    h(sig, fill)
                except Exception as e:
                    db.log(f"Registro pós-execução falhou: {e}", "WARN")
            msg = (f"✅ {'COMPRA' if side=='buy' else 'VENDA'} executada: {fill.qty:.6g} {sym} @ {fill.price:.6g}"
                   + (f" | resultado {pnl:+.2f}" if side == "sell" else ""))
            db.log(msg)
            self.notify(msg)
            return msg
        except Exception as e:
            db.execute("UPDATE signals SET status='failed', note=? WHERE id=?", (str(e), sig["id"]))
            msg = f"❌ Sinal #{sig['id']} não executado: {e}"
            db.log(msg, "ERROR")
            self.notify(msg)
            return msg

    def _exit_now(self, sym: str, reason: str) -> str:
        """Saída de proteção: executa na hora, em qualquer modo."""
        pos = db.one("SELECT * FROM positions WHERE symbol=?", (sym,))
        if not pos:
            return "sem posição"
        db.execute("UPDATE signals SET status='cancelled' WHERE symbol=? AND status='pending'", (sym,))
        sid = db.execute(
            "INSERT INTO signals(ts,symbol,side,qty,price,stop,take,strategy,reason,status,expires_at,market)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now(), sym, "sell", pos["qty"], self.prices.get(sym, pos["avg_price"]), 0, 0, pos["strategy"],
             reason, "pending", db.now(), pos["market"]))
        return self.execute_signal(db.one("SELECT * FROM signals WHERE id=?", (sid,)))

    def close_position(self, sym: str) -> str:
        with self.lock:
            return self._exit_now(sym, "fechada manualmente por você")

    # ------------------------------------------------------------ evolução
    def _maybe_evolve(self) -> None:
        hours = db.get_float("evolve_every_hours")
        last = db.get_setting("last_evolve")
        if not last:  # 1ª vez: a evolução inicial já roda por ativo em process_symbol
            db.set_setting("last_evolve", db.now())
            return
        if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=hours):
            return
        self.evolve_all()

    def evolve_all(self) -> list[dict]:
        out = []
        db.set_setting("last_evolve", db.now())
        for market in self.strategy_markets:
            if self.sim_paused(market):
                continue
            for sym in self.symbols_for(market):
                try:
                    df = self.broker(market).fetch_ohlcv(sym, self.timeframe, self.cfg.history_bars)
                    r = evolution.run_for_symbol(sym, df, self.cfg, self.notify, self.timeframe,
                                                 self.fee_for(market))
                    out.append({"symbol": sym, "promoted": r.get("promote"), "best": r.get("best")})
                except Exception as e:
                    db.log(f"Evolução {sym} falhou: {e}", "ERROR")
        return out

    def promote(self, strategy_id: int) -> str:
        r = db.one("SELECT * FROM strategies WHERE id=?", (strategy_id,))
        if not r:
            return "estratégia não encontrada"
        db.execute("UPDATE strategies SET status='retired' WHERE symbol=? AND status='champion'", (r["symbol"],))
        db.execute("UPDATE strategies SET status='champion' WHERE id=?", (strategy_id,))
        db.log(f"Estratégia #{strategy_id} ({r['name']}) ativada para {r['symbol']} por você")
        return "ok"
