"""Seed the DB and the channel with the listings from listings.json.

Reads listings.json from the same folder. Photos: looks for
deploy/test_data/images/<name>.jpg - if a file is missing, generates a labelled
PIL placeholder so the seed never fails. Runs on the VPS, not locally.

Usage:
  powershell -File deploy\\run.ps1 deploy\\test_data\\listings.json \
      'echo ok' -RemoteDir /opt/imobiliare/test_data
  powershell -File deploy\\run.ps1 deploy\\test_data\\seed_test_data.py \
      'echo ok' -RemoteDir /opt/imobiliare/test_data
  # optionally scp images first:
  scp deploy/test_data/images/*.jpg root@<ip>:/opt/imobiliare/test_data/images/
  powershell -File deploy\\run.ps1 deploy\\_hc.ps1 \
      'cd /opt/imobiliare/test_data && /opt/imobiliare/venv/bin/python seed_test_data.py'
"""
import asyncio
import io
import json
import os
import sys

from aiogram import Bot
from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(os.path.join(HERE, "listings.json"), encoding="utf-8"))
IMAGES_DIR = os.path.join(HERE, "images")

sys.path.insert(0, "/opt/imobiliare/bot")
import config  # noqa: E402
import db  # noqa: E402
import publish  # noqa: E402


# 10 base hues so demo tiles look varied but cohesive (all sit in one brand family)
_PALETTE = ["#0f766e", "#155e75", "#1d4ed8", "#4338ca", "#6d28d9",
            "#7e22ce", "#0e7490", "#065f46", "#0f5132", "#1e3a8a"]


