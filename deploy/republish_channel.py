"""Drain seeded listings that Telegram flood-control kept out of the channel.
Run with the venv python on the server. Finds active listings with NO channel_post
event and re-posts them one by one, honouring TelegramRetryAfter. Idempotent + converging:
already-posted listings are skipped. Print is flushed so a captured log is readable.
"""
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, publish, config
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError

BETWEEN = 8  # seconds between channel posts, well under the album flood limit


def log(*a):
    print(*a, flush=True)


async def stuck(bot):
    rows = await db._conn.execute_fetchall(
        "SELECT l.id FROM listings l "
        "LEFT JOIN events e ON e.listing_id = l.id AND e.name = 'channel_post' "
        "WHERE l.status = 'active' AND e.id IS NULL ORDER BY l.id ASC")
    return [r[0] for r in rows]


async def main():
    await db.init_db()
    if not config.CHANNEL_ID:
        log("CHANNEL_ID not set - nothing to do")
        await db.close_db()
        return
    bot = Bot(token=config.BOT_TOKEN)
    done = 0
    try:
        rows = await stuck(bot)
        log("stuck active listings (no channel post):", len(rows))
        for lid in rows:
            row = await db.get_listing(lid)
            for attempt in range(3):
                try:
                    ids = await publish.to_channel(bot, row)
                    log(f"  #{lid} -> channel ids {ids}")
                    done += 1
                    break
                except TelegramRetryAfter as e:
                    wait = int(getattr(e, "retry_after", 10)) + 3
                    log(f"  #{lid} flood, sleeping {wait}s (try {attempt+1})")
                    await asyncio.sleep(wait)
                except TelegramAPIError as e:
                    log(f"  #{lid} !! {type(e).__name__}: {str(e)[:140]}")
                    break
            await asyncio.sleep(BETWEEN)
        cur2 = await db._conn.execute_fetchall(
            "SELECT l.id FROM listings l LEFT JOIN events e "
            "ON e.listing_id=l.id AND e.name='channel_post' "
            "WHERE l.status='active' AND e.id IS NULL")
        log(f"posted {done}; still stuck: {[r[0] for r in cur2]}")
    finally:
        await bot.session.close()
        await db.close_db()


asyncio.run(main())
