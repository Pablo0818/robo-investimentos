"""Autoteste: confere, NO SERVIDOR e contra a corretora de verdade, se cada peça do robô funciona.

Cada item volta como {"item", "ok", "detail"}; ok=None é aviso (não impede operar).
O teste de ordem real (order_test) compra o mínimo da corretora e vende em seguida: custa só as taxas.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, opportunity

ROOT = Path(__file__).resolve().parents[1]


def _ago(ts: str | None) -> float | None:
    if not ts:
        return None
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def run(engine) -> dict:
    items: list[dict] = []

    def add(item: str, ok, detail: str) -> None:
        items.append({"item": item, "ok": ok, "detail": detail})

    # 1. versão no ar e última atualização
    ver = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else ""
    failed = (ROOT / "UPDATE_FAILED").read_text().strip() if (ROOT / "UPDATE_FAILED").exists() else ""
    if failed:
        add("Atualização automática", False, f"a versão {failed[:7]} foi BLOQUEADA porque falhou nos testes; "
                                             f"continua no ar a {ver[:7] or 'anterior'}")
    else:
        add("Atualização automática", True if ver else None,
            f"versão {ver[:7]} no ar, testada em {ver.split(' ')[1][:16] if ' ' in ver else '?'}"
            if ver else "versão sem registro (ainda não passou por uma atualização testada)")

    # 2. o robô está rodando
    age = _ago(engine.last_cycle)
    add("Robô rodando", age is not None and age < 180,
        f"último ciclo há {int(age)} s" if age is not None else "nenhum ciclo concluído ainda")
    if engine.last_error:
        add("Erro no ciclo", False, str(engine.last_error)[:200])

    # 3. travas e modo
    add("Modo automático", db.get_setting("execution_mode") == "auto",
        "compra e vende sozinho" if db.get_setting("execution_mode") == "auto"
        else "modo manual: cada compra espera sua aprovação")
    add("Robô não pausado", not db.get_bool("paused"), "rodando" if not db.get_bool("paused") else "PAUSADO no painel")
    add("Trava geral", not db.get_bool("kill_switch"), "livre" if not db.get_bool("kill_switch") else "TRAVADO")

    live = engine.live_markets()
    if not live:
        add("Dinheiro real", False, "nenhuma corretora real ligada (falta a chave da API no servidor)")
        return _done(items)
    m = live[0]
    b = engine.broker(m)
    add("Dinheiro real", True, f"ligado na {getattr(b.ex, 'id', '?')} · taxa {engine.fee_for(m):.2%} por ordem")
    add("Trava do dinheiro real", not db.get_bool(f"live_lock:{m}"),
        "livre" if not db.get_bool(f"live_lock:{m}") else "TRAVADA por queda máxima: destrave em Ajustes")

    # 4. conexão e saldo
    try:
        t0 = time.time()
        cash = b.get_cash()
        add("Conexão e saldo", cash > 0, f"saldo livre R$ {cash:.2f} ({(time.time() - t0) * 1000:.0f} ms)"
            if cash > 0 else "conectou, mas o saldo livre em reais é 0")
    except Exception as e:
        cash = 0.0
        add("Conexão e saldo", False, f"a corretora recusou: {str(e)[:160]} (chave errada ou sem permissão?)")

    # 5. candles de cada moeda (1h para estratégias, 15 min para oportunidade)
    syms = engine.symbols_for(m)
    bad, stale = [], []
    for s in syms:
        df = engine.candles.get((m, s))
        if df is None or len(df) < 100:
            bad.append(s)
            continue
        last = df["ts"].iloc[-1].to_pydatetime()
        if datetime.now(timezone.utc) - last > timedelta(hours=3):
            stale.append(s)
    add("Gráficos de 1h", not bad and not stale and bool(syms),
        f"{len(syms) - len(bad) - len(stale)}/{len(syms)} moedas com candles recentes"
        + (f" · sem dados: {', '.join(bad[:6])}" if bad else "") + (f" · atrasados: {', '.join(stale[:6])}" if stale else ""))
    have15 = [s for s in syms if s in engine.opp_frames]
    add("Gráficos de 15 min", len(have15) >= max(1, len(syms) // 2),
        f"{len(have15)}/{len(syms)} moedas" if db.get_bool("opportunity_enabled") else "agente de oportunidade desligado")

    # 6. ordem mínima cabe no saldo
    try:
        lim = (b.markets.get(syms[0], {}) or {}).get("limits", {}) if syms else {}
        min_cost = float((lim.get("cost") or {}).get("min") or 0)
        eq = cash + sum(p["qty"] * engine.prices.get(p["symbol"], p["avg_price"])
                        for p in db.query("SELECT * FROM positions WHERE market=?", (m,)))
        add("Ordem mínima", (min_cost <= 0.35 * eq) if min_cost else None,
            f"mínimo da corretora R$ {min_cost:.2f} · cabe {int(eq // min_cost) if min_cost else '?'} ordens no saldo"
            if min_cost else "a corretora não informa o mínimo; o robô tenta e mostra o erro se recusar")
    except Exception as e:
        add("Ordem mínima", None, f"não consegui ler: {str(e)[:120]}")

    # 7. cérebro: estratégias e agente de oportunidade
    champs = [s for s in syms if engine.champion(s)]
    add("Estratégias aprovadas", True if champs else None,
        f"{len(champs)}/{len(syms)} moedas com estratégia provada fora da amostra"
        + ("" if champs else " · normal no começo: o robô testa de novo a cada minuto"))
    if db.get_bool("opportunity_enabled"):
        r = opportunity.rules()
        cal = _ago(db.get_setting("opp_calibrated"))
        add("Agente de oportunidade", True if r else None,
            (f"regras provadas: queda ≥{r['drop']:.1%}, alvo +{r['take']:.1%}, stop -{r['stop']:.1%}, "
             f"{r['trades']} operações na prova, média {r['avg']:+.2%} já com taxas")
            if r else ("nenhuma regra deu lucro na última prova; fica sem operar por oportunidade até a próxima"
                       if cal is not None else "ainda não fez a primeira prova (precisa de 3+ moedas com dados)"))

    # 8. erros recentes
    rows = db.query("SELECT msg FROM logs WHERE level='ERROR' AND ts>=? ORDER BY id DESC LIMIT 200",
                    ((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),))
    other = {x for mk in engine.strategy_markets if mk != m for x in engine.symbols_for(mk)}
    # só erros do dinheiro real (a simulação parada e o próprio autoteste não contam)
    errs = [e for e in rows if not e["msg"].startswith("🩺")
            and not any(e["msg"].startswith(f"{x}:") for x in other)][:5]
    add("Erros nos últimos 30 min", not errs, "nenhum" if not errs else " | ".join(e["msg"][:90] for e in errs[:3]))

    # 9. o que cada moeda está fazendo
    sts = [v for k, v in engine.state.items() if k[0] == m]
    if sts:
        import collections
        c = collections.Counter(v["status"] for v in sts)
        add("Situação das moedas", True, ", ".join(f"{n} {k}" for k, n in c.most_common()))
    return _done(items)


def _done(items: list[dict]) -> dict:
    fails = [i for i in items if i["ok"] is False]
    return {"ts": db.now(), "ok": not fails, "fails": len(fails), "items": items}


def order_test(engine) -> dict:
    """Compra o mínimo da corretora na moeda mais líquida e vende em seguida. Custa só as taxas."""
    live = engine.live_markets()
    if not live:
        return {"ok": False, "detail": "dinheiro real não está ligado"}
    m = live[0]
    b = engine.broker(m)
    sym = next((s for s in ("BTC/BRL", "ETH/BRL", "USDT/BRL") if s in b.markets), engine.symbols_for(m)[0])
    steps = []
    try:
        px = engine.price(sym, m)
        lim = (b.markets.get(sym, {}) or {}).get("limits", {})
        min_cost = float((lim.get("cost") or {}).get("min") or 0) or 10.0
        qty = b.normalize_qty(sym, min_cost * 1.05 / px, px)
        if qty <= 0:
            return {"ok": False, "detail": f"não consegui calcular a quantidade mínima de {sym}"}
        cash0 = b.get_cash()
        buy = b.market_order(sym, "buy", qty)
        steps.append(f"✅ comprou {buy.qty:.8g} {sym} a {buy.price:.2f} (R$ {buy.qty * buy.price:.2f})")
        time.sleep(2)
        sell = b.market_order(sym, "sell", buy.qty)
        steps.append(f"✅ vendeu {sell.qty:.8g} a {sell.price:.2f}")
        time.sleep(1)
        cash1 = b.get_cash()
        steps.append(f"custo do teste (taxas e diferença de preço): R$ {cash0 - cash1:.2f}")
        db.log("🧪 Teste de ordem real concluído: " + " · ".join(steps))
        return {"ok": True, "detail": " · ".join(steps)}
    except Exception as e:
        steps.append(f"❌ {str(e)[:220]}")
        db.log("🧪 Teste de ordem real FALHOU: " + " · ".join(steps), "ERROR")
        return {"ok": False, "detail": " · ".join(steps)}