def _shade(hex_color: str, factor: float) -> tuple:
    """Darken/lighten a #rrggbb into an (r,g,b) tuple for the gradient stop."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(max(0, min(255, int(c * factor))) for c in (r, g, b))


def _placeholder(text: str, idx: int) -> bytes:
    """A polished 800x600 demo tile (brand gradient + house line-art + district/price) used when
    a real photo is not present. It reads as an intentional showcase card, never a broken image,
    and is honest: it says ДЕМО. Cyrillic-safe via DejaVu; no emoji (the font has none)."""
    W, H = 800, 600
    top = _shade(_PALETTE[idx % len(_PALETTE)], 1.15)
    bottom = _shade(_PALETTE[idx % len(_PALETTE)], 0.6)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # simple vertical gradient
        t = y / (H - 1)
        d.line((0, y, W, y), fill=tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3)))
    try:
        f1 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        f3 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except OSError:
        f1 = f2 = f3 = ImageFont.load_default()
    # house line-art, translucent white, upper-centre
    line = (255, 255, 255, 210)
    ox = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ox)
    od.line((320, 250, 400, 185, 480, 250), fill=line, width=7, joint="curve")  # roof
    od.rectangle((335, 250, 465, 345), outline=line, width=7)                    # body
    od.rectangle((388, 300, 412, 345), outline=line, width=5)                    # door
    img.paste(Image.alpha_composite(img.convert("RGBA"), ox).convert("RGB"))
    d = ImageDraw.Draw(img)
    d.rectangle((18, 18, W - 18, H - 18), outline=(255, 255, 255, 180), width=2)
    # text: line 0 = "district · price", line 1 = small street note
    parts = text.split("\n")
    title = parts[0][:40]
    sub = parts[1][:52] if len(parts) > 1 else ""
    bbox = d.textbbox((0, 0), title, font=f1)
    d.text(((W - bbox[2] + bbox[0]) / 2, 392), title, fill="white", font=f1)
    if sub:
        bbox2 = d.textbbox((0, 0), sub, font=f2)
        d.text(((W - bbox2[2] + bbox2[0]) / 2, 444), sub, fill=(255, 255, 255, 220), font=f2)
    d.text((30, 30), "ДЕМО", fill="white", font=f2)
    bbox3 = d.textbbox((0, 0), "Однушка.md", font=f3)
    d.text((W - bbox3[2] + bbox3[0] - 30, H - 42), "Однушка.md", fill=(255, 255, 255, 200), font=f3)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _compose_description(item: dict) -> str:
    """Same join as add.py:s_confirm - features become a ' · ' line at the end."""
    desc = item.get("description", "")
    feats = item.get("features") or []
    if feats:
        desc = (desc.rstrip() + "\n\n" + " · ".join(feats)).strip()
    return desc


_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _load_pool() -> list:
    """All real photos dropped into images/ (any names), sorted for determinism."""
    if not os.path.isdir(IMAGES_DIR):
        return []
    names = [n for n in os.listdir(IMAGES_DIR) if n.lower().endswith(_EXT)]
    return sorted(names)


async def _send_one(bot: Bot, admin_tg_id: int, db_user_id: int, item: dict,
                    idx: int, pool: list, cursor: int) -> tuple[int | None, int]:
    """Upload this item's OWN photos (by filename in images/, e.g. rNN.jpg), create the
    listing and publish it. A photo file that is missing falls back to a labelled PIL
    placeholder so the seed never fails. Returns (listing_id, cursor)."""
    names = item.get("photos") or []
    if not names:
        names = [f"ph{idx}.jpg"]
    file_ids = []
    for k, name in enumerate(names):
        path = os.path.join(IMAGES_DIR, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                blob = f.read()
        else:
            blob = _placeholder(f"{item['district']} \u00b7 {item['price']} \u20ac\n{name}", idx)
        msg = await bot.send_photo(admin_tg_id, BufferedInputFile(blob, filename=name))
        file_ids.append(msg.photo[-1].file_id)
    lid = await db.create_listing(
        user_id=db_user_id,
        deal=item["deal"], ptype=item["ptype"], rooms=item["rooms"],
        price=item["price"], currency=item.get("currency", "EUR"),
        district=item["district"],
        area=item.get("area"), floor=item.get("floor"), floors=item.get("floors"),
        phone=item.get("phone"),
        description=_compose_description(item),
        photos=file_ids,
        contacts=item.get("contacts") or {},
        is_demo=int(item.get("is_demo", 1)),
    )
    await db.set_listing_status(lid, "active")
    row = await db.get_listing(lid)
    try:
        # publish_listing = download_photos (save {id}_{i}.jpg to PHOTOS_DIR) + to_channel.
        # Calling to_channel directly would post TEXT only - the channel/Mini App build the
        # album from the on-disk jpgs, so the real publish path must run here.
        ids = await publish.publish_listing(bot, row)
        print(f"  #{lid} '{item['slug']}' -> channel msg ids {ids}")
    except Exception as e:
        print(f"  #{lid} '{item['slug']}' -> !! publish failed: {type(e).__name__}: {e}")
    return lid, cursor


async def main() -> None:
    await db.init_db()
    admin_tg = config.ADMIN_IDS[0] if config.ADMIN_IDS else 1182157446
    owner = await db.get_or_create_user(admin_tg, "test_owner", "Test Owner")
    bot = Bot(token=config.BOT_TOKEN)
    print(f"admin_tg={admin_tg}, db_user_id={owner['id']}")
    pool = _load_pool()
    print(f"photo pool: {len(pool)} real file(s)" if pool else "photo pool EMPTY -> PIL placeholders")
    created = []
    cursor = 0
    for i, item in enumerate(DATA["listings"]):
        try:
            lid, cursor = await _send_one(bot, admin_tg, owner["id"], item, i, pool, cursor)
            if lid:
                created.append(lid)
        except Exception as e:
            print(f"!! item '{item['slug']}' failed: {type(e).__name__}: {e}")
        await asyncio.sleep(1.0)
    print(f"created {len(created)} listings: {created}")
    # record created ids so a re-run can clean exactly these up (self-cleaning seed)
    try:
        with open("/tmp/seed_created.txt", "w", encoding="utf-8") as f:
            f.write(",".join(str(x) for x in created))
    except OSError:
        pass
    print("stats:", await db.stats())
    await bot.session.close()
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
