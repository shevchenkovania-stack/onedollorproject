"""Hard-remove the seeded ДЕМО listings: their channel posts, DB rows and photo files.

Why not unseed_board.sh: that only soft-archives (rows stay, board hides them). Here the owner
asked for the fakes GONE, so we delete for real. Safe & idempotent:
  - the demo channel posts were posted BY the bot, so the bot can delete them (unlike the 24
    legacy Acces Imobil posts it cannot touch);
  - only rows with is_demo=1 are touched; real owner ads (is_demo=0) are never matched;
  - a second run finds nothing to do.

Run on prod from the app dir:  cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python /tmp/wipe_demos.py
"""
import asyncio
import glob
import os
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

from aiogram import Bot

import config
import db


async def main() -> None:
    await db.init_db()
    conn = db._db()
    rows = await (await conn.execute("SELECT id FROM listings WHERE is_demo=1")).fetchall()
    demo_ids = [r["id"] for r in rows]
    print("demo listings found:", demo_ids)

    bot = Bot(token=config.BOT_TOKEN)
    deleted_posts = 0
    if config.CHANNEL_ID and demo_ids:
        for lid in demo_ids:
            evs = await (await conn.execute(
                "SELECT meta FROM events WHERE name='channel_post' AND listing_id=?", (lid,)
            )).fetchall()
            msg_ids = [int(x) for e in evs for x in (e["meta"] or "").split(",") if x.strip().isdigit()]
            for mid in msg_ids:
                try:
                    await bot.delete_message(config.CHANNEL_ID, mid)
                    deleted_posts += 1
                except Exception as e:
                    print(f"  WARN cannot delete channel msg {mid} (listing {lid}): {e}")

    # photo files on disk ({id}_{i}.jpg) + the on-disk gallery the Mini App serves
    removed_files = 0
    for lid in demo_ids:
        for path in glob.glob(os.path.join(config.PHOTOS_DIR, f"{lid}_*.jpg")):
            try:
                os.remove(path)
                removed_files += 1
            except OSError as e:
                print(f"  WARN cannot remove {path}: {e}")

    # rows: boosts/events reference the listing, so clear those first, then the listings
    for lid in demo_ids:
        await conn.execute("DELETE FROM boosts WHERE listing_id=?", (lid,))
        await conn.execute("DELETE FROM events WHERE listing_id=?", (lid,))
        await conn.execute("DELETE FROM listings WHERE id=?", (lid,))
    await conn.commit()

    still = await (await conn.execute("SELECT COUNT(*) c FROM listings WHERE is_demo=1")).fetchone()
    active = await (await conn.execute("SELECT COUNT(*) c FROM listings WHERE status='active'")).fetchone()
    print(f"deleted channel posts: {deleted_posts}")
    print(f"deleted photo files:   {removed_files}")
    print(f"demo rows remaining:   {still['c']}")
    print(f"active listings now:   {active['c']}  (should be 0 - board shows only real ads)")

    await bot.session.close()
    await db.close_db()
    sys.stdout.flush()
    os._exit(0)  # aiogram session.close() can hang; all work is committed


asyncio.run(main())
