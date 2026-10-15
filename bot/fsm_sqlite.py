"""SQLite-backed FSM storage (bug #7).

aiogram's default storage lives in process RAM, so a restart - a deploy, a crash, a
host sleep - wipes every half-filled submission form and the owner has to start over.
This keeps the wizard state in the same sqlite file the listings live in: a restart in
the middle of "подача" resumes exactly where the owner left off, because the state and
the answers are read back from disk on the next update.

Only the five abstract methods of BaseStorage are required; update_data/get_value/
clear_state/set_address are provided by the base class on top of these.
"""
import json
from typing import Any, Mapping, Optional

import aiosqlite
from aiogram.fsm.storage.base import BaseStorage, StorageKey


class SqliteStorage(BaseStorage):
    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def start(self) -> None:
        """Open (and create the table). Idempotent - safe to call once from main()."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute(
            """CREATE TABLE IF NOT EXISTS fsm_state (
                   key  TEXT PRIMARY KEY,
                   state TEXT,
                   data  TEXT NOT NULL DEFAULT '{}'
               )"""
        )
        await self._conn.commit()

    def _db(self) -> aiosqlite.Connection:
        assert self._conn is not None, "SqliteStorage.start() was not awaited"
        return self._conn

    @staticmethod
    def _key(key: StorageKey) -> str:
        # one row per (bot, chat, user, thread, destiny) - the same identity aiogram
        # uses to scope an FSM, flattened to a stable primary key.
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        await self._db().execute(
            "INSERT INTO fsm_state(key, state, data) VALUES(?, ?, '{}') "
            "ON CONFLICT(key) DO UPDATE SET state=excluded.state",
            (self._key(key), getattr(state, "state", state)))
        await self._db().commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        cur = await self._db().execute(
            "SELECT state FROM fsm_state WHERE key=?", (self._key(key),))
        row = await cur.fetchone()
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await self._db().execute(
            "INSERT INTO fsm_state(key, state, data) VALUES(?, NULL, ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (self._key(key), json.dumps(dict(data), ensure_ascii=False)))
        await self._db().commit()

    async def get_data(self, key: StorageKey) -> dict:
        cur = await self._db().execute(
            "SELECT data FROM fsm_state WHERE key=?", (self._key(key),))
        row = await cur.fetchone()
        if not row or not row["data"]:
            return {}
        try:
            loaded = json.loads(row["data"])
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
