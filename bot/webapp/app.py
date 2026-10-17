"""ChirieBot Mini App backend: read-only listings API + static frontend + payment webhook.

Runs as separate systemd service (webapp.service), proxied by nginx.
DB access is read-only except view/contact counters and the card payment inbox
(card_orders): the provider webhook stores the order here, the bot process applies it.
"""
import hashlib
import hmac
import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse

import aiosqlite
import aiohttp
from aiohttp import web

try:  # Pillow is present on prod; used to verify + normalise uploads to JPEG. Optional in tests.
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import db
import lemon
from constants import STATUS_LABELS

log = logging.getLogger("chiriebot.webapp")

DB_PATH = config.DB_PATH
PHOTOS_DIR = config.PHOTOS_DIR
BOT_TOKEN = config.BOT_TOKEN
INIT_DATA_MAX_AGE = 24 * 3600  # hours accepted by Telegram too

DEAL_LABELS = {"rent": "Аренда", "sale": "Продажа"}
PTYPE_LABELS = {"apartment": "Квартира", "house": "Дом", "room": "Комната"}


# ---------- Telegram WebApp initData validation (deterministic, per docs) ----------

def validate_init_data(init_data: str) -> dict | None:
    """Return authed user dict or None. Rejects are logged with the reason (never the payload)
    so a live "no auth" report can be told apart: empty header (app opened as a link) vs a real
    initData that fails the HMAC/expiry check (wrong token or clock)."""
    if not BOT_TOKEN:
        log.warning("auth reject: webapp has no BOT_TOKEN in its env — every write would 401")
        return None
    if not init_data:
        log.warning("auth reject: empty X-Init-Data (app likely opened as a plain link, not web_app)")
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        hash_ = parsed.pop("hash", "")
        if not hash_:
            log.warning("auth reject: initData has no hash field")
            return None
        auth_date = int(parsed.get("auth_date", "0"))
        age = int(time.time() - auth_date)
        if age > INIT_DATA_MAX_AGE:
            log.warning("auth reject: initData expired (auth_date age=%ss > %ss)", age, INIT_DATA_MAX_AGE)
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, hash_):
            log.warning("auth reject: hash mismatch (BOT_TOKEN used by webapp != token that signed this app)")
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception as e:
        log.warning("auth reject: could not parse initData: %r", e)
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


def _ts_active(row, col: str) -> bool:
    """True when a *_until timestamp column is set and in the future. Tolerates a row that
    predates the column (a fresh test fixture or a webapp that started before the migration)."""
    try:
        v = row[col]
    except (IndexError, KeyError):
        return False
    return bool(v and v > time.strftime("%Y-%m-%d %H:%M:%S"))


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
        "featured": _ts_active(row, "featured_until"),
        "demo": bool(row["is_demo"]) if "is_demo" in row.keys() else False,
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

async def index(request: web.Request) -> web.Response:
    path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(path, encoding="utf-8") as f:
        html = (f.read()
                .replace("{{BOT_URL}}", config.BOT_URL or "https://t.me/gaseste_casa_bot")
                .replace("{{FOCUS_DEAL}}", config.FOCUS_DEAL)
                .replace("{{FOCUS_ROOMS}}", config.FOCUS_ROOMS))
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def api_listings(request: web.Request) -> web.Response:
    q = dict(request.query)
    # focus lock: when the board is narrowed, the visible set is the focus regardless of what
    # the client sent - a hand-crafted ?deal=sale must not leak non-focus ads back into search.
    if config.FOCUS_DEAL:
        q["deal"] = config.FOCUS_DEAL
    if config.FOCUS_ROOMS:
        q["rooms"] = config.FOCUS_ROOMS
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
    if q.get("q"):
        # free text over district + description, the way a person actually searches
        like = "%" + q["q"].strip()[:60] + "%"
        where += " AND (district LIKE ? OR description LIKE ?)"
        args += [like, like]
    page = max(int(q.get("page", "1")), 1)
    per = 20
    # highlighted ("Срочно") ads lead, then boosted, then the sort the user picked (new / cheap / dear)
    top = ("(featured_until IS NOT NULL AND featured_until > datetime('now')) DESC, "
           "(boosted_until IS NOT NULL AND boosted_until > datetime('now')) DESC")
    sort = q.get("sort")
    if sort == "cheap":
        tail = "price ASC"
    elif sort == "expensive":
        tail = "price DESC"
    else:
        tail = "created_at DESC"
    order = f"ORDER BY {top}, {tail} LIMIT {per} OFFSET {(page - 1) * per}"
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
    # bug #3: never expose the owner's Telegram id to anonymous callers. the frontend only needs to
    # know "is this my listing" to show the Boost button, so compute that server-side as a boolean.
    cur = await db.execute("SELECT u.tg_id FROM listings l JOIN users u ON u.id = l.user_id WHERE l.id = ?",
                           (listing_id,))
    owner_tg = (await cur.fetchone())["tg_id"]
    viewer = auth_user(request) or {}
    item["is_owner"] = bool(viewer.get("id")) and str(viewer.get("id")) == str(owner_tg)
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


