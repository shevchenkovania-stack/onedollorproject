#!/bin/bash
set -e
echo "== restart services =="
systemctl restart imobiliare imobiliare-web
sleep 3
systemctl is-active imobiliare imobiliare-web
echo
echo "== find listings that never got published =="
cd /opt/imobiliare/bot
/opt/imobiliare/venv/bin/python <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, publish, config
from aiogram import Bot

async def main():
    await db.init_db()
    # listings that are 'active' but no channel_post event exists for them (never made it out)
    cur = await db._conn.execute(
        "SELECT l.* FROM listings l "
        "LEFT JOIN events e ON e.listing_id = l.id AND e.name = 'channel_post' "
        "WHERE l.status = 'active' AND e.id IS NULL "
        "ORDER BY l.id DESC"
    )
    cur.row_factory = db._conn.row_factory
    rows = await cur.fetchall()
    print("stuck active listings:", len(rows))
    if not rows:
        await db.close_db()
        return
    if not config.CHANNEL_ID:
        print("!! CHANNEL_ID not set - cannot publish")
        await db.close_db()
        return
    bot = Bot(token=config.BOT_TOKEN)
    try:
        for r in rows:
            lid = r["id"]
            print(f"republish #{lid} deal={r['deal']} dist={r['district']} price={r['price']}")
            try:
                ids = await publish.to_channel(bot, r)
                print("  -> channel message ids:", ids)
            except Exception as e:
                print("  !! FAILED:", type(e).__name__, str(e)[:200])
    finally:
        await bot.session.close()
        await db.close_db()

asyncio.run(main())
PY
echo
echo "== final listing status =="
/opt/imobiliare/venv/bin/python <<'PY'
import sqlite3, os
c = sqlite3.connect("/opt/imobiliare/data/imobiliare.db"); c.row_factory = sqlite3.Row
rows = list(c.execute("SELECT id, status, district, price, created_at FROM listings ORDER BY id DESC LIMIT 8"))
print("count:", len(rows))
for r in rows:
    d = dict(r)
    print(f"  #{d['id']} {d['status']:>10s}  {d['district']} · {d['price']} · {d['created_at']}")
PY
