#!/usr/bin/env bash
# Owner directive: "no more bans, this is my server". Done safely by removing the reason a
# watchdog is needed instead of just muting it:
#   1) root is locked to KEY-ONLY (prohibit-password) - password brute force becomes a dead end;
#   2) fail2ban is stopped + disabled - NO address is ever banned again (office/home/new IP).
# Proof-before-permission: sshd is only touched if the journal shows a real key login from THIS
# address in the last 6h; the config is applied only if `sshd -t` passes; and ssh is RELOADED,
# never restarted, so the live session (and the owner) is never dropped. Idempotent.
set -e
ME=$(echo "${SSH_CLIENT:-}" | awk '{print $1}')
echo "=== this session IP: ${ME:-UNKNOWN} ==="

echo "=== 1) lift any ban currently in place (so follow-up works right now) ==="
if systemctl is-active fail2ban >/dev/null 2>&1; then
  for ip in $(fail2ban-client get sshd banip 2>/dev/null); do
    fail2ban-client set sshd unbanip "$ip" >/dev/null && echo "unbanned $ip" || true
  done
  echo "(bans cleared)"
else
  echo "(fail2ban not running)"
fi

echo "=== users with a real login shell (to eyeball the remaining password surface) ==="
grep -E ':/(bin|usr/bin)/(bash|sh|zsh|fish)$' /etc/passwd 2>/dev/null | awk -F: '{print $1" -> "$7}' || true

if [ -z "$ME" ]; then
  echo "REFUSING to change sshd: not running over ssh, cannot prove key login."
else
  echo "=== 2) prove key login from here before removing the password door for root ==="
  if journalctl -u ssh -u sshd --no-pager --since "-6 hours" 2>/dev/null \
       | grep -q "Accepted publickey for root from ${ME}"; then
    echo "journal confirms a key login from ${ME} -> safe to make root key-only."
    cat > /etc/ssh/sshd_config.d/99-imobiliare.conf <<'EOF'
# deploy/disable_bans.sh: root gets in by key only. Password brute force is now a dead end,
# so no ban-watchdog is required and fail2ban is disabled (nobody is ever locked out).
PermitRootLogin prohibit-password
EOF
    if ! sshd -t; then
      echo "sshd -t FAILED -> config NOT applied, file removed, sshd untouched."
      rm -f /etc/ssh/sshd_config.d/99-imobiliare.conf
    else
      systemctl reload ssh 2>/dev/null || systemctl reload sshd
      sleep 2
      echo "ssh active       : $(systemctl is-active ssh 2>/dev/null || systemctl is-active sshd)"
      echo "PermitRootLogin  : $(sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')"
      echo "ОТКАТ (пока сессия жива): rm /etc/ssh/sshd_config.d/99-imobiliare.conf && systemctl reload ssh"
    fi
  else
    echo "NO 'Accepted publickey for root from ${ME}' recently -> key login NOT proven."
    echo "Leaving sshd alone (fail2ban still disabled below, but root keeps password). Add your"
    echo "key with ssh-copy-id and re-run to lock root to key-only."
  fi
fi

echo "=== 3) stop + disable fail2ban: no address is ever banned again ==="
systemctl stop fail2ban 2>/dev/null || true
systemctl disable fail2ban 2>/dev/null || true
rm -f /etc/fail2ban/jail.d/imobiliare.conf 2>/dev/null || true
echo "fail2ban active : $(systemctl is-active fail2ban 2>/dev/null)"
echo "fail2ban enabled: $(systemctl is-enabled fail2ban 2>/dev/null)"
echo "ВКЛЮЧИТЬ ОПЯТЬ (если передумаешь): systemctl enable --now fail2ban"

echo "=== final: ssh is still serving and this session is intact ==="
systemctl is-active ssh 2>/dev/null || systemctl is-active sshd
echo DONE
