#!/usr/bin/env bash
# Turn the money machine ON so the experiment can conclude. Reversible and safe:
#   * publishing stays FREE; only extend / boost / highlight / alerts draw from the wallet;
#   * FREE_FIRST_LISTING stays on, so a brand-new owner still gets a free first week - the
#     cold-start hook is not broken by going paid;
#   * if anything is misconfigured we do NOT leave a half-restarted stack: both services are
#     restarted together and their state is re-read at the end.
# Idempotent .env edit: set PAID_ENABLED=1 in place (or append once). Never prints secrets.
set -uo pipefail
cd /opt/imobiliare

echo "=== 1) switch PAID_ENABLED -> 1 in .env (idempotent) ==="
if grep -qE '^PAID_ENABLED=' bot/.env; then
  sed -i -E 's/^PAID_ENABLED=.*/PAID_ENABLED=1/' bot/.env
else
  printf '\nPAID_ENABLED=1\n' >> bot/.env
fi
grep -E '^PAID_ENABLED=' bot/.env | sed 's/^/  now: /'

echo "=== 2) sanity: money-in rail is actually configured (values not printed) ==="
for k in PAY_PHONE PAY_NAME; do
  v=$(grep -E "^$k=" bot/.env | head -1 | cut -d= -f2-)
  if [ -n "$v" ]; then echo "  $k is set"; else echo "  !! $k is EMPTY - MIA top-ups cannot be reconciled"; fi
done

echo "=== 3) compile gate: never restart onto a broken file ==="
/opt/imobiliare/venv/bin/python -m py_compile \
  bot/config.py bot/db.py bot/keyboards.py bot/render.py bot/scheduler.py bot/main.py \
  bot/handlers/*.py bot/webapp/app.py && echo "  py_compile OK" || { echo "  COMPILE FAILED - aborting, no restart"; exit 1; }

echo "=== 4) restart both services together ==="
systemctl restart imobiliare imobiliare-web
sleep 3

echo "=== 5) verify both came back up ==="
echo "  imobiliare     : $(systemctl is-active imobiliare)"
echo "  imobiliare-web : $(systemctl is-active imobiliare-web)"
echo "  web /api/stats -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/api/stats)"

echo "=== 6) confirm the running bot actually sees PAID_ENABLED=1 (last journal lines) ==="
journalctl -u imobiliare --no-pager -n 3 | sed 's/^/  /'
