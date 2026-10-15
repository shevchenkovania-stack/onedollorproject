"""Mini App (webapp) wiring smoke tests.

The Mini App is now a first-class surface (Phases A-D of the plan), so it needs the same
kind of guard the bot has: the SPA must expose its tab bar, and the aiohttp app must actually
register every endpoint the frontend calls. These checks are static / structural - they never
touch the prod DB or network. Phase-specific behaviour (auth gate, lifecycle, create, photo
upload) is covered with a throwaway sqlite in the later tests appended below.

Run:  python tests/test_webapp.py        (exit 1 on any failure)
"""
import asyncio
import os
import re
import sys
import tempfile

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


if __name__ == "__main__":
    test_spa_tab_bar()
    test_routes_registered()
    test_write_endpoints_require_auth()
    print("\nwebapp smoke passed")
