#!/usr/bin/env bash
# Remote-side traffic-stats deploy: unpack stats_code.tgz, install 5 py files, compile-check,
# restart imobiliare + imobiliare-web, verify /traffic works and web_open fires on a board load.
# Rollback: the .bak_<ts> files stay on disk, cp them back + restart.
set -eu
TS=$(date +%Y%m%d_%H%M%S)
TGZ=/tmp/stats_code.tgz
BASE=/opt/imobiliare/bot
PY=/opt/imobiliare/venv/bin/python

echo "== 1. unpack =="
cd /tmp && tar xzf $TGZ
test -f /tmp/bot/db.py && test -f /tmp/bot/jobs.py && test -f /tmp/bot/main.py && \
  test -f /tmp/bot/handlers/admin.py && test -f /tmp/bot/webapp/app.py
echo "  5 files present"

echo "== 2. install (with .bak_<ts> next to each) =="
# /tmp/bot/... maps 1:1 to $BASE/... (BASE already IS the bot/ dir)
for f in db.py jobs.py main.py handlers/admin.py webapp/app.py; do
  cp -f "$BASE/$f" "$BASE/$f.bak_${TS}"
  install -m 0644 "/tmp/bot/$f" "$BASE/$f"
  echo "  /tmp/bot/$f -> $BASE/$f"
done

echo "== 3. compile check =="
$PY -m py_compile "$BASE/db.py" "$BASE/jobs.py" "$BASE/main.py" \
                 "$BASE/handlers/admin.py" "$BASE/webapp/app.py"
echo "  py_compile OK"

echo "== 4. restart both services =="
systemctl restart imobiliare imobiliare-web
sleep 3
echo -n "  imobiliare=";     systemctl is-active imobiliare
echo -n "  imobiliare-web="; systemctl is-active imobiliare-web

echo "== 5. verify web_open fires (one api/listings call = one event row) =="
BEFORE=$($PY -c "import sqlite3;print(sqlite3.connect('/opt/imobiliare/data/imobiliare.db').execute(\"SELECT COUNT(*) FROM events WHERE name='web_open'\").fetchone()[0])")
curl -sf "http://127.0.0.1:8085/api/listings?page=1" > /dev/null
sleep 1
AFTER=$($PY -c "import sqlite3;print(sqlite3.connect('/opt/imobiliare/data/imobiliare.db').execute(\"SELECT COUNT(*) FROM events WHERE name='web_open'\").fetchone()[0])")
echo "  web_open rows: BEFORE=$BEFORE AFTER=$AFTER (expect AFTER >= BEFORE+1)"

echo "== 6. verify daily_stats query runs (dry, admin-independent) =="
$PY - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db
async def main():
    await db.init_db()
    rows = await db.daily_stats(7)
    print("  rows returned:", len(rows))
    print("  sample keys:", sorted(rows[0].keys()) if rows else "(empty)")
    await db.close_db()
asyncio.run(main())
PYEOF

echo "== 7. startup tracebacks =="
BC=$(journalctl -u imobiliare --since '-30 seconds' --no-pager 2>/dev/null | grep -ci traceback || true)
WC=$(journalctl -u imobiliare-web --since '-30 seconds' --no-pager 2>/dev/null | grep -ci traceback || true)
echo "  imobiliare tracebacks=$BC | imobiliare-web tracebacks=$WC"

echo
echo "== done. rollback: for f in db jobs main handlers/admin webapp/app; do cp -f \$BASE/\$f.py.bak_${TS} \$BASE/\$f.py; done && systemctl restart imobiliare imobiliare-web =="
