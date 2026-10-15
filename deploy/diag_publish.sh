#!/bin/bash
set -e
echo "== last 40 lines of bot log =="
journalctl -u imobiliare -n 40 --no-pager || true
echo
echo "== last 20 lines of web log =="
journalctl -u imobiliare-web -n 20 --no-pager || true
echo
echo "== listings in DB =="
cd /opt/imobiliare/bot
/opt/imobiliare/venv/bin/python <<'PY'
import sqlite3, os
db_path = os.environ.get("DB_PATH", "/opt/imobiliare/data/bot.db")
if not os.path.exists(db_path):
    for p in ("/opt/imobiliare/bot/bot.db", "/opt/imobiliare/data/chiriebot.db", "/opt/imobiliare/bot/chiriebot.db"):
        if os.path.exists(p): db_path = p; break
print("using db:", db_path)
c = sqlite3.connect(db_path); c.row_factory = sqlite3.Row
rows = list(c.execute("SELECT id, status, user_id, deal, district, price, photo_ids, channel_post_id, created_at FROM listings ORDER BY id DESC LIMIT 8"))
print("count:", len(rows))
for r in rows:
    d = dict(r)
    ph = (d.get("photo_ids") or "")[:30]
    cp = d.get("channel_post_id")
    print(f"  #{d['id']} {d['status']:>10s}  deal={d['deal']} dist={d['district']} price={d['price']} photos={ph!r}.. chpost={cp} created={d['created_at']}")
PY
echo
echo "== channel config =="
/opt/imobiliare/venv/bin/python <<'PY'
import sys, os
sys.path.insert(0, "/opt/imobiliare/bot")
try:
    from dotenv import load_dotenv; load_dotenv("/opt/imobiliare/bot/.env")
except Exception as e:
    print("dotenv:", e)
import config
print("CHANNEL_ID :", repr(getattr(config, "CHANNEL_ID", None)))
print("CHANNEL_URL:", repr(getattr(config, "CHANNEL_URL", None)))
print("ADMIN_IDS  :", repr(getattr(config, "ADMIN_IDS", None)))
PY
echo
echo "== is bot a member/admin of channel? =="
/opt/imobiliare/venv/bin/python <<'PY'
import sys, asyncio
sys.path.insert(0, "/opt/imobiliare/bot")
from dotenv import load_dotenv; load_dotenv("/opt/imobiliare/bot/.env")
import config
from aiogram import Bot
async def main():
    b = Bot(token=config.BOT_TOKEN)
    if not config.CHANNEL_ID:
        print("CHANNEL_ID not set - nothing to publish to")
        return
    try:
        m = await b.get_chat_member(config.CHANNEL_ID, (await b.get_me()).id)
        print("channel:", config.CHANNEL_ID, "bot status:", m.status, "custom:", getattr(m, "custom_role", None))
    except Exception as e:
        print("get_chat_member FAILED:", type(e).__name__, e)
    try:
        ch = await b.get_chat(config.CHANNEL_ID)
        print("chat title:", ch.title, "type:", ch.type)
    except Exception as e:
        print("get_chat FAILED:", type(e).__name__, e)
    await b.session.close()
asyncio.run(main())
PY
