#!/usr/bin/env bash
# Localize the "Отправить = 401" bug in one shot: replay a Mini App submit against the running
# webapp using a VALID, bot-token-signed initData. 200 => server path is perfect and the live 401
# can only mean the browser is not sending initData (ephemeral tunnel / WebApp URL mismatch).
# 401 => the validation itself is wrong. Cleans up the throwaway listing + user afterwards.
set -uo pipefail
/opt/imobiliare/venv/bin/python - <<'PY'
import sys, json, time, hmac, hashlib, urllib.parse, urllib.request
sys.path.insert(0, "/opt/imobiliare/bot")
import config
tok = config.BOT_TOKEN
secret = hmac.new(b"WebAppData", tok.encode(), hashlib.sha256).digest()
user = {"id": 990000000001, "first_name": "AuthProbe", "username": "auth_probe", "language_code": "ru"}
pairs = {"auth_date": str(int(time.time())), "query_id": "", "user": json.dumps(user, separators=(",", ":"))}
dcs = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
initdata = urllib.parse.urlencode({**pairs, "hash": h})

body = json.dumps({"deal": "rent", "ptype": "apartment", "rooms": "1", "district": "Центр",
                   "price": "1", "currency": "EUR", "description": "probe", "phone": "060000000"}).encode()
req = urllib.request.Request("http://127.0.0.1:8085/api/listing", data=body, method="POST",
                             headers={"Content-Type": "application/json", "X-Init-Data": initdata})
try:
    with urllib.request.urlopen(req) as r:
        print("  STATUS:", r.status); print("  BODY  :", r.read().decode())
        created = json.loads(r.read().decode()) if False else None
except urllib.error.HTTPError as e:
    print("  STATUS:", e.code); print("  BODY  :", e.read().decode())

# cleanup: remove the probe user + any listing it created, leave prod as we found it
import sqlite3
db = sqlite3.connect("/opt/imobiliare/data/imobiliare.db")
db.execute("DELETE FROM listings WHERE user_id IN (SELECT id FROM users WHERE tg_id=990000000001)")
db.execute("DELETE FROM events  WHERE user_id IN (SELECT id FROM users WHERE tg_id=990000000001)")
db.execute("DELETE FROM users   WHERE tg_id=990000000001")
db.commit()
print("  cleanup done; remaining probe rows:",
      db.execute("SELECT (SELECT COUNT(*) FROM users WHERE tg_id=990000000001)").fetchone()[0])
db.close()
PY
