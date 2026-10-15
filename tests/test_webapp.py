"""Mini App (webapp) wiring smoke tests.

The Mini App is now a first-class surface (Phases A-D of the plan), so it needs the same
kind of guard the bot has: the SPA must expose its tab bar, and the aiohttp app must actually
register every endpoint the frontend calls. These checks are static / structural - they never
touch the prod DB or network. Phase-specific behaviour (auth gate, lifecycle, create, photo
upload) is covered with a throwaway sqlite in the later tests appended below.

Run:  python tests/test_webapp.py        (exit 1 on any failure)
"""
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_DIR = os.path.join(ROOT, "bot")
WEB_DIR = os.path.join(BOT_DIR, "webapp")
sys.path.insert(0, BOT_DIR)
sys.path.insert(0, WEB_DIR)

STATIC = os.path.join(WEB_DIR, "static", "index.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_spa_tab_bar():
    """Phase A: the app is one window with a bottom tab bar (search / submit / my)."""
    html = _read(STATIC)
    for tab_id in ("tabSearch", "tabSubmit", "tabMy"):
        assert f'id="{tab_id}"' in html, f"missing tab {tab_id}"
    for view_id in ("viewSearch", "viewSubmit", "viewMy"):
        assert f'id="{view_id}"' in html, f"missing view {view_id}"
    assert "function showView(" in html, "showView() not defined"
    assert "showView('search')" in html and "showView('my')" in html and "showView('submit')" in html
    # the tab bar must be the real navigation, not dead links
    assert 'class="tabs"' in html
    print("PASS  test_spa_tab_bar")


def test_routes_registered():
    """The aiohttp app must route everything the SPA calls, or the buttons 404."""
    src = _read(os.path.join(WEB_DIR, "app.py"))
    registered = set(re.findall(r"add_(?:get|post)\(\s*[\"']([^\"']+)[\"']\s*,\s*(\w+)", src))
    paths = {p for p, _ in registered}
    handlers = {h for _, h in registered}
    # base endpoints that were already there
    for need in ("/", "/api/listings", "/api/stats"):
        assert need in paths, f"route {need} not registered"
    assert "index" in handlers and "api_listings" in handlers
    # phase B + C endpoints
    for need in ("/api/my/listings", "/api/listing/{id}/action", "/api/listing", "/api/listing/{id}/photos"):
        assert need in paths, f"route {need} not registered"
    for h in ("api_my_listings", "api_listing_action", "api_create_listing", "api_upload_photos"):
        assert h in handlers, f"handler {h} not wired"
    # saved-search endpoints (screen 08)
    for need in ("/api/search/alert", "/api/search/alerts", "/api/search/alert/{id}/delete"):
        assert need in paths, f"route {need} not registered"
    for h in ("api_create_search_alert", "api_search_alerts", "api_delete_search_alert"):
        assert h in handlers, f"handler {h} not wired"
    print("PASS  test_routes_registered")


def test_saved_search_ui():
    """The app must expose live search + the subscribe-to-search loop in the UI."""
    html = _read(STATIC)
    for el in ("followBtn", "favChip", "mySubs", "sort", "q"):
        assert f'id="{el}"' in html, f"search UI missing {el}"
    for fn in ("function toggleFollow(", "function renderMySubs(", "function alertKey(", "/api/search/alert"):
        assert fn in html, f"saved-search JS missing {fn}"
    # free-text + sort must reach the API through params()
    assert 'p.set("q"' in html and '"sort"' in html
    print("PASS  test_saved_search_ui")


def test_submit_form_native():
    """Phase C: «Подать» is a real 3-step form, not a hand-off to the bot."""
    html = _read(STATIC)
    assert 'id="subForm"' in html
    for el in ("subDistrict", "subPrice", "subPhone", "subDesc", "subNext"):
        assert f'id="{el}"' in html, f"submit form missing {el}"
    assert "function subNext(" in html and '"/api/listing"' in html
    print("PASS  test_submit_form_native")


def test_write_endpoints_require_auth():
    """Every mutation handler must consult auth (the plan's security line: 401 without initData)."""
    src = _read(os.path.join(WEB_DIR, "app.py"))
    # find each POST handler body and confirm it references auth_user (or is the webhook)
    for m in re.finditer(r"app\.router\.add_post\([^,]+,\s*(\w+)\)", src):
        name = m.group(1)
        body = _handler_body(src, name)
        assert ("auth_user" in body) or name.startswith("webhook"), (
            f"POST handler {name} writes without auth_user()")
    print("PASS  test_write_endpoints_require_auth")


def _handler_body(src: str, name: str) -> str:
    i = src.find(f"async def {name}(")
    if i < 0:
        return ""
    j = src.find("\nasync def ", i + 1)
    k = src.find("\n# ---", i)
    end = min(x for x in (j if j > 0 else len(src), k if k > 0 else len(src), len(src)))
    return src[i:end]


# ---------- Phase B: endpoint behaviour on a throwaway sqlite ----------------

class _FakeReq:
    """Minimal stand-in for aiohttp.web.Request: only what the handlers touch."""
    def __init__(self, headers=None, app=None, match_info=None, body=None, post=None):
        self.headers = headers or {}
        self.app = app or {}
        self.match_info = match_info or {}
        self._body = body
        self._post = post

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body

    async def post(self):
        if self._post is None:
            raise ValueError("no multipart")
        return self._post


def _signed_init_data(user_id: int, token: str) -> str:
    """Build a Telegram-valid initData string (HMAC-SHA256, per the WebApp docs)."""
    payload = {"auth_date": str(int(time.time())),
               "user": json.dumps({"id": user_id, "first_name": "Test"})}
    data_check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**payload, "hash": h})


