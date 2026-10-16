#!/usr/bin/env bash
# Read-only snapshot of the live experiment: is the money machine actually able to run?
# Changes nothing. Prints service state + the numbers that decide "does it work".
set -uo pipefail
echo "=== services ==="
echo "imobiliare     : $(systemctl is-active imobiliare)"
echo "imobiliare-web : $(systemctl is-active imobiliare-web)"

echo "=== .env switches that gate the experiment ==="
grep -E '^(PAID_ENABLED|BOOST_MDL|PRICE_LISTING_MDL|PRICE_HIGHLIGHT_MDL|PRICE_ALERTS_MDL|FREE_FIRST_LISTING|FOCUS_DEAL|FOCUS_ROOMS|CARD_ENABLED|MIA)' /opt/imobiliare/bot/.env | sed 's/^/  /' || echo "  (none of these keys in .env)"

echo "=== live DB snapshot (read-only) ==="
/opt/imobiliare/venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect("file:/opt/imobiliare/data/imobiliare.db?mode=ro", uri=True)
db.row_factory = sqlite3.Row
def one(q, *a):
    try:
        return db.execute(q, a).fetchone()[0]
    except Exception as e:
        return f"ERR:{e}"
print("  users                :", one("SELECT COUNT(*) FROM users"))
print("  listings (all)       :", one("SELECT COUNT(*) FROM listings"))
print("  listings active      :", one("SELECT COUNT(*) FROM listings WHERE status='active'"))
print("  active one-bed rent  :", one("SELECT COUNT(*) FROM listings WHERE status='active' AND deal='rent' AND rooms=1"))
print("  listings pending     :", one("SELECT COUNT(*) FROM listings WHERE status='pending'"))
print("  featured active      :", one("SELECT COUNT(*) FROM listings WHERE status='active' AND featured_until IS NOT NULL AND featured_until > datetime('now')"))
print("  boosted active       :", one("SELECT COUNT(*) FROM listings WHERE status='active' AND boosted_until IS NOT NULL AND boosted_until > datetime('now')"))
print("  payments rows        :", one("SELECT COUNT(*) FROM payments"))
print("  topups (pending/paid):", one("SELECT COUNT(*) FROM topups WHERE status='pending'"), "/", one("SELECT COUNT(*) FROM topups WHERE status='paid'"))
print("  saved-search alerts  :", one("SELECT COUNT(*) FROM search_alerts"))
print("  events (last 24h)    :", one("SELECT COUNT(*) FROM events WHERE created_at > datetime('now','-1 day')"))
# revenue honesty: real money in
try:
    r = db.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE currency IN ('MDL') ").fetchone()[0]
    print("  MDL booked           :", r)
except Exception as e:
    print("  MDL booked           : ERR", e)
# a snapshot of the active board so we can eyeball whether supply is real
print("  --- first 6 active one-bed rentals ---")
for row in db.execute("SELECT id,price,currency,district,status,expires_at FROM listings WHERE status='active' AND deal='rent' AND rooms=1 ORDER BY id DESC LIMIT 6"):
    print("   ", dict(row))
PY

echo "=== webapp self-check (does the public board serve?) ==="
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/api/stats)
echo "  GET /api/stats -> $code"
curl -s http://127.0.0.1:8085/api/stats | head -c 300; echo
