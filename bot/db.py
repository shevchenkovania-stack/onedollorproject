import json
from typing import Any, Optional

import aiosqlite

from config import DB_PATH

_conn: Optional[aiosqlite.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    phone TEXT,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    deal TEXT NOT NULL,
    ptype TEXT NOT NULL,
    rooms INTEGER,
    price INTEGER NOT NULL,
    currency TEXT DEFAULT 'EUR',
    district TEXT,
    area REAL,
    floor INTEGER,
    floors INTEGER,
    phone TEXT,
    description TEXT,
    photos TEXT DEFAULT '[]',
    status TEXT DEFAULT 'pending',
    boosted_until TEXT,
    contacts TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    views INTEGER DEFAULT 0,
    -- Stage 4: when this active ad leaves the board on its own (set on approval)
    expires_at TEXT,
    -- cold-start display board: a seeded example (1), not a real owner's ad (0)
    is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status);
CREATE INDEX IF NOT EXISTS idx_listings_district ON listings(district);

CREATE TABLE IF NOT EXISTS boosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    stars INTEGER NOT NULL,
    days INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    -- what paid for this boost: 'ls:<order_id>' / 'tg:<charge_id>' / 'manual:<boost_id>'.
    -- Without it a refund cannot be traced back to the boost it must switch off.
    order_ref TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    listing_id INTEGER,
    purpose TEXT,
    stars INTEGER,
    currency TEXT,
    telegram_charge_id TEXT,
    provider_charge_id TEXT,
    -- set once the provider told us the money went back; refunded money is not revenue
    refunded_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    user_id INTEGER,
    listing_id INTEGER,
    meta TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Inbox for card payments: the webhook (webapp process) only stores what the provider told us,
-- the bot process applies money/boost logic (single writer, idempotent by order id).
CREATE TABLE IF NOT EXISTS card_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    listing_id INTEGER,
    tg_id INTEGER,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    test_mode INTEGER NOT NULL DEFAULT 0,
    customer TEXT,
    applied INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Saved search: a Mini App user watches a filter set and the bot pushes new matching ads to them.
-- Written by the webapp process, read by the scheduler (single writer for the push). last_listing_id
-- is the newest listing id already delivered, so a subscriber never sees the same ad twice.
CREATE TABLE IF NOT EXISTS search_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tg_id INTEGER NOT NULL,
    deal TEXT,
    district TEXT,
    rooms INTEGER,
    price_max INTEGER,
    query TEXT,
    last_listing_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alerts_user ON search_alerts(user_id);

-- Balance top-up inbox: the buyer reports an MIA/card transfer for a lump sum, an admin (or the
-- card webhook) ticks it, and only then does the money land in users.balance_mdl. Kept separate
-- from `boosts` because a top-up has no listing behind it. Idempotent by the status transition.
CREATE TABLE IF NOT EXISTS topups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    tg_id INTEGER NOT NULL,
    amount_mdl INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    global _conn
    _conn = await aiosqlite.connect(DB_PATH)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.executescript(SCHEMA)
    await _migrate()
    await _conn.commit()


