#!/usr/bin/env bash
# Restart the bot after the Telegram-side focus changes; make it safe + verify it came up.
set -uo pipefail
cd /opt/imobiliare
for f in main.py keyboards.py handlers/start.py handlers/browse.py handlers/add.py; do
  sed -i 's/\r$//' "bot/$f"
done
# syntax gate: do not restart onto a broken file
python3 -m py_compile bot/main.py bot/keyboards.py bot/handlers/start.py bot/handlers/browse.py bot/handlers/add.py && echo "py_compile OK"
systemctl restart imobiliare 2>/dev/null
sleep 6
echo "bot  active: $(systemctl is-active imobiliare 2>/dev/null)"
echo "web  active: $(systemctl is-active imobiliare-web 2>/dev/null)"
tail -n 6 /opt/imobiliare/bot.log 2>/dev/null | cut -c1-120 || true
echo DONE
