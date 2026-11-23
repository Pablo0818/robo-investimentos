"""Evolução automática de estratégias (algoritmo genético + validação fora da amostra).

Como funciona, para cada ativo:
1. Divide o histórico: 70% para "treino" e os 30% mais recentes para "prova".
2. Cria uma população de estratégias com parâmetros aleatórios.
3. Por várias gerações, mantém as melhores (no treino) e cria variações delas.
4. As finalistas são testadas nos 30% que elas NUNCA viram (evita "decorar" o passado).
5. Só vira campeã se for melhor que a campeã atual nesse período de prova,
   com uma margem mínima, e respeitando o limite de queda máxima.
"""
from __future__ import annotations

import json
import random

import pandas as pd

from . import backtest, db
from .strategies import STRATEGIES


def _random_params(name: str, rng: random.Random) -> dict:
    sd = STRATEGIES[name]
    p = {}
    for k, (lo, hi, kind) in sd.space.items():
        if k == "trend_filter":
            p[k] = rng.choice([0, 0, 50, 100, 200])
        else:
            p[k] = rng.randint(lo, hi) if kind == "int" else rng.uniform(lo, hi)
    return sd.constrain(p)


def _mutate(name: str, params: dict, rng: random.Random, strength: float = 0.15) -> dict:
    sd = STRATEGIES[name]
    p = dict(params)
    for k, (lo, hi, kind) in sd.space.items():
        if rng.random() < 0.5:
            p[k] = p[k] + rng.gauss(0, (hi - lo) * strength)
    return sd.constrain(p)


def _crossover(name: str, a: dict, b: dict, rng: random.Random) -> dict:
    return STRATEGIES[name].constrain({k: (a[k] if rng.random() < 0.5 else b[k]) for k in a})


def score(r: backtest.Result, max_dd: float, min_trades: int) -> float:
    """Nota de uma estratégia: retorno ajustado ao risco, punindo poucas operações e quedas grandes."""
    s = r.sharpe * min(1.0, r.trades / max(1, min_trades))
    if r.max_drawdown > max_dd:
        s -= 5 * (r.max_drawdown - max_dd) * 10
    return s


