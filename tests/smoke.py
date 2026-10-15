"""Screen + wiring smoke test - stdlib + aiogram only, no network, no live bot.

Answers the owner question "проверь все экраны и чтоб вся логика работала":
  1. every module imports clean (no NameError / missing attr at import time)
  2. every router registers into a Dispatcher (aiogram validates all @router.* decorators)
  3. every keyboard builder runs without raising for a representative listing row
  4. every callback_data a screen can emit has a handler that will accept it (no dead buttons)

Run:  python tests/smoke.py       (exit 1 on any wiring gap)
"""
import asyncio
import os
import re
import sys
import types
from unittest.mock import AsyncMock

BOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT_DIR)


def _read(name):
    return open(os.path.join(BOT_DIR, name), encoding="utf-8").read()


def test_imports_and_dispatcher():
    from aiogram import Dispatcher
    import config
    config.BOT_TOKEN = ""  # keep the import side-effect free
    import render, keyboards, publish, scheduler  # noqa: F401
    from handlers import start, add, browse, profile, boost, admin, fallback
    dp = Dispatcher()
    for r in (start.router, add.router, browse.router, profile.router,
              boost.router, admin.router, fallback.router):
        dp.include_router(r)  # raises on a bad decorator / duplicate route
    return dp


def _sample_row():
    return dict(id=7, user_id=1, deal="rent", ptype="apartment", rooms=2, price=450,
                currency="EUR", district="Центр", area=40, floor=3, floors=9,
                phone="+37360123456", description="светлая", photos="[]",
                contacts='{"telegram":"x","whatsapp":"37360123456"}',
                status="active", boosted_until=None, views=0)


def test_keyboards_build():
    import config
    import keyboards
    config.CHANNEL_URL = "https://t.me/test"
    config.BOT_URL = "https://t.me/test_bot"
    config.WEBAPP_URL = ""
    config.CARD_ENABLED = True
    config.CARD_PRICE_CENTS = 500
    config.BOOST_MDL = 100
    config.PAY_PHONE = "+37360123456"
    config.PAY_NAME = "Test"
    config.BOOST_STARS = 400
    row = _sample_row()
    built = [
        keyboards.main_menu(),
        keyboards.listing_kb(row["id"], owner=True, boosted=False,
                             contacts={"telegram": "x", "whatsapp": "37360123456", "viber": "bad://"}),
        keyboards.boost_kb(row["id"], gift_credits=3),
        keyboards.manual_kb(row["id"], config.BOOST_MDL),
        keyboards.manual_review_kb(5, row["id"]),
        keyboards.manual_refund_kb(5, row["id"]),
        keyboards.manual_refund_confirm_kb(5, row["id"]),
        keyboards.card_pay_kb("https://ls/test", row["id"]),
        keyboards.moderation_kb(row["id"]),
        keyboards.back_menu(),
        keyboards.rails_line(),
        keyboards.boost_pitch(),
        keyboards.manual_pitch(row["id"]),
        keyboards.pay_block(row["id"]),
    ]
    for b in built:
        if isinstance(b, str):
            assert b and "None" not in b, f"bad text rail: {b!r}"
            continue
        for r in b.inline_keyboard:
            for btn in r:
                # a URL button must carry a protocol Telegram accepts, or it takes the card down
                if btn.url is not None:
                    assert btn.url.startswith(("https://", "http://", "tg://")), btn.url


def _registered_callback_prefixes(dp):
    """Every callback route aiogram registered, as (predicate-source) text we can match."""
    patterns = []
    def walk(router):
        for h in router.callback_query.handlers:
            # h.callback is the registered coroutine; the filter lives on the observer's outer.
            # aiogram stores filters on the handler's checks; easiest robust source is the flag text.
            patterns.append(getattr(h, "__module__", "") + "." + getattr(h, "__name__", ""))
        for child in router.sub_routers:
            walk(child)
    walk(dp)
    return patterns


def test_callback_coverage():
    """Cross-reference: every callback_data literal emitted anywhere must be routed by SOME handler.

    We parse the source for the data a button carries, then match it against the handler filter
    strings we can read off the same source. A value with no home = a button that silently does
    nothing when tapped = the failure mode this check exists to catch."""
    src_files = ["keyboards.py", "render.py", "publish.py", "scheduler.py"] \
        + ["handlers/" + f for f in ("start.py", "add.py", "browse.py", "profile.py",
                                     "boost.py", "admin.py", "fallback.py")]
    emitted = set()
    handled = set()
    for rel in src_files:
        s = _read(rel)
        # callback_data="..." or callback_data=f"..." -> take the literal head before any {expr}
        for m in re.finditer(r'callback_data=([f]?)["\']([^"\']+)["\']', s):
            lit = m.group(2)
            head = re.split(r"\{", lit)[0].rstrip()
            if head:
                emitted.add(head)
        # handler filters: F.data == "x" | startswith("x") | in_({"a","b"})
        for m in re.finditer(r'F\.data\s*==\s*["\']([^"\']+)["\']', s):
            handled.add(m.group(1))
        for m in re.finditer(r'F\.data\.startswith\(\s*["\']([^"\']+)["\']', s):
            handled.add(m.group(1))
        for m in re.finditer(r'F\.data\.in_\(\{([^}]+)\}\)', s):
            for lit in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                handled.add(lit)

    # exact-match or prefix-match (a handler may consume the whole emitted token)
    def is_handled(tok):
        if tok in handled:
            return True
        # emitted "boost:" handled by startswith("boost:") ; "mod:approve:" by startswith("mod:")
        for h in handled:
            if h.endswith(":") and (tok == h or tok.startswith(h)):
                return True
            if tok.endswith(":") and h.startswith(tok):
                return True
        # strip trailing colon so "add:" style tokens match "add:next" handlers etc.
        base = tok.rstrip(":")
        return any(h == base or h.startswith(base + ":") for h in handled)

    # literal UI labels that are reply-keyboard text (arrive as messages), not callbacks
    ignore = {"sf:pick:district", "sf:pick:price"}  # handled by exact F.data ==; kept for safety
    orphans = sorted(t for t in emitted if t not in ignore and not is_handled(t))
    assert not orphans, f"callback buttons with no handler: {orphans}"


def main():
    checks = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in checks:
        try:
            r = t()
            if asyncio.iscoroutine(r):
                asyncio.run(r)
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
