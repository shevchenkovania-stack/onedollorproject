"""E2E of the simple mechanism: listing -> moderation -> channel post -> Mini App API.

Runs the REAL production code path (publish.publish_listing, the same call admin.py makes
on approve), verifies the post appeared in the channel and the object appears in the webapp
API, then deletes everything: channel messages, DB rows, events. Zero residue - safe to run
on prod at any time. Money paths are not touched.

Run on prod: cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python test_publish.py
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

import aiohttp
from aiogram import Bot

import config
import db
import publish

MARKER = "ТЕХНИЧЕСКИЙ ТЕСТ конвейера (не объявление). Самоудалимся."


def token() -> str:
    return re.search(r"^BOT_TOKEN=(\S+)", open("/opt/imobiliare/bot/.env").read(), re.M).group(1)


async def counts() -> tuple:
    c = await db._db().execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM events)")
    # sqlite3.Row does NOT define __eq__ by value - compare plain tuples or the residue
    # check below can never pass (and the zombie aiosqlite thread keeps the process alive
    # after the AssertionError, which looked like an infinite hang).
    return tuple(await c.fetchone())


async def main() -> None:
    await db.init_db()
    bot = Bot(token=token())
    before = await counts()

    # 1. user submitted -> admin approved (same DB states as admin.moderate produces).
    # rooms=1 on purpose: prod locks the board to FOCUS_ROOMS=1 and the API filter must see it.
    listing_id = await db.create_listing(
        user_id=1, deal="rent", ptype="apartment", rooms=1, price=580, currency="EUR",
        district="Центр", area=55.0, floor=4, floors=9, phone="+37379999999",
        description=MARKER, photos=[],
    )
    await db.set_listing_status(listing_id, "active")
    await db.log_event("moderation_approve", listing_id=listing_id)

    # 2. the real publish path
    row = await db.get_listing(listing_id)
    post_ids = await publish.publish_listing(bot, row)
    assert post_ids, "channel post not created - CHANNEL_ID wrong or bot not in channel"
    print("channel post ids:", post_ids)

    # 3. Mini App must serve it
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        async with sess.get(f"{config.WEBAPP_URL.rstrip('/')}/api/listings") as resp:
            data = await resp.json()
        ids = [it["id"] for it in data["items"]]
        assert listing_id in ids, f"listing {listing_id} missing from webapp API: {ids}"
    print("webapp /api/listings: served")

    # 4. cleanup: posts, rows, events - board must be exactly as before
    for mid in post_ids:
        try:
            await bot.delete_message(config.CHANNEL_ID, mid)
        except Exception as e:
            print("WARN delete post:", mid, e)
    conn = db._db()
    await conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))
    await conn.execute("DELETE FROM events WHERE listing_id=?", (listing_id,))
    await conn.commit()
    after = await counts()
    # listings must be byte-identical; events may grow by the rows this test itself caused
    # through the public API (api_listings logs an anonymous 'web_open' per board load) -
    # those are real events with listing_id NULL, not residue.
    assert after[0] == before[0], f"listing residue: before={before} after={after}"
    assert after[1] >= before[1], f"events vanished: before={before} after={after}"
    print(f"events delta: +{after[1] - before[1]} (web_open from our own API check is expected)")

    await db.close_db()
    await bot.session.close()
    print("✅ BOARD PIPELINE OK (create -> moderate -> channel -> webapp -> zero residue)")
    sys.stdout.flush()
    # session.close() can hang the loop after everything important is already done:
    # the proof is printed and the board is verified clean - hard exit, no zombie.
    os._exit(0)


asyncio.run(main())
