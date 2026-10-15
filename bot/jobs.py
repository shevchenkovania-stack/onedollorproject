"""Stage 4: the listing life cycle worker.

One task, woken once an hour, doing the two time-based jobs the board needs so it never
turns into a graveyard of year-old ads:

  1. archive ads whose LISTING_DAYS window has passed (status -> archived, off the board);
  2. warn owners EXPIRE_REMINDER_DAYS before that happens and offer a one-tap "Продлить".

The reminder dedup lives in `events` (an `expire_remind` row per listing), so a restart in
the middle of a window cannot re-spam an owner. Archiving is soft: the row stays and
"Продлить" brings it back, which also makes "Сдано"/"Удалить" reversible from the card.
"""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import db

log = logging.getLogger("chiriebot.jobs")

TICK_SECONDS = 3600
# remind about the same ad at most once a week, not every hourly tick
REMIND_COOLDOWN_HOURS = 24 * 7


def _extend_kb(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="\U0001F501 Продлить", callback_data=f"extend:{listing_id}"),
    ]])


def _brief(row) -> str:
    district = f", {row['district']}" if row["district"] else ""
    return f"#{row['id']} ({row['price']} {row['currency']}{district})"


async def _safe_send(bot: Bot, tg_id: int, text: str, markup: InlineKeyboardMarkup) -> None:
    """A user who blocked the bot or was deleted must never break the loop for everyone else."""
    try:
        await bot.send_message(tg_id, text, reply_markup=markup)
    except Exception:
        log.warning("job message to %s failed", tg_id, exc_info=True)


async def archive_and_remind(bot: Bot) -> tuple[int, int]:
    """Run one pass. Returns (archived, reminded) counts for logging/tests."""
    archived = await db.archive_expired()
    for row in archived:
        owner = await db.get_user_by_id(row["user_id"])
        if not owner or not owner["tg_id"]:
            continue
        await _safe_send(
            bot, owner["tg_id"],
            f"\u23F3 Объявление {_brief(row)} провисело {config.LISTING_DAYS} дней и ушло в архив.\n"
            f"Продлить ещё на {config.LISTING_DAYS} дней?",
            _extend_kb(row["id"]),
        )

    reminded = 0
    soon = await db.listings_expiring_in(config.EXPIRE_REMINDER_DAYS)
    for row in soon:
        if await db.event_recent("expire_remind", str(row["id"]), REMIND_COOLDOWN_HOURS):
            continue
        if not row["tg_id"]:
            continue
        await _safe_send(
            bot, row["tg_id"],
            f"\u267B\uFE0F Объявление {_brief(row)} в ближайшие "
            f"{config.EXPIRE_REMINDER_DAYS} дней уйдёт в архив.\n"
            f"Продлить ещё на {config.LISTING_DAYS} дней?",
            _extend_kb(row["id"]),
        )
        await db.log_event("expire_remind", user_id=row["user_id"],
                           listing_id=row["id"], meta=str(row["id"]))
        reminded += 1
    return len(archived), reminded


async def run(bot: Bot) -> None:
    log.info("life-cycle job: waking every %ss", TICK_SECONDS)
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            archived, reminded = await archive_and_remind(bot)
            if archived or reminded:
                log.info("life-cycle: archived=%s reminded=%s", archived, reminded)
        except Exception:
            log.exception("life-cycle tick failed")
