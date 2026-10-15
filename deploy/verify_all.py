"""Full production verification: search + create + lifecycle + saved search + payment rails.

Runs in the prod venv. The functional part uses a throwaway sqlite (never the live DB);
the live part is strictly read-only against the running services. No secrets are printed.

Run: cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python /tmp/verify_all.py
"""
import asyncio
import hashlib
import hmac
import io
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/opt/imobiliare/bot")
sys.path.insert(0, "/opt/imobiliare/bot/webapp")

BASE = "http://127.0.0.1:8085"
FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


def code(path):
    try:
        return get(path)[0]
    except urllib.error.HTTPError as e:
        return e.code


# ---------- Part 1: the LIVE service (read-only) ----------
def live_checks():
    st, body = get("/api/stats")
    check("live /api/stats 200", st == 200, f"active={json.loads(body).get('active')}")

    st, body = get("/api/listings")
    data = json.loads(body)
    check("live /api/listings default 200 + items", st == 200 and "items" in data, f"total={data.get('total')}")
    first_id = data["items"][0]["id"] if data.get("items") else None

    for qs in ("?deal=rent", "?deal=sale", "?district=" + urllib.parse.quote("Центр"),
               "?rooms=2", "?price_max=500", "?sort=cheap", "?sort=expensive",
               "?q=" + urllib.parse.quote("балкон"), "?deal=rent&sort=cheap&rooms=1"):
        st, _ = get("/api/listings" + qs)
        check(f"live /api/listings{qs} 200", st == 200)

    if first_id:
        st, body = get(f"/api/listing/{first_id}")
        it = json.loads(body) if st == 200 else {}
        check("live /api/listing/{id} detail 200", st == 200, f"views={it.get('views')}")
    else:
        print("SKIP  live detail (no active listing on the board)")

    check("live /api/my/listings -> 401 anon", code("/api/my/listings") == 401)
    check("live /api/search/alerts -> 401 anon", code("/api/search/alerts") == 401)

    st, html = get("/")
    for marker in ('class="tabs"', 'id="followBtn"', 'id="favChip"', 'id="mySubs"', 'id="subForm"', 'id="q"'):
        check(f"live index.html has {marker}", marker in html)


# ---------- Part 2: functional flows in the PROD env (throwaway DB) ----------
def _signed(user_id, token):
    payload = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "T"})}
    data_check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**payload, "hash": h})


class Req:
    def __init__(self, headers=None, app=None, match_info=None, body=None, post=None, query=None):
        self.headers = headers or {}
        self.app = app or {}
        self.match_info = match_info or {}
        self._body = body
        self._post = post
        self.query = query or {}

    async def json(self):
        return self._body

    async def post(self):
        return self._post


class Multi:
    def __init__(self, fields):
        self._f = fields
    def getall(self, name, default=None):
        return list(self._f) if name == "photos" else (default or [])


class FF:
    def __init__(self, raw, ctype):
        self.file = io.BytesIO(raw)
        self.content_type = ctype


def _status(resp):
    return resp.status, json.loads(resp.body)


