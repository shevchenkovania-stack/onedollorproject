import datetime
import html
import os

from aiogram.types import FSInputFile, InputMediaPhoto

import config
import db
import keyboards
from constants import DEAL_LABELS, PTYPE_LABELS, STATUS_LABELS


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def resolve_photo(value):
    """A listing photo can be a Telegram file_id (from the bot wizard) or a local file name /
    absolute path (uploaded through the Mini App, Phase D). aiogram only uploads a real file
    when handed an InputFile, so wrap anything that resolves to a file on disk; pass the rest
    (file_id / URL) straight through."""
    if not isinstance(value, str):
        return value
    cand = value if os.path.isabs(value) else os.path.join(config.PHOTOS_DIR, value)
    if os.path.isfile(cand):
        return FSInputFile(cand)
    return value


def hashtags(row) -> str:
    """One line of search keywords: the public channel and its posts are indexed by Telegram
    search, so an untagged card is invisible to anyone typing 'аренда кишинёв'."""
    tags = ["#продажа" if row["deal"] == "sale" else "#аренда", "#кишинёв", "#chisinau"]
    district = row["district"]
    if district and district != "Другое":
        tags.append("#" + str(district).replace(" ", ""))
    return " ".join(tags)


def _featured(row) -> bool:
    """Paid "Срочно" badge is live when featured_until is set and in the future."""
    try:
        v = row["featured_until"]
    except (IndexError, KeyError):
        return False
    return bool(v and str(v) > _now_str())


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def listing_caption(row, show_status: bool = False, founding: int = 0) -> str:
    deal = DEAL_LABELS.get(row["deal"], row["deal"])
    ptype = PTYPE_LABELS.get(row["ptype"], row["ptype"])
    badge = "\U0001F525 <b>СРОЧНО</b> · " if _featured(row) else ""
    lines = [f"<b>#{row['id']} · {badge}{deal} · {ptype}</b>"]
    parts = []
    if row["rooms"]:
        parts.append(f"{row['rooms']} комн.")
    if row["area"]:
        parts.append(f"{row['area']} м²")
    if row["floor"]:
        f = f"{row['floor']}"
        if row["floors"]:
            f += f"/{row['floors']}"
        parts.append(f"{f} этаж")
    if parts:
        lines.append(" · ".join(parts))
    lines.append(f"\U0001F4CD {esc(row['district'])}")
    lines.append(f"\U0001F4B0 <b>{row['price']} {esc(row['currency'])}</b>")
    if row["description"]:
        lines.append("")
        lines.append(esc(row["description"]))
    if founding:
        # scarcity/status: a real paid founding member, visible to every viewer
        lines.append("")
        lines.append(f"\U0001F3C6 <b>Founding-участник #{founding}</b> · Однушка.md")
    lines.append("")
    lines.append(hashtags(row))
    if show_status:
        lines.append("")
        lines.append(f"Статус: {STATUS_LABELS.get(row['status'], row['status'])}")
    return "\n".join(lines)


async def send_listing(bot, chat_id: int, row, owner: bool = False, with_keyboard: bool = True) -> None:
    photos = db.listing_photos(row)
    owner_user = await db.get_user_by_id(row["user_id"])
    try:
        founding = int(owner_user["founding"]) if owner_user and owner_user["founding"] else 0
    except (IndexError, KeyError, TypeError):
        founding = 0
    caption = listing_caption(row, show_status=owner, founding=founding)
    kb = keyboards.listing_kb(row["id"], owner, bool(row["boosted_until"]),
                              contacts=db.listing_contacts(row)) if with_keyboard else None
    if photos:
        media = []
        for i, p in enumerate(photos[:10]):
            src = resolve_photo(p)
            if i == 0:
                media.append(InputMediaPhoto(media=src, caption=caption, parse_mode="HTML"))
            else:
                media.append(InputMediaPhoto(media=src))
        await bot.send_media_group(chat_id, media)
        if kb:
            await bot.send_message(chat_id, "\u2b07\uFE0F", reply_markup=kb)
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=kb)


def listing_button_text(row) -> str:
    deal = DEAL_LABELS.get(row["deal"], row["deal"])[:2]
    ptype = PTYPE_LABELS.get(row["ptype"], row["ptype"])[:4]
    rooms = f"{row['rooms']}к " if row["rooms"] else ""
    star = "\u2B50 " if row["boosted_until"] else ""
    return f"{star}{deal} · {ptype} · {rooms}{row['district']} · {row['price']}{row['currency']}"
