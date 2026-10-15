"""Stage-4 life-cycle tests: expiry, auto-archive, extend, reminders.

These run against a THROWAWAY sqlite file (never the prod DB) and substitute time by
writing expires_at in the past / near future directly - the TZ acceptance is "after 30
days the status becomes archived", which is exactly what a past expires_at models.

Run:  python tests/test_stage4.py        (exit 1 on any failure)
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock

BOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT_DIR)

import config  # noqa: E402
import db      # noqa: E402
import jobs    # noqa: E402


async def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await db.close_db()
    db.DB_PATH = path
    await db.init_db()
    return path


async def _seed(tg_id: int) -> int:
    user = await db.get_or_create_user(tg_id, f"u{tg_id}", "Test")
    return user["id"]


async def _listing(user_id: int, status: str, expires_offset: str | None) -> int:
    lid = await db.create_listing(user_id=user_id, deal="rent", ptype="apartment", rooms=2,
                                  price=450, currency="EUR", district="Центр", phone="+3731")
    await db._db().execute("UPDATE listings SET status='active' WHERE id=?", (lid,))
    if status != "active":
        await db._db().execute("UPDATE listings SET status=? WHERE id=?", (status, lid))
    if expires_offset is not None:
        await db._db().execute(
            "UPDATE listings SET expires_at=datetime('now', ?) WHERE id=?", (expires_offset, lid))
    else:
        await db._db().execute("UPDATE listings SET expires_at=NULL WHERE id=?", (lid,))
    await db._db().commit()
    return lid


# ---- activate puts a real end date on the board -----------------------------
def test_activate_sets_expiry():
    async def body():
        await _fresh_db()
        uid = await _seed(1001)
        lid = await db.create_listing(user_id=uid, deal="rent", ptype="apartment", rooms=1,
                                      price=300, currency="EUR", district="Центр")
        await db.activate_listing(lid, 30)
        row = await db.get_listing(lid)
        assert row["status"] == "active"
        days = await db._db().execute_fetchall(
            "SELECT CAST((julianday(expires_at) - julianday('now')) AS INTEGER) d "
            "FROM listings WHERE id=?", (lid,))
        assert 29 <= days[0][0] <= 30, f"expires_at should be ~30 days out, got {days[0][0]}"
    asyncio.run(body())


# ---- the archiver: past expiry -> archived, future stays live ---------------
def test_archive_expired():
    async def body():
        await _fresh_db()
        uid = await _seed(2001)
        gone = await _listing(uid, "active", "-1 day")
        keep = await _listing(uid, "active", "+10 days")
        forever = await _listing(uid, "active", None)
        archived = await db.archive_expired()
        ids = {r["id"] for r in archived}
        assert ids == {gone}, f"only the past-due ad archives, got {ids}"
        assert (await db.get_listing(gone))["status"] == "archived"
        assert (await db.get_listing(keep))["status"] == "active"
        assert (await db.get_listing(forever))["status"] == "active"
    asyncio.run(body())


# ---- an expired ad must not show in search even before the job runs ---------
def test_search_hides_expired():
    async def body():
        await _fresh_db()
        uid = await _seed(3001)
        expired = await _listing(uid, "active", "-1 hour")
        fresh = await _listing(uid, "active", "+5 days")
        rows = await db.search_listings()
        seen = {r["id"] for r in rows}
        assert fresh in seen and expired not in seen, "expired-but-not-yet-arched ad must be hidden"
        assert await db.count_search_listings() == 1
    asyncio.run(body())


# ---- Продлить revives an archived ad with a fresh window --------------------
def test_extend_reactivates():
    async def body():
        await _fresh_db()
        uid = await _seed(4001)
        lid = await _listing(uid, "archived", "-3 days")
        await db.extend_listing(lid, config.LISTING_DAYS)
        row = await db.get_listing(lid)
        assert row["status"] == "active"
        d = await db._db().execute_fetchall(
            "SELECT CAST((julianday(expires_at) - julianday('now')) AS INTEGER) FROM listings WHERE id=?",
            (lid,))
        assert 29 <= d[0][0] <= 30
    asyncio.run(body())


# ---- the expiring-soon feed only catches the reminder window ----------------
def test_expiring_in_window():
    async def body():
        await _fresh_db()
        uid = await _seed(5001)
        soon = await _listing(uid, "active", "+3 days")
        later = await _listing(uid, "active", "+20 days")
        rows = await db.listings_expiring_in(config.EXPIRE_REMINDER_DAYS)
        ids = {r["id"] for r in rows}
        assert soon in ids and later not in ids
    asyncio.run(body())


# ---- the hourly job archives + reminds once (dedup) and messages owners ------
def test_job_archive_and_remind_dedup():
    async def body():
        await _fresh_db()
        uid = await _seed(6001)
        expired = await _listing(uid, "active", "-1 day")
        soon = await _listing(uid, "active", "+2 days")
        bot = AsyncMock()
        archived, reminded = await jobs.archive_and_remind(bot)
        assert archived == 1 and reminded == 1, (archived, reminded)
        assert (await db.get_listing(expired))["status"] == "archived"
        assert bot.send_message.await_count == 2, "one archive notice + one reminder"
        # second pass: expired already archived, reminder deduped by events -> nothing new
        archived2, reminded2 = await jobs.archive_and_remind(bot)
        assert archived2 == 0 and reminded2 == 0, (archived2, reminded2)
        assert soon is not None
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
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
