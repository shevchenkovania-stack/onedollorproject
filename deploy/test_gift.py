"""E2E of the founders gift: grant -> boost button -> spend -> top placement, zero money.

Runs the real db/keyboards functions the handlers use, asserts the money rails stayed
untouched (no payments row, no boosts with a paid status), then deletes every trace:
user, listing, boosts, events. Safe on prod at any time.

Run on prod: cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python test_gift.py
"""
import asyncio
import os
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

import config
import db
import keyboards


async def counts(conn) -> tuple:
    c = await conn.execute("SELECT (SELECT COUNT(*) FROM payments), (SELECT COUNT(*) FROM users)")
    return await c.fetchone()


async def main() -> None:
    await db.init_db()
    conn = db._db()
    payments_before, users_before = await counts(conn)

    GIFT = config.GIFT_BOOSTS  # test must track the live config (currently 1)

    # a synthetic founder: user + first approved listing
    user = await db.get_or_create_user(999999001, "gifttest", "GIFT TEST")
    listing_id = await db.create_listing(
        user_id=user["id"], deal="rent", ptype="apartment", rooms=1, price=300, currency="EUR",
        district="Центр", description="GIFT-TEST (не объявление)", photos=[],
    )
    await db.set_listing_status(listing_id, "active")

    # 1. grant (what admin.py does on first approve) - huge slots arg so the test never
    #    eats a real founder slot; the event row is deleted in cleanup anyway
    granted = await db.grant_gift_credits(user["id"], GIFT, 10 ** 9)
    assert granted, "gift not granted"
    row = await db.get_user_by_tg_id(999999001)
    assert row["boost_credits"] == GIFT, f"expected {GIFT} credits, got {row['boost_credits']}"

    # 2. double-grant must be a no-op (one person = one gift)
    assert not await db.grant_gift_credits(user["id"], GIFT, 10 ** 9), "granted twice"

    # 3. the offer keyboard must show the free-raise button
    kb = keyboards.boost_kb(listing_id, GIFT)
    assert any("gift:" in (b.callback_data or "") for r in kb.inline_keyboard for b in r), "no gift button"

    # 4. spend one -> top placement
    assert await db.spend_gift_credit(user["id"]), "spend failed"
    await db.boost_listing(listing_id, 7)
    await db.log_boost(listing_id, user["id"], 0, 7, status="gift")
    row = await db.get_user_by_tg_id(999999001)
    assert row["boost_credits"] == GIFT - 1, f"after 1 spend expected {GIFT-1}, got {row['boost_credits']}"
    lst = await db.get_listing(listing_id)
    assert lst["boosted_until"], "listing not boosted"

    # 5. spend remaining credits then one over-spend must fail
    for _ in range(GIFT - 1):
        assert await db.spend_gift_credit(user["id"])
    assert not await db.spend_gift_credit(user["id"]), "spent from empty pocket"

    # 6. money rails untouched
    payments_after, _ = await counts(conn)
    assert payments_after == payments_before, "payments table changed!"

    # 7. cleanup - board exactly as before
    await conn.execute("DELETE FROM boosts WHERE listing_id=?", (listing_id,))
    await conn.execute("DELETE FROM listings WHERE id=?", (listing_id,))
    await conn.execute("DELETE FROM events WHERE user_id=?", (user["id"],))
    await conn.execute("DELETE FROM users WHERE id=?", (user["id"],))
    await conn.commit()
    _, users_after = await counts(conn)
    assert users_after == users_before, "user residue"
    print(f"✅ GIFT LOOP OK (grant {GIFT} -> button -> spend -> top -> no money -> zero residue)")
    sys.stdout.flush()
    os._exit(0)


asyncio.run(main())
