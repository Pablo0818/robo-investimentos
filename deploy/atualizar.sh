#!/usr/bin/env bash
# Atualiza o robô na VPS com a última versão do GitHub (mantém dados, senha e .env).
# Uso: bash /root/robo-investimentos/deploy/atualizar.sh
set -euo pipefail
cd "$(dirname "$0")/.."
git pull -q
cp -r app main.py requirements.txt /opt/robo/
/opt/robo/.venv/bin/pip install -q -r /opt/robo/requirements.txt
systemctl restart robo
sleep 3
systemctl is-active --quiet robo && echo "Atualizado e rodando!" || { echo "Erro ao iniciar:"; journalctl -u robo -n 30 --no-pager; }
