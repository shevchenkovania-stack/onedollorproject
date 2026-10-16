#!/bin/bash
# Fill the empty board with 20 REAL one-bedroom rentals cross-listed from Acces Imobil.
# Owner-authorized rule change (was MISSION "no copying"): cold-start needs supply.
# Reversible: step 1 only SOFT-archives whatever is active now (ids saved to a file),
# nothing is deleted; step 3 seeds the new listings and posts them to channel + Mini App.
#
# Usage (from Windows):
#   powershell -File deploy\run.ps1 deploy\test_data\seed_real_acces.sh "bash /opt/imobiliare/test_data/seed_real_acces.sh" -RemoteDir /opt/imobiliare/test_data
set -e
PY=/opt/imobiliare/venv/bin/python
cd /opt/imobiliare/test_data

echo "== 1. soft-archive the current active listings (reversible) =="
$PY <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db
async def main():
    await db.init_db()
    rows = await db._conn.execute_fetchall("SELECT id FROM listings WHERE status='active'")
    ids = [r[0] for r in rows]
    for lid in ids:
        await db._conn.execute("UPDATE listings SET status='archived' WHERE id=?", (lid,))
    await db._conn.commit()
    open("/tmp/pre_active_ids.txt", "w").write(",".join(str(i) for i in ids))
    print("archived active ids:", ids if ids else "(none)")
    print("stats after archive:", await db.stats())
    await db.close_db()
asyncio.run(main())
PY

echo
echo "== 2. download the real photos from Acces Imobil =="
$PY fetch_acces_images.py 2>&1 | tail -30

echo
echo "== 3. seed the 20 listings (photos -> channel + Mini App) =="
$PY seed_test_data.py 2>&1 | tail -40

echo
echo "== 4. final board state =="
$PY <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db
async def main():
    await db.init_db()
    print("stats:", await db.stats())
    rows = await db._conn.execute_fetchall(
        "SELECT id, district, price, currency FROM listings WHERE status='active' ORDER BY id DESC LIMIT 30")
    print("ACTIVE:", len(rows))
    for r in rows:
        print("  ", tuple(r))
    await db.close_db()
asyncio.run(main())
PY
