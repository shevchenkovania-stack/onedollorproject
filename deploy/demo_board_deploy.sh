#!/usr/bin/env bash
# Rebuild the board as a clean DEMO showcase (owner directive 17.10):
#   - drop the third-party (Acces Imobil) listings AND their watermarked photos for good,
#   - install the is_demo plumbing (db/render/app/start),
#   - seed 10 legal, self-generated demo one-bedrooms, each flagged ДЕМО,
#   - put a "how it works" banner front-and-centre in the Mini App.
#
# Destructive to LISTINGS only. Every current row is demo/admin-owned (verified by the read-only
# inspection: no real owner had a live ad), and users / payments / wallet are NEVER touched. The DB
# is backed up first and the whole run prints, so a problem is obvious and reversible:
#   rollback = cp the .bak_demo file back over the DB + restart.
set -u
TS=$(date +%Y%m%d_%H%M%S)
STAGE=/tmp/demo_code
DB=/opt/imobiliare/data/imobiliare.db
PY=/opt/imobiliare/venv/bin/python

echo "== 1. backup DB =="
cp -f "$DB" "${DB}.bak_demo_${TS}"
echo "  backed up -> ${DB}.bak_demo_${TS}"

echo "== 2. install bot + test_data code from staging =="
install -m 0644 "$STAGE/bot/db.py"                 /opt/imobiliare/bot/db.py
install -m 0644 "$STAGE/bot/render.py"             /opt/imobiliare/bot/render.py
install -m 0644 "$STAGE/bot/handlers/start.py"     /opt/imobiliare/bot/handlers/start.py
install -m 0644 "$STAGE/bot/webapp/app.py"         /opt/imobiliare/bot/webapp/app.py
install -m 0644 "$STAGE/bot/webapp/static/index.html" /opt/imobiliare/bot/webapp/static/index.html
install -m 0644 "$STAGE/test_data/listings.json"   /opt/imobiliare/test_data/listings.json
install -m 0644 "$STAGE/test_data/seed_test_data.py" /opt/imobiliare/test_data/seed_test_data.py
echo "  copied 7 files"

echo "== 3. compile the bot code =="
cd /opt/imobiliare/bot
$PY -m py_compile db.py render.py handlers/start.py webapp/app.py && echo "COMPILE_OK" || { echo "COMPILE FAILED"; exit 1; }

echo "== 4. stop services (avoid WAL contention while we rewrite the board) =="
systemctl stop imobiliare imobiliare-web
sleep 1

echo "== 5. wipe ALL listings + old photos (users/payments/wallet kept) =="
$PY - <<'PYEOF'
import sqlite3, os, glob, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import config
db = sqlite3.connect('/opt/imobiliare/data/imobiliare.db')
before = db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
db.execute("DELETE FROM boosts")
db.execute("DELETE FROM events WHERE listing_id IS NOT NULL")
db.execute("DELETE FROM listings")
db.commit()
print("  deleted listing rows:", before)
n = 0
for fp in glob.glob(os.path.join(config.PHOTOS_DIR, "*.jpg")):
    os.remove(fp); n += 1
print("  removed Mini App photo files:", n)
db.close()
# the infringing scraped photos must not linger on the server either
img_dir = "/opt/imobiliare/test_data/images"
purged = 0
if os.path.isdir(img_dir):
    for pat in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        for fp in glob.glob(os.path.join(img_dir, pat)):
            os.remove(fp); purged += 1
print("  purged scraped test_data/images photos:", purged)
PYEOF

echo "== 6. seed the 10 demo listings (placeholder tiles -> real Telegram file_ids + on-disk jpgs) =="
cd /opt/imobiliare/test_data
$PY seed_test_data.py 2>&1 | tail -20

echo "== 7. start services to pick up the new code =="
systemctl start imobiliare imobiliare-web
sleep 3
echo -n "  imobiliare=";     systemctl is-active imobiliare
echo -n "  imobiliare-web="; systemctl is-active imobiliare-web

echo "== 8. verify board + demo flagging + the howto banner is served =="
$PY - <<'PYEOF'
import sqlite3
db = sqlite3.connect('/opt/imobiliare/data/imobiliare.db'); db.row_factory = sqlite3.Row
try:
    tot = db.execute("SELECT COUNT(*) c FROM listings WHERE status='active'").fetchone()["c"]
    demo = db.execute("SELECT COUNT(*) c FROM listings WHERE status='active' AND is_demo=1").fetchone()["c"]
    print(f"  active={tot}  of which demo={demo}")
except Exception as e:
    print("  is_demo column check failed:", e)
PYEOF
echo -n "  howto banner in served HTML: "; curl -s http://127.0.0.1:8085/ | grep -c "Как это работает"
echo -n "  api stats: "; curl -s http://127.0.0.1:8085/api/stats
echo
echo -n "  startup tracebacks: "; (journalctl -u imobiliare -n 40 --no-pager 2>/dev/null | grep -ci traceback) || echo 0
echo "== done. rollback: cp ${DB}.bak_demo_${TS} over the DB and restart =="
