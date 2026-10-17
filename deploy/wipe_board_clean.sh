#!/usr/bin/env bash
# Owner directive 18.10: fully clean the board so he can start posting his OWN real listings.
# Wipes ALL listings + boosts + listing-events + every on-disk photo + every test_data image.
# Users / payments / wallet / search_alerts / topups are LEFT ALONE (real money and preferences).
# DB is backed up first, so rollback is one cp + restart.
set -u
TS=$(date +%Y%m%d_%H%M%S)
DB=/opt/imobiliare/data/imobiliare.db
PY=/opt/imobiliare/venv/bin/python

echo "== 1. install updated index.html (drop the stale 'демо-объявления' note) =="
if [ -f /tmp/index.html ]; then
  install -m 0644 /tmp/index.html /opt/imobiliare/bot/webapp/static/index.html
  echo "  /tmp/index.html -> bot/webapp/static/index.html"
else
  echo "  /tmp/index.html not present, keeping current HTML"
fi

echo "== 2. backup DB =="
cp -f "$DB" "${DB}.bak_wipe_${TS}"
echo "  backed up -> ${DB}.bak_wipe_${TS}"

echo "== 3. stop services =="
systemctl stop imobiliare imobiliare-web
sleep 1

echo "== 4. wipe listings + boosts + listing-events + every photo file =="
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
print("  listings removed:", before)
n = 0
for fp in glob.glob(os.path.join(config.PHOTOS_DIR, "*.jpg")):
    os.remove(fp); n += 1
print("  Mini App photos removed:", n)
m = 0
img_dir = "/opt/imobiliare/test_data/images"
if os.path.isdir(img_dir):
    for pat in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        for fp in glob.glob(os.path.join(img_dir, pat)):
            os.remove(fp); m += 1
print("  test_data/images files removed:", m)
db.close()
PYEOF

echo "== 5. start services =="
systemctl start imobiliare imobiliare-web
sleep 2
echo -n "  imobiliare=";     systemctl is-active imobiliare
echo -n "  imobiliare-web="; systemctl is-active imobiliare-web

echo "== 6. verify board is empty + no demo note is served =="
echo -n "  api stats: "; curl -s http://127.0.0.1:8085/api/stats; echo
echo -n "  'демо-объявления' string in HTML (expect 0): "
curl -s http://127.0.0.1:8085/ | grep -c "демо-объявления"
echo -n "  PHOTOS_DIR count: "; ls /opt/imobiliare/data/photos 2>/dev/null | wc -l
echo -n "  test_data/images count: "; ls /opt/imobiliare/test_data/images 2>/dev/null | wc -l
echo -n "  startup tracebacks: "
(journalctl -u imobiliare -n 40 --no-pager 2>/dev/null | grep -ci traceback) || echo 0
echo
echo "== done. rollback: cp ${DB}.bak_wipe_${TS} over the DB + restart both services =="