async def _migrate() -> None:
    """Additive column migrations for tables created before the column existed."""
    assert _conn is not None
    cur = await _conn.execute("PRAGMA table_info(users)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "boost_credits" not in cols:
        # free top-raises granted to the first listing publishers (founders gift)
        await _conn.execute("ALTER TABLE users ADD COLUMN boost_credits INTEGER NOT NULL DEFAULT 0")
    if "balance_mdl" not in cols:
        # the small MDL wallet every paid service draws from (see config PAID_ENABLED)
        await _conn.execute("ALTER TABLE users ADD COLUMN balance_mdl INTEGER NOT NULL DEFAULT 0")
    if "alerts_until" not in cols:
        # a saved-search subscription is paid: alerts push only while now < alerts_until
        await _conn.execute("ALTER TABLE users ADD COLUMN alerts_until TEXT")
    if "founding" not in cols:
        # founding-member slot (1..FOUNDING_SLOTS) stamped on the first pack buyers; 0 = not a member
        await _conn.execute("ALTER TABLE users ADD COLUMN founding INTEGER NOT NULL DEFAULT 0")
    cur = await _conn.execute("PRAGMA table_info(listings)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "featured_until" not in cols:
        # the paid "Срочно" badge: a highlighted ad leads the board with a fire label
        await _conn.execute("ALTER TABLE listings ADD COLUMN featured_until TEXT")
    if "nudged_at" not in cols:
        await _conn.execute("ALTER TABLE listings ADD COLUMN nudged_at TEXT")
    if "contacts" not in cols:
        # owner-set public channels: JSON {telegram, instagram, whatsapp, viber, facebook, site}
        await _conn.execute("ALTER TABLE listings ADD COLUMN contacts TEXT DEFAULT NULL")
    if "expires_at" not in cols:
        # Stage 4: ads leave the board on their own date instead of living forever
        await _conn.execute("ALTER TABLE listings ADD COLUMN expires_at TEXT")
    if "is_demo" not in cols:
        # cold-start display board: a seeded example, not a real owner's ad (badge + honesty)
        await _conn.execute("ALTER TABLE listings ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    cur = await _conn.execute("PRAGMA table_info(payments)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "amount" not in cols:
        # manual bank transfers are priced in real MDL, not Stars
        await _conn.execute("ALTER TABLE payments ADD COLUMN amount REAL")
    if "is_test" not in cols:
        # provider test-mode orders are real rows but never real revenue
        await _conn.execute("ALTER TABLE payments ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0")
    if "refunded_at" not in cols:
        # money the provider sent back must leave REAL_REVENUE, but the audit row stays
        await _conn.execute("ALTER TABLE payments ADD COLUMN refunded_at TEXT")
    cur = await _conn.execute("PRAGMA table_info(boosts)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "order_ref" not in cols:
        # without this a refund cannot be traced back to the boost it must switch off
        await _conn.execute("ALTER TABLE boosts ADD COLUMN order_ref TEXT")
    cur = await _conn.execute("PRAGMA table_info(topups)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "credit_mdl" not in cols:
        # promo leu to grant, when it differs from the money received ($1 pack credits 10x)
        await _conn.execute("ALTER TABLE topups ADD COLUMN credit_mdl INTEGER")
    if "kind" not in cols:
        # 'wallet' = a plain top-up; 'pack' = the $1 promotion bundle (drives the founding badge)
        await _conn.execute("ALTER TABLE topups ADD COLUMN kind TEXT NOT NULL DEFAULT 'wallet'")


async def close_db() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def ensure_schema(conn: aiosqlite.Connection) -> None:
    """Create tables on a foreign connection.

    The Mini App process writes the card payment inbox, so it must not depend on the bot
    having created the schema first. All statements are IF NOT EXISTS -> safe to re-run.
    Column migrations for the additive fields are handled here too (the bot runs them in
    _migrate); if the webapp restarts before the bot, the schema must still be ready.
    """
    await conn.executescript(SCHEMA)
    cur = await conn.execute("PRAGMA table_info(listings)")
    cols = {r["name"] for r in await cur.fetchall()}
    if "contacts" not in cols:
        await conn.execute("ALTER TABLE listings ADD COLUMN contacts TEXT DEFAULT NULL")
    if "expires_at" not in cols:
        await conn.execute("ALTER TABLE listings ADD COLUMN expires_at TEXT")
    if "featured_until" not in cols:
        await conn.execute("ALTER TABLE listings ADD COLUMN featured_until TEXT")
    if "is_demo" not in cols:
        await conn.execute("ALTER TABLE listings ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    cur = await conn.execute("PRAGMA table_info(users)")
    ucols = {r["name"] for r in await cur.fetchall()}
    if "balance_mdl" not in ucols:
        await conn.execute("ALTER TABLE users ADD COLUMN balance_mdl INTEGER NOT NULL DEFAULT 0")
    if "alerts_until" not in ucols:
        await conn.execute("ALTER TABLE users ADD COLUMN alerts_until TEXT")
    await conn.commit()


def _db() -> aiosqlite.Connection:
    assert _conn is not None, "DB not initialized"
    return _conn


async def all_search_alerts():
    """Every saved-search row. The scheduler matches them in Python (the rules mirror
    api_listings: deal / exact district / rooms bucket / price ceiling / free-text query)."""
    cur = await _db().execute("SELECT * FROM search_alerts")
    return await cur.fetchall()


async def touch_search_alert(alert_id: int, listing_id: int) -> None:
    """Remember the newest listing already delivered to this subscriber, so it is never re-sent."""
    await _db().execute("UPDATE search_alerts SET last_listing_id = ? WHERE id = ? AND last_listing_id < ?",
                        (listing_id, alert_id, listing_id))
    await _db().commit()


async def get_or_create_user(tg_id: int, username: str | None, first_name: str | None) -> aiosqlite.Row:
    db = _db()
    cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = await cur.fetchone()
    if row is None:
        await db.execute(
            "INSERT INTO users (tg_id, username, first_name) VALUES (?, ?, ?)",
            (tg_id, username, first_name),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
    else:
        await db.execute(
            "UPDATE users SET username = ?, first_name = ? WHERE tg_id = ?",
            (username, first_name, tg_id),
        )
        await db.commit()
    return row


async def set_user_phone(user_id: int, phone: str) -> None:
    db = _db()
    await db.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, user_id))
    await db.commit()


async def get_user_by_id(user_id: int) -> aiosqlite.Row | None:
    cur = await _db().execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return await cur.fetchone()


async def get_user_by_tg_id(tg_id: int) -> aiosqlite.Row | None:
    """Lookup without touching username/first_name (get_or_create_user would overwrite them)."""
    cur = await _db().execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    return await cur.fetchone()


# --- wallet: one small MDL balance every paid service draws from -------------

async def get_balance(user_id: int) -> int:
    cur = await _db().execute("SELECT balance_mdl FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    return int(row["balance_mdl"]) if row else 0


async def add_balance(user_id: int, mdl: int) -> None:
    db = _db()
    await db.execute("UPDATE users SET balance_mdl = balance_mdl + ? WHERE id = ?", (int(mdl), user_id))
    await db.commit()


async def spend_balance(user_id: int, mdl: int) -> bool:
    """Atomically move money out of the wallet. The WHERE guard means a double-tap or a
    concurrent spend can never take the same leu twice or drive the balance negative."""
    db = _db()
    cur = await db.execute(
        "UPDATE users SET balance_mdl = balance_mdl - ? WHERE id = ? AND balance_mdl >= ?",
        (int(mdl), user_id, int(mdl)))
    await db.commit()
    return cur.rowcount > 0


async def has_published_listing(user_id: int) -> bool:
    """True once an owner has had ANY ad go live (active or since archived): the cold-start free
    hook applies only to a first-time publisher, so we must not hand it out again."""
    cur = await _db().execute(
        "SELECT 1 FROM listings WHERE user_id = ? AND status IN ('active','archived') LIMIT 1",
        (user_id,))
    return await cur.fetchone() is not None


async def count_demo_active() -> int:
    """Active seeded examples still on the board. The welcome copy shows an honest 'это демо'
    note only while such cards exist, so it disappears automatically once real owners arrive."""
    cur = await _db().execute(
        "SELECT COUNT(*) c FROM listings WHERE status = 'active' AND is_demo = 1")
    return (await cur.fetchone())["c"]


# --- founding members: the first FOUNDING_SLOTS pack buyers, stamped for life --

async def count_founding_members() -> int:
    cur = await _db().execute("SELECT COUNT(*) c FROM users WHERE founding > 0")
    return (await cur.fetchone())["c"]


async def claim_founding_slot(user_id: int, slots: int) -> int:
    """Stamp the next founding slot on a user who just paid a pack, capped at `slots`. Returns the
    slot granted (1..N), the user's existing slot if already stamped, or 0 once the slots are gone.
    Counting distinct stamped users keeps the scarcity real: re-approving one payment cannot mint a
    second slot, and a buyer never loses the slot number they earned on their first pack."""
    db = _db()
    cur = await db.execute("SELECT founding FROM users WHERE id = ?", (user_id,))
    row = await cur.fetchone()
    if not row:
        return 0
    if row["founding"] and int(row["founding"]) > 0:
        return int(row["founding"])
    used = await count_founding_members()
    if used >= slots:
        return 0
    slot = used + 1
    cur = await db.execute("UPDATE users SET founding = ? WHERE id = ? AND founding = 0", (slot, user_id))
    await db.commit()
    return slot if cur.rowcount > 0 else 0


# --- top-ups: a lump-sum report an admin (or the card webhook) credits --------

async def create_topup(user_id: int, tg_id: int, amount_mdl: int,
                       credit_mdl: int | None = None, kind: str = "wallet") -> int:
    """Open a pending top-up. amount_mdl = real money the buyer transfers (the revenue).
    credit_mdl = leu to actually grant the wallet (a $1 pack grants 10x). When credit_mdl is
    None the two coincide (a plain wallet top-up)."""
    db = _db()
    credit = int(credit_mdl) if credit_mdl is not None else int(amount_mdl)
    cur = await db.execute(
        "INSERT INTO topups (user_id, tg_id, amount_mdl, credit_mdl, kind) VALUES (?, ?, ?, ?, ?)",
        (user_id, tg_id, int(amount_mdl), credit, kind))
    await db.commit()
    return cur.lastrowid


async def get_topup(topup_id: int) -> aiosqlite.Row | None:
    cur = await _db().execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
    return await cur.fetchone()


async def credit_topup(topup_id: int) -> aiosqlite.Row | None:
    """Move one pending top-up into the wallet. Returns the row the first time, None on a repeat,
    so a double admin tick can never mint money twice."""
    db = _db()
    cur = await db.execute("SELECT * FROM topups WHERE id = ? AND status = 'pending'", (topup_id,))
    row = await cur.fetchone()
    if not row:
        return None
    cur = await db.execute("UPDATE topups SET status = 'paid' WHERE id = ? AND status = 'pending'", (topup_id,))
    if cur.rowcount == 0:
        await db.commit()
        return None
    # grant the leu (a pack grants credit_mdl > amount_mdl; the extra is marketing, not revenue)
    credit = int(row["credit_mdl"]) if row["credit_mdl"] is not None else int(row["amount_mdl"])
    await db.execute("UPDATE users SET balance_mdl = balance_mdl + ? WHERE id = ?",
                     (credit, row["user_id"]))
    await db.commit()
    return row


async def reject_topup(topup_id: int) -> bool:
    db = _db()
    cur = await db.execute("UPDATE topups SET status = 'rejected' WHERE id = ? AND status = 'pending'", (topup_id,))
    await db.commit()
    return cur.rowcount > 0


async def pending_topups(limit: int = 20):
    cur = await _db().execute(
        "SELECT t.*, u.username FROM topups t JOIN users u ON u.id = t.user_id "
        "WHERE t.status = 'pending' ORDER BY t.id LIMIT ?", (limit,))
    return await cur.fetchall()


async def feature_listing(listing_id: int, days: int) -> None:
    """Turn on the paid "Срочно" badge for `days`: a highlighted ad leads the board (see search
    ORDER BY) and carries a fire label on the card."""
    db = _db()
    await db.execute(
        "UPDATE listings SET featured_until = datetime('now', ?), updated_at = datetime('now') WHERE id = ?",
        (f"+{int(days)} days", listing_id))
    await db.commit()


async def subscribe_alerts(user_id: int, days: int) -> None:
    """Extend the saved-search subscription: alerts only push while now < alerts_until."""
    db = _db()
    await db.execute(
        "UPDATE users SET alerts_until = datetime("
        "CASE WHEN alerts_until IS NULL OR alerts_until <= datetime('now') THEN datetime('now') "
        "ELSE alerts_until END, ?) WHERE id = ?",
        (f"+{int(days)} days", user_id))
    await db.commit()


async def is_alerts_subscribed(user_id: int) -> bool:
    """True while the paid saved-search subscription is live. Callers gate on config.PAID_ENABLED:
    a free board pushes every alert, a paid one only to subscribers. Checked in SQL so the clock
    stays the DB's UTC, same as every other *_until comparison."""
    cur = await _db().execute(
        "SELECT 1 FROM users WHERE id = ? AND alerts_until IS NOT NULL "
        "AND alerts_until > datetime('now')", (user_id,))
    return await cur.fetchone() is not None


async def grant_alerts_trial(user_id: int, days: int) -> bool:
    """Cold-start hook for alerts: a first-time watcher gets a free window. Only fires when the
    user has NEVER been subscribed (alerts_until IS NULL), so an expired trial or a lapsed paid
    subscription is not re-baited for free. Returns True if the trial was just granted."""
    db = _db()
    cur = await db.execute(
        "UPDATE users SET alerts_until = datetime('now', ?) "
        "WHERE id = ? AND alerts_until IS NULL",
        (f"+{int(days)} days", user_id))
    await db.commit()
    return cur.rowcount > 0


async def create_listing(**kw: Any) -> int:
    db = _db()
    cur = await db.execute(
        """INSERT INTO listings
           (user_id, deal, ptype, rooms, price, currency, district, area,
            floor, floors, phone, description, photos, contacts, status, is_demo)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (
            kw["user_id"], kw["deal"], kw["ptype"], kw.get("rooms"), kw["price"],
            kw.get("currency", "EUR"), kw.get("district"), kw.get("area"),
            kw.get("floor"), kw.get("floors"), kw.get("phone"),
            kw.get("description"), json.dumps(kw.get("photos", [])),
            json.dumps(kw.get("contacts") or {}, ensure_ascii=False),
            int(kw.get("is_demo", 0)),
        ),
    )
    await db.commit()
    return cur.lastrowid


async def get_listing(listing_id: int) -> aiosqlite.Row | None:
    cur = await _db().execute("SELECT * FROM listings WHERE id = ?", (listing_id,))
    return await cur.fetchone()


async def set_listing_status(listing_id: int, status: str) -> None:
    db = _db()
    await db.execute(
        "UPDATE listings SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, listing_id),
    )
    await db.commit()


# --- Stage 4: listing life cycle -------------------------------------------------

async def activate_listing(listing_id: int, days: int) -> None:
    """Approve onto the board with a real end date. A fresh active ad lives `days` days."""
    db = _db()
    await db.execute(
        "UPDATE listings SET status = 'active', "
        "expires_at = datetime('now', ?), updated_at = datetime('now') WHERE id = ?",
        (f"+{int(days)} days", listing_id),
    )
    await db.commit()


async def extend_listing(listing_id: int, days: int) -> None:
    """Продлить: push the end date out from now and make sure the ad is live again
    (so a sold/archived one can be revived with the same one tap the reminder offers)."""
    db = _db()
    await db.execute(
        "UPDATE listings SET status = 'active', "
        "expires_at = datetime('now', ?), updated_at = datetime('now') WHERE id = ?",
        (f"+{int(days)} days", listing_id),
    )
    await db.commit()


async def archive_listing(listing_id: int) -> None:
    """Сдано / Удалить: leave the board but keep the row (soft, reversible via extend)."""
    await set_listing_status(listing_id, "archived")


async def archive_expired(limit: int = 200):
    """The hourly archiver: active ads whose end date has passed become archived.
    Returns the rows we archived so the caller can tell their owners."""
    db = _db()
    cur = await db.execute(
        "SELECT * FROM listings WHERE status = 'active' "
        "AND expires_at IS NOT NULL AND expires_at <= datetime('now') ORDER BY id LIMIT ?",
        (limit,))
    rows = await cur.fetchall()
    for r in rows:
        await db.execute(
            "UPDATE listings SET status = 'archived', updated_at = datetime('now') WHERE id = ?",
            (r["id"],))
    await db.commit()
    return rows


async def listings_expiring_in(days: int, limit: int = 200):
    """Active ads that will leave the board within `days` - the extend-reminder feed."""
    cur = await _db().execute(
        "SELECT l.*, u.tg_id FROM listings l JOIN users u ON u.id = l.user_id "
        "WHERE l.status = 'active' AND l.expires_at IS NOT NULL "
        "AND l.expires_at > datetime('now') AND l.expires_at <= datetime('now', ?) "
        "ORDER BY l.expires_at LIMIT ?",
        (f"+{int(days)} days", limit))
    return await cur.fetchall()


def listing_photos(row) -> list[str]:
    try:
        return json.loads(row["photos"] or "[]")
    except (TypeError, ValueError):
        return []


def listing_contacts(row) -> dict:
    """Owner's extra channels: {telegram, instagram, whatsapp, viber, facebook, site}.
    Empty dict when nothing was set - the card just shows the phone."""
    try:
        raw = row["contacts"]
    except (IndexError, KeyError, TypeError):
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


async def inc_views(listing_id: int) -> None:
    db = _db()
    await db.execute("UPDATE listings SET views = views + 1 WHERE id = ?", (listing_id,))
    await db.commit()


async def boost_listing(listing_id: int, days: int) -> None:
    db = _db()
    await db.execute(
        """UPDATE listings
           SET boosted_until = datetime('now', ?), updated_at = datetime('now')
           WHERE id = ?""",
        (f"+{days} days", listing_id),
    )
    await db.commit()


async def unboost_listing(listing_id: int) -> None:
    """Take the listing out of the top: used when the money for its Boost went back to the buyer."""
    db = _db()
    await db.execute(
        "UPDATE listings SET boosted_until = NULL, updated_at = datetime('now') "
        "WHERE id = ? AND boosted_until IS NOT NULL", (listing_id,))
    await db.commit()


async def search_listings(
    deal: str | None = None,
    ptype: str | None = None,
    district: str | None = None,
    rooms: int | None = None,
    price_max: int | None = None,
    limit: int = 10,
    offset: int = 0,
):
    q = "SELECT * FROM listings WHERE status = 'active' AND (expires_at IS NULL OR expires_at > datetime('now'))"
    args: list[Any] = []
    if deal:
        q += " AND deal = ?"
        args.append(deal)
    if ptype:
        q += " AND ptype = ?"
        args.append(ptype)
    if district:
        q += " AND district = ?"
        args.append(district)
    if rooms:
        # the filter exposes 1 / 2 / 3+ : a "3+" tap should catch every home with 3 or more rooms
        q += " AND rooms >= ?" if rooms >= 3 else " AND rooms = ?"
        args.append(rooms)
    if price_max:
        q += " AND price <= ?"
        args.append(price_max)
    q += (" ORDER BY (featured_until IS NOT NULL AND featured_until > datetime('now')) DESC, "
          "(boosted_until IS NOT NULL AND boosted_until > datetime('now')) DESC, "
          "created_at DESC LIMIT ? OFFSET ?")
    args.extend([limit, offset])
    cur = await _db().execute(q, args)
    return await cur.fetchall()


async def count_search_listings(
    deal: str | None = None,
    ptype: str | None = None,
    district: str | None = None,
    rooms: int | None = None,
    price_max: int | None = None,
) -> int:
    q = "SELECT COUNT(*) c FROM listings WHERE status = 'active' AND (expires_at IS NULL OR expires_at > datetime('now'))"
    args: list[Any] = []
    if deal:
        q += " AND deal = ?"
        args.append(deal)
    if ptype:
        q += " AND ptype = ?"
        args.append(ptype)
    if district:
        q += " AND district = ?"
        args.append(district)
    if rooms:
        # the filter exposes 1 / 2 / 3+ : a "3+" tap should catch every home with 3 or more rooms
        q += " AND rooms >= ?" if rooms >= 3 else " AND rooms = ?"
        args.append(rooms)
    if price_max:
        q += " AND price <= ?"
        args.append(price_max)
    cur = await _db().execute(q, args)
    row = await cur.fetchone()
    return row["c"] if row else 0


async def get_user_listings(user_id: int):
    cur = await _db().execute(
        "SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    )
    return await cur.fetchall()


async def listings_to_nudge(after_days: int, cooldown_days: int, limit: int = 20):
    """Active listings that are currently NOT boosted and were not nudged recently.

    This is the revenue loop: the owner gets a reminder that Boost makes the
    listing first in search. Cooldown keeps the reminder from becoming spam.
    """
    q = """SELECT * FROM listings
           WHERE status = 'active'
             AND (boosted_until IS NULL OR boosted_until <= datetime('now'))
             AND created_at <= datetime('now', ?)
             AND (nudged_at IS NULL OR nudged_at <= datetime('now', ?))
           ORDER BY created_at LIMIT ?"""
    cur = await _db().execute(q, (f"-{after_days} days", f"-{cooldown_days} days", limit))
    return await cur.fetchall()


async def mark_nudged(listing_id: int) -> None:
    db = _db()
    await db.execute("UPDATE listings SET nudged_at = datetime('now') WHERE id = ?", (listing_id,))
    await db.commit()


async def all_user_tg_ids():
    cur = await _db().execute("SELECT tg_id FROM users ORDER BY id")
    return [r["tg_id"] for r in await cur.fetchall()]


async def log_boost(listing_id: int, user_id: int, stars: int, days: int, status: str = "paid",
                    order_ref: str | None = None) -> int:
    db = _db()
    cur = await db.execute(
        "INSERT INTO boosts (listing_id, user_id, stars, days, status, order_ref) VALUES (?, ?, ?, ?, ?, ?)",
        (listing_id, user_id, stars, days, status, order_ref),
    )
    await db.commit()
    return cur.lastrowid


async def grant_gift_credits(user_id: int, amount: int, slots: int) -> bool:
    """Founders gift: one-time grant of free top-raises, capped by total grantees.

    Idempotent twice over: the UPDATE only fires while credits are still 0 (a user is granted
    once), and the cap counts distinct granted users from events, so re-approving someone's
    second listing cannot mint credits and cannot consume a second slot.
    """
    if amount <= 0:
        return False
    db = _db()
    cur = await db.execute("SELECT COUNT(DISTINCT user_id) c FROM events WHERE name='gift_granted'")
    used = (await cur.fetchone())["c"]
    if used >= slots:
        return False
    cur = await db.execute(
        "UPDATE users SET boost_credits = ? WHERE id = ? AND boost_credits = 0",
        (amount, user_id),
    )
    if cur.rowcount == 0:
        await db.commit()
        return False
    await log_event("gift_granted", user_id=user_id, meta=str(amount))
    await db.commit()
    return True


async def spend_gift_credit(user_id: int) -> bool:
    """Take one free raise. The WHERE keeps a double-tap from spending a credit that isn't there."""
    db = _db()
    cur = await db.execute(
        "UPDATE users SET boost_credits = boost_credits - 1 WHERE id = ? AND boost_credits > 0",
        (user_id,),
    )
    await db.commit()
    return cur.rowcount > 0


async def get_boost(boost_id: int):
    cur = await _db().execute("SELECT * FROM boosts WHERE id = ?", (boost_id,))
    return await cur.fetchone()


async def find_boost(listing_id: int, status: str):
    cur = await _db().execute(
        "SELECT * FROM boosts WHERE listing_id = ? AND status = ? ORDER BY id DESC LIMIT 1",
        (listing_id, status),
    )
    return await cur.fetchone()


async def set_boost_status(boost_id: int, status: str, order_ref: str | None = None) -> None:
    db = _db()
    if order_ref is None:
        await db.execute("UPDATE boosts SET status = ? WHERE id = ?", (status, boost_id))
    else:
        await db.execute("UPDATE boosts SET status = ?, order_ref = ? WHERE id = ?",
                         (status, order_ref, boost_id))
    await db.commit()


async def log_payment(user_id: int, listing_id: int | None, purpose: str, stars: int,
                      currency: str, telegram_charge_id: str | None, provider_charge_id: str | None,
                      amount: float | None = None, is_test: bool = False) -> None:
    db = _db()
    await db.execute(
        """INSERT INTO payments
           (user_id, listing_id, purpose, stars, currency, telegram_charge_id, provider_charge_id, amount, is_test)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, listing_id, purpose, stars, currency, telegram_charge_id, provider_charge_id,
         amount, 1 if is_test else 0),
    )
    await db.commit()


async def store_card_order(conn: aiosqlite.Connection, order: dict) -> bool:
    """Save one provider order event on the given connection. True when the order id is new.

    Called by the Mini App process (it owns the webhook); the bot process reads the rows and
    applies them, so only one writer ever touches money. Duplicate deliveries only move the
    status forward and never create a second row -> a paid order cannot be credited twice.
    """
    try:
        await conn.execute(
            """INSERT INTO card_orders (order_id, listing_id, tg_id, amount_cents,
                                        currency, status, test_mode, customer)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (order["order_id"], order.get("listing_id"), order.get("tg_id"), order["amount_cents"],
             order["currency"], order["status"], 1 if order.get("test_mode") else 0,
             order.get("customer")),
        )
        await conn.commit()
        return True
    except aiosqlite.IntegrityError:
        await conn.execute("UPDATE card_orders SET status = ? WHERE order_id = ?",
                           (order["status"], order["order_id"]))
        await conn.commit()
        return False


async def card_orders_to_apply(limit: int = 20):
    q = """SELECT * FROM card_orders
           WHERE applied = 0 AND status = 'paid' AND listing_id IS NOT NULL
           ORDER BY id LIMIT ?"""
    cur = await _db().execute(q, (limit,))
    return await cur.fetchall()


async def mark_card_order_applied(row_id: int) -> None:
    db = _db()
    await db.execute("UPDATE card_orders SET applied = 1 WHERE id = ?", (row_id,))
    await db.commit()


async def payments_refunds(limit: int = 20):
    """Applied card payments the provider has since taken back, still unprocessed.

    Full refunds and chargebacks only: a `partial_refund` keeps the boost (the buyer still owes us
    nothing, but the money we keep is real) and stays in revenue. `refunded_at IS NULL` is the
    marker, so a repeated order_refunded delivery can never strip the same boost twice.
    """
    q = """SELECT p.id AS payment_id, p.listing_id, p.user_id, p.amount, p.currency,
                  p.provider_charge_id, o.order_id, o.status AS order_status
           FROM payments p
           JOIN card_orders o ON o.order_id = p.provider_charge_id
           WHERE p.refunded_at IS NULL AND p.provider_charge_id IS NOT NULL
             AND o.applied = 1 AND o.status IN ('refunded', 'fraudulent')
           ORDER BY p.id LIMIT ?"""
    cur = await _db().execute(q, (limit,))
    return await cur.fetchall()


async def mark_payment_refunded(payment_id: int) -> None:
    db = _db()
    await db.execute("UPDATE payments SET refunded_at = datetime('now') WHERE id = ?",
                     (payment_id,))
    await db.commit()


async def find_payment_by_charge(telegram_charge_id: str):
    """The payment row a manual (MIA/MDL) approval wrote, so a later hand-refund can be booked
    against exactly that money instead of against a guess."""
    cur = await _db().execute(
        "SELECT * FROM payments WHERE telegram_charge_id = ? ORDER BY id DESC LIMIT 1",
        (telegram_charge_id,))
    return await cur.fetchone()


async def find_boost_by_ref(order_ref: str):
    cur = await _db().execute(
        "SELECT * FROM boosts WHERE order_ref = ? ORDER BY id DESC LIMIT 1", (order_ref,))
    return await cur.fetchone()


async def latest_boost_for_listing(listing_id: int):
    """Newest boost of a listing: a refund only takes the top spot away if nothing newer paid for it."""
    cur = await _db().execute(
        "SELECT * FROM boosts WHERE listing_id = ? ORDER BY id DESC LIMIT 1", (listing_id,))
    return await cur.fetchone()


async def manual_claims_waiting(older_than_hours: int, limit: int = 20):
    """MIA claims a buyer reported but nobody has confirmed yet, with everything an admin needs
    to find the transfer in the bank statement (payer phone lives on the listing)."""
    q = """SELECT b.id AS boost_id, b.listing_id, b.created_at,
                  CAST((julianday('now') - julianday(b.created_at)) * 24 AS INTEGER) AS waited_hours,
                  l.district, l.price, l.currency, l.phone AS listing_phone,
                  u.id AS user_id, u.tg_id, u.username
           FROM boosts b
           JOIN listings l ON l.id = b.listing_id
           JOIN users u ON u.id = b.user_id
           WHERE b.status = 'manual_pending'
             AND b.created_at < datetime('now', ?)
           ORDER BY b.id LIMIT ?"""
    cur = await _db().execute(q, (f"-{int(older_than_hours)} hours", limit))
    return await cur.fetchall()


async def boosts_that_just_expired(hours: int = 48, limit: int = 20):
    """Active listings whose Boost window has passed and which nobody has been told about yet.

    The dedup lives in `events`: a `boost_expired` row newer than `boosted_until` means this exact
    window was already announced. Re-boosting moves boosted_until forward, so the next expiry gets
    noticed again - no extra column, no state to clean up.
    """
    q = """SELECT l.id, l.user_id, l.district, l.price, l.currency, l.rooms, l.boosted_until,
                  u.tg_id
           FROM listings l JOIN users u ON u.id = l.user_id
           WHERE l.status = 'active' AND l.boosted_until IS NOT NULL
             AND l.boosted_until <= datetime('now')
             AND l.boosted_until >= datetime('now', ?)
             AND NOT EXISTS (SELECT 1 FROM events e WHERE e.name = 'boost_expired'
                             AND e.listing_id = l.id AND e.created_at > l.boosted_until)
           ORDER BY l.id LIMIT ?"""
    cur = await _db().execute(q, (f"-{int(hours)} hours", limit))
    return await cur.fetchall()


async def card_checkouts_since(minutes: int) -> int:
    """Card payment pages we handed out in the window (demand side of the webhook)."""
    cur = await _db().execute(
        "SELECT COUNT(*) c FROM events WHERE name='card_checkout' "
        "AND created_at > datetime('now', ?)", (f"-{int(minutes)} minutes",))
    return (await cur.fetchone())["c"]


async def card_orders_since(minutes: int) -> int:
    """Deliveries the provider actually managed to store in the window (supply side)."""
    cur = await _db().execute(
        "SELECT COUNT(*) c FROM card_orders WHERE created_at > datetime('now', ?)",
        (f"-{int(minutes)} minutes",))
    return (await cur.fetchone())["c"]


async def card_checkout_ago_seconds() -> int | None:
    """Seconds since the last card payment link was handed out. None if it never happened."""
    cur = await _db().execute(
        "SELECT CAST((julianday('now') - julianday(MAX(created_at))) * 86400 AS INTEGER) s "
        "FROM events WHERE name='card_checkout'")
    row = await cur.fetchone()
    return row["s"] if row and row["s"] is not None else None


async def log_event(name: str, user_id: int | None = None, listing_id: int | None = None, meta: str | None = None) -> None:
    db = _db()
    await db.execute(
        "INSERT INTO events (name, user_id, listing_id, meta) VALUES (?, ?, ?, ?)",
        (name, user_id, listing_id, meta),
    )
    await db.commit()


async def note_startup(window_minutes: int = 60) -> tuple[bool, int]:
    """Records a bot start. Admins get the startup message only for the FIRST start in
    `window_minutes`: a crash loop or a run of deploys must not spam them (it did - 35
    messages over two days). Returns (should_notify, starts_in_window)."""
    db = _db()
    cur = await db.execute(
        "SELECT COUNT(*) c FROM events WHERE name='startup' AND created_at > datetime('now', ?)",
        (f"-{window_minutes} minutes",),
    )
    prev = (await cur.fetchone())["c"]
    await log_event("startup", meta=str(prev + 1))
    return prev == 0, prev + 1


async def event_recent(name: str, meta: str | None, hours: int) -> bool:
    """Was this exact event logged inside the window? Reminder dedup, restart-safe by design."""
    q = ("SELECT 1 FROM events WHERE name = ? AND IFNULL(meta,'') = IFNULL(?, '') "
         "AND created_at > datetime('now', ?) LIMIT 1")
    cur = await _db().execute(q, (name, meta, f"-{int(hours)} hours"))
    return await cur.fetchone() is not None


async def stats() -> dict:
    db = _db()
    out: dict[str, Any] = {}
    for name, q in {
        "users": "SELECT COUNT(*) c FROM users",
        "listings": "SELECT COUNT(*) c FROM listings",
        "active": "SELECT COUNT(*) c FROM listings WHERE status='active'",
        "pending": "SELECT COUNT(*) c FROM listings WHERE status='pending'",
        "boosts": "SELECT COUNT(*) c FROM boosts WHERE status IN ('paid','manual')",
        "payments": "SELECT COUNT(*) c FROM payments",
        # REAL_REVENUE must stay honest: test invoices and provider test-mode orders excluded,
        # and money the provider took back is not revenue either (refunded_at IS NULL)
        "revenue_stars": "SELECT COALESCE(SUM(stars),0) c FROM payments "
                         "WHERE currency='XTR' AND is_test=0 AND refunded_at IS NULL",
        "revenue_mdl": "SELECT COALESCE(SUM(amount),0) c FROM payments "
                       "WHERE currency='MDL' AND is_test=0 AND refunded_at IS NULL",
        # card rail is priced in the store currency (USD), keep it on its own line
        "revenue_card": "SELECT COALESCE(SUM(amount),0) c FROM payments "
                        "WHERE currency NOT IN ('XTR','MDL','XTEST') AND is_test=0 "
                        "AND refunded_at IS NULL",
        "card_unapplied": "SELECT COUNT(*) c FROM card_orders WHERE applied = 0 AND status='paid'",
        "refunds": "SELECT COUNT(*) c FROM payments WHERE refunded_at IS NOT NULL",
        "manual_waiting": "SELECT COUNT(*) c FROM boosts WHERE status='manual_pending'",
        "test_payments": "SELECT COUNT(*) c FROM payments WHERE currency = 'XTEST' OR is_test = 1",
        # founders gift: how many got it and how many free raises are still in pockets
        "gift_holders": "SELECT COUNT(*) c FROM events WHERE name='gift_granted'",
        "gift_credits": "SELECT COALESCE(SUM(boost_credits),0) c FROM users WHERE boost_credits > 0",
    }.items():
        cur = await db.execute(q)
        row = await cur.fetchone()
        out[name] = row["c"]
    return out
