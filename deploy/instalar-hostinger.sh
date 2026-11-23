#!/usr/bin/env bash
# Instala o robô numa VPS Hostinger (Ubuntu 24.04). Rode como root dentro da pasta do projeto:
#   bash deploy/instalar-hostinger.sh
# O script pergunta o endereço (domínio) e o seu e-mail de login.
set -euo pipefail
read -rp "Endereço do painel (ex: robo.seudominio.com.br): " DOMAIN
read -rp "Seu e-mail de login: " EMAIL
read -rsp "Senha inicial (a que o Claude te passou): " PASS; echo
[ -n "$DOMAIN" ] && [ -n "$EMAIL" ] || { echo "Domínio e e-mail são obrigatórios"; exit 1; }
# Não mexe no que já existe na VPS: se as portas 80/443 estiverem ocupadas, para aqui.
BUSY=$(ss -ltnp 2>/dev/null | awk '$4 ~ /:(80|443)$/' || true)
if [ -n "$BUSY" ] && ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q caddy; then
  echo "As portas 80/443 já estão em uso por outro programa nesta VPS:"
  echo "$BUSY"
  echo "Nada foi alterado. Mande este print para o Claude para ajustar a instalação."
  exit 1
fi
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
[ -f .env ] || cp deploy/env.hostinger .env
setvar(){ grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
setvar DOMAIN "$DOMAIN"
setvar ADMIN_EMAIL "$EMAIL"
setvar ADMIN_INITIAL_PASSWORD "$PASS"
setvar SEC_USER_AGENT "Pablo $EMAIL"
mkdir -p data
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
# só liga o firewall se ele ainda não estiver ativo (para não bloquear outros serviços seus)
ufw status 2>/dev/null | grep -q "Status: active" || echo "Firewall (ufw) não estava ativo; deixei como estava."
docker compose --env-file .env -f docker-compose.prod.yml up -d --build
echo
echo "Pronto! Acesse: https://$DOMAIN"
echo "(o certificado HTTPS pode levar 1-2 minutos na primeira vez)"
