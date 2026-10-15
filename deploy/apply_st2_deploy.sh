#!/usr/bin/env bash
# Stage-2 deploy: apply the 8 behavior-neutral bug-fix files with a 1-minute rollback.
#
#   powershell -File deploy\run.ps1 deploy\apply_st2_deploy.sh "bash /tmp/apply_st2_deploy.sh"
#   (expects /tmp/st2_code.tgz already scp'd, holding the LF-normalized new files)
#
# Order per TZ: backup(rollback copy) -> apply -> py_compile -> restart -> 5-min checklist.
# If compile fails we auto-restore the rollback and DO NOT restart onto broken code.
set -e

BOT=/opt/imobiliare/bot
VENV_PY=/opt/imobiliare/venv/bin/python
[ -x "$VENV_PY" ] || VENV_PY=python3
FILES="handlers/admin.py handlers/fallback.py keyboards.py main.py publish.py webapp/app.py webapp/static/index.html"

TS=$(date +%Y%m%d-%H%M%S)
ROLL=/root/stage2_rollback_$TS
mkdir -p "$ROLL"
cd "$BOT"

echo "=== 0. rollback copy of files we overwrite ==="
for f in $FILES; do
  mkdir -p "$ROLL/$(dirname "$f")"
  cp -a "$f" "$ROLL/$f"
done
echo "rollback saved: $ROLL"

restore_and_abort() {
  echo "!!! problem detected -> restoring rollback to $ROLL"
  for f in $FILES; do [ -f "$ROLL/$f" ] && cp -a "$ROLL/$f" "$BOT/$f"; done
  systemctl restart imobiliare imobiliare-web || true
  echo "restored old code and restarted. DEPLOY ABORTED."
  exit 1
}

echo "=== 1. apply new code ==="
tar xzf /tmp/st2_code.tgz -C "$BOT"

echo "=== 2. py_compile (bot + webapp) ==="
if ! "$VENV_PY" -m py_compile handlers/admin.py handlers/fallback.py keyboards.py main.py publish.py webapp/app.py; then
  restore_and_abort
fi
echo "compile ok"

echo "=== 3. restart services ==="
systemctl restart imobiliare imobiliare-web
sleep 4
echo "imobiliare     : $(systemctl is-active imobiliare)"
echo "imobiliare-web : $(systemctl is-active imobiliare-web)"
[ "$(systemctl is-active imobiliare)" = active ]     || restore_and_abort
[ "$(systemctl is-active imobiliare-web)" = active ] || restore_and_abort

echo "=== 4. checklist: webapp still serves the same actives (incl real #34) ==="
echo "-- /api/stats:"; curl -s -m 8 http://127.0.0.1:8085/api/stats; echo
"$VENV_PY" - <<'PY'
import sqlite3
c=sqlite3.connect("/opt/imobiliare/data/imobiliare.db"); c.row_factory=sqlite3.Row
act=c.execute("select count(*) n from listings where status='active'").fetchone()["n"]
r34=c.execute("select id,status from listings where id=34").fetchone()
print("active listings:", act, "| #34:", (dict(r34) if r34 else "absent"))
PY

echo "=== 5. journal tail (must be free of new tracebacks) ==="
echo "-- imobiliare:"; journalctl -u imobiliare -n 15 --no-pager -o cat
echo "-- imobiliare-web:"; journalctl -u imobiliare-web -n 15 --no-pager -o cat
echo "APPLY_DONE  (rollback dir: $ROLL)"
