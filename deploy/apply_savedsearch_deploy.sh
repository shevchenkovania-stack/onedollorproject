#!/usr/bin/env bash
# Live-search + saved-search rollout: text query/sort/favorites in the app, and a
# "Follow this search" subscription that pushes new matching ads to the bot.
#
#   scp ss_code.tgz apply_savedsearch_deploy.sh root@VPS:/tmp/
#   ssh root@VPS "bash /tmp/apply_savedsearch_deploy.sh"
#
# Order: DB backup -> rollback copy -> apply -> py_compile -> restart BOTH -> verify.
# Auto-restores the previous code and re-restarts if compile or a service check fails.
set -e

BOT=/opt/imobiliare/bot
DATA=/opt/imobiliare/data
VENV_PY=/opt/imobiliare/venv/bin/python
[ -x "$VENV_PY" ] || VENV_PY=python3
DB=$DATA/imobiliare.db

FILES="db.py scheduler.py handlers/admin.py webapp/app.py webapp/static/index.html"
PYFILES="db.py scheduler.py handlers/admin.py webapp/app.py"

TS=$(date +%Y%m%d-%H%M%S)
ROLL=/root/ss_rollback_$TS
mkdir -p "$ROLL"
cd "$BOT"

echo "=== 0. DB backup + rollback copy ==="
"$VENV_PY" - "$DB" "/root/imobiliare-pre-ss-$TS.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
print("integrity:", src.execute("pragma integrity_check").fetchone()[0])
PY
for f in $FILES; do
  if [ -f "$f" ]; then mkdir -p "$ROLL/$(dirname "$f")"; cp -a "$f" "$ROLL/$f"; fi
done

restore_and_abort() {
  echo "!!! problem detected -> restoring rollback"
  for f in $FILES; do
    if [ -f "$ROLL/$f" ]; then cp -a "$ROLL/$f" "$BOT/$f"; else rm -f "$BOT/$f"; fi
  done
  systemctl restart imobiliare imobiliare-web || true
  echo "restored old code and restarted. DEPLOY ABORTED."
  exit 1
}

echo "=== 1. apply new code ==="
tar xzf /tmp/ss_code.tgz -C "$BOT"

echo "=== 2. py_compile ==="
if ! "$VENV_PY" -m py_compile $PYFILES; then
  restore_and_abort
fi
echo "compile ok"

echo "=== 3. restart both services ==="
systemctl restart imobiliare imobiliare-web
sleep 6
echo "imobiliare     : $(systemctl is-active imobiliare)"
echo "imobiliare-web : $(systemctl is-active imobiliare-web)"
[ "$(systemctl is-active imobiliare)" = active ] || restore_and_abort
[ "$(systemctl is-active imobiliare-web)" = active ] || restore_and_abort

echo "=== 4. verify the app UI + endpoints + search_alerts table ==="
curl -s http://127.0.0.1:8085/ | grep -q 'id="followBtn"' && echo "index.html: Follow button present" || { echo "index.html: FOLLOW BUTTON MISSING"; restore_and_abort; }
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8085/api/search/alerts)
echo "GET /api/search/alerts -> HTTP $code (expect 401 without auth)"
[ "$code" = "401" ] || restore_and_abort
"$VENV_PY" - <<'PY'
import sqlite3
c = sqlite3.connect("/opt/imobiliare/data/imobiliare.db")
tabs = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
print("search_alerts table present:", "search_alerts" in tabs)
assert "search_alerts" in tabs, "migration did not create search_alerts"
PY

echo "=== 5. journal tails ==="
journalctl -u imobiliare -n 12 --no-pager -o cat
echo "---"
journalctl -u imobiliare-web -n 12 --no-pager -o cat
echo "APPLY_DONE  (rollback dir: $ROLL)"
