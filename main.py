"""Ponto de entrada: `python main.py` sobe o robô (24h) e o painel em http://localhost:8000"""
from __future__ import annotations

import os
import signal
import sys
import threading

import uvicorn

from app import auth, db, research
from app.api import create_app
from app.brokers import build_b3_broker, build_broker, build_us_broker
from app.config import settings
from app.engine import Engine
from app.notifier import notify, telegram


def build():
    db.conn()
    created = auth.ensure_admin(settings.admin_email, settings.admin_initial_password)
    if created and not settings.admin_initial_password:
        print(f"\n*** Usuário {settings.admin_email} criado. Senha inicial: {created} (troque no 1º acesso) ***\n",
              flush=True)
    broker = build_broker(settings)
    us = None
    try:
        us = build_us_broker(settings)
    except Exception as e:
        db.log(f"Bolsa dos EUA desativada: {e}", "WARN")
    engine = Engine(settings, broker, notify=notify, on_signal=telegram.send_signal, us_broker=us)
    if us is not None:
        research.attach(engine)
    try:
        b3 = build_b3_broker(settings)
        if b3 is not None:
            engine.add_strategy_market("b3", b3, settings.b3_symbols)
            engine.indices.append(("b3", "^BVSP", "Ibovespa"))
            db.log(f"B3 em simulação (preços reais, R$): {', '.join(settings.b3_symbols)}")
    except Exception as e:
        db.log(f"B3 desativada: {e}", "WARN")

    def status_txt():
        eq, cash = engine.equity()
        st = db.all_settings()
        return (f"Patrimônio: {eq:,.2f} | Caixa: {cash:,.2f}\nModo: {st['execution_mode']} | "
                f"pausado: {st['paused']} | trava: {st['kill_switch']}\n"
                f"Posições: {', '.join(p['symbol'] for p in db.query('SELECT symbol FROM positions')) or 'nenhuma'}")

    def pend():
        rows = db.query("SELECT * FROM signals WHERE status='pending'")
        for r in rows:
            telegram.send_signal(r)
        return f"{len(rows)} sinal(is) pendente(s)"

    def setp(k, v, msg):
        def f():
            db.set_setting(k, v)
            db.log(f"Telegram: {msg}")
            return msg
        return f

    telegram.handlers.update({
        "approve": engine.approve, "reject": engine.reject, "status": status_txt, "pendentes": pend,
        "pausar": setp("paused", True, "Robô pausado ⏸️"), "retomar": setp("paused", False, "Robô retomado ▶️"),
        "travar": setp("kill_switch", True, "Trava de segurança LIGADA 🛑"), "start": status_txt,
    })
    return engine


def auto_update(stop: threading.Event) -> None:
    """Atualização automática: a cada 10 min confere o GitHub e, se houver versão nova,
    roda deploy/atualizar.sh fora deste processo (o robô reinicia sozinho em segundos)."""
    import shutil
    import subprocess

    repo = os.getenv("REPO_DIR", "/root/robo-investimentos")
    if os.getenv("AUTO_UPDATE", "true").lower() not in ("1", "true", "sim") or not os.path.isdir(f"{repo}/.git") \
            or not shutil.which("systemd-run"):
        return
    while not stop.wait(600):
        try:
            subprocess.run(["git", "-C", repo, "fetch", "-q"], timeout=60, check=True)
            local = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True).stdout
            remote = subprocess.run(["git", "-C", repo, "rev-parse", "@{u}"], capture_output=True, text=True).stdout
            if local.strip() and remote.strip() and local != remote:
                db.log("Nova versão encontrada no GitHub: atualizando sozinho...")
                notify("🔄 Atualizando o robô para a nova versão (volta em segundos).")
                subprocess.run(["systemd-run", "--unit", f"robo-update-{int(__import__('time').time())}",
                                "bash", f"{repo}/deploy/atualizar.sh"], timeout=30)
        except Exception as e:
            db.log(f"Atualização automática falhou: {e}", "WARN")


def main():
    engine = build()
    stop = threading.Event()
    threading.Thread(target=engine.run_forever, args=(stop,), daemon=True, name="engine").start()
    threading.Thread(target=telegram.poll_forever, args=(stop,), daemon=True, name="telegram").start()
    threading.Thread(target=auto_update, args=(stop,), daemon=True, name="auto-update").start()
    if telegram.enabled:
        telegram.send(f"🤖 Robô ligado ({settings.trading_mode.upper()}, {settings.broker}). /status")

    def bye(*_):
        stop.set()
        sys.exit(0)

    signal.signal(signal.SIGTERM, bye)
    print(f"Painel: http://localhost:{settings.port}  (login: {settings.admin_email})", flush=True)
    uvicorn.run(create_app(engine), host=os.getenv("HOST", "0.0.0.0"), port=settings.port, log_level="warning",
                proxy_headers=settings.trust_proxy, forwarded_allow_ips="*" if settings.trust_proxy else None)


if __name__ == "__main__":
    main()
