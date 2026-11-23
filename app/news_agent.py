"""Agente de notícias de cripto.

A cada 15 minutos lê as manchetes (RSS públicos), identifica as moedas citadas e
dá uma nota de sentimento simples por palavras-chave. Uso no robô:
- notícia muito NEGATIVA recente (6h) sobre uma moeda -> bloqueia compras dela;
- notícia POSITIVA -> aparece no painel como "ideia" e reforça o sinal da estratégia.
Ele não compra sozinho só por causa de uma manchete: quem decide a entrada continua
sendo a estratégia validada (manchetes erram e chegam tarde para o preço).
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from . import db

FEEDS = ["https://cointelegraph.com/rss", "https://www.coindesk.com/arc/outboundfeeds/rss/",
         "https://decrypt.co/feed"]
COINS = {"BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether", "eth"], "SOL": ["solana", "sol"],
         "BNB": ["bnb", "binance coin"], "XRP": ["xrp", "ripple"], "DOGE": ["dogecoin", "doge"],
         "ADA": ["cardano", "ada"], "AVAX": ["avalanche", "avax"], "LINK": ["chainlink"],
         "TRX": ["tron", "trx"], "LTC": ["litecoin", "ltc"], "DOT": ["polkadot"]}
POS = ["surge", "soar", "rally", "record high", "all-time high", "approval", "approved", "etf inflow",
       "partnership", "adoption", "bullish", "breakout", "upgrade", "launch", "buyback", "gains"]
NEG = ["hack", "exploit", "stolen", "lawsuit", "sued", "ban", "crash", "plunge", "collapse", "bankrupt",
       "fraud", "delist", "outage", "sec charges", "bearish", "liquidation", "dump", "investigation"]
_last = {"t": 0.0}


def score(text: str) -> int:
    t = text.lower()
    return sum(w in t for w in POS) - 2 * sum(w in t for w in NEG)


def coins_in(text: str) -> list[str]:
    t = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
    return [c for c, keys in COINS.items() if any(f" {k} " in t for k in keys)]


def fetch() -> int:
    """Lê os feeds e grava manchetes novas. Retorna quantas entraram."""
    new = 0
    for url in FEEDS:
        try:
            r = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 robo-investimentos"},
                          follow_redirects=True)
            root = ET.fromstring(r.content)
        except Exception:
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            if not title or db.one("SELECT 1 FROM alerts WHERE source='cripto-news' AND title=?", (title,)):
                continue
            coins = coins_in(title + " " + (it.findtext("description") or "")[:300])
            if not coins:
                continue
            s = score(title)
            db.execute("INSERT INTO alerts(ts,source,title,body,url) VALUES(?,?,?,?,?)",
                       (db.now(), "cripto-news", title, f"{','.join(coins)}|{s}", link))
            new += 1
    return new


def sentiment(coin: str, hours: int = 6) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    total = 0
    for r in db.query("SELECT body FROM alerts WHERE source='cripto-news' AND ts>=?", (since,)):
        try:
            coins, s = r["body"].split("|")
        except ValueError:
            continue
        if coin in coins.split(","):
            total += int(s)
    return total


def blocks_buy(symbol: str) -> str | None:
    coin = symbol.split("/")[0].upper()
    if coin not in COINS:
        return None
    s = sentiment(coin)
    return f"notícias negativas recentes sobre {coin} (nota {s})" if s <= -4 else None


def ideas(limit: int = 15) -> list[dict]:
    out = []
    for r in db.query("SELECT * FROM alerts WHERE source='cripto-news' ORDER BY id DESC LIMIT 60"):
        try:
            coins, s = r["body"].split("|")
        except ValueError:
            continue
        out.append({"ts": r["ts"], "title": r["title"], "url": r["url"], "coins": coins.split(","), "score": int(s)})
    return out[:limit]


def hook(engine) -> None:
    if time.time() - _last["t"] < 900:
        return
    _last["t"] = time.time()
    try:
        n = fetch()
        if n:
            db.log(f"Agente de notícias: {n} manchete(s) nova(s) sobre cripto")
        for i in ideas(5):
            if i["score"] >= 2 and i["ts"] >= (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat():
                engine.notify(f"📰💡 Ideia: {', '.join(i['coins'])} — {i['title']}")
            if i["score"] <= -4 and i["ts"] >= (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat():
                engine.notify(f"📰⚠️ Alerta: {', '.join(i['coins'])} — {i['title']} (compras bloqueadas por 6h)")
    except Exception as e:
        db.log(f"Agente de notícias falhou: {e}", "WARN")
