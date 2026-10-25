#!/usr/bin/env bash
# tunnel_guard.sh - self-healing repointer for the ephemeral Cloudflare quick tunnel.
#
# A quick tunnel hands out a RANDOM https://<words>.trycloudflare.com host that CHANGES every time
# cloudflared-tunnel restarts (reboot, crash, `systemctl restart`). .env pins WEBAPP_URL to whatever
# host existed at deploy time, so after any restart the bot's Mini App button, the web_app keyboards
# and the Lemon Squeezy payment webhook all point at a DEAD host: the app 404s and card payments stop
# arriving in silence. This guard closes that gap without a domain: it reads the live host from the
# tunnel's own log, and the moment it differs from .env it re-points everything and verifies.
#
# Idempotent and cheap: when nothing changed it exits in a few ms. Meant to run under a systemd timer.
# Safe to run by hand too:  bash /opt/imobiliare/tunnel_guard.sh
set -u
ENV=/opt/imobiliare/bot/.env
BOTSRC=/opt/imobiliare/bot
PY=/opt/imobiliare/venv/bin/python
MENU=/opt/imobiliare/set_menu_button.py
LSWH=/opt/imobiliare/register_ls_webhook.py
TAG=tunnel_guard

log(){ echo "[$(date -u +%FT%TZ)] $*" | logger -t "$TAG"; echo "guard: $*"; }

# the newest host the tunnel has announced
LIVE=$(journalctl -u cloudflared-tunnel --no-pager -o cat 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
[ -n "${LIVE:-}" ] || { log "no live trycloudflare host in the journal yet"; exit 0; }

CUR=$(grep -E '^WEBAPP_URL=' "$ENV" 2>/dev/null | head -1 | cut -d= -f2-)
if [ "$CUR" = "$LIVE" ]; then exit 0; fi   # unchanged: nothing to do

# never repoint at a host that does not actually serve the app (tunnel still warming up)
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$LIVE/api/stats" 2>/dev/null || echo 000)
if [ "$CODE" != "200" ]; then log "live $LIVE returned $CODE on /api/stats, not repointing"; exit 0; fi

log "host changed: $CUR -> $LIVE (verified $CODE), repointing"
if grep -q '^WEBAPP_URL=' "$ENV"; then
  sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=$LIVE|" "$ENV"
else
  echo "WEBAPP_URL=$LIVE" >> "$ENV"
fi

# the bot reads WEBAPP_URL from .env via load_dotenv, so restart it to pick up the new host
systemctl restart imobiliare imobiliare-web 2>/dev/null || true

# re-point the chat menu button (Bot API) onto the live host; run from the app dir so config loads .env
if [ -f "$MENU" ]; then
  ( cd "$BOTSRC" && "$PY" "$MENU" ) > >(sed 's/^/menu: /' | logger -t "$TAG") 2>&1 || log "menu button re-point failed"
fi

# re-point the Lemon Squeezy webhook (drops the stale host, keeps the secret stable). only if wired up.
if grep -qE '^LS_API_KEY=..' "$ENV" && grep -qE '^LS_STORE_ID=..' "$ENV"; then
  if [ -f "$LSWH" ]; then
    ( cd "$BOTSRC" && "$PY" "$LSWH" ) > >(sed 's/^/lswh: /' | logger -t "$TAG") 2>&1 || log "LS webhook re-point failed"
  fi
fi
log "done"
