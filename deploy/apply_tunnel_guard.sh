#!/usr/bin/env bash
# Installs the tunnel self-healing guard: a systemd timer that runs tunnel_guard.sh every minute,
# plus a first-run right after boot (OnBootSec) so a VPS reboot re-points everything on its own.
#
# Run:  powershell -File deploy\run.ps1 deploy\apply_tunnel_guard.sh "bash /tmp/apply_tunnel_guard.sh"
# Requires these already in place (this script wires them, run.ps1 uploads them):
#   /opt/imobiliare/tunnel_guard.sh        (this run copies it from /tmp)
#   /opt/imobiliare/set_menu_button.py
#   /opt/imobiliare/register_ls_webhook.py
set -e

# 1. the guard body. tunnel_guard.sh sits next to this script in /tmp after run.ps1 uploads it;
#    if invoked directly it may already be at /opt, so accept either source.
if [ -f /tmp/tunnel_guard.sh ]; then
  install -m 755 /tmp/tunnel_guard.sh /opt/imobiliare/tunnel_guard.sh
elif [ -f /opt/imobiliare/tunnel_guard.sh ]; then
  chmod 755 /opt/imobiliare/tunnel_guard.sh
else
  echo "tunnel_guard.sh not found in /tmp or /opt"; exit 1
fi

# 2. the helpers the guard calls must be present; warn (not fail) if a deploy forgot to upload them.
for f in set_menu_button.py register_ls_webhook.py; do
  if [ -f "/opt/imobiliare/$f" ]; then echo "ok: /opt/imobiliare/$f"; else echo "WARN: /opt/imobiliare/$f missing - guard will skip that step"; fi
done

# 3. oneshot service that runs the guard
cat > /etc/systemd/system/tunnel-guard.service <<'UNIT_EOF'
[Unit]
Description=Repoint Mini App menu button + payment webhook when the Cloudflare quick tunnel host changes

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/imobiliare/tunnel_guard.sh
# no EnvironmentFile: the guard edits .env and the child python reads it via load_dotenv from the app dir
UNIT_EOF

# 4. timer: first pass shortly after boot (tunnel needs ~30-60s to publish a host), then every minute
cat > /etc/systemd/system/tunnel-guard.timer <<'UNIT_EOF'
[Unit]
Description=Tunnel guard timer (every minute)

[Timer]
OnBootSec=45
OnUnitActiveSec=60
AccuracySec=10

[Install]
WantedBy=timers.target
UNIT_EOF

systemctl daemon-reload
systemctl enable --now tunnel-guard.timer

# 5. run the guard once right now so a currently-stale .env is fixed without waiting for the timer
echo "=== immediate pass ==="
bash /opt/imobiliare/tunnel_guard.sh || true

echo "=== state ==="
systemctl is-active tunnel-guard.timer
systemctl list-timers tunnel-guard.timer --no-pager | head -3
echo "DONE"
