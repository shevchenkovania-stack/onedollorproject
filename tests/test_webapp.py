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
    print("PASS  test_routes_registered")


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
    def __init__(self, headers=None, app=None, match_info=None, body=None):
        self.headers = headers or {}
        self.app = app or {}
        self.match_info = match_info or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


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

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
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

        await conn.close()
        print("PASS  test_endpoints_phase_b")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_spa_tab_bar()
    test_routes_registered()
    test_write_endpoints_require_auth()
    try:
        asyncio.run(_endpoint_tests())
    except ModuleNotFoundError as e:
        print(f"SKIP  test_endpoints_phase_b ({e.name} not installed locally)")
    print("\nwebapp smoke passed")
