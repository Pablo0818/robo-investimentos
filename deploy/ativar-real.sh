#!/usr/bin/env bash
# Liga o DINHEIRO REAL na Foxbit ou no Mercado Bitcoin.
# As chaves são digitadas aqui no servidor (não aparecem na tela e nunca vão para o GitHub).
set -euo pipefail
ENV=/opt/robo/.env
[ -f "$ENV" ] || { echo "Não achei $ENV"; exit 1; }

echo "Corretora: 1) Foxbit   2) Mercado Bitcoin"
read -r -p "Escolha 1 ou 2: " op
case "$op" in
  1) EX=foxbit ;;
  2) EX=mercado ;;
  *) echo "Opção inválida"; exit 1 ;;
esac
read -r -p "Cole a API KEY e aperte Enter: " KEY
read -r -s -p "Cole a API SECRET e aperte Enter (não aparece na tela): " SEC; echo
[ -n "$KEY" ] && [ -n "$SEC" ] || { echo "Chave vazia, nada foi alterado"; exit 1; }

sed -i '/^REAL_EXCHANGE=/d;/^REAL_API_KEY=/d;/^REAL_SECRET=/d' "$ENV"
{
  echo "REAL_EXCHANGE=$EX"
  echo "REAL_API_KEY=\"$KEY\""
  echo "REAL_SECRET=\"$SEC\""
} >> "$ENV"
chmod 600 "$ENV"
systemctl restart robo
sleep 8
echo
echo "Pronto. Últimas mensagens do robô:"
journalctl -u robo -n 40 --no-pager | grep -iE "real|erro|error" | tail -8 || true
echo
echo "Abra o painel > Registro e procure '💰 DINHEIRO REAL ligado'."
