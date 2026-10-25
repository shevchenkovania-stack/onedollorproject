#!/bin/bash
# Flood-safe republish of the rentix cards that were created active but never reached the channel
# (TelegramRetryAfter during the paced seed). Only touches 'active' listings with NO channel_post
# event, sleeps between sends and honours retry_after so it cannot re-trigger flood control.
set -e
/opt/imobiliare/venv/bin/python <<'PY'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, publish, config
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

async def main():
    await db.init_db()
    cur = await db._conn.execute(
        "SELECT l.* FROM listings l "
        "LEFT JOIN events e ON e.listing_id = l.id AND e.name = 'channel_post' "
        "WHERE l.status = 'active' AND e.id IS NULL ORDER BY l.id")
    cur.row_factory = db._conn.row_factory
    rows = await cur.fetchall()
    print("stuck active listings:", [r["id"] for r in rows])
    if not rows or not config.CHANNEL_ID:
        print("nothing to republish" if not rows else "!! CHANNEL_ID not set")
        await db.close_db(); return
    bot = Bot(token=config.BOT_TOKEN)
    try:
        for r in rows:
            for attempt in range(4):
                try:
                    ids = await publish.to_channel(bot, r)
                    print(f"  #{r['id']} -> {ids}")
                    break
                except TelegramRetryAfter as e:
                    wait = int(e.retry_after) + 5
                    print(f"  #{r['id']} flood, sleep {wait}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                except Exception as e:
                    print(f"  #{r['id']} !! {type(e).__name__}: {str(e)[:160]}")
                    break
            await asyncio.sleep(20)   # calm gap between cards
    finally:
        await bot.session.close()
        await db.close_db()

asyncio.run(main())
PY