def _jresp(resp):
    return resp.status, json.loads(resp.body)


async def _endpoint_tests():
    import aiosqlite
    import app as webapp  # bot/webapp/app.py (WEB_DIR is on sys.path)
    webapp.BOT_TOKEN = "test-token"
    import config
    config.ADMIN_IDS = []  # keep the admin notify a no-op (no network) in tests

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = None
    try:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        import db
        await db.ensure_schema(conn)
        # owner (tg_id 111) + a stranger (222); one active listing owned by 111
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (111,'o','O')")
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (222,'s','S')")
        await conn.execute(
            "INSERT INTO listings (user_id, deal, ptype, price, currency, district, status, "
            "photos, expires_at) VALUES (1,'rent','apartment',500,'EUR','Centr','active','[]',"
            "datetime('now','+30 days'))")
        await conn.commit()
        app = {"db": conn}

        # 1. auth gate: no init data -> 401 on both new endpoints
        st, _ = _jresp(await webapp.api_my_listings(_FakeReq(headers={}, app=app)))
        assert st == 401, f"my/listings without auth should be 401, got {st}"
        st, _ = _jresp(await webapp.api_listing_action(
            _FakeReq(headers={}, app=app, match_info={"id": "1"}, body={"action": "sold"})))
        assert st == 401, f"action without auth should be 401, got {st}"

        # 2. owner sees their listing
        owner_hdr = {"X-Init-Data": _signed_init_data(111, "test-token")}
        st, data = _jresp(await webapp.api_my_listings(_FakeReq(headers=owner_hdr, app=app)))
        assert st == 200 and len(data["items"]) == 1 and data["items"][0]["status"] == "active", data

        # 3. stranger cannot act on someone else's ad -> 403
        other_hdr = {"X-Init-Data": _signed_init_data(222, "test-token")}
        st, _ = _jresp(await webapp.api_listing_action(
            _FakeReq(headers=other_hdr, app=app, match_info={"id": "1"}, body={"action": "sold"})))
        assert st == 403, f"non-owner action should be 403, got {st}"

        # 4. bad action -> 400
        st, _ = _jresp(await webapp.api_listing_action(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": "1"}, body={"action": "nuke"})))
        assert st == 400, f"bad action should be 400, got {st}"

        # 5. sold -> archived (soft)
        st, data = _jresp(await webapp.api_listing_action(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": "1"}, body={"action": "sold"})))
        assert st == 200 and data["status"] == "archived", data

        # 6. extend revives to active with a future expiry (reversible archive)
        st, data = _jresp(await webapp.api_listing_action(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": "1"}, body={"action": "extend"})))
        assert st == 200 and data["status"] == "active" and data["expires_at"], data
        cur = await conn.execute("SELECT COUNT(*) c FROM listings WHERE id=1")
        assert (await cur.fetchone())["c"] == 1, "archive must be soft (row kept)"

        # ---------- Phase C: submit a listing ----------
        good = {"deal": "rent", "rooms": "2", "district": "\u0426\u0435\u043d\u0442\u0440", "price": " 450 ",
                "phone": "+373 60 123 456", "description": "Светлая"}
        # no auth -> 401
        st, _ = _jresp(await webapp.api_create_listing(_FakeReq(headers={}, app=app, body=good)))
        assert st == 401, f"create without auth should be 401, got {st}"
        # bad deal -> 400
        st, _ = _jresp(await webapp.api_create_listing(_FakeReq(headers=owner_hdr, app=app, body={**good, "deal": "x"})))
        assert st == 400, f"bad deal should be 400, got {st}"
        # price missing -> 400
        st, _ = _jresp(await webapp.api_create_listing(_FakeReq(headers=owner_hdr, app=app, body={**good, "price": ""})))
        assert st == 400, f"missing price should be 400, got {st}"
        # phone missing -> 400
        st, _ = _jresp(await webapp.api_create_listing(_FakeReq(headers=owner_hdr, app=app, body={**good, "phone": "нет"})))
        assert st == 400, f"missing phone should be 400, got {st}"
        # valid -> pending row owned by tg 111
        st, data = _jresp(await webapp.api_create_listing(_FakeReq(headers=owner_hdr, app=app, body=good)))
        assert st == 200 and data["status"] == "pending" and data["id"], data
        cur = await conn.execute("SELECT user_id, status, price FROM listings WHERE id = ?", (data["id"],))
        r = await cur.fetchone()
        assert r["user_id"] == 1 and r["status"] == "pending" and r["price"] == 450, dict(r)
        # rate limit -> 429 once the owner has a pile of fresh pending ads
        for _ in range(6):
            await conn.execute(
                "INSERT INTO listings (user_id, deal, ptype, price, currency, district, status) "
                "VALUES (1,'rent','apartment',100,'EUR','x','pending')")
        await conn.commit()
        st, _ = _jresp(await webapp.api_create_listing(_FakeReq(headers=owner_hdr, app=app, body=good)))
        assert st == 429, f"rate limit should be 429, got {st}"
        print("PASS  test_endpoints_phase_c_create")

        await conn.close()
        print("PASS  test_endpoints_phase_b")
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


class _Multi:
    """Fake aiohttp multipart result: getall('photos') -> list of FileField-like."""
    def __init__(self, fields):
        self._f = fields
    def getall(self, name, default=None):
        return list(self._f) if name == "photos" else (default or [])


class _FileField:
    def __init__(self, raw, content_type):
        self.file = io.BytesIO(raw)
        self.content_type = content_type


def _tiny_jpeg():
    from PIL import Image as _Img
    buf = io.BytesIO()
    _Img.new("RGB", (8, 8), (120, 180, 220)).save(buf, format="JPEG")
    return buf.getvalue()


def test_alert_match():
    """The pure scheduler rule that decides if a new ad satisfies a saved search."""
    try:
        import scheduler
    except ModuleNotFoundError as e:
        print(f"SKIP  test_alert_match ({e.name} not installed locally)")
        return
    m = scheduler._alert_match
    listing = {"deal": "rent", "district": "\u0426\u0435\u043d\u0442\u0440", "rooms": 2, "price": 450,
               "description": "\u0441 \u0431\u0430\u043b\u043a\u043e\u043d\u043e\u043c", "user_id": 1, "id": 5}
    any_ = {"deal": None, "district": None, "rooms": None, "price_max": None, "query": None}
    assert m(any_, listing), "empty filter matches everything"
    assert m({**any_, "deal": "rent"}, listing)
    assert not m({**any_, "deal": "sale"}, listing), "wrong deal excluded"
    assert m({**any_, "district": "\u0426\u0415\u041d\u0422\u0420"}, listing), "district is case-insensitive"
    assert not m({**any_, "district": "\u0411\u043e\u0442\u0430\u043d\u0438\u043a\u0430"}, listing)
    assert m({**any_, "rooms": 3}, {**listing, "rooms": 4}), "3+ bucket matches 4 rooms"
    assert not m({**any_, "rooms": 3}, {**listing, "rooms": 2}), "3+ excludes 2 rooms"
    assert not m({**any_, "rooms": 1}, listing), "exact 1 != 2"
    assert m({**any_, "price_max": 500}, listing)
    assert not m({**any_, "price_max": 400}, listing), "over the ceiling excluded"
    assert m({**any_, "query": "\u0431\u0430\u043b\u043a\u043e\u043d"}, listing), "free text hits description"
    assert not m({**any_, "query": "\u043f\u0430\u0440\u043a\u043e\u0432\u043a\u0430"}, listing)
    print("PASS  test_alert_match")


async def _alert_tests():
    import aiosqlite
    import app as webapp
    import config
    webapp.BOT_TOKEN = "test-token"
    config.ADMIN_IDS = []

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = None
    try:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        import db
        await db.ensure_schema(conn)
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (111,'o','O')")
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (222,'s','S')")
        await conn.execute(
            "INSERT INTO listings (user_id, deal, ptype, price, currency, district, status, photos) "
            "VALUES (1,'rent','apartment',500,'EUR','Centr','active','[]')")
        await conn.commit()
        app = {"db": conn}
        owner_hdr = {"X-Init-Data": _signed_init_data(111, "test-token")}
        other_hdr = {"X-Init-Data": _signed_init_data(222, "test-token")}
        filt = {"deal": "rent", "district": "Centr", "rooms": "2", "price_max": "450", "query": "park"}

        # 1. auth gate
        st, _ = _jresp(await webapp.api_create_search_alert(_FakeReq(headers={}, app=app, body=filt)))
        assert st == 401, f"create alert without auth should be 401, got {st}"
        # 2. owner creates
        st, data = _jresp(await webapp.api_create_search_alert(_FakeReq(headers=owner_hdr, app=app, body=filt)))
        assert st == 200 and data["ok"] and not data["existed"], (st, data)
        aid = data["id"]
        # 3. identical filter re-arms instead of duplicating
        st, data = _jresp(await webapp.api_create_search_alert(_FakeReq(headers=owner_hdr, app=app, body=filt)))
        assert st == 200 and data["existed"] and data["id"] == aid, (st, data)
        # 4. last_listing_id seeded to newest listing id so only future ads ping
        cur = await conn.execute("SELECT last_listing_id FROM search_alerts WHERE id=?", (aid,))
        assert (await cur.fetchone())["last_listing_id"] == 1, "should start at the newest live id"
        # 5. list returns it
        st, data = _jresp(await webapp.api_search_alerts(_FakeReq(headers=owner_hdr, app=app)))
        assert st == 200 and len(data["items"]) == 1 and data["items"][0]["id"] == aid, (st, data)
        # 6. another user cannot delete it (owner-scoped) -> 404
        st, _ = _jresp(await webapp.api_delete_search_alert(
            _FakeReq(headers=other_hdr, app=app, match_info={"id": str(aid)})))
        assert st == 404, f"non-owner delete should be 404, got {st}"
        # 7. owner deletes
        st, data = _jresp(await webapp.api_delete_search_alert(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": str(aid)})))
        assert st == 200 and data["ok"], (st, data)
        cur = await conn.execute("SELECT COUNT(*) c FROM search_alerts")
        assert (await cur.fetchone())["c"] == 0
        print("PASS  test_endpoints_saved_search")
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


async def _photo_tests():
    import aiosqlite
    import app as webapp
    import config
    webapp.BOT_TOKEN = "test-token"
    config.ADMIN_IDS = []

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    pdir = tempfile.mkdtemp()
    webapp.PHOTOS_DIR = pdir
    config.PHOTOS_DIR = pdir
    conn = None
    try:
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        import db
        await db.ensure_schema(conn)
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (111,'o','O')")
        await conn.execute("INSERT INTO users (tg_id, username, first_name) VALUES (222,'s','S')")
        await conn.execute(
            "INSERT INTO listings (user_id, deal, ptype, price, currency, district, status, photos) "
            "VALUES (1,'rent','apartment',500,'EUR','Centr','pending','[]')")
        await conn.commit()
        app = {"db": conn}
        owner_hdr = {"X-Init-Data": _signed_init_data(111, "test-token")}
        other_hdr = {"X-Init-Data": _signed_init_data(222, "test-token")}
        jpg = _tiny_jpeg()

        # 1. auth gate
        st, _ = _jresp(await webapp.api_upload_photos(
            _FakeReq(headers={}, app=app, match_info={"id": "1"}, post=_Multi([_FileField(jpg, "image/jpeg")]))))
        assert st == 401, f"upload without auth should be 401, got {st}"
        # 2. non-owner -> 403
        st, _ = _jresp(await webapp.api_upload_photos(
            _FakeReq(headers=other_hdr, app=app, match_info={"id": "1"}, post=_Multi([_FileField(jpg, "image/jpeg")]))))
        assert st == 403, f"upload by non-owner should be 403, got {st}"
        # 3. two valid images -> files on disk + photos column updated
        st, data = _jresp(await webapp.api_upload_photos(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": "1"},
                     post=_Multi([_FileField(jpg, "image/jpeg"), _FileField(jpg, "image/jpeg")]))))
        assert st == 200 and len(data["photos"]) == 2, (st, data)
        assert os.path.isfile(os.path.join(pdir, "1_0.jpg")) and os.path.isfile(os.path.join(pdir, "1_1.jpg"))
        cur = await conn.execute("SELECT photos FROM listings WHERE id=1")
        stored = json.loads((await cur.fetchone())["photos"])
        assert stored == ["1_0.jpg", "1_1.jpg"], stored
        # 4. a non-image never gets written
        before = sorted(os.listdir(pdir))
        st, _ = _jresp(await webapp.api_upload_photos(
            _FakeReq(headers=owner_hdr, app=app, match_info={"id": "1"},
                     post=_Multi([_FileField(b"this is not a photo", "image/jpeg")]))))
        assert st == 400, f"non-image upload should be 400, got {st}"
        assert sorted(os.listdir(pdir)) == before, "invalid upload must not touch disk"
        # 5. render.resolve_photo: a stored name -> FSInputFile, a file_id -> untouched
        import render
        from aiogram.types import FSInputFile
        assert isinstance(render.resolve_photo("1_0.jpg"), FSInputFile)
        assert render.resolve_photo("AgACAgQAAx...") == "AgACAgQAAx..."
        print("PASS  test_endpoints_phase_d_photos")
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


if __name__ == "__main__":
    test_spa_tab_bar()
    test_routes_registered()
    test_saved_search_ui()
    test_submit_form_native()
    test_write_endpoints_require_auth()
    test_alert_match()
    try:
        asyncio.run(_endpoint_tests())
    except ModuleNotFoundError as e:
        print(f"SKIP  test_endpoints_phase_b ({e.name} not installed locally)")
    try:
        asyncio.run(_alert_tests())
    except ModuleNotFoundError as e:
        print(f"SKIP  test_endpoints_saved_search ({e.name} not installed locally)")
    try:
        asyncio.run(_photo_tests())
    except ModuleNotFoundError as e:
        print(f"SKIP  test_endpoints_phase_d_photos ({e.name} not installed locally)")
    print("\nwebapp smoke passed")
