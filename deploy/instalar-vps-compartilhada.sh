#!/usr/bin/env bash
# Instala o robô numa VPS que JÁ TEM outros sistemas rodando, sem mexer neles:
#  - não usa as portas 80/443 nem Docker;
#  - roda num ambiente Python isolado (/opt/robo) como serviço do sistema;
#  - escuta só em 127.0.0.1:8000 (invisível na internet);
#  - o acesso pelo celular/computador é pelo Tailscale (HTTPS privado, só seus aparelhos).
#
# Uso (como root, dentro da pasta do projeto):  bash deploy/instalar-vps-compartilhada.sh
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$(pwd)"
DEST=/opt/robo

if ss -ltn | awk '{print $4}' | grep -qE '(^|:)8000$' && ! systemctl is-active --quiet robo; then
  echo "A porta 8000 já está em uso por outro sistema. Nada foi alterado."
  echo "Mande este print para o Claude para escolher outra porta."
  exit 1
fi

read -rp "Seu e-mail de login: " EMAIL
read -rsp "Senha inicial (a que o Claude te passou): " PASS; echo
[ -n "$EMAIL" ] && [ -n "$PASS" ] || { echo "E-mail e senha são obrigatórios"; exit 1; }

echo "==> Instalando dependências (python venv)"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip poppler-utils >/dev/null

echo "==> Copiando o sistema para $DEST"
mkdir -p "$DEST"
cp -r "$SRC"/app "$SRC"/main.py "$SRC"/requirements.txt "$DEST"/
mkdir -p "$DEST/data"
if [ ! -f "$DEST/.env" ]; then
  cp "$SRC/deploy/env.hostinger" "$DEST/.env"
  sed -i "s|^ADMIN_EMAIL=.*|ADMIN_EMAIL=$EMAIL|; s|^ADMIN_INITIAL_PASSWORD=.*|ADMIN_INITIAL_PASSWORD=$PASS|; s|^SEC_USER_AGENT=.*|SEC_USER_AGENT=Pablo $EMAIL|; s|^TRUST_PROXY=.*|TRUST_PROXY=true|" "$DEST/.env"
  echo "PORT=8000" >> "$DEST/.env"
fi
chmod 600 "$DEST/.env"

echo "==> Criando ambiente Python isolado"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

echo "==> Criando o serviço 'robo' (liga sozinho e reinicia se cair)"
cat > /etc/systemd/system/robo.service <<EOF
[Unit]
Description=Robo de Investimentos
After=network-online.target

[Service]
WorkingDirectory=$DEST
EnvironmentFile=$DEST/.env
Environment=HOST=127.0.0.1
ExecStart=$DEST/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now robo

echo "==> Instalando Tailscale (acesso privado com HTTPS)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
echo
echo ">>> Abra o link que vai aparecer abaixo no celular e entre com a sua conta Tailscale <<<"
tailscale up
tailscale serve --bg 8000
echo
echo "PRONTO! Endereço do painel (só funciona nos aparelhos com o app Tailscale ligado):"
tailscale serve status | grep -o 'https://[^ ]*' | head -1
