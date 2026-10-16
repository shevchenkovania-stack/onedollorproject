#!/usr/bin/env bash
# Turn off the noise: archive the SEEDED demo board so nothing fake is visible to a real user.
# Non-destructive - rows are set to status='archived' (search/API hide archived), never deleted.
# Scoped to the single demo owner account, idempotent, safe to re-run.
# Roll back later with: UPDATE listings SET status='active' WHERE user_id=(SELECT id FROM users WHERE tg_id=1182157446);
set -e
/opt/imobiliare/venv/bin/python - <<'PYEOF'
import sqlite3
d = sqlite3.connect('/opt/imobiliare/data/imobiliare.db')
d.row_factory = sqlite3.Row
DEMO_TG = 1182157446
u = d.execute("SELECT id FROM users WHERE tg_id=?", (DEMO_TG,)).fetchone()
if not u:
    print("no demo owner found - nothing to do"); raise SystemExit(0)
before = d.execute("SELECT count(*) c FROM listings WHERE user_id=? AND status='active'", (u["id"],)).fetchone()["c"]
d.execute("UPDATE listings SET status='archived', updated_at=datetime('now') "
          "WHERE user_id=? AND status='active'", (u["id"],))
d.commit()
print("archived demo listings:", before)
print("still active (any owner):", d.execute("SELECT count(*) c FROM listings WHERE status='active'").fetchone()["c"])
print("total rows kept (reversible):", d.execute("SELECT count(*) c FROM listings").fetchone()["c"])
PYEOF
echo "board cleaned (soft-archive). Mini App search should now show 0."
