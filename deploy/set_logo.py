"""Set the brand logos: the BOT gets its mark, the CHANNEL gets the same family in its own colour.

Two files, not one — the bot (tool, teal) and the channel (feed, slate) are distinct surfaces of one
brand, so they must not share an identical square. Regenerate both with `python deploy/make_logo.py`.

Run on prod (files copied to /tmp by run.ps1 first):
    cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python set_logo.py /tmp/logo_bot.png /tmp/logo_channel.png
Token and CHANNEL_ID come from the service .env via config - nothing is hardcoded.
"""
import asyncio
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

import aiohttp

import config

BOT_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/logo_bot.png"
CHANNEL_PATH = sys.argv[2] if len(sys.argv) > 2 else "/tmp/logo_channel.png"


async def set_bot_photo(sess: aiohttp.ClientSession, blob: bytes) -> None:
    # setMyProfilePhoto does not accept a bare multipart part named "photo" (Bot API quirk): the
    # file goes under an arbitrary name and "photo" references it via attach://.
    data = aiohttp.FormData()
    data.add_field("photo", '{"type":"static","photo":"attach://p"}')
    data.add_field("p", blob, filename="logo.png", content_type="image/png")
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/setMyProfilePhoto"
    async with sess.post(url, data=data) as resp:
        print("setMyProfilePhoto", resp.status, await resp.text())


async def set_channel_photo(sess: aiohttp.ClientSession, blob: bytes) -> None:
    if not config.CHANNEL_ID:
        print("CHANNEL_ID not set - channel photo skipped")
        return
    # setChatPhoto happily takes the plain part.
    data = aiohttp.FormData()
    data.add_field("chat_id", config.CHANNEL_ID)
    data.add_field("photo", blob, filename="logo.png", content_type="image/png")
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/setChatPhoto"
    async with sess.post(url, data=data) as resp:
        print("setChatPhoto", resp.status, await resp.text())


async def main() -> None:
    bot_blob = open(BOT_PATH, "rb").read()
    channel_blob = open(CHANNEL_PATH, "rb").read()
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as sess:
        await set_bot_photo(sess, bot_blob)
        await set_channel_photo(sess, channel_blob)


asyncio.run(main())