# ---------- Phase B: "my listings" + owner lifecycle (auth-gated) ----------

def _my_item(row) -> dict:
    """Owner-facing view of one ad: status + expiry + a thumbnail, no private fields leaked."""
    n_photos = len(json.loads(row["photos"] or "[]"))
    return {
        "id": row["id"],
        "deal_label": DEAL_LABELS.get(row["deal"], row["deal"]),
        "ptype_label": PTYPE_LABELS.get(row["ptype"], row["ptype"]),
        "price": row["price"],
        "currency": row["currency"],
        "district": row["district"],
        "rooms": row["rooms"],
        "status": row["status"],
        "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "expires_at": row["expires_at"],
        "views": row["views"],
        "boosted": bool(row["boosted_until"] and row["boosted_until"] > time.strftime("%Y-%m-%d %H:%M:%S")),
        "featured": _ts_active(row, "featured_until"),
        "photos": photo_urls(row["id"], n_photos),
    }


async def api_my_listings(request: web.Request) -> web.Response:
    """The owner's own ads with their moderation / live / archived state. Requires auth."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    conn = request.app["db"]
    cur = await conn.execute("SELECT id FROM users WHERE tg_id = ?", (viewer["id"],))
    urow = await cur.fetchone()
    if not urow:
        return web.json_response({"items": []})
    cur = await conn.execute(
        "SELECT * FROM listings WHERE user_id = ? ORDER BY created_at DESC", (urow["id"],))
    rows = await cur.fetchall()
    return web.json_response({"items": [_my_item(r) for r in rows]})


# sold and delete are the same soft archive (row kept, "extend" revives it) - mirrors profile.py
_OWNER_ACTIONS = {"sold": "archived", "delete": "archived", "extend": "active"}


async def api_listing_action(request: web.Request) -> web.Response:
    """One-tap lifecycle from the app: Сдано / Продлить / Удалить. Owner-only, auth-gated."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = body.get("action")
    if action not in _OWNER_ACTIONS:
        return web.json_response({"error": "bad_action"}, status=400)
    listing_id = int(request.match_info["id"])
    conn = request.app["db"]
    cur = await conn.execute(
        "SELECT l.id, l.user_id, u.tg_id FROM listings l JOIN users u ON u.id = l.user_id WHERE l.id = ?",
        (listing_id,))
    row = await cur.fetchone()
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    if str(row["tg_id"]) != str(viewer["id"]):
        return web.json_response({"error": "not_owner"}, status=403)
    charged = False
    if action == "extend":
        # paid board keeps an ad live for LISTING_WEEK_DAYS at PRICE_LISTING_MDL, drawn from the
        # wallet (a spend is an event, not a payments row - the leu were booked on top-up). With
        # PAID_ENABLED=0 this is the same free one-tap it always was.
        days, event = config.LISTING_DAYS, "listing_extended"
        if config.PAID_ENABLED:
            cur = await conn.execute("SELECT balance_mdl FROM users WHERE id = ?", (row["user_id"],))
            urow = await cur.fetchone()
            bal = int((urow["balance_mdl"] if urow else 0) or 0)
            if bal < config.PRICE_LISTING_MDL:
                return web.json_response({"error": "insufficient_balance",
                                          "need": config.PRICE_LISTING_MDL, "balance": bal}, status=402)
            cur = await conn.execute(
                "UPDATE users SET balance_mdl = balance_mdl - ? WHERE id = ? AND balance_mdl >= ?",
                (config.PRICE_LISTING_MDL, row["user_id"], config.PRICE_LISTING_MDL))
            if cur.rowcount == 0:
                await conn.rollback()
                return web.json_response({"error": "insufficient_balance",
                                          "need": config.PRICE_LISTING_MDL, "balance": 0}, status=402)
            days, event, charged = config.LISTING_WEEK_DAYS, "renewal_paid", True
        await conn.execute(
            "UPDATE listings SET status = 'active', expires_at = datetime('now', ?), "
            "updated_at = datetime('now') WHERE id = ?",
            (f"+{days} days", listing_id))
    else:
        event = f"listing_{action}"
        await conn.execute(
            "UPDATE listings SET status = 'archived', updated_at = datetime('now') WHERE id = ?",
            (listing_id,))
    await conn.execute(
        "INSERT INTO events (name, user_id, listing_id) VALUES (?, ?, ?)",
        (event, row["user_id"], listing_id))
    await conn.commit()
    cur = await conn.execute("SELECT status, expires_at FROM listings WHERE id = ?", (listing_id,))
    new = await cur.fetchone()
    return web.json_response({
        "ok": True, "id": listing_id, "status": new["status"],
        "status_label": STATUS_LABELS.get(new["status"], new["status"]),
        "expires_at": new["expires_at"], "charged": charged,
    })


