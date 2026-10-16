"""Read-only: dump every listing so we can tell demo rows from real ones before cleaning.

Prints id, status, owner tg_id, deal, rooms, district, price, photos count and a description
snippet. Run: /opt/imobiliare/venv/bin/python /tmp/inspect_board.py
"""
import json
import sqlite3

d = sqlite3.connect("/opt/imobiliare/data/imobiliare.db")
d.row_factory = sqlite3.Row
rows = d.execute(
    """SELECT l.id, l.status, l.deal, l.ptype, l.rooms, l.district, l.price, l.currency,
              l.photos, substr(coalesce(l.description,''),1,40) AS dsc, u.tg_id
       FROM listings l JOIN users u ON u.id = l.user_id ORDER BY l.id""").fetchall()
print(f"total listings = {len(rows)}\n")
for r in rows:
    try:
        npic = len(json.loads(r["photos"] or "[]"))
    except Exception:
        npic = -1
    print(f"#{r['id']:<3} {r['status']:<9} tg={r['tg_id']:<11} {r['deal']}/{r['ptype']} "
          f"rooms={r['rooms']} {r['dsc']!r} {r['price']} {r['currency']} "
          f"{r['district']!r} photos={npic}")
