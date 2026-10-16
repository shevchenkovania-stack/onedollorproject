#!/usr/bin/env bash
# Reload the "open the app = real Mini App launch" entry fix (scheduler + publish now use
# web_app instead of a plain url, so Telegram always injects initData and submit stops 401-ing).
# Compile gate first: never restart onto a broken file. Then restart both services and re-read state.
set -uo pipefail
cd /opt/imobiliare

echo "=== 1) compile gate (only the two changed modules) ==="
/opt/imobiliare/venv/bin/python -m py_compile bot/scheduler.py bot/publish.py \
  && echo "  py_compile OK" || { echo "  COMPILE FAILED - aborting, no restart"; exit 1; }

echo "=== 2) restart both services together ==="
systemctl restart imobiliare imobiliare-web
sleep 4

echo "=== 3) verify both came back up ==="
echo "  imobiliare     : $(systemctl is-active imobiliare)"
echo "  imobiliare-web : $(systemctl is-active imobiliare-web)"
echo "  web /api/stats -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/api/stats)"

echo "=== 4) last bot journal lines (should show clean polling, no traceback) ==="
journalctl -u imobiliare --no-pager -n 4 | sed 's/^/  /'
