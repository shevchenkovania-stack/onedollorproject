#!/usr/bin/env bash
# Read-only: why does the Mini App get "no auth"? Compare the URL the bot's MENU BUTTON actually
# points to against WEBAPP_URL in .env, and check that host answers over public HTTPS. Prints no
# secrets — only the host and HTTP code. Also tails recent webapp auth-reject reasons.
set -uo pipefail
cd /opt/imobiliare

echo "=== 0) apply the auth instrumentation (compile gate, then restart web only) ==="
/opt/imobiliare/venv/bin/python -m py_compile bot/webapp/app.py && echo "  py_compile OK" || { echo "  COMPILE FAILED - aborting, no restart"; exit 1; }
systemctl restart imobiliare-web
sleep 3
echo "  imobiliare-web : $(systemctl is-active imobiliare-web)"
echo "  web /api/stats -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/api/stats)"

echo "=== 1) WEBAPP_URL as the webapp/bot processes see it (host only) ==="
grep -E '^WEBAPP_URL=' bot/.env | sed -E 's#(https?://[^/]+).*#\1#'

echo "=== 2) what the bot's chat menu button really points to (live from Telegram) ==="
/opt/imobiliare/venv/bin/python - <<'PY'
import asyncio, os, sys
sys.path.insert(0, "/opt/imobiliare/bot")
from aiogram import Bot
import config
async def main():
    bot = Bot(config.BOT_TOKEN)
    got = await bot.get_chat_menu_button()
    url = getattr(getattr(got, "web_app", None), "url", None)
    await bot.session.close()
    print("  button type:", type(got).__name__)   # MenuButtonDefault/Commands -> NOT a Mini App
    print("  button url :", (url or "").rstrip("/"))
    env = (config.WEBAPP_URL or "").rstrip("/")
    if not url:
        print("  !! menu button is NOT a web_app button -> opening it never injects initData")
    elif url.rstrip("/") != env:
        print(f"  !! MISMATCH: button host != WEBAPP_URL ({env})")
    else:
        print("  ok: button == WEBAPP_URL")
asyncio.run(main())
PY

echo "=== 3) is that public host actually alive right now? ==="
HOST=$(grep -E '^WEBAPP_URL=' bot/.env | head -1 | cut -d= -f2- | sed -E 's#/*$##')
echo "  probing: $HOST"
curl -s -o /dev/null -m 10 -w "  public / -> %{http_code}\n" "$HOST/" || echo "  !! curl failed (tunnel down / host dead)"

echo "=== 4) last webapp auth-reject reasons from the journal ==="
journalctl -u imobiliare-web --no-pager -n 200 2>/dev/null | grep -i "auth reject" | tail -8 || echo "  (no auth-reject lines yet)"
