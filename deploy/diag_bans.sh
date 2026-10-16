#!/usr/bin/env bash
# Read-only: figure out exactly why the owner keeps tripping fail2ban and whether it is safe to
# remove SSH bans for good. Prints state, changes nothing.
echo "=== this session ==="
echo "whoami   : $(whoami)"
echo "SSH_USER : ${SSH_USER:-?}  SSH_CLIENT: ${SSH_CLIENT:-?}"
ME=$(echo "${SSH_CLIENT:-}" | awk '{print $1}')
echo "my IP    : ${ME:-UNKNOWN}"

echo "=== sshd effective auth (is password login still open?) ==="
sshd -T 2>/dev/null | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication) '

echo "=== proof the owner actually logs in by KEY (journal, last 6h) ==="
journalctl -u ssh -u sshd --no-pager --since "-6 hours" 2>/dev/null \
  | grep -E "Accepted (publickey|password) for" | tail -n 12 || echo "(no journal access)"

echo "=== fail2ban: service + jails + any live bans ==="
echo "active: $(systemctl is-active fail2ban 2>/dev/null)"
fail2ban-client status 2>/dev/null || echo "(fail2ban not reporting)"
echo "--- sshd jail ---"
fail2ban-client status sshd 2>/dev/null || echo "(no sshd jail)"

echo "=== current sshd drop-in + fail2ban drop-in ==="
echo "--- /etc/ssh/sshd_config.d/*.conf ---"
grep -RHiE 'permitrootlogin|passwordauthentication' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null || echo "(none)"
echo "--- /etc/fail2ban/jail.d/imobiliare.conf ---"
cat /etc/fail2ban/jail.d/imobiliare.conf 2>/dev/null || echo "(none)"

echo "=== the last bans fail2ban recorded (who/when) ==="
journalctl -u fail2ban-server --no-pager --since "-24 hours" 2>/dev/null \
  | grep -Ei "ban(un)?ip" | tail -n 15 || echo "(no fail2ban journal)"
echo DONE
