"""ChirieBot Mini App backend: read-only listings API + static frontend + payment webhook.

Runs as separate systemd service (webapp.service), proxied by nginx.
DB access is read-only except view/contact counters and the card payment inbox
(card_orders): the provider webhook stores the order here, the bot process applies it.
"""
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.parse

import aiosqlite
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import db
import lemon

log = logging.getLogger("chiriebot.webapp")

DB_PATH = config.DB_PATH
PHOTOS_DIR = config.PHOTOS_DIR
BOT_TOKEN = config.BOT_TOKEN
INIT_DATA_MAX_AGE = 24 * 3600  # hours accepted by Telegram too

DEAL_LABELS = {"rent": "Аренда", "sale": "Продажа"}
PTYPE_LABELS = {"apartment": "Квартира", "house": "Дом", "room": "Комната"}


# ---------- Telegram WebApp initData validation (deterministic, per docs) ----------

def validate_init_data(init_data: str) -> dict | None:
    """Return authed user dict or None."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_ = parsed.pop("hash", "")
        auth_date = int(parsed.get("auth_date", "0"))
        if time.time() - auth_date > INIT_DATA_MAX_AGE:
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, hash_):
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def auth_user(request: web.Request) -> dict | None:
    return validate_init_data(request.headers.get("X-Init-Data", ""))


# ---------- helpers ----------

def photo_urls(listing_id: int, count: int) -> list[str]:
    out = []
    for i in range(min(count, 10)):
        name = f"{listing_id}_{i}.jpg"
        if os.path.exists(os.path.join(PHOTOS_DIR, name)):
            out.append(f"/photos/{name}")
    return out


def row_to_item(row, with_detail: bool = False) -> dict:
    item = {
        "id": row["id"],
        "deal": row["deal"],
        "deal_label": DEAL_LABELS.get(row["deal"], row["deal"]),
        "ptype": row["ptype"],
        "ptype_label": PTYPE_LABELS.get(row["ptype"], row["ptype"]),
        "rooms": row["rooms"],
        "price": row["price"],
        "currency": row["currency"],
        "district": row["district"],
        "area": row["area"],
        "floor": row["floor"],
        "floors": row["floors"],
        "views": row["views"],
        "boosted": bool(row["boosted_until"] and row["boosted_until"] > time.strftime("%Y-%m-%d %H:%M:%S")),
        "photos": photo_urls(row["id"], len(json.loads(row["photos"] or "[]"))),
    }
    if with_detail:
        item["description"] = row["description"]
        # owner's public channels (telegram / instagram / whatsapp / viber / facebook / site):
        # these are not private like the phone, so they render directly on the sheet without
        # the "reveal" gate. phone stays behind revealContact so the web_contact metric keeps
        # counting real intent.
        try:
            item["contacts"] = json.loads(row["contacts"] or "{}") if row["contacts"] else {}
        except (ValueError, TypeError):
            item["contacts"] = {}
    return item


# ---------- handlers ----------

async def index(request: web.Request) -> web.StreamResponse:
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read().replace("{{BOT_URL}}", config.BOT_URL or "https://t.me/gaseste_casa_bot")
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def api_listings(request: web.Request) -> web.Response:
    q = request.query
    where = "status = 'active'"
    args: list = []
    for col, key in (("deal", "deal"), ("ptype", "ptype"), ("district", "district")):
        if q.get(key):
            where += f" AND {col} = ?"
            args.append(q[key])
    if q.get("rooms"):
        r = int(q["rooms"])
        # 1 / 2 are exact, the 3+ bucket means three or more rooms
        where += " AND rooms >= ?" if r >= 3 else " AND rooms = ?"
        args.append(r)
    if q.get("price_max"):
        where += " AND price <= ?"
        args.append(int(q["price_max"]))
    page = max(int(q.get("page", "1")), 1)
    per = 20
    order = ("ORDER BY (boosted_until IS NOT NULL AND boosted_until > datetime('now')) DESC, created_at DESC "
             f"LIMIT {per} OFFSET {(page - 1) * per}")
    db = request.app["db"]
    cur = await db.execute(f"SELECT * FROM listings WHERE {where} {order}", args)
    rows = await cur.fetchall()
    cur = await db.execute(f"SELECT COUNT(*) c FROM listings WHERE {where}", args)
    total = (await cur.fetchone())["c"]
    items = [row_to_item(r) for r in rows]
    return web.json_response({"total": total, "page": page, "per": per, "items": items})


async def api_listing(request: web.Request) -> web.Response:
    listing_id = int(request.match_info["id"])
    db = request.app["db"]
    cur = await db.execute("SELECT * FROM listings WHERE id = ? AND status = 'active'", (listing_id,))
    row = await cur.fetchone()
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    await db.execute("UPDATE listings SET views = views + 1 WHERE id = ?", (listing_id,))
    await db.execute("INSERT INTO events (name, user_id, listing_id) VALUES ('web_view', ?, ?)",
                     ((auth_user(request) or {}).get("id"), listing_id))
    await db.commit()
    item = row_to_item(row, with_detail=True)
    cur = await db.execute("SELECT u.tg_id FROM listings l JOIN users u ON u.id = l.user_id WHERE l.id = ?",
                           (listing_id,))
    item["owner_tg"] = (await cur.fetchone())["tg_id"]
    return web.json_response(item)


async def api_contact(request: web.Request) -> web.Response:
    """Reveal owner contact. Requires authed Telegram user -> honest 'contacts' metric."""
    tg_user = auth_user(request)
    if not tg_user:
        return web.json_response({"error": "auth_required"}, status=401)
    listing_id = int(request.match_info["id"])
    db = request.app["db"]
    cur = await db.execute(
        "SELECT l.phone, u.tg_id, u.username FROM listings l JOIN users u ON u.id = l.user_id "
        "WHERE l.id = ? AND l.status = 'active'", (listing_id,))
    row = await cur.fetchone()
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    await db.execute("INSERT INTO events (name, user_id, listing_id) VALUES ('web_contact', ?, ?)",
                     (tg_user["id"], listing_id))
    await db.commit()
    return web.json_response({"phone": row["phone"], "tg": row["username"] or (f"tg://user?id={row['tg_id']}" if row["tg_id"] else None)})


async def api_stats(request: web.Request) -> web.Response:
    """Public counter for the header ('N live listings')."""
    cur = await request.app["db"].execute("SELECT COUNT(*) c FROM listings WHERE status='active'")
    return web.json_response({"active": (await cur.fetchone())["c"]})


async def webhook_lemonsqueezy(request: web.Request) -> web.Response:
    """Card payment inbox. No init-data auth: the HMAC signature of the raw body is the proof.

    Only stores what the provider reported. 200 on ignored events, otherwise Lemon Squeezy
    retries the delivery three more times.
    """
    raw = await request.read()
    if not lemon.verify_signature(raw, request.headers.get("X-Signature", "")):
        log.warning("LS webhook rejected: bad signature (%s bytes)", len(raw))
        return web.json_response({"error": "invalid_signature"}, status=401)
    try:
        event = json.loads(raw)
    except ValueError:
        return web.json_response({"error": "bad_json"}, status=400)
    order = lemon.parse_order_event(event)
    if not order:
        return web.json_response({"ok": True, "ignored": True})
    fresh = await db.store_card_order(request.app["db"], order)
    log.info("LS order %s: %s %s listing=%s status=%s new=%s", order["order_id"],
             order["amount_cents"], order["currency"], order["listing_id"],
             order["status"], fresh)
    return web.json_response({"ok": True, "recorded": fresh})


# ---------- lifecycle ----------

async def app_factory() -> web.Application:
    app = web.Application()
    conn = await aiosqlite.connect(f"file:{DB_PATH}?mode=rwc", uri=True)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=5000")
    await db.ensure_schema(conn)  # card_orders must exist even if the bot restarts later
    app["db"] = conn
    app.router.add_get("/", index)
    app.router.add_get("/api/listings", api_listings)
    app.router.add_get("/api/listing/{id}", api_listing)
    app.router.add_post("/api/listing/{id}/contact", api_contact)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_post("/webhook/lemonsqueezy", webhook_lemonsqueezy)
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    app.router.add_static("/photos/", PHOTOS_DIR)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(app_factory(), host="127.0.0.1", port=8085)
