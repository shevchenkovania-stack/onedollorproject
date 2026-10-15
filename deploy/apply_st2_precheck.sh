#!/usr/bin/env bash
# Stage-2 pre-deploy: make an integrity-checked backup, then snapshot the CURRENT prod code
# of exactly the files we are about to change, so we can diff for drift before overwriting.
#
#   powershell -File deploy\run.ps1 deploy\apply_st2_precheck.sh "bash /tmp/apply_st2_precheck.sh"
#
# Safe to re-run. Prints BACKUP_DONE / SNAPSHOT_DONE / md5 list at the end.
set -e

DB=/opt/imobiliare/data/imobiliare.db
ENV=/opt/imobiliare/bot/.env
DIR=/opt/imobiliare/data/backups
VENV_PY=/opt/imobiliare/venv/bin/python
[ -x "$VENV_PY" ] || VENV_PY=python3

mkdir -p "$DIR"

echo "=== 1. backup (WAL-safe snapshot + integrity check + .env) ==="
"$VENV_PY" - <<'PY'
import os, sqlite3, time
DB="/opt/imobiliare/data/imobiliare.db"
ENV="/opt/imobiliare/bot/.env"
OUT="/opt/imobiliare/data/backups"
dst=os.path.join(OUT,"imobiliare-%s.db"%time.strftime("%Y%m%d-%H%M%S"))
src=sqlite3.connect(DB); dc=sqlite3.connect(dst)
with dc: src.backup(dc)
src.close(); dc.close(); os.chmod(dst,0o600)
c=sqlite3.connect(dst)
ok=c.execute("pragma integrity_check").fetchone()[0]
nl=c.execute("select count(*) from listings").fetchone()[0]
np_=c.execute("select count(*) from payments").fetchone()[0]
nc=c.execute("select count(*) from card_orders").fetchone()[0]
mp=c.execute("select count(*) from boosts where status='manual_pending'").fetchone()[0]
c.close()
assert ok=="ok", "integrity_check=%s"%ok
print("backup:",dst,os.path.getsize(dst),"bytes | integrity ok |",
      "listings",nl,"payments",np_,"card_orders",nc,"manual_pending",mp)
if os.path.exists(ENV):
    e=dst+".env"; os.system("cp %s %s && chmod 600 %s"%(ENV,e,e)); print("env snapshot:",e)
PY
echo "BACKUP_DONE"

echo "=== 2. money-in-flight check (TZ: do not switch a rail while claims are open) ==="
"$VENV_PY" - <<'PY'
import sqlite3
c=sqlite3.connect("/opt/imobiliare/data/imobiliare.db")
mp=c.execute("select count(*) from boosts where status='manual_pending'").fetchone()[0]
ca=c.execute("select count(*) from card_orders where applied=0").fetchone()[0] if c.execute("select name from sqlite_master where type='table' and name='card_orders'").fetchone() else 0
print("manual_pending boosts:",mp,"| unapplied card_orders:",ca)
PY

echo "=== 3. snapshot CURRENT prod code for drift diff ==="
cd /opt/imobiliare/bot
tar czf /tmp/prod_code.tgz \
  handlers/admin.py handlers/fallback.py keyboards.py main.py publish.py \
  webapp/app.py webapp/static/index.html
md5sum handlers/admin.py handlers/fallback.py keyboards.py main.py publish.py \
  webapp/app.py webapp/static/index.html
echo "SNAPSHOT_DONE  (pull /tmp/prod_code.tgz to diff before overwriting)"
