# Robô de Investimentos

Sistema de trading 24h com aprovação manual ou execução automática, evolução de estratégias, gestão de risco e painel web.

**Comece pelo [GUIA.md](GUIA.md).**

## Estrutura

```
main.py                  inicia o robô + painel (http://localhost:8000)
app/config.py            .env e configurações padrão
app/db.py                banco SQLite (data/robo.db)
app/brokers/             synthetic, paper, crypto (ccxt), mt5 (B3), us (Yahoo/Alpaca)
app/strategies.py        6 estratégias (médias, tendência EMA, IFR, Donchian, Bollinger, MACD)
app/backtest.py          backtest com stop ATR, alvo, stop móvel, taxas e derrapagem
app/evolution.py         algoritmo genético + validação fora da amostra
app/risk.py              tamanho de posição, limites, perda diária, trava total
app/engine.py            ciclo 24h, sinais, aprovação, execução, stops
app/notifier.py          Telegram (aprovar pelo celular)
app/research/            trader-skill integrado (11 ferramentas EUA)
app/api.py + app/web/    painel
.claude/skills/          as 11 skills do trader-skill para o Claude Code
extras/trader-skill/     repositório original (MIT) com testes
tests/                   pytest
```

## Comandos

```bash
cp .env.example .env            # configure
python main.py                  # ou ./iniciar.sh / iniciar-windows.bat
docker compose up -d --build    # servidor 24h
python -m pytest -q             # testes (inclui os do trader-skill)
```

Créditos: módulo de pesquisa baseado em [trader-skill](https://github.com/Jakeschincariol/trader-skill) (MIT, Jake Schincariol).
