#!/usr/bin/env bash
# Stop fail2ban from banning the OWNER so deploys/verifications can run freely.
#
#   scp deploy/unlock_ssh.sh root@<IP>:/tmp/
#   ssh root@<IP> "sed -i 's/\r$//' /tmp/unlock_ssh.sh && bash /tmp/unlock_ssh.sh"
#
# Why: fail2ban counts "connections to port 22", it does not know a legit deploy burst
# is the owner. A normal interactive session opens many SCP/SSH connections in under a
# minute and trips the old maxretry=5 rule. This script (a) puts your current address in
# the permanent whitelist and (b) loosens the trigger so bursts are fine while real brute
# force is still caught. It only rewrites the fail2ban drop-in and restarts fail2ban - it
# NEVER reloads/restarts sshd, so this live session is not dropped. Idempotent: re-running
# changes nothing and keeps previously whitelisted addresses.
set -e

# The address fail2ban would see for THIS connection. When run over ssh, SSH_CLIENT is set
# by sshd to "client-ip client-port server-ip". If run some other way it is empty.
ME=$(echo "${SSH_CLIENT:-}" | awk '{print $1}')
echo "=== your admin IP (from this session): ${ME:-UNKNOWN} ==="
if [ -z "$ME" ]; then
  echo "not running over ssh - cannot detect your IP. Run this through ssh."
  exit 1
fi

FILE=/etc/fail2ban/jail.d/imobiliare.conf

# Preserve every address we whitelisted before, so a changing IP history (office + home)
# stays allowed. Then rebuild the line de-duplicated: localhost + prior addresses + this
# session's IP. Without the dedup, each re-run re-appends the base and the current IP and
# the line grows with duplicates.
PREV_IGNORE=$(awk -F= '/^ignoreip/{print $2}' "$FILE" 2>/dev/null)
IGNORE=$( { echo "127.0.0.1/8 ::1"; echo "$PREV_IGNORE"; echo "$ME"; } \
  | tr -s ' \t' '\n' | awk 'NF && !seen[$0]++' | tr '\n' ' ')
echo "=== final whitelist: ${IGNORE} ==="

cat > "$FILE" <<EOF
# managed by deploy/unlock_ssh.sh - owner whitelist + deploy-friendly limits
[sshd]
enabled  = true
port     = 22
backend  = systemd
# interactive deploys open many ssh/scp connections fast; only stop real brute force
maxretry = 30
findtime = 10m
# short ban: a whitelisted owner IP is never banned at all, and even an unexpected NEW
# address (office/home DHCP changed) self-heals in 10 min instead of locking us out.
bantime  = 10m
# never ban localhost or any address the owners have connected from
ignoreip = ${IGNORE}
EOF

systemctl restart fail2ban
sleep 2

# Lift any ban currently sitting on us so this session's follow-up works right away.
if fail2ban-client status sshd 2>/dev/null | grep -qw "$ME"; then
  fail2ban-client set sshd unbanip "$ME" >/dev/null && echo "=== unbanned $ME ==="
else
  echo "=== no active ban on $ME ==="
fi

echo "=== fail2ban: $(systemctl is-active fail2ban) ==="
fail2ban-client status sshd
echo "OK: your IP is whitelisted and the trigger is loosened - work freely."
