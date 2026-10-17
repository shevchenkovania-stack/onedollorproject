#!/usr/bin/env bash
# READ-ONLY: what event data already exists in the DB. Prints per-day rollups grouped by event
# name + top-line totals for the last 14 days. No writes, no restarts.
set -u
/opt/imobiliare/venv/bin/python - <<'PYEOF'
import sqlite3
d = sqlite3.connect('/opt/imobiliare/data/imobiliare.db')
d.row_factory = sqlite3.Row
print("== per-day rollup (last 14 days) ==")
q = ("SELECT date(created_at) day, name, COUNT(*) c, COUNT(DISTINCT user_id) uniq "
     "FROM events WHERE created_at > datetime('now','-14 days') "
     "GROUP BY 1,2 ORDER BY 1 DESC, c DESC")
rows = list(d.execute(q))
if not rows:
    print("  (no events in the last 14 days)")
else:
    day_events = {}
    for r in rows:
        day_events.setdefault(r['day'], []).append((r['name'], r['c'], r['uniq']))
    for day, evs in day_events.items():
        total = sum(c for _, c, _ in evs)
        uids  = max(u for _, _, u in evs) if evs else 0
        parts = ", ".join(f"{n}:{c}({u}u)" for n, c, u in evs)
        print(f"  {day}  total={total}  uniq_users~={uids}  ::  {parts}")
print("\n== totals last 14 days ==")
for r in d.execute("SELECT name, COUNT(*) c, COUNT(DISTINCT user_id) u "
                   "FROM events WHERE created_at > datetime('now','-14 days') GROUP BY name ORDER BY c DESC"):
    print(f"  {r['name']:<22} events={r['c']:<5} unique_users={r['u']}")
print("\n== lifetime totals ==")
for r in d.execute("SELECT name, COUNT(*) c, COUNT(DISTINCT user_id) u FROM events GROUP BY name ORDER BY c DESC LIMIT 20"):
    print(f"  {r['name']:<22} events={r['c']:<5} unique_users={r['u']}")
print("\n== users table ==")
print("  total users:", d.execute("SELECT COUNT(*) c FROM users").fetchone()["c"])
print("  new users last 7d:", d.execute("SELECT COUNT(*) c FROM users WHERE created_at > datetime('now','-7 days')").fetchone()["c"])
PYEOF
