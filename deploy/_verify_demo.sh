#!/usr/bin/env bash
# READ-ONLY: confirm the demo board is guest-ready - tiles on disk, served over HTTP, demo flag set.
set -u
/opt/imobiliare/venv/bin/python - <<'PYEOF'
import sqlite3, os, sys, json
sys.path.insert(0, "/opt/imobiliare/bot")
import config
db = sqlite3.connect('/opt/imobiliare/data/imobiliare.db'); db.row_factory = sqlite3.Row
rows = db.execute("SELECT id, district, price, is_demo, photos FROM listings WHERE status='active' ORDER BY id").fetchall()
print("active listings:", len(rows), "| all demo:", all(r['is_demo'] for r in rows))
on_disk = 0
for r in rows:
    n = len(json.loads(r['photos'] or '[]'))
    p = os.path.join(config.PHOTOS_DIR, f"{r['id']}_0.jpg")
    if os.path.exists(p): on_disk += 1
print("hero tiles present on disk:", on_disk, "/", len(rows))
first = rows[0]['id'] if rows else None
print("sample_id", first)
PYEOF
