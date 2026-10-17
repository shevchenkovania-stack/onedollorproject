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
from datetime import datetime, timedelta, timezone

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
        try:
            await maybe_daily_digest(bot)
        except Exception:
            log.exception("daily digest failed")


async def maybe_daily_digest(bot: Bot) -> bool:
    """Once per UTC-day (after 07:00 UTC ~ 10:00 Chisinau) DM the admins a compact funnel summary.

    Unconditional (no opt-in flag): the whole point is that the owner sees the number whether he
    asked for it or not. If traffic is zero, that fact has to arrive as a message, not stay in a
    DB nobody queries. Dedup by date in events.meta so a mid-hour restart can't resend.
    """
    now = datetime.now(timezone.utc)
    if now.hour < 7:
        return False
    yesterday = (now - timedelta(days=1)).date().isoformat()
    if await db.event_recent("daily_digest", yesterday, 30):
        return False
    # 8 rows: yesterday .. 7 days before. Show yesterday's day + a 7-day total below.
    rows = await db.daily_stats(8)
    yday = rows[0] if rows else None
    prev7 = rows[1:8]
    lines = [f"\U0001F4CA <b>Дайджест за {yesterday}</b>"]
    if yday:
        parts = []
        if yday["bot_opens"]:
            parts.append(f"бот открывали {yday['bot_opens']}× ({yday['bot_uniq']} чел)")
        if yday["app_opens"]:
            parts.append(f"Mini App {yday['app_opens']}× ({yday['app_uniq']} чел)")
        if yday["card_views"]:
            parts.append(f"карточек посмотрели {yday['card_views']}")
        if yday["contacts"]:
            parts.append(f"телефон открыли {yday['contacts']}")
        if yday["new_ads"]:
            parts.append(f"+{yday['new_ads']} объяв")
        if yday["alerts"]:
            parts.append(f"🔔 {yday['alerts']}")
        if yday["new_users"]:
            parts.append(f"+{yday['new_users']} юзеров")
        lines.append(" · ".join(parts) if parts else "— на доске был ноль —")
    if prev7:
        tot = {k: sum(r[k] for r in prev7) for k in
               ("bot_opens", "app_opens", "card_views", "contacts", "new_ads", "alerts", "new_users")}
        lines.append(
            f"За 7 дней: бот {tot['bot_opens']}, app {tot['app_opens']}, "
            f"карточек {tot['card_views']}, телефонов {tot['contacts']}, "
            f"объяв {tot['new_ads']}, 🔔 {tot['alerts']}, юзеров +{tot['new_users']}"
        )
    text = "\n".join(lines)
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, text, parse_mode="HTML")
        except Exception:
            log.warning("daily digest to %s failed", admin, exc_info=True)
    await db.log_event("daily_digest", meta=yesterday)
    return True
