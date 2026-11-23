"""Notificações e aprovação pelo Telegram.

Com TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env, o robô manda cada sinal com
botões ✅ Aprovar / ❌ Recusar, e aceita comandos:
/status  /pausar  /retomar  /travar  /pendentes
Só responde ao seu chat (TELEGRAM_CHAT_ID).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

import httpx

from .config import settings


class Telegram:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or settings.telegram_token
        self.chat_id = str(chat_id or settings.telegram_chat_id)
        self.enabled = bool(self.token and self.chat_id)
        self._offset = 0
        self.handlers: dict[str, Callable[..., str]] = {}

    def _api(self, method: str, **data):
        if not self.enabled:
            return None
        try:
            r = httpx.post(f"https://api.telegram.org/bot{self.token}/{method}", json=data, timeout=35)
            return r.json()
        except Exception as e:  # rede instável não pode derrubar o robô
            print(f"[WARN] Telegram falhou: {e}", flush=True)
            return None

    def send(self, text: str, buttons: list[list[dict]] | None = None) -> None:
        data = {"chat_id": self.chat_id, "text": text[:4000]}
        if buttons:
            data["reply_markup"] = {"inline_keyboard": buttons}
        self._api("sendMessage", **data)

    def send_signal(self, sig: dict) -> None:
        txt = (f"📊 Sinal #{sig['id']}: {'COMPRAR' if sig['side']=='buy' else 'VENDER'} {sig['symbol']}\n"
               f"Qtd: {sig['qty']:.6g} @ ~{sig['price']:.6g}\n"
               + (f"Stop: {sig['stop']:.6g} | Alvo: {sig['take']:.6g}\n" if sig.get("stop") else "")
               + f"Motivo: {sig['reason']}")
        self.send(txt, [[{"text": "✅ Aprovar", "callback_data": f"approve:{sig['id']}"},
                         {"text": "❌ Recusar", "callback_data": f"reject:{sig['id']}"}]])

    def poll_forever(self, stop: threading.Event) -> None:
        if not self.enabled:
            return
        while not stop.is_set():
            res = self._api("getUpdates", offset=self._offset, timeout=25)
            if not res or not res.get("ok"):
                time.sleep(5)
                continue
            for up in res["result"]:
                self._offset = up["update_id"] + 1
                try:
                    self._handle(up)
                except Exception as e:
                    self.send(f"Erro: {e}")

    def _handle(self, up: dict) -> None:
        if "callback_query" in up:
            cq = up["callback_query"]
            if str(cq["message"]["chat"]["id"]) != self.chat_id:
                return
            action, sid = cq["data"].split(":")
            out = self.handlers[action](int(sid))
            self._api("answerCallbackQuery", callback_query_id=cq["id"], text=out[:180])
            self.send(out)
        elif "message" in up:
            m = up["message"]
            if str(m["chat"]["id"]) != self.chat_id:
                return
            cmd = (m.get("text") or "").split()[0].lstrip("/").split("@")[0]
            if cmd in self.handlers:
                self.send(self.handlers[cmd]())
            else:
                self.send("Comandos: /status /pendentes /pausar /retomar /travar")


telegram = Telegram()


def notify(msg: str) -> None:
    telegram.send(msg)
