#!/bin/bash
# Fill / refresh the board with REAL rentix.md one-bedroom rentals (owner-authorized cold-start supply).
# Idempotent: it first removes any previously-seeded rentix cards (channel messages + DB rows), then
# re-parses and publishes fresh ones, so re-running never piles up duplicates.
#
# Usage (from Windows):
#   powershell -File deploy\run.ps1 deploy\test_data\seed_rentix.sh "bash /opt/imobiliare/test_data/seed_rentix.sh" -RemoteDir /opt/imobiliare/test_data
set -e
PY=/opt/imobiliare/venv/bin/python
cd /opt/imobiliare/test_data

echo "== 0. purge existing rentix cards (channel messages + rows) =="
$PY <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, config
from aiogram import Bot

async def main():
    await db.init_db()
    rows = await db._conn.execute_fetchall(
        "SELECT id FROM listings WHERE description LIKE '%rentix.md%'")
    ids = [r[0] for r in rows]
    print("rentix listings to remove:", ids or "(none)")
    if ids:
        bot = Bot(token=config.BOT_TOKEN)
        try:
            for lid in ids:
                evs = await db._conn.execute_fetchall(
                    "SELECT meta FROM events WHERE listing_id=? AND name='channel_post'", (lid,))
                for ev in evs:
                    for mid in str(ev[0] or "").split(","):
                        if mid.strip().isdigit():
                            try:
                                await bot.delete_message(config.CHANNEL_ID, int(mid))
                            except Exception:
                                pass  # already gone / too old to delete
        finally:
            await bot.session.close()
        for lid in ids:
            await db._conn.execute("DELETE FROM events WHERE listing_id=?", (lid,))
            await db._conn.execute("DELETE FROM listings WHERE id=?", (lid,))
        await db._conn.commit()
    print("stats after purge:", await db.stats())
    await db.close_db()
asyncio.run(main())
PY

echo
echo "== 1. parse rentix one-bedrooms + download photos =="
$PY parse_rentix.py 2>&1 | tail -30

echo
echo "== 2. seed + publish the scraped listings =="
$PY seed_test_data.py rentix_listings.json 2>&1 | tail -30

echo
echo "== 3. flood-safe finish of any card that hit TelegramRetryAfter =="
$PY <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, publish, config
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

async def main():
    await db.init_db()
    if not config.CHANNEL_ID:
        print("no CHANNEL_ID"); await db.close_db(); return
    cur = await db._conn.execute(
        "SELECT l.* FROM listings l LEFT JOIN events e "
        "ON e.listing_id=l.id AND e.name='channel_post' "
        "WHERE l.status='active' AND e.id IS NULL ORDER BY l.id")
    cur.row_factory = db._conn.row_factory
    rows = await cur.fetchall()
    print("unposted active listings:", [r["id"] for r in rows] or "(all posted)")
    bot = Bot(token=config.BOT_TOKEN)
    try:
        for r in rows:
            for attempt in range(4):
                try:
                    ids = await publish.to_channel(bot, r)
                    print(f"  #{r['id']} -> {ids}"); break
                except TelegramRetryAfter as e:
                    w = int(e.retry_after) + 5
                    print(f"  #{r['id']} flood, sleep {w}s"); await asyncio.sleep(w)
                except Exception as e:
                    print(f"  #{r['id']} !! {type(e).__name__}: {str(e)[:150]}"); break
            await asyncio.sleep(18)
    finally:
        await bot.session.close(); await db.close_db()
asyncio.run(main())
PY

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
        "SELECT l.id, l.district, l.price, l.currency, "
        "(SELECT count(*) FROM events e WHERE e.listing_id=l.id AND e.name='channel_post') cp "
        "FROM listings l WHERE l.status='active' ORDER BY l.id")
    print("ACTIVE:", len(rows), "| posted:", sum(1 for r in rows if r[4] > 0))
    for r in rows:
        print("  #%d  %s %s %s  channel_post=%d" % (r[0], r[3], r[1], r[2], r[4]))
    await db.close_db()
asyncio.run(main())
PY
