#!/usr/bin/env bash
# Atualiza o robô na VPS com a última versão do GitHub (mantém dados, senha e .env).
# A versão nova só entra no ar se passar em TODOS os testes; se falhar, fica a versão anterior.
# Uso: bash /root/robo-investimentos/deploy/atualizar.sh
set -uo pipefail
cd "$(dirname "$0")/.."
OLD=$(git rev-parse HEAD)
git pull -q || { echo "git pull falhou"; exit 1; }
NEW=$(git rev-parse HEAD)
/opt/robo/.venv/bin/pip install -q -r requirements.txt
if ! DB_PATH=/tmp/robo-teste.db /opt/robo/.venv/bin/python -m pytest -q -x -p no:cacheprovider tests \
        > /tmp/robo-testes.log 2>&1; then
  echo "TESTES FALHARAM: a versão nova NÃO foi aplicada (continua a anterior)."
  tail -25 /tmp/robo-testes.log
  echo "$NEW $(date -Iseconds)" > /opt/robo/UPDATE_FAILED
  cp /tmp/robo-testes.log /opt/robo/UPDATE_FAILED.log
  git reset -q --hard "$OLD"
  exit 1
fi
rm -f /opt/robo/UPDATE_FAILED /opt/robo/UPDATE_FAILED.log
cp -r app main.py requirements.txt /opt/robo/
echo "$NEW $(date -Iseconds) $(tail -1 /tmp/robo-testes.log)" > /opt/robo/VERSION
systemctl restart robo
sleep 3
systemctl is-active --quiet robo && echo "Atualizado, testado e rodando!" || { echo "Erro ao iniciar:"; journalctl -u robo -n 30 --no-pager; }
