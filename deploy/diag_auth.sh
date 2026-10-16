#!/usr/bin/env bash
# Read-only: why does the Mini App submit return 401? Distinguish "no initData sent" (all authed
# calls 401) from "initData sent but signature rejected" (config/token issue).
set -uo pipefail
echo "=== request mix by method+status (last 8h) ==="
journalctl -u imobiliare-web --no-pager --since '-8 hours' \
  | grep -oE '"(GET|POST) [^"]+" [0-9]{3}' \
  | sed -E 's/\?.*"/"/' \
  | awk '{print $1, $2, $3}' | sort | uniq -c | sort -rn | head -40

echo "=== were there ANY successful (2xx) writes? ==="
journalctl -u imobiliare-web --no-pager --since '-8 hours' \
  | grep -E 'POST /api/(listing|search|listing/)' | grep -E ' 2[0-9]{2} ' | tail -5 || echo "  (none)"

echo "=== webapp BOT_TOKEN (id prefix only, never the secret) ==="
/opt/imobiliare/venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/opt/imobiliare/bot")
import config
t = config.BOT_TOKEN or ""
print("  token present :", bool(t), " len:", len(t))
print("  bot id prefix :", t.split(":")[0] if ":" in t else "MALFORMED")
PY

echo "=== tunnel vs registered Mini App URL ==="
grep -E '^(WEBAPP_URL|BOT_USERNAME)=' /opt/imobiliare/bot/.env | sed 's/^/  env: /'
echo "  nginx server_name:"; grep -RhoE 'server_name [^;]+;' /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^/    /' | sort -u
