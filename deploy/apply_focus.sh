#!/usr/bin/env bash
# Apply the positioning focus on prod and restart the Mini App. Idempotent.
#  - strip any CRLF that came in over scp
#  - set FOCUS_DEAL=rent / FOCUS_ROOMS=1 in the service env file (one-bedroom rentals only)
#  - restart the webapp unit and prove the lock is live (search empty + token injected)
set -e
cd /opt/imobiliare/bot
for f in config.py webapp/app.py webapp/static/index.html; do
  [ -f "$f" ] && sed -i 's/\r$//' "$f"
done
sed -i '/^FOCUS_DEAL=/d;/^FOCUS_ROOMS=/d' .env
printf 'FOCUS_DEAL=rent\nFOCUS_ROOMS=1\n' >> .env
systemctl restart imobiliare-web
sleep 2
echo "web active: $(systemctl is-active imobiliare-web)"
echo "stats:      $(curl -s http://127.0.0.1:8085/api/stats)"
echo -n "index FOCUS_DEAL token: "
curl -s http://127.0.0.1:8085/ | grep -o 'const FOCUS_DEAL = "[a-z]*"' | head -1
echo "done."
