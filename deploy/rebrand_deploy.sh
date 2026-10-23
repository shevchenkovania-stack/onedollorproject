#!/usr/bin/env bash
# Deploy the "Однушка.md" rebrand: compile the runtime files, restart, re-identity the bot,
# verify the Mini App is serving the new title. Reversible by redeploying prior files.
set -u
cd /opt/imobiliare/bot

echo "== compile runtime =="
/opt/imobiliare/venv/bin/python -m py_compile main.py keyboards.py handlers/start.py \
  && echo COMPILE_OK || { echo COMPILE_FAIL; exit 1; }

echo "== restart services =="
systemctl restart imobiliare imobiliare-web
sleep 4
printf 'bot='; systemctl is-active imobiliare
printf 'web='; systemctl is-active imobiliare-web

echo "== re-identity bot + channel (seo_seeds.py owns every indexed name/description) =="
/opt/imobiliare/venv/bin/python /tmp/seo_seeds.py

echo "== Mini App title served =="
curl -s http://127.0.0.1:8085/ | grep -o '<title>[^<]*</title>' | head -1
echo "== board alive =="
curl -s http://127.0.0.1:8085/api/stats
echo
echo DONE
