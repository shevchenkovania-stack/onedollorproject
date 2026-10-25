"""Publishing approved listings: download photos for Mini App + post to public channel."""
import os

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

import config
import db
import keyboards
import render

MAX_PHOTOS = 10


async def download_photos(bot: Bot, row) -> None:
    """Save listing photos as {listing_id}_{i}.jpg in PHOTOS_DIR for the webapp."""
    photos = db.listing_photos(row)[:MAX_PHOTOS]
    if not photos:
        return
    os.makedirs(config.PHOTOS_DIR, exist_ok=True)
    for i, file_id in enumerate(photos):
        dest = os.path.join(config.PHOTOS_DIR, f"{row['id']}_{i}.jpg")
        if os.path.exists(dest):
            continue
        try:
            f = await bot.get_file(file_id)
            await bot.download_file(f.file_path, dest)
        except Exception:
            pass  # missing photo only degrades the web card


def contact_kb(row, owner) -> InlineKeyboardMarkup | None:
    rows = []
    contacts = db.listing_contacts(row) or {}
    # owner may set an explicit public tg handle on the listing; if so it wins over the
    # personal @username from their Telegram profile, so we do not render two Telegram buttons
    if contacts.get("telegram"):
        rows.append([InlineKeyboardButton(
            text="\U0001F4AC Написать в Telegram",
            url="https://t.me/" + str(contacts["telegram"]).lstrip("@").removeprefix("https://t.me/"))])
    elif owner and owner["username"]:
        rows.append([InlineKeyboardButton(text="\U0001F4AC Написать в Telegram",
                                         url=f"https://t.me/{owner['username']}")])
    # every other public channel the owner was willing to attach: instagram / whatsapp /
    # viber / facebook / site. same builder as the DM card, one source of truth
    without_tg = {k: v for k, v in contacts.items() if k != "telegram"}
    rows.extend(keyboards.contact_buttons(without_tg))
    if config.WEBAPP_URL:
        # a channel post CANNOT carry a web_app button (Telegram -> BUTTON_TYPE_INVALID), so the
        # Mini App is linked as a plain url here. The initData-gated flow (placing / boosting an
        # ad) lives in the bot DM card; the channel card only needs to open the board to browse.
        rows.append([InlineKeyboardButton(text="\U0001F50D Смотреть все объекты",
                                         url=config.WEBAPP_URL)])
    elif config.CHANNEL_URL:
        rows.append([InlineKeyboardButton(text="\U0001F50D Смотреть все объекты", url=config.CHANNEL_URL)])
    # every channel post is a shareable advert: forwarding brings new viewers AND new owners
    share = keyboards.share_url(row["id"])
    if share:
        rows.append([InlineKeyboardButton(text="\U0001F4E4 Поделиться", url=share)])
    if config.BOT_URL:
        rows.append([InlineKeyboardButton(text="\u2795 Разместить своё объявление", url=config.BOT_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def to_channel(bot: Bot, row) -> list[int]:
    if not config.CHANNEL_ID:
        return []
    photos = db.listing_photos(row)[:MAX_PHOTOS]
    caption = render.listing_caption(row)
    if row["phone"]:
        # the phone is free text typed by the owner; a stray < or & would break the whole HTML
        # caption and the post would fail (bug #1). escape it like every other owner field.
        caption += f"\n\U0001F4DE {render.esc(row['phone'])}"
    # reinforce the CTA keyboard in text: when a card is forwarded, the buttons can be cut off
    # on preview, but the caption always travels with the photo.
    if config.BOT_URL:
        caption += f"\n\n\U0001F4E3 Также бесплатно → {config.BOT_URL}?start=add"
    owner = await db.get_user_by_id(row["user_id"])
    kb = contact_kb(row, owner)
    web_photos = [os.path.join(config.PHOTOS_DIR, f"{row['id']}_{i}.jpg")
                  for i in range(min(len(photos), MAX_PHOTOS))
                  if os.path.exists(os.path.join(config.PHOTOS_DIR, f"{row['id']}_{i}.jpg"))]
    if web_photos:
        media = [InputMediaPhoto(media=render.resolve_photo(p), caption=caption if i == 0 else None, parse_mode="HTML")
                 for i, p in enumerate(web_photos)]
        sent = await bot.send_media_group(config.CHANNEL_ID, media)
        try:
            msg = await bot.send_message(config.CHANNEL_ID, "↑ контакты и детали ниже", reply_markup=kb)
        except Exception:
            # the album is already live; if the buttons message is rejected (a bad button type,
            # a lost connection) do not leave a button-less orphan card on the board - retract the
            # just-posted album and let the caller retry the whole card cleanly.
            for m in sent:
                try:
                    await bot.delete_message(config.CHANNEL_ID, m.message_id)
                except Exception:
                    pass
            raise
        ids = [m.message_id for m in sent] + [msg.message_id]
    else:
        msg = await bot.send_message(config.CHANNEL_ID, caption, parse_mode="HTML", reply_markup=kb)
        ids = [msg.message_id]
    await db.log_event("channel_post", listing_id=row["id"], meta=",".join(map(str, ids)))
    return ids


async def publish_listing(bot: Bot, row) -> list[int]:
    await download_photos(bot, row)
    return await to_channel(bot, row)
