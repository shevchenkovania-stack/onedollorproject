#!/usr/bin/env bash
# Mini App primary-surface deploy (Phases A-D): one-window SPA + my/lifecycle/create/photos,
# plus the bot-side render/publish + start/profile touch-ups.
#
#   scp miniapp_code.tgz apply_miniapp_deploy.sh root@VPS:/tmp/
#   ssh root@VPS "bash /tmp/apply_miniapp_deploy.sh"
#
# Order: DB backup + rollback copy -> apply -> py_compile -> restart BOTH -> verify.
# Auto-restores the previous code and re-restarts if compile or a service check fails.
set -e

BOT=/opt/imobiliare/bot
DATA=/opt/imobiliare/data
VENV_PY=/opt/imobiliare/venv/bin/python
[ -x "$VENV_PY" ] || VENV_PY=python3
DB=$DATA/imobiliare.db

# every deployed file (for backup/restore), and the python subset for py_compile
FILES="render.py publish.py handlers/start.py handlers/profile.py webapp/app.py webapp/static/index.html"
PYFILES="render.py publish.py handlers/start.py handlers/profile.py webapp/app.py"

TS=$(date +%Y%m%d-%H%M%S)
ROLL=/root/miniapp_rollback_$TS
mkdir -p "$ROLL"
cd "$BOT"

echo "=== 0. DB backup + rollback copy ==="
"$VENV_PY" - "$DB" "/root/imobiliare-pre-miniapp-$TS.db" <<'PY'
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
tar xzf /tmp/miniapp_code.tgz -C "$BOT"

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

echo "=== 4. verify the app is serving the new UI + endpoints ==="
# SPA must expose the tab bar; my/listings must answer (401 = endpoint live, gated by auth).
if curl -s http://127.0.0.1:8085/ | grep -q 'class="tabs"'; then echo "index.html: tab bar present"; else echo "index.html: TAB BAR MISSING"; restore_and_abort; fi
code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8085/api/my/listings)
echo "GET /api/my/listings -> HTTP $code (expect 401 without auth)"
[ "$code" = "401" ] || restore_and_abort
echo "active: $(curl -s http://127.0.0.1:8085/api/stats)"

echo "=== 5. journal tails (must be free of new tracebacks) ==="
journalctl -u imobiliare -n 15 --no-pager -o cat
echo "---"
journalctl -u imobiliare-web -n 15 --no-pager -o cat
echo "APPLY_DONE  (rollback dir: $ROLL)"
