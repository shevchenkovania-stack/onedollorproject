#!/usr/bin/env bash
# Deploy the monetization foundation + enforcement (wallet, paid renewal, boost-from-wallet,
# highlight, alerts subscription). Safe by construction:
#   * syntax gate: never restart onto a broken file;
#   * PAID_ENABLED stays 0 unless the operator flips it: every new charge is a no-op, so the live
#     board keeps working exactly as before until the board has real one-bedroom supply.
# Idempotent .env writes via set_env (update in place, or append once).
set -uo pipefail
cd /opt/imobiliare

tar xzf /tmp/monet.tgz -C bot || { echo "TAR EXTRACT FAILED"; exit 1; }

FILES="config.py db.py keyboards.py render.py scheduler.py main.py \
handlers/admin.py handlers/profile.py handlers/boost.py handlers/wallet.py \
webapp/app.py webapp/static/index.html"
for f in $FILES; do sed -i 's/\r$//' "bot/$f"; done

PY="bot/config.py bot/db.py bot/keyboards.py bot/render.py bot/scheduler.py bot/main.py \
bot/handlers/admin.py bot/handlers/profile.py bot/handlers/boost.py bot/handlers/wallet.py \
bot/webapp/app.py"
python3 -m py_compile $PY && echo "py_compile OK" || { echo "PY_COMPILE FAILED - NOT RESTARTING"; exit 1; }

ENV=/opt/imobiliare/bot/.env
set_env() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "$ENV" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$ENV"
  else
    echo "${k}=${v}" >> "$ENV"
  fi
}
# prices per the owner's spec: 1 leu / week live, 10 lei boost, 20 lei (~1$) "Срочно", 40 lei (~2$) / month alerts
set_env PRICE_LISTING_MDL 1
set_env PRICE_HIGHLIGHT_MDL 20
set_env PRICE_ALERTS_MDL 40
set_env LISTING_WEEK_DAYS 7
set_env HIGHLIGHT_DAYS 7
set_env ALERTS_MONTH_DAYS 30
set_env BOOST_MDL 10
set_env FREE_FIRST_LISTING 1
set_env FREE_FIRST_DAYS 7
# the master switch, left OFF: the board stays free until it has real supply
set_env PAID_ENABLED 0
echo "--- .env monetization keys ---"
grep -E '^(PAID_ENABLED|PRICE_LISTING_MDL|PRICE_HIGHLIGHT_MDL|PRICE_ALERTS_MDL|BOOST_MDL|LISTING_WEEK_DAYS|FREE_FIRST)' "$ENV"

systemctl restart imobiliare imobiliare-web 2>/dev/null
sleep 6
echo "bot  active: $(systemctl is-active imobiliare 2>/dev/null)"
echo "web  active: $(systemctl is-active imobiliare-web 2>/dev/null)"
tail -n 8 /opt/imobiliare/bot.log 2>/dev/null | cut -c1-140 || true
echo DONE
