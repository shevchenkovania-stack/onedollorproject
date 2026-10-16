"""Quick prod health: admins configured + moderation queue + board composition.

Read-only. Prints no secrets (only the admin COUNT, never the ids).
Run: /opt/imobiliare/venv/bin/python /tmp/check_admins.py
"""
import glob
import os
import re
import sqlite3

raw = os.getenv("ADMIN_IDS", "")
if not raw:  # not exported into the shell -> read the single key straight from .env
    txt = open("/opt/imobiliare/bot/.env", encoding="utf-8").read()
    raw = (re.search(r"^ADMIN_IDS=(.*)$", txt, re.M) or [None, ""])[1]
print("admins_configured =", len([x for x in raw.replace(";", ",").split(",") if x.strip()]))

DB = None
for cand in (os.getenv("DB_PATH", ""), "/opt/imobiliare/data/imobiliare.db",
             "/opt/imobiliare/bot/board.db", "/opt/imobiliare/data/board.db"):
    if cand and os.path.exists(cand):
        DB = cand
        break
if DB is None:  # last resort: any real sqlite file under the app tree
    for f in glob.glob("/opt/imobiliare/**/*.db", recursive=True):
        if open(f, "rb").read(15) == b"SQLite format 3":
            DB = f
            break
print("db_path =", DB)

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
for status in ("pending", "active", "archived", "rejected"):
    n = db.execute("SELECT count(*) FROM listings WHERE status=?", (status,)).fetchone()[0]
    print(f"listings_{status} =", n)
# owners of the active board: how many distinct real users vs one demo seed
rows = db.execute(
    "SELECT user_id, count(*) c FROM listings WHERE status='active' GROUP BY user_id").fetchall()
print("active_owners =", len(rows), "| per owner:", [r["c"] for r in rows])
# any photo-less ads (look empty to a visitor)?
n = db.execute("SELECT count(*) FROM listings WHERE status='active' AND photos='[]'").fetchone()[0]
print("active_without_photos =", n)
# saved-search subscribers (the notification loop has an audience?)
try:
    n = db.execute("SELECT count(*) FROM search_alerts").fetchone()[0]
    print("search_alerts =", n)
except sqlite3.OperationalError:
    print("search_alerts = TABLE MISSING")
# revenue so far
n = db.execute("SELECT count(*) FROM payments").fetchone()[0]
print("payments_rows =", n)