# ---------- Phase C: submit a listing from the app (auth-gated) ----------

# same phone rule as the bot wizard (add.py): 9+ digits, optional +, only horizontal separators.
_PHONE_RE = re.compile(r"\+?\d[\d().\- \t]{7,}\d")


def _esc(v) -> str:
    return (str(v) if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _notify_admins(listing_id: int, it: dict) -> bool:
    """Ping the admins for moderation. The callback buttons are handled by the polling bot
    process (same BOT_TOKEN), so approving from the app works exactly like from the wizard."""
    if not BOT_TOKEN or not config.ADMIN_IDS:
        return False
    cap = (f"\U0001F195 <b>Новое объявление #{listing_id}</b> (из Mini App)\n"
           f"{_esc(DEAL_LABELS.get(it['deal'], it['deal']))} · {_esc(it['ptype'])}\n"
           f"\U0001F4CD {_esc(it['district'])} · \U0001F4B8 {it['price']} {_esc(it['currency'])}")
    if it.get("rooms"):
        cap += f" · {it['rooms']} комн."
    if it.get("phone"):
        cap += f"\n\U0001F4DE {_esc(it['phone'])}"
    if it.get("description"):
        cap += f"\n\n{_esc(it['description'])[:500]}"
    kb = {"inline_keyboard": [[
        {"text": "\u2705 Одобрить", "callback_data": f"mod:approve:{listing_id}"},
        {"text": "\u274C Отклонить", "callback_data": f"mod:reject:{listing_id}"}],
        [{"text": "\U0001F441 Показать", "callback_data": f"view:{listing_id}"}]]}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    ok = False
    try:
        async with aiohttp.ClientSession() as s:
            for admin in config.ADMIN_IDS:
                try:
                    async with s.post(url, json={"chat_id": admin, "text": cap, "parse_mode": "HTML",
                                                 "reply_markup": kb}) as r:
                        ok = ok or r.status == 200
                except Exception:
                    pass
    except Exception:
        pass
    return ok


async def api_create_listing(request: web.Request) -> web.Response:
    """Create a pending listing from the Mini App. Mirrors the bot wizard's 3 steps:
    deal+rooms -> district+price -> phone+description. Never publishes: status stays 'pending'."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)

    deal = str(body.get("deal") or "").strip()
    if deal not in ("rent", "sale"):
        return web.json_response({"error": "bad_deal"}, status=400)
    # focus lock: the board only accepts its own situation, so a hand-crafted sale upload
    # cannot slip a non-focus ad past the (already hidden) UI selector.
    if config.FOCUS_DEAL and deal != config.FOCUS_DEAL:
        return web.json_response({"error": "deal_not_accepted"}, status=400)
    district = str(body.get("district") or "").strip()[:80]
    if not district:
        return web.json_response({"error": "district_required"}, status=400)
    try:
        price = int(re.sub(r"[^\d]", "", str(body.get("price") or "")) or "0")
    except ValueError:
        price = 0
    if price <= 0:
        return web.json_response({"error": "price_required"}, status=400)
    phone_raw = str(body.get("phone") or "")
    m = _PHONE_RE.search(phone_raw)
    digits = re.sub(r"\D", "", phone_raw)
    # a phone must actually contain a phone: the wizard's 9+-digit match, else any 6+-digit input
    phone = m.group(0).strip() if m else (phone_raw.strip()[:32] if len(digits) >= 6 else "")
    if not phone:
        return web.json_response({"error": "phone_required"}, status=400)
    description = str(body.get("description") or "")[:2000]
    rooms_raw = str(body.get("rooms") or "").strip()
    rooms = int(rooms_raw) if rooms_raw.isdigit() else None
    # focus lock: a narrowed board is one room count - every upload lands there, whatever the
    # (already hidden) selector sent, so the feed stays consistent.
    if config.FOCUS_ROOMS:
        rooms = int(config.FOCUS_ROOMS)
    ptype = (str(body.get("ptype") or "").strip() or "apartment")[:16]
    currency = (str(body.get("currency") or config.DEFAULT_CURRENCY or "EUR").strip() or "EUR")[:8]

    conn = request.app["db"]
    cur = await conn.execute("SELECT id FROM users WHERE tg_id = ?", (viewer["id"],))
    urow = await cur.fetchone()
    if urow:
        uid = urow["id"]
    else:
        cur = await conn.execute(
            "INSERT INTO users (tg_id, username, first_name) VALUES (?, ?, ?)",
            (viewer["id"], viewer.get("username"), viewer.get("first_name")))
        await conn.commit()
        uid = cur.lastrowid

    # rate limit: a flood of pending ads is spam, not supply (5 per 10 min per owner)
    cur = await conn.execute(
        "SELECT COUNT(*) c FROM listings WHERE user_id = ? "
        "AND created_at > datetime('now','-10 minutes')", (uid,))
    if (await cur.fetchone())["c"] >= 5:
        return web.json_response({"error": "too_many", "retry_after_min": 10}, status=429)

    cur = await conn.execute(
        "INSERT INTO listings (user_id, deal, ptype, rooms, price, currency, district, phone, "
        "description, photos, contacts, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', 'pending')",
        (uid, deal, ptype, rooms, price, currency, district, phone, description))
    await conn.commit()
    listing_id = cur.lastrowid
    await conn.execute(
        "INSERT INTO events (name, user_id, listing_id) VALUES ('listing_created', ?, ?)",
        (uid, listing_id))
    await conn.commit()
    await _notify_admins(listing_id, {
        "deal": deal, "ptype": ptype, "rooms": rooms, "price": price, "currency": currency,
        "district": district, "phone": phone, "description": description})
    return web.json_response({"ok": True, "id": listing_id, "status": "pending"})


# ---------- Phase D: photo upload for a listing (auth + owner, size/type limits) ----------

MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB per file
_ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/pjpeg", "image/png"}


def _normalize_image(raw: bytes, content_type: str):
    """Return (ok, jpeg_bytes). Prefer Pillow (verify it is a real image + re-encode to JPEG,
    which also strips EXIF/GPS - owners rarely mean to publish their geotag or phone model).
    Without Pillow fall back to a magic-byte sniff so a non-image can never slip in."""
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        return False, b""
    if Image is not None:
        try:
            im = Image.open(io.BytesIO(raw))
            im.verify()  # reject truncated / mislabelled files
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            return True, buf.getvalue()
        except Exception:
            return False, b""
    is_jpeg = raw[:3] == b"\xff\xd8\xff"
    is_png = raw[:8] == b"\x89PNG\r\n\x1a\n"
    if is_jpeg or is_png:
        return True, raw
    return False, b""


async def api_upload_photos(request: web.Request) -> web.Response:
    """Attach up to 10 photos to the owner's own listing. Files are written by us as
    {listing_id}_{i}.jpg - the client never chooses the name, so there is no path traversal."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    listing_id = int(request.match_info["id"])
    conn = request.app["db"]
    cur = await conn.execute(
        "SELECT l.user_id, u.tg_id FROM listings l JOIN users u ON u.id = l.user_id WHERE l.id = ?",
        (listing_id,))
    row = await cur.fetchone()
    if not row:
        return web.json_response({"error": "not_found"}, status=404)
    if str(row["tg_id"]) != str(viewer["id"]):
        return web.json_response({"error": "not_owner"}, status=403)

    try:
        data = await request.post()
    except Exception:
        return web.json_response({"error": "bad_multipart"}, status=400)
    # a real aiohttp MultiDictProxy; tests pass a plain dict-like with getall().
    fields = data.getall("photos", []) if hasattr(data, "getall") else data.get("photos", [])
    if not isinstance(fields, list):
        fields = [fields]
    files = [f for f in fields if hasattr(f, "file") and hasattr(f.file, "read")][:MAX_PHOTOS]
    if not files:
        return web.json_response({"error": "no_files"}, status=400)

    names = []
    for f in files:
        try:
            raw = f.file.read()
        except Exception:
            continue
        ok, blob = _normalize_image(raw, getattr(f, "content_type", ""))
        if not ok:
            continue
        name = f"{listing_id}_{len(names)}.jpg"
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        try:
            with open(os.path.join(PHOTOS_DIR, name), "wb") as fh:
                fh.write(blob)
        except OSError:
            continue
        names.append(name)
    if not names:
        return web.json_response({"error": "no_valid_images"}, status=400)
    await conn.execute(
        "UPDATE listings SET photos = ?, updated_at = datetime('now') WHERE id = ?",
        (json.dumps(names), listing_id))
    await conn.commit()
    return web.json_response({"ok": True, "id": listing_id, "photos": [f"/photos/{n}" for n in names]})


# ---------- Saved search ("Следить за поиском"): auth-gated, owner-scoped ----------

def _alert_filters(body: dict) -> dict:
    """Normalise a filter set the same way api_listings reads it. Empty strings become None
    (= 'any'), so a saved alert matches the live search exactly."""
    deal = (str(body.get("deal") or "").strip() or None)
    if deal not in ("rent", "sale"):
        deal = None
    district = (str(body.get("district") or "").strip() or None)
    rooms_raw = str(body.get("rooms") or "").strip()
    rooms = int(rooms_raw) if rooms_raw.isdigit() else None
    if rooms is not None and rooms >= 3:
        rooms = 3  # the 3+ bucket, mirroring api_listings
    pm_raw = str(body.get("price_max") or "").strip()
    price_max = int(re.sub(r"[^\d]", "", pm_raw) or 0) if pm_raw else None
    if price_max is not None and price_max <= 0:
        price_max = None
    query = (str(body.get("query") or "").strip()[:60] or None)
    return {"deal": deal, "district": district, "rooms": rooms, "price_max": price_max, "query": query}


async def api_create_search_alert(request: web.Request) -> web.Response:
    """Watch the current search. Re-subscribing the same filter re-arms it without duplicating.
    last_listing_id starts at the newest live id, so the owner is only pinged for genuinely new ads."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "bad_json"}, status=400)
    f = _alert_filters(body)
    conn = request.app["db"]
    cur = await conn.execute("SELECT id FROM users WHERE tg_id = ?", (viewer["id"],))
    urow = await cur.fetchone()
    if not urow:
        return web.json_response({"error": "no_user"}, status=400)
    uid = urow["id"]
    # Cold-start alert hook: a first-time watcher gets a free subscription window, so the value of
    # alerts lands before any money is asked. Fires only when they have NEVER subscribed
    # (alerts_until IS NULL) - an expired trial or lapsed paid plan is not re-baited for free.
    if config.PAID_ENABLED:
        await conn.execute(
            "UPDATE users SET alerts_until = datetime('now', ?) "
            "WHERE id = ? AND alerts_until IS NULL",
            (f"+{config.ALERTS_MONTH_DAYS} days", uid))
        await conn.commit()
    # one alert per identical filter set -> re-arm instead of piling up duplicates
    cur = await conn.execute(
        "SELECT id FROM search_alerts WHERE user_id = ? AND ifnull(deal,'') = ifnull(?, '') "
        "AND ifnull(district,'') = ifnull(?, '') AND ifnull(rooms,-1) = ifnull(?, -1) "
        "AND ifnull(price_max,-1) = ifnull(?, -1) AND lower(ifnull(query,'')) = lower(ifnull(?, ''))",
        (uid, f["deal"], f["district"], f["rooms"], f["price_max"], f["query"]))
    existing = await cur.fetchone()
    cur = await conn.execute("SELECT COALESCE(MAX(id), 0) m FROM listings")
    newest = (await cur.fetchone())["m"]
    if existing:
        await conn.execute("UPDATE search_alerts SET last_listing_id = ? WHERE id = ?",
                           (newest, existing["id"]))
        await conn.commit()
        return web.json_response({"ok": True, "id": existing["id"], "existed": True})
    cur = await conn.execute(
        "INSERT INTO search_alerts (user_id, tg_id, deal, district, rooms, price_max, query, last_listing_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (uid, viewer["id"], f["deal"], f["district"], f["rooms"], f["price_max"], f["query"], newest))
    await conn.execute("INSERT INTO events (name, user_id) VALUES ('search_alert_created', ?)", (uid,))
    await conn.commit()
    return web.json_response({"ok": True, "id": cur.lastrowid, "existed": False})


async def api_search_alerts(request: web.Request) -> web.Response:
    """The viewer's saved searches, so the app can show which filters are already watched."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    conn = request.app["db"]
    cur = await conn.execute("SELECT id FROM users WHERE tg_id = ?", (viewer["id"],))
    urow = await cur.fetchone()
    if not urow:
        return web.json_response({"items": []})
    cur = await conn.execute(
        "SELECT * FROM search_alerts WHERE user_id = ? ORDER BY created_at DESC", (urow["id"],))
    rows = await cur.fetchall()
    items = [{
        "id": r["id"], "deal": r["deal"], "district": r["district"], "rooms": r["rooms"],
        "price_max": r["price_max"], "query": r["query"],
    } for r in rows]
    return web.json_response({"items": items})


async def api_delete_search_alert(request: web.Request) -> web.Response:
    """Unsubscribe. Owner-only: DELETE is scoped by user_id, so a foreign id simply no-ops to 404."""
    viewer = auth_user(request)
    if not viewer:
        return web.json_response({"error": "auth_required"}, status=401)
    alert_id = int(request.match_info["id"])
    conn = request.app["db"]
    cur = await conn.execute("SELECT id FROM users WHERE tg_id = ?", (viewer["id"],))
    urow = await cur.fetchone()
    if not urow:
        return web.json_response({"error": "not_found"}, status=404)
    cur = await conn.execute("DELETE FROM search_alerts WHERE id = ? AND user_id = ?",
                             (alert_id, urow["id"]))
    await conn.commit()
    if cur.rowcount == 0:
        return web.json_response({"error": "not_found"}, status=404)
    return web.json_response({"ok": True, "id": alert_id})


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
    app.router.add_get("/api/my/listings", api_my_listings)
    app.router.add_post("/api/listing/{id}/action", api_listing_action)
    app.router.add_post("/api/listing", api_create_listing)
    app.router.add_post("/api/listing/{id}/photos", api_upload_photos)
    app.router.add_post("/api/search/alert", api_create_search_alert)
    app.router.add_get("/api/search/alerts", api_search_alerts)
    app.router.add_post("/api/search/alert/{id}/delete", api_delete_search_alert)
    app.router.add_post("/webhook/lemonsqueezy", webhook_lemonsqueezy)
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    app.router.add_static("/photos/", PHOTOS_DIR)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    web.run_app(app_factory(), host="127.0.0.1", port=8085)