async def functional():
    import aiosqlite
    import app as webapp
    import config
    import db
    webapp.BOT_TOKEN = "test-token"
    config.ADMIN_IDS = []  # never DM real admins from the verification
    pdir = tempfile.mkdtemp()
    webapp.PHOTOS_DIR = pdir
    config.PHOTOS_DIR = pdir

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = None
    try:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await db.ensure_schema(conn)
        tabs = {r["name"] for r in await (await conn.execute(
            "select name from sqlite_master where type='table'")).fetchall()}
        check("prod schema has search_alerts", "search_alerts" in tabs)

        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (111,'o','O')")
        await conn.commit()
        app = {"db": conn}
        hdr = {"X-Init-Data": _signed(111, "test-token")}

        # CREATE a listing (Mini App path) -> pending; admin-notify code runs (no real send)
        st, resp = _status(await webapp.api_create_listing(
            Req(headers=hdr, app=app, body={"deal": "rent", "rooms": "2", "district": "Центр",
                                            "price": " 450 ", "phone": "+373 60 123 456",
                                            "description": "светлая"})))
        lid = resp.get("id")
        check("create listing -> 200 pending", st == 200 and resp.get("status") == "pending", f"id={lid}")

        # PHOTOS upload (does the prod env really have Pillow?)
        try:
            from PIL import Image
            buf = io.BytesIO(); Image.new("RGB", (10, 10), (10, 120, 90)).save(buf, format="JPEG")
            jpg = buf.getvalue()
            check("prod has Pillow (photo normalise)", True)
            st, pr = _status(await webapp.api_upload_photos(
                Req(headers=hdr, app=app, match_info={"id": str(lid)}, post=Multi([FF(jpg, "image/jpeg")]))))
            check("upload photo -> 200 + file", st == 200 and len(pr.get("photos", [])) == 1)
            cur = await conn.execute("SELECT photos FROM listings WHERE id=?", (lid,))
            check("photos persisted on listing", json.loads((await cur.fetchone())["photos"]) == [f"{lid}_0.jpg"])
        except ModuleNotFoundError:
            check("prod has Pillow (photo normalise)", False, "Pillow missing on prod")

        # SEARCH logic on the same DB: hidden until active, then found + filterable
        st, d = _status(await webapp.api_listings(Req(app=app)))
        check("pending ad not in public search yet", d["total"] == 0)
        await conn.execute("UPDATE listings SET status='active', expires_at=datetime('now','+30 days') WHERE id=?", (lid,))
        await conn.commit()
        st, d = _status(await webapp.api_listings(Req(app=app)))
        check("active ad appears in search", d["total"] == 1, f"total={d.get('total')}")
        st, d = _status(await webapp.api_listings(Req(app=app, query={"q": "светлая"})))
        check("search text query matches", d["total"] == 1)
        st, d = _status(await webapp.api_listings(Req(app=app, query={"q": "нет-такого"})))
        check("search text query excludes", d["total"] == 0)
        st, d = _status(await webapp.api_listings(Req(app=app, query={"sort": "cheap"})))
        check("search sort runs", st == 200)
        st, d = _status(await webapp.api_listings(Req(app=app, query={"deal": "sale"})))
        check("search deal filter excludes rent", d["total"] == 0)

        # LIFECYCLE actions (owner)
        st, r = _status(await webapp.api_listing_action(
            Req(headers=hdr, app=app, match_info={"id": str(lid)}, body={"action": "extend"})))
        check("lifecycle extend -> active", st == 200 and r.get("status") == "active")
        st, r = _status(await webapp.api_listing_action(
            Req(headers=hdr, app=app, match_info={"id": str(lid)}, body={"action": "sold"})))
        check("lifecycle sold -> archived", st == 200 and r.get("status") == "archived")

        # SAVED SEARCH create + list + delete
        st, r = _status(await webapp.api_create_search_alert(
            Req(headers=hdr, app=app, body={"deal": "rent", "district": "Центр"})))
        aid = r.get("id")
        check("saved search create", st == 200 and bool(aid))
        st, r = _status(await webapp.api_search_alerts(Req(headers=hdr, app=app)))
        check("saved search list", st == 200 and len(r.get("items", [])) == 1)
        st, r = _status(await webapp.api_delete_search_alert(
            Req(headers=hdr, app=app, match_info={"id": str(aid)})))
        check("saved search delete", st == 200 and r.get("ok"))
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass
        import shutil
        shutil.rmtree(pdir, ignore_errors=True)


# ---------- Part 3: payment rails status (no secrets, only booleans/host) ----------
def rails():
    import config
    print("--- payment rails (derived, no secrets) ---")
    print(f"Stars boost:       price={config.BOOST_STARS} {config.STARS_CURRENCY}, days={config.BOOST_DAYS} "
          "(invoice is Telegram-side; works whenever BOT_TOKEN works)")
    print(f"MIA / MDL manual:  amount={config.BOOST_MDL} MDL, "
          f"pay_phone_set={bool(config.PAY_PHONE)}, pay_name_set={bool(config.PAY_NAME)}")
    print(f"Card (LemonSqueezy): enabled={config.CARD_ENABLED} "
          f"(api={bool(config.LS_API_KEY)} store={bool(config.LS_STORE_ID)} "
          f"variant={bool(config.LS_VARIANT_ID)} webhook_secret={bool(config.LS_WEBHOOK_SECRET)})")
    host = urllib.parse.urlparse(config.WEBAPP_URL).netloc if config.WEBAPP_URL else "(пусто)"
    print(f"Mini App URL host: {host}")
    print(f"Channel:           {'set' if config.CHANNEL_ID else 'NOT SET'}")
    print(f"Admins:            {len(config.ADMIN_IDS)} id(s)")


if __name__ == "__main__":
    print("=== LIVE service (read-only) ===")
    live_checks()
    print("=== FUNCTIONAL flows (throwaway DB, prod deps) ===")
    asyncio.run(functional())
    print("=== Payment rails config ===")
    rails()
    print("\nRESULT:", "ALL GREEN" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
