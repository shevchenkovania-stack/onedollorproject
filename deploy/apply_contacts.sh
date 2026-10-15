#!/usr/bin/env bash
# Apply the "public channels on every card" update.
# Pre-conditions: db.py keyboards.py render.py publish.py handlers/add.py webapp/app.py
# webapp/static/index.html have already been scp'ed to /opt/imobiliare/bot by deploy/run.ps1.
#
# What this does, in order and stop-on-fail:
#   1. py_compile every Python file (a syntax error should not take the bot down)
#   2. run the schema migration once via db.init_db so listings has the contacts column
#      before either service tries to SELECT it
#   3. restart imobiliare-web, hit /api/stats and /api/listings to prove the webapp is up
#   4. restart imobiliare (bot), check the service is active and print last journal lines
set -e
cd /opt/imobiliare/bot

echo "--- py_compile"
/opt/imobiliare/venv/bin/python -m py_compile \
    db.py keyboards.py render.py publish.py handlers/add.py webapp/app.py
echo "compile OK"

echo "--- schema migration"
/opt/imobiliare/venv/bin/python - <<'EOF'
import asyncio
import db
async def go():
    await db.init_db()
    # sanity: the contacts column must be present now
    cur = await db._conn.execute("PRAGMA table_info(listings)")
    cols = {r["name"] for r in await cur.fetchall()}
    assert "contacts" in cols, "contacts column missing after migration"
    await db.close_db()
    print("migration OK: listings has", ", ".join(sorted(cols)))
asyncio.run(go())
EOF

echo "--- restarting webapp"
systemctl restart imobiliare-web
sleep 2
systemctl is-active imobiliare-web
curl -s http://127.0.0.1:8085/api/stats; echo
echo "api listings sample:"
curl -s "http://127.0.0.1:8085/api/listings?deal=rent" | head -c 400; echo

echo "--- restarting bot"
systemctl restart imobiliare
sleep 3
systemctl is-active imobiliare imobiliare-web
journalctl -u imobiliare -n 5 --no-pager

echo "DONE"
