#!/usr/bin/env bash
# Hide the public channel from the product (owner decision 17.10: channel not needed yet).
# Reversible: .env is backed up to .env.bak_channelhide; uncomment CHANNEL_ID to re-enable.
# The bot search + Mini App read the DB and keep working; only channel posting / channel
# links are turned off. Every channel link in code is already guarded by `if CHANNEL_URL`.
set -u
cd /opt/imobiliare/bot

echo "== compile admin.py =="
/opt/imobiliare/venv/bin/python -m py_compile handlers/admin.py && echo COMPILE_OK || { echo COMPILE_FAIL; exit 1; }

echo "== backup .env =="
cp -f .env .env.bak_channelhide && echo BACKED_UP

echo "== hide channel: comment CHANNEL_ID =="
if grep -qE '^CHANNEL_ID=' .env; then
  sed -i -E 's/^CHANNEL_ID=.*/# CHANNEL_ID hidden 17.10 (bot+Mini App are the surfaces; uncomment to re-enable channel)/' .env
  echo COMMENTED
else
  echo ALREADY_INACTIVE
fi
grep -nE 'CHANNEL_ID' .env || echo "no CHANNEL_ID line"

echo "== restart services =="
systemctl restart imobiliare imobiliare-web
sleep 4
printf 'bot='; systemctl is-active imobiliare
printf 'web='; systemctl is-active imobiliare-web

echo "== effective config (channel must be None now) =="
/opt/imobiliare/venv/bin/python -c "import config as c; print('CHANNEL_ID', repr(c.CHANNEL_ID), 'CHANNEL_URL', repr(c.CHANNEL_URL))"

echo "== board still alive via Mini App =="
curl -s http://127.0.0.1:8085/api/stats
echo
echo DONE
