"""Stage-2 bug regression tests - stdlib only, no network, no live bot.

Run:  python tests/run_tests.py        (exit 1 if any bug is back)
Each test reproduces a bug from the TZ "Баги" list and asserts the FIXED behaviour.
They import the real bot modules and monkeypatch config/db/bot in memory only.
"""
import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

BOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT_DIR)

import config          # noqa: E402
import db              # noqa: E402
import keyboards       # noqa: E402
import publish         # noqa: E402
import main as botmain # noqa: E402
from handlers import admin, fallback  # noqa: E402


def _row(**over):
    base = dict(id=7, user_id=1, deal="rent", ptype="apartment", rooms=1, price=450,
                currency="EUR", district="Центр", area=None, floor=None, floors=None,
                phone=None, description="hi", photos="[]", contacts="{}",
                status="active", boosted_until=None, views=0)
    base.update(over)
    return base


# ---- bug #8: Stars rail hidden when BOOST_STARS=0 ---------------------------
def test_bug8_stars_flag():
    config.CARD_ENABLED = False
    config.BOOST_MDL = 100
    config.PAY_PHONE = ""
    config.BOOST_STARS = 0
    assert "Stars" not in keyboards.rails_line(), "rails_line must not mention Stars when off"
    kb = keyboards.boost_kb(5)
    assert not any((b.callback_data or "").startswith("boostpay:")
                   for row in kb.inline_keyboard for b in row), "no Stars button when off"
    config.BOOST_STARS = 400
    assert "Stars" in keyboards.rails_line(), "rails_line must show Stars when on"
    kb = keyboards.boost_kb(5)
    assert any((b.callback_data or "").startswith("boostpay:")
               for row in kb.inline_keyboard for b in row), "Stars button present when on"


# ---- bug #1: phone is HTML-escaped in the channel caption -------------------
def test_bug1_phone_escaped():
    config.CHANNEL_ID = "@testchan"
    config.BOT_URL = ""
    config.WEBAPP_URL = ""
    orig = db.get_user_by_id
    orig_evt = db.log_event
    db.get_user_by_id = AsyncMock(return_value=None)
    db.log_event = AsyncMock(return_value=None)  # to_channel logs a channel_post event; no live db
    try:
        bot = AsyncMock()
        bot.send_message.return_value = types.SimpleNamespace(message_id=99)
        row = _row(phone="<b>&x")
        ids = asyncio.run(publish.to_channel(bot, row))
        assert ids == [99]
        caption = bot.send_message.call_args.args[1]
        assert "&lt;b&gt;&amp;x" in caption, "phone must be escaped in caption"
        assert "<b>&x" not in caption, "raw phone markup must not leak into caption"
    finally:
        db.get_user_by_id = orig
        db.log_event = orig_evt


# ---- bug #2: moderation is idempotent (no double publish) -------------------
def test_bug2_moderate_idempotent():
    config.ADMIN_IDS = [1]
    orig_get = db.get_listing
    orig_pub = publish.publish_listing
    db.get_listing = AsyncMock(return_value=_row(status="active"))
    publish.publish_listing = AsyncMock(side_effect=AssertionError("must not publish twice"))
    try:
        cb = MagicMock()
        cb.from_user = types.SimpleNamespace(id=1, username="adm")
        cb.data = "mod:approve:7"
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.answer = AsyncMock()
        cb.message.edit_reply_markup = AsyncMock()
        asyncio.run(admin.moderate(cb, AsyncMock()))
        answers = " ".join(a.args[0] for a in cb.message.answer.await_args_list)
        assert "Уже обработано" in answers, "second approve must be refused"
    finally:
        db.get_listing = orig_get
        publish.publish_listing = orig_pub


# ---- bug #5: fallback throttles a single user -------------------------------
def test_bug5_fallback_throttle():
    config.ADMIN_IDS = [1]
    fallback._LAST_FWD.clear()
    bot = AsyncMock()

    def msg():
        m = MagicMock()
        m.from_user = types.SimpleNamespace(id=42, username=None)
        m.text = "привет"
        m.answer = AsyncMock()
        return m

    asyncio.run(fallback.forward_to_admins(msg(), bot))
    first = bot.send_message.await_count
    asyncio.run(fallback.forward_to_admins(msg(), bot))  # immediate second -> throttled
    assert bot.send_message.await_count == first, "second message within window must be dropped"
    # private-only guard is declarative; assert it is wired on the route
    src = open(os.path.join(BOT_DIR, "handlers", "fallback.py"), encoding="utf-8").read()
    assert 'F.chat.type == "private"' in src, "fallback must filter to private chats"


# ---- bug #6: /stats is scoped to admins only --------------------------------
def test_bug6_stats_scope():
    from aiogram.types import BotCommandScopeChat
    config.ADMIN_IDS = [1, 2]
    bot = AsyncMock()
    asyncio.run(botmain.set_commands(bot))
    calls = bot.set_my_commands.await_args_list
    assert len(calls) == 1 + 2, "one default + one per admin"
    default_names = [c.command for c in calls[0].args[0]]
    assert "stats" not in default_names, "public menu must not carry /stats"
    for c in calls[1:]:
        names = [b.command for b in c.args[0]]
        assert "stats" in names and isinstance(c.kwargs.get("scope"), BotCommandScopeChat)


# ---- bug #3: api_listing no longer leaks owner_tg ---------------------------
def test_bug3_no_owner_tg_leak():
    src = open(os.path.join(BOT_DIR, "webapp", "app.py"), encoding="utf-8").read()
    assert 'item["owner_tg"]' not in src, "owner_tg must not be returned by api_listing"
    assert 'item["is_owner"]' in src, "api_listing must return a boolean is_owner instead"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
