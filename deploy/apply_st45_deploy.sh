#!/usr/bin/env bash
# Stage-4 + Stage-5 deploy: listing life cycle + 3-step wizard + SQLite FSM store.
#
#   scp st45_code.tgz apply_st45_deploy.sh root@VPS:/tmp/
#   ssh root@VPS "bash /tmp/apply_st45_deploy.sh"
#
# Order: DB backup(+integrity) & rollback copy -> apply -> py_compile -> restart -> verify.
# Auto-restores the previous code and re-restarts if compile or the service check fails.
# Only the `imobiliare` bot service changes here (no webapp files), so we do not touch imobiliare-web.
set -e

BOT=/opt/imobiliare/bot
DATA=/opt/imobiliare/data
VENV_PY=/opt/imobiliare/venv/bin/python
[ -x "$VENV_PY" ] || VENV_PY=python3
DB=$DATA/imobiliare.db
FILES="config.py db.py jobs.py fsm_sqlite.py main.py keyboards.py handlers/admin.py handlers/profile.py handlers/add.py"

TS=$(date +%Y%m%d-%H%M%S)
ROLL=/root/st45_rollback_$TS
mkdir -p "$ROLL"
cd "$BOT"

echo "=== 0. DB backup + money-in-flight + rollback copy ==="
"$VENV_PY" - "$DB" "/root/imobiliare-pre-st45-$TS.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
print("backup ->", sys.argv[2], "| integrity:", src.execute("pragma integrity_check").fetchone()[0])
print("money in flight (manual_pending boosts):",
      src.execute("select count(*) from boosts where status='manual_pending'").fetchone()[0])
PY
for f in $FILES; do
  if [ -f "$f" ]; then mkdir -p "$ROLL/$(dirname "$f")"; cp -a "$f" "$ROLL/$f"; fi
done
echo "rollback saved: $ROLL"

restore_and_abort() {
  echo "!!! problem detected -> restoring rollback"
  for f in $FILES; do
    if [ -f "$ROLL/$f" ]; then cp -a "$ROLL/$f" "$BOT/$f"; else rm -f "$BOT/$f"; fi
  done
  systemctl restart imobiliare || true
  echo "restored old code and restarted. DEPLOY ABORTED."
  exit 1
}

echo "=== 1. apply new code ==="
tar xzf /tmp/st45_code.tgz -C "$BOT"

echo "=== 2. py_compile ==="
if ! "$VENV_PY" -m py_compile $FILES; then
  restore_and_abort
fi
echo "compile ok"

echo "=== 3. restart bot service ==="
systemctl restart imobiliare
sleep 5
echo "imobiliare : $(systemctl is-active imobiliare)"
[ "$(systemctl is-active imobiliare)" = active ] || restore_and_abort

echo "=== 4. verify migration + fsm table (created on bot start) ==="
"$VENV_PY" - <<'PY'
import sqlite3
c = sqlite3.connect("/opt/imobiliare/data/imobiliare.db"); c.row_factory = sqlite3.Row
cols = {r["name"] for r in c.execute("pragma table_info(listings)")}
print("expires_at column present:", "expires_at" in cols)
tabs = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
print("fsm_state table present  :", "fsm_state" in tabs)
print("active:", c.execute("select count(*) from listings where status='active'").fetchone()[0],
      "| pending:", c.execute("select count(*) from listings where status='pending'").fetchone()[0])
PY

echo "=== 5. journal tail (must be free of new tracebacks) ==="
journalctl -u imobiliare -n 20 --no-pager -o cat
echo "APPLY_DONE  (rollback dir: $ROLL)"
