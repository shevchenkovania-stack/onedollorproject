#!/bin/bash
# Fill the empty board with REAL rentix.md one-bedroom rentals (owner-authorized cold-start supply).
# Pipeline: 1) parse rentix + download/convert photos  2) seed + publish to channel & Mini App
#           3) print the final board state. Reversible: the seeder records created ids to
#           /tmp/seed_created.txt; nothing here deletes or archives anything (board starts empty).
#
# Usage (from Windows):
#   powershell -File deploy\run.ps1 deploy\test_data\seed_rentix.sh "bash /opt/imobiliare/test_data/seed_rentix.sh" -RemoteDir /opt/imobiliare/test_data
set -e
PY=/opt/imobiliare/venv/bin/python
cd /opt/imobiliare/test_data

echo "== 1. parse rentix one-bedrooms + download photos =="
$PY parse_rentix.py 2>&1 | tail -40

echo
echo "== 2. seed + publish the scraped listings =="
$PY seed_test_data.py rentix_listings.json 2>&1 | tail -40

echo
echo "== 3. final board state =="
$PY <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db
async def main():
    await db.init_db()
    print("stats:", await db.stats())
    rows = await db._conn.execute_fetchall(
        "SELECT id, rooms, district, price, currency FROM listings WHERE status='active' ORDER BY id")
    print("ACTIVE:", len(rows))
    for r in rows:
        print("  ", tuple(r))
    await db.close_db()
asyncio.run(main())
PY
