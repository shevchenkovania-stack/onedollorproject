#!/usr/bin/env bash
# Pull the exact auth-reject reasons captured since the instrumentation went live, plus a broader
# look at recent write attempts (POST/PUT) so we can tell "no initData at all" from "bad signature".
set -uo pipefail
echo "=== auth reject lines (last 24h, most recent 30) ==="
journalctl -u imobiliare-web --no-pager --since "24 hours ago" 2>/dev/null | grep -i "auth reject" | tail -30
echo
echo "=== recent non-GET requests hitting the webapp (method + path + status) ==="
journalctl -u imobiliare-web --no-pager --since "6 hours ago" 2>/dev/null | grep -E '" (POST|PUT|PATCH|DELETE) ' | tail -30
echo
echo "=== if both blocks above are EMPTY, the app never reached the server: show last 15 webapp lines ==="
journalctl -u imobiliare-web --no-pager -n 15 2>/dev/null
