#!/usr/bin/env bash
# Settle the "no auth on the phone" question with evidence, all read-only except a self-test row
# that is created and deleted in the same run:
#   * server clock sanity (a wrong clock would expire every real initData),
#   * the live auth-reject reasons from the journal,
#   * a signed request made with the webapp's OWN BOT_TOKEN against 127.0.0.1 -> proves the server
#     side + token are correct, so a phone 401 can only mean initData never reached the client.
set -uo pipefail
cd /opt/imobiliare

echo "=== 1) server clock (must be close to real UTC now or auth_date would look expired) ==="
echo "  server local : $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  server epoch : $(date +%s)"

echo "=== 2) auth-reject reasons captured by the freshly deployed webapp (last 30 min) ==="
journalctl -u imobiliare-web --no-pager --since "30 min ago" 2>/dev/null | grep -i "auth reject" | tail -20 || true
echo "  (empty above = no write attempt has hit the new code yet)"

echo "=== 3) self-test: POST a listing signed with the webapp's own BOT_TOKEN against 127.0.0.1 ==="
/opt/imobiliare/venv/bin/python - <<'PY'
import sys, time, hmac, hashlib, json, urllib.parse, urllib.request
sys.path.insert(0, "/opt/imobiliare/bot")
import config
token = config.BOT_TOKEN
print("  using BOT_TOKEN of bot id:", token.split(":")[0] if ":" in token else "?")
user = {"id": 999000000001, "first_name": "Probe", "username": "probe_selftest"}
pairs = {"auth_date": str(int(time.time())), "query_id": "PROBE", "user": json.dumps(user)}
secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
init_data = urllib.parse.urlencode(pairs)
body = json.dumps({"deal": "rent", "ptype": "apartment", "rooms": "1", "district": "probe",
                   "price": 1, "currency": "EUR", "description": "selftest", "phone": "+37300000"}).encode()
req = urllib.request.Request("http://127.0.0.1:8085/api/listing", data=body,
                            headers={"Content-Type": "application/json", "X-Init-Data": init_data}, method="POST")
code = None; payload = b""
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        code, payload = r.status, r.read()
except urllib.error.HTTPError as e:
    code, payload = e.code, e.read()
print("  HTTP", code, payload.decode("utf-8", "replace")[:200])
print("  => server+token ACCEPT a valid signature" if code == 200 else "  => server rejected even a valid signature - BUG IS SERVER-SIDE")
PY

echo "=== 4) cleanup any probe rows so the board/tests stay untouched ==="
/opt/imobiliare/venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect("/opt/imobiliare/data/imobiliare.db")
c = db.cursor()
c.execute("SELECT id FROM users WHERE tg_id='999000000001'")
uids = [r[0] for r in c.fetchall()]
for u in uids:
    c.execute("DELETE FROM listings WHERE user_id=?", (u,))
c.execute("DELETE FROM events WHERE user_id IN (SELECT id FROM users WHERE tg_id='999000000001')")
c.execute("DELETE FROM users WHERE tg_id='999000000001'")
db.commit()
print("  deleted probe listings for users:", uids, "| remaining probe rows:",
      c.execute("SELECT COUNT(*) FROM users WHERE tg_id='999000000001'").fetchone()[0])
db.close()
PY
