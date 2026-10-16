"""Wallet money-path regression - stdlib + aiosqlite only, no network, no live bot.

Money code earns its keep by being provably right on the two things that can quietly lose or
fake revenue:
  1. spend_balance is atomic - a wallet can never go negative and a double-tap cannot spend a
     leu that is not there;
  2. credit_topup is idempotent - a repeated admin tick cannot mint the same lump sum twice.

Run:  python tests/test_wallet.py        (exit 1 on any failure)
"""
import asyncio
import os
import sys
import tempfile

BOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT_DIR)

# point db at a throwaway file BEFORE importing config (config binds DB_PATH at import)
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DB_PATH"] = _db_path

import db  # noqa: E402


def _fresh_db():
    """Reset the temp DB to an empty, migrated schema for one test."""
    if os.path.exists(_db_path):
        os.remove(_db_path)


async def _seed_owner(tg_id=1001):
    await db.init_db()
    user = await db.get_or_create_user(tg_id, "owner", "Owner")
    return user


def test_spend_balance_atomic():
    _fresh_db()

    async def body():
        user = await _seed_owner()
        uid = user["id"]
        await db.add_balance(uid, 5)
        assert await db.get_balance(uid) == 5
        # over-spend: refused, nothing leaves the wallet
        assert await db.spend_balance(uid, 20) is False
        assert await db.get_balance(uid) == 5
        # exact spend: allowed, drains to zero
        assert await db.spend_balance(uid, 5) is True
        assert await db.get_balance(uid) == 0
        # a second tap now has no money to take - never negative
        assert await db.spend_balance(uid, 1) is False
        assert await db.get_balance(uid) == 0
        await db.close_db()
    asyncio.run(body())


def test_credit_topup_idempotent():
    _fresh_db()

    async def body():
        user = await _seed_owner(tg_id=2002)
        uid = user["id"]
        tid = await db.create_topup(uid, 2002, 50)
        # a pending top-up is not money yet
        assert await db.get_balance(uid) == 0
        row = await db.credit_topup(tid)
        assert row is not None and row["amount_mdl"] == 50
        assert await db.get_balance(uid) == 50
        # a second tick on the same id mints nothing
        assert await db.credit_topup(tid) is None
        assert await db.get_balance(uid) == 50
        await db.close_db()
    asyncio.run(body())


def test_reject_topup_only_pending():
    _fresh_db()

    async def body():
        user = await _seed_owner(tg_id=3003)
        uid = user["id"]
        tid = await db.create_topup(uid, 3003, 30)
        assert await db.reject_topup(tid) is True
        # already terminal: cannot reject twice, cannot credit after reject
        assert await db.reject_topup(tid) is False
        assert await db.credit_topup(tid) is None
        assert await db.get_balance(uid) == 0
        await db.close_db()
    asyncio.run(body())


def test_has_published_and_feature():
    _fresh_db()

    async def body():
        user = await _seed_owner(tg_id=4004)
        uid = user["id"]
        assert await db.has_published_listing(uid) is False
        lid = await db.create_listing(user_id=uid, deal="rent", ptype="apartment", rooms=1,
                                      price=100, currency="EUR", district="Центр", phone="060123456")
        # a pending ad has not gone live yet - the free hook still applies
        assert await db.has_published_listing(uid) is False
        await db.activate_listing(lid, 7)
        assert await db.has_published_listing(uid) is True
        await db.feature_listing(lid, 7)
        row = await db.get_listing(lid)
        assert row["featured_until"] is not None
        await db.close_db()
    asyncio.run(body())


def test_alerts_gate():
    _fresh_db()

    async def body():
        user = await _seed_owner(tg_id=5005)
        uid = user["id"]
        # unsubscribed: the paid board must not push
        assert await db.is_alerts_subscribed(uid) is False
        # a first-time watcher is granted exactly one free window
        assert await db.grant_alerts_trial(uid, 30) is True
        assert await db.is_alerts_subscribed(uid) is True
        # the trial is not re-baited once they have ever been subscribed
        assert await db.grant_alerts_trial(uid, 30) is False
        # a paid subscription extends the live clock
        await db.subscribe_alerts(uid, 30)
        assert await db.is_alerts_subscribed(uid) is True
        await db.close_db()
    asyncio.run(body())


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
    if os.path.exists(_db_path):
        os.remove(_db_path)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
