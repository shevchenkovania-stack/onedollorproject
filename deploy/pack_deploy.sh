#!/usr/bin/env bash
# Deploy the $1 promotion pack: switch the board to paid (promotion only - posting/browsing stay
# free), run the money-path self-test on a COPY of the live DB (never touches real money), then
# restart the real services and verify config + schema migration + the board is still serving.
#
# Reversibility: PAID_ENABLED=0 in .env + `systemctl restart imobiliare imobiliare-web` returns the
# board to fully free. .env is backed up first. Every number comes from .env / config, none coded.
set -u
cd /opt/imobiliare/bot
PY=/opt/imobiliare/venv/bin/python

echo "== backup .env =="
cp -f .env .env.bak_pack
echo "backed up -> .env.bak_pack"

echo "== set paid-promotion env (idempotent) =="
setenv() {
  local k="$1" v="$2"
  if grep -qE "^${k}=" .env; then
    sed -i -E "s|^${k}=.*|${k}=${v}|" .env
  else
    printf '%s=%s\n' "$k" "$v" >> .env
  fi
  echo "  $k=$v"
}
setenv PAID_ENABLED     1      # money switch
setenv PRICE_LISTING_MDL 0     # posting/keeping a listing live stays FREE; only promotion is sold
setenv PACK_ENABLED     1
setenv PACK_PRICE_MDL   18     # real MDL received for the pack (= our "1 $")
setenv PACK_CREDIT_MDL  180    # promo leu granted to the wallet (10x the money)
setenv FOUNDING_SLOTS   10

echo "== compile runtime =="
$PY -m py_compile main.py keyboards.py config.py db.py render.py handlers/wallet.py \
  && echo COMPILE_OK || { echo COMPILE_FAIL; exit 1; }

echo "== self-test the pack money path on a COPY of the DB =="
cp -f /opt/imobiliare/data/imobiliare.db /tmp/pack_test.db
DB_PATH=/tmp/pack_test.db $PY - <<'PY'
import asyncio, os
import config as c, db

async def main():
    await db.init_db()                       # runs the additive migrations on the copy
    u = await db.get_or_create_user(999999999, "pack_selftest", "selftest")
    bal0 = await db.get_balance(u["id"])
    t = await db.create_topup(u["id"], 999999999, c.PACK_PRICE_MDL,
                             credit_mdl=c.PACK_CREDIT_MDL, kind="pack")
    row = await db.credit_topup(t)           # grants the promo credit (idempotent)
    bal1 = await db.get_balance(u["id"])
    again = await db.credit_topup(t)         # a second tick must mint nothing
    slot = await db.claim_founding_slot(u["id"], c.FOUNDING_SLOTS)
    slot2 = await db.claim_founding_slot(u["id"], c.FOUNDING_SLOTS)  # keeps same slot
    print("price/credit", c.PACK_PRICE_MDL, c.PACK_CREDIT_MDL,
          "mult", c.PACK_CREDIT_MDL // c.PACK_PRICE_MDL)
    print("row", row["kind"], row["amount_mdl"], "credit_mdl", row["credit_mdl"])
    print("balance", bal0, "->", bal1, "| idempotent re-credit:", again is None)
    print("founding slot", slot, "reclaim keeps:", slot2)
    assert bal1 == bal0 + c.PACK_CREDIT_MDL, bal1
    assert again is None
    assert slot == 1 and slot2 == 1
    await db.close_db()
    print("PACK_SELFCHECK_OK")

asyncio.run(main())
PY
rc=$?
rm -f /tmp/pack_test.db
[ $rc -ne 0 ] && { echo "SELFCHECK FAILED rc=$rc - NOT restarting real services"; exit 1; }

echo "== restart services =="
systemctl restart imobiliare imobiliare-web
sleep 4
printf 'bot='; systemctl is-active imobiliare
printf 'web='; systemctl is-active imobiliare-web

echo "== real config in effect =="
$PY -c "import config as c; print('PAID_ENABLED', c.PAID_ENABLED, '| PRICE_LISTING_MDL', c.PRICE_LISTING_MDL, '| PACK', c.PACK_PRICE_MDL, c.PACK_CREDIT_MDL, repr(c.PACK_DISPLAY), '| rail_ready', c.pack_rail_ready(), '| FOUNDING_SLOTS', c.FOUNDING_SLOTS)"

echo "== schema migrated on the live DB =="
$PY -c "import sqlite3; con=sqlite3.connect('/opt/imobiliare/data/imobiliare.db'); print('users cols', sorted(r[1] for r in con.execute('PRAGMA table_info(users)'))); print('topups cols', sorted(r[1] for r in con.execute('PRAGMA table_info(topups)')))"

echo "== board still serving =="
curl -s http://127.0.0.1:8085/api/stats
echo
echo "== last bot log lines (watch for tracebacks) =="
journalctl -u imobiliare -n 12 --no-pager | tail -12

echo DONE
