#!/usr/bin/env bash
# READ-ONLY inspection of the board before wiping the seeded demo data. Prints who owns what so we
# can delete only the demo/infringing rows and keep any real owner listing. Touches nothing.
set -u
/opt/imobiliare/venv/bin/python - <<'PYEOF'
import sqlite3, os
d = sqlite3.connect('/opt/imobiliare/data/imobiliare.db'); d.row_factory = sqlite3.Row
print("== listings by status ==")
for r in d.execute("SELECT status, COUNT(*) c FROM listings GROUP BY status"):
    print(f"  {r['status']}: {r['c']}")
print("== owners of listings (tg_id, username) ==")
q = ("SELECT u.tg_id, u.username, COUNT(*) c, "
     "SUM(l.status='active') active "
     "FROM listings l JOIN users u ON u.id=l.user_id GROUP BY u.id ORDER BY c DESC")
for r in d.execute(q):
    print(f"  tg={r['tg_id']} user={r['username']!r} total={r['c']} active={r['active']}")
print("== Acces Imobil-sourced (infringing) rows ==")
print("  ", d.execute("SELECT COUNT(*) c FROM listings WHERE description LIKE '%Acces Imobil%'").fetchone()["c"])
print("== any ACTIVE listing NOT owned by the demo account (tg=1182157446)? ==")
q2 = ("SELECT l.id, u.tg_id, u.username, l.district, l.price FROM listings l "
      "JOIN users u ON u.id=l.user_id WHERE l.status='active' AND u.tg_id<>1182157446 LIMIT 20")
rows = list(d.execute(q2))
if not rows:
    print("   none - all active listings belong to the demo account (safe to wipe all)")
for r in rows:
    print(f"   REAL? #{r['id']} tg={r['tg_id']} {r['username']!r} {r['district']} {r['price']}")
print("== stats endpoint source (active with live expiry) ==")
n = d.execute("SELECT COUNT(*) c FROM listings WHERE status='active' AND (expires_at IS NULL OR expires_at>datetime('now'))").fetchone()["c"]
print("   active-and-live:", n)
print("== photos on disk ==", len(os.listdir('/opt/imobiliare/data/photos')), "files in PHOTOS_DIR")
PYEOF
echo DONE
