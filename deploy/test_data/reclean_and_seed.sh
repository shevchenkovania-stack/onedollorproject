#!/bin/bash
# Remove the previously-seeded TEST listings and their channel posts, then re-run
# the seed with the real uploaded photos (now via the full publish path so the
# photos actually reach the channel + Mini App).
# Which ids to delete: read /tmp/seed_created.txt (written by the last seed run);
# if it's missing, fall back to the known placeholder/test range 46..56.
set -e
cd /opt/imobiliare/test_data
/opt/imobiliare/venv/bin/python <<'PY'
import asyncio, os, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import db, config
from aiogram import Bot

def _targets():
    p = "/tmp/seed_created.txt"
    if os.path.exists(p):
        txt = open(p, encoding="utf-8").read().strip()
        if txt:
            return [int(x) for x in txt.split(",") if x.strip()]
    return list(range(46, 57))  # fallback: today's buggy-run test listings

async def main():
    await db.init_db()
    b = Bot(token=config.BOT_TOKEN)
    ids = _targets()
    print("target test listings:", ids)
    for lid in ids:
        # delete every channel message we logged for this listing
        ev = await db._conn.execute_fetchall(
            "SELECT meta FROM events WHERE listing_id=? AND name='channel_post'", (lid,))
        for (meta,) in ev:
            for mid in str(meta).split(","):
                if not mid.strip():
                    continue
                try:
                    await b.delete_message(config.CHANNEL_ID, int(mid))
                    print("  deleted channel msg", mid)
                except Exception as e:
                    print("  channel msg", mid, "->", type(e).__name__, str(e)[:60])
        await db._conn.execute("DELETE FROM events WHERE listing_id=?", (lid,))
        await db._conn.execute("DELETE FROM listings WHERE id=?", (lid,))
    await db._conn.commit()
    # also drop any stale on-disk web photos for these ids so re-seed re-downloads cleanly
    for lid in ids:
        for i in range(12):
            fp = os.path.join(config.PHOTOS_DIR, f"{lid}_{i}.jpg")
            if os.path.exists(fp):
                os.remove(fp)
    print("cleaned. stats now:", await db.stats())
    await b.session.close()
    await db.close_db()

asyncio.run(main())
PY

echo "== running seed with real photos (full publish path) =="
/opt/imobiliare/venv/bin/python seed_test_data.py 2>&1 | tail -30
