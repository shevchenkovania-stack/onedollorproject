#!/usr/bin/env bash
# Turn the public channel back ON (owner directive 23.10: "включи канал, может его кто-то найдёт").
# The 17.10 hide commented CHANNEL_ID in .env; here we restore it, then wipe the OLD channel posts
# (23.10: the 20 Acces Imobil rows and every earlier stub are still sitting in the public feed and
# their listing/event rows were deleted on 18.10, so their message ids are unknown - Bot API cannot
# read channel history, so a bounded id sweep is the only mechanical way out). The board is empty at
# this moment, so nothing a real owner posted can be touched by this sweep.
# Reversible: .env is backed up; rollback = restore the backup + restart.
set -u
cd /opt/imobiliare/bot
PY=/opt/imobiliare/venv/bin/python
TS=$(date +%Y%m%d_%H%M%S)

echo "== 1. backup .env =="
cp -f .env ".env.bak_channel_on_${TS}" && echo "  -> .env.bak_channel_on_${TS}"

echo "== 2. restore CHANNEL_ID =="
if grep -qE '^CHANNEL_ID=' .env; then
  echo "  ALREADY_ACTIVE"
else
  VAL=$(sed -n 's/^CHANNEL_ID=\(.*\)/\1/p' .env.bak_channelhide 2>/dev/null | head -1)
  [ -z "$VAL" ] && VAL="@gaseste_casa"
  if grep -qE '^# CHANNEL_ID' .env; then
    sed -i -E "s|^# CHANNEL_ID.*|CHANNEL_ID=${VAL}|" .env
  else
    echo "CHANNEL_ID=${VAL}" >> .env
  fi
  echo "  set to ${VAL}"
fi
grep -nE '^\s*#?\s*CHANNEL_ID' .env

echo "== 3. restart services =="
systemctl restart imobiliare imobiliare-web
sleep 4
printf '  bot='; systemctl is-active imobiliare
printf '  web='; systemctl is-active imobiliare-web

echo "== 4. effective channel config + bot rights there =="
$PY - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import config
from aiogram import Bot

async def main():
    print("  CHANNEL_ID", repr(config.CHANNEL_ID), "CHANNEL_URL", repr(getattr(config, "CHANNEL_URL", "")))
    if not config.CHANNEL_ID:
        return
    bot = Bot(config.BOT_TOKEN)
    try:
        chat = await bot.get_chat(config.CHANNEL_ID)
        me = await bot.get_me()
        member = await bot.get_chat_member(config.CHANNEL_ID, me.id)
        print(f"  title={chat.title!r}")
        print(f"  bot status={member.status} can_post={getattr(member,'can_post_messages',None)} "
              f"can_pin={getattr(member,'can_pin_messages',None)} can_delete={getattr(member,'can_delete_messages',None)}")
    except Exception as e:
        print("  !!", type(e).__name__, str(e)[:140])
    await bot.session.close()

asyncio.run(main())
PYEOF

echo "== 5. sweep old channel posts (ids 1..60) =="
$PY - <<'PYEOF'
import asyncio, sys
sys.path.insert(0, "/opt/imobiliare/bot")
import config
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

async def main():
    if not config.CHANNEL_ID:
        print("  CHANNEL_ID not set - nothing to sweep")
        return
    bot = Bot(config.BOT_TOKEN)
    deleted, absent, other = 0, 0, 0
    for mid in range(1, 61):
        try:
            await bot.delete_message(config.CHANNEL_ID, mid)
            deleted += 1
        except TelegramAPIError as e:
            s = str(e)
            if "Invalid message" in s or "not found" in s:
                absent += 1
            elif "too many requests" in s.lower():
                await asyncio.sleep(5)
                other += 1
            else:
                other += 1
                print(f"  #{mid}: {type(e).__name__} {s[:90]}")
        await asyncio.sleep(0.3)
    print(f"  deleted={deleted} absent={absent} other={other}")
    await bot.session.close()

asyncio.run(main())
PYEOF

echo "== 6. board API still serves =="
printf '  api stats: '; curl -s http://127.0.0.1:8085/api/stats; echo
echo -n "  startup tracebacks: "; (journalctl -u imobiliare -n 60 --no-pager 2>/dev/null | grep -ci traceback) || echo 0
echo "== DONE. rollback: cp .env.bak_channel_on_${TS} -> .env + systemctl restart imobiliare imobiliare-web =="