def evolve(df: pd.DataFrame, *, timeframe: str = "1h", fee: float = 0.001, slippage: float = 0.0005,
           atr_mult: float = 2.0, take_r: float = 3.0, trailing: bool = True, max_dd: float = 0.15,
           generations: int = 8, population: int = 40, champion: dict | None = None,
           seed: int | None = None, names: list[str] | None = None) -> dict:
    rng = random.Random(seed)
    split = int(len(df) * 0.7)
    train = df.iloc[:split].reset_index(drop=True)
    kw = dict(fee=fee, slippage=slippage, atr_mult=atr_mult, take_r=take_r, trailing=trailing, timeframe=timeframe)
    min_tr_train = 8

    def fit(ind):
        try:
            return score(backtest.run(train, ind["name"], ind["params"], **kw), max_dd, min_tr_train)
        except Exception:
            return -99.0

    names = names or list(STRATEGIES)
    if champion and champion["name"] not in names:
        champion = None  # campeã de outro estilo não entra na disputa
    pop = [{"name": n, "params": _random_params(n, rng)} for n in rng.choices(names, k=population)]
    if champion:
        pop[0] = {"name": champion["name"], "params": champion["params"]}
    for _ in range(generations):
        for ind in pop:
            if "fit" not in ind:
                ind["fit"] = fit(ind)
        pop.sort(key=lambda x: x["fit"], reverse=True)
        elite = pop[: max(4, population // 4)]
        children = []
        while len(children) < population - len(elite):
            r = rng.random()
            if r < 0.15:  # imigrante aleatório mantém diversidade
                n = rng.choice(names)
                children.append({"name": n, "params": _random_params(n, rng)})
                continue
            a = rng.choice(elite)
            same = [e for e in elite if e["name"] == a["name"] and e is not a]
            if same and r < 0.4:
                p = _crossover(a["name"], a["params"], rng.choice(same)["params"], rng)
            else:
                p = _mutate(a["name"], a["params"], rng)
            children.append({"name": a["name"], "params": p})
        pop = elite + children
    for ind in pop:
        if "fit" not in ind:
            ind["fit"] = fit(ind)
    pop.sort(key=lambda x: x["fit"], reverse=True)

    # finalistas: as 6 melhores distintas
    finalists, seen = [], set()
    for ind in pop:
        key = (ind["name"], json.dumps(ind["params"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            finalists.append(ind)
        if len(finalists) >= 6:
            break

    def oos(ind):
        r = backtest.run(df, ind["name"], ind["params"], start=split, **kw)
        return r, score(r, max_dd, 3)

    best = None
    valid = []
    for ind in finalists:
        r, s = oos(ind)
        ok = r.trades >= 2 and r.max_drawdown <= max_dd and r.sharpe > 0
        cand = {"name": ind["name"], "params": ind["params"], "train_score": ind["fit"], "oos": r.dict(),
                "oos_score": s, "valid": ok}
        if ok:
            valid.append({"name": ind["name"], "params": ind["params"], "sharpe": r.sharpe})
        if ok and (best is None or s > best["oos_score"]):
            best = cand

    champ_eval = None
    if champion:
        r, s = oos(champion)
        champ_eval = {"oos": r.dict(), "oos_score": s}

    hold = float(df["close"].iloc[-1] / df["close"].iloc[split] - 1)
    promote = False
    if best:
        if champ_eval is None:
            promote = True
        else:
            promote = best["oos_score"] > champ_eval["oos_score"] * 1.1 + 0.05 and not (
                best["name"] == champion["name"] and best["params"] == champion["params"])
    valid.sort(key=lambda m: m["sharpe"], reverse=True)
    return {"best": best, "champion_eval": champ_eval, "promote": promote, "buy_hold_oos": hold, "split": split,
            "committee": valid[:3]}


def run_for_symbol(symbol: str, df: pd.DataFrame, cfg, notify=None, timeframe: str | None = None,
                   fee: float | None = None) -> dict:
    """Roda a evolução para um ativo e grava o resultado no banco."""
    champ_row = db.one("SELECT * FROM strategies WHERE symbol=? AND status='champion'", (symbol,))
    champion = {"name": champ_row["name"], "params": json.loads(champ_row["params"])} if champ_row else None
    res = evolve(df, timeframe=timeframe or cfg.timeframe, fee=cfg.fee_rate if fee is None else fee, slippage=cfg.slippage,
                 atr_mult=db.get_float("atr_stop_mult"), take_r=db.get_float("take_profit_r"),
                 trailing=db.get_bool("trailing_stop"),
                 # o backtest usa 100% do capital; na conta cada ativo usa só uma fração dele
                 max_dd=min(0.35, db.get_float("max_drawdown_pct") / max(0.05, db.get_float("max_position_pct"))),
                 champion=champion, names=__import__("app.strategies", fromlist=["allowed"]).allowed(
                     db.get_setting("strategy_style")))
    best = res["best"]
    if not best and db.get_setting("strategy_style") not in ("", "todas"):
        db.log(f"Evolução {symbol}: nenhuma estratégia de 'comprar na baixa' passou; testando todos os estilos")
        res = evolve(df, timeframe=timeframe or cfg.timeframe, fee=cfg.fee_rate if fee is None else fee, slippage=cfg.slippage,
                     atr_mult=db.get_float("atr_stop_mult"), take_r=db.get_float("take_profit_r"),
                     trailing=db.get_bool("trailing_stop"),
                     max_dd=min(0.35, db.get_float("max_drawdown_pct") / max(0.05, db.get_float("max_position_pct"))),
                     champion=champion)
        best = res["best"]
    if best and res.get("committee"):
        from . import brain
        brain.save_committee(symbol, res["committee"])  # as 3 melhores votam nas próximas compras
    if not best:
        db.log(f"Evolução {symbol}: nenhuma estratégia passou na prova fora da amostra; mantendo a atual"
               + ("" if champion else " (ativo fica sem operar até achar uma boa)"), "WARN")
        return res
    if not res["promote"]:
        db.log(f"Evolução {symbol}: campeã atual continua melhor ({champion['name']})")
        return res
    auto = db.get_bool("auto_evolve") or champion is None
    status = "champion" if auto else "proposed"
    if auto:
        db.execute("UPDATE strategies SET status='retired' WHERE symbol=? AND status='champion'", (symbol,))
    db.execute("UPDATE strategies SET status='rejected' WHERE symbol=? AND status='proposed'", (symbol,))
    o = best["oos"]
    db.execute(
        "INSERT INTO strategies(symbol,name,params,train_score,oos_sharpe,oos_return,oos_maxdd,oos_trades,status,created)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (symbol, best["name"], json.dumps(best["params"]), best["train_score"], o["sharpe"], o["total_return"],
         o["max_drawdown"], o["trades"], status, db.now()))
    msg = (f"Nova estratégia {'ativada' if auto else 'PROPOSTA'} para {symbol}: {best['name']} | "
           f"prova: retorno {o['total_return']:.1%}, sharpe {o['sharpe']:.2f}, queda máx {o['max_drawdown']:.1%}, "
           f"{o['trades']} operações (comprar e segurar: {res['buy_hold_oos']:.1%})")
    db.log(msg)
    if notify:
        notify(msg + ("" if auto else "\nAprove no painel em Estratégias."))
    return res
