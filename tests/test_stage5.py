"""Stage-5 tests: the SQLite FSM store survives a restart, and step-3 phone parsing.

The TZ acceptance for this stage is "рестарт посреди подачи не теряет ответы" (bug #7).
We model a restart literally: open the store on a temp file, fill the wizard, close the
process, open a fresh store on the same file, and assert the state + answers come back.

Run:  python tests/test_stage5.py        (exit 1 on any failure)
"""
import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

BOT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
sys.path.insert(0, BOT_DIR)

from aiogram.fsm.context import FSMContext                  # noqa: E402
from aiogram.fsm.storage.base import StorageKey             # noqa: E402

import handlers.add as add                                  # noqa: E402
from fsm_sqlite import SqliteStorage                        # noqa: E402


def _key() -> StorageKey:
    return StorageKey(bot_id=1, chat_id=7, user_id=42)


def _fake_msg() -> MagicMock:
    m = MagicMock()
    m.edit_text = AsyncMock()
    m.answer = AsyncMock()
    return m


# ---- the whole point: state + answers come back after the process restarts ---
def test_state_survives_restart():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async def body():
        # --- first "process" ---
        s1 = SqliteStorage(path)
        await s1.start()
        ctx1 = FSMContext(storage=s1, key=_key())
        await add.begin(_fake_msg(), ctx1)               # enters step 1
        assert (await ctx1.get_state()) == "AddListing:specs"
        await ctx1.update_data(deal="rent", rooms=2, district="Центр", price=450)
        await ctx1.set_state(add.AddListing.media)       # owner is on the photos step
        await s1.close()

        # --- restart: brand new store instance, same file ---
        s2 = SqliteStorage(path)
        await s2.start()
        ctx2 = FSMContext(storage=s2, key=_key())
        assert (await ctx2.get_state()) == "AddListing:media", "wizard step was lost"
        data = await ctx2.get_data()
        assert data["deal"] == "rent" and data["rooms"] == 2
        assert data["district"] == "Центр" and data["price"] == 450
        await s2.close()
    asyncio.run(body())


# ---- update_data merges (does not clobber earlier answers) and persists ------
def test_update_data_merges():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async def body():
        s = SqliteStorage(path)
        await s.start()
        ctx = FSMContext(storage=s, key=_key())
        await ctx.update_data(deal="sale")
        await ctx.update_data(price=999)
        await ctx.update_data(photos=["f1", "f2"])
        data = await ctx.get_data()
        assert data == {"deal": "sale", "price": 999, "photos": ["f1", "f2"]}
        await s.close()

        s2 = SqliteStorage(path)
        await s2.start()
        ctx2 = FSMContext(storage=s2, key=_key())
        assert (await ctx2.get_data())["photos"] == ["f1", "f2"]
        await s2.close()
    asyncio.run(body())


# ---- clearing the form leaves nothing behind ---------------------------------
def test_clear_resets():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    async def body():
        s = SqliteStorage(path)
        await s.start()
        ctx = FSMContext(storage=s, key=_key())
        await ctx.set_state(add.AddListing.specs)
        await ctx.update_data(price=1)
        await ctx.clear()
        assert (await ctx.get_state()) is None
        assert (await ctx.get_data()) == {}
        await s.close()
    asyncio.run(body())


# ---- step 3 pulls the phone out of a free-text caption -----------------------
def test_extract_phone():
    phone, rest = add._extract_phone("+373 69 123 456\n54 м², 3/9 этаж. Свободна с 1 ноября.")
    assert phone == "+37369123456", phone
    assert "54 м" in rest and "373" not in rest

    phone, _ = add._extract_phone("Звоните 060123456")
    assert phone == "060123456", phone

    # a bare price or a room count must NOT be mistaken for a phone
    assert add._extract_phone("450")[0] is None
    assert add._extract_phone("Просторная, 2 комнаты")[0] is None
    assert add._extract_phone("")[0] is None


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
