#!/usr/bin/env bash
# Verify the channel is a real public SEO surface again: identity, board, photos, and exactly
# which OLD posts the bot could not delete (those need the owner's account or a rights grant).
set -u
cd /opt/imobiliare/bot
PY=/opt/imobiliare/venv/bin/python

$PY - <<'PYEOF'
import asyncio, sys, os, glob
sys.path.insert(0, "/opt/imobiliare/bot")
import config, db
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

STUCK = [1, 4, 5, 9, 10, 11, 13, 15, 20, 21, 22, 23, 44, 46, 47, 48, 49, 50, 51,
         52, 53, 54, 55, 56, 57, 58, 59, 60]
OURS = {10, 21, 22, 23}  # nav pin + the three storefront posts, rewritten in place


async def main():
    await db.init_db()
    bot = Bot(config.BOT_TOKEN)
    cid = config.CHANNEL_ID
    chat = await bot.get_chat(cid)
    print("title:", chat.title)
    print("desc :", (chat.description or "").replace("\n", " | ")[:200])
    try:
        print("subscribers:", await bot.get_chat_member_count(cid))
    except Exception as e:
        print("subscribers: ? ", str(e)[:60])
    rows = await db._conn.execute_fetchall(
        "SELECT COUNT(*), SUM(is_demo) FROM listings WHERE status='active'")
    tot, demo = rows[0][0], rows[0][1]
    print(f"board: active={tot} demo={demo}")
    ph = glob.glob(os.path.join(config.PHOTOS_DIR, "*.jpg"))
    print("photo files on disk:", len(ph))
    await db.close_db()

    print("-- retry sweep of the ids the first pass refused --")
    absent, gone, refused = [], [], []
    for mid in STUCK:
        try:
            await bot.delete_message(cid, mid)
            gone.append(mid)
        except TelegramAPIError as e:
            s = str(e)
            if "Invalid message" in s or "not found" in s:
                absent.append(mid)
            else:
                refused.append(mid)
        except Exception:
            refused.append(mid)
        await asyncio.sleep(0.2)
    print("  deleted now:", gone or "none")
    print("  no such message (already gone):", len(absent))
    print("  REFUSED (old third-party/stub posts the owner must delete in the client):",
          [m for m in refused if m not in OURS] or "none")
    print("  our own posts kept and rewritten:", sorted(m for m in refused if m in OURS) or "none")
    await bot.session.close()

asyncio.run(main())
PYEOF

printf 'api stats: '; curl -s http://127.0.0.1:8085/api/stats; echo
printf 'api listings first caption: '; curl -s "http://127.0.0.1:8085/api/listings?limit=1" | head -c 300; echo
printf 'photo http: '; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8085/photos/$(ls /opt/imobiliare/data/photos | head -1)"
printf 'bot='; systemctl is-active imobiliare
printf 'web='; systemctl is-active imobiliare-web
echo -n "startup tracebacks: "; (journalctl -u imobiliare -n 80 --no-pager 2>/dev/null | grep -ci traceback) || echo 0
echo DONE
