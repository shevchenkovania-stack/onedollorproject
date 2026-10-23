#!/usr/bin/env python3
"""First-glance clarity for the channel, the standard way: a short 'what is this / what to do'
pinned post with tappable buttons.

Idempotent: if a post is already pinned, it is EDITED in place (no duplicate clutter); only when
nothing is pinned does it send a fresh one and pin it. Safe to re-run whenever the text changes.

The channel NAME and DESCRIPTION are owned by seo_seeds.py (single source for everything the
Telegram search indexes) - this file only writes the pinned navigation post.

Run on prod from /opt/imobiliare/bot:  /opt/imobiliare/venv/bin/python pin_roles.py
"""
import asyncio
import sys

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config

# intent first, two lines, then buttons - a newcomer maps 'I'm looking' vs 'I'm posting' at a glance
PINNED = (
    "\U0001F4CD <b>Однушки в Кишинёве</b> — здесь выходит каждая новая однушка от хозяина: "
    "район, цена, контакт на карточке.\n\n"
    "\U0001F50D Ты <b>ищешь</b> → открыл карточку, связываешься напрямую.\n"
    "\u2795 Ты <b>сдаёшь</b> → подай объявление в боте. Бесплатно, 2 минуты.\n\n"
    "Комиссии нет ни с кого. Жми кнопку \U0001F447\n\n"
    "#аренда #однушки #кишинёв #chirie #безкомиссии"
)


def _buttons() -> InlineKeyboardMarkup:
    rows = [[
        # the channel is a search surface: its button carries a tracked deep link
        InlineKeyboardButton(text="\U0001F50D Искать жильё", url=f"{config.BOT_URL}?start=seo_board"),
        InlineKeyboardButton(text="\u2795 Подать объявление", url=f"{config.BOT_URL}?start=add"),
    ]]
    if config.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="\U0001F4F1 Открыть доску со всеми", url=config.WEBAPP_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def main() -> None:
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest
    if not config.CHANNEL_ID:
        raise SystemExit("CHANNEL_ID not set - the channel is hidden, nothing to pin")
    bot = Bot(token=config.BOT_TOKEN)
    chat = await bot.get_chat(config.CHANNEL_ID)
    pm = getattr(chat, "pinned_message", None)
    if pm is not None:
        # already pinned -> replace it in place; keeps the channel free of duplicate nav posts
        try:
            await bot.edit_message_text(
                chat_id=config.CHANNEL_ID, message_id=pm.message_id,
                text=PINNED, parse_mode="HTML", reply_markup=_buttons(),
            )
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise
        await bot.pin_chat_message(config.CHANNEL_ID, pm.message_id, disable_notification=True)
        print(f"\u2705 NAV EDITED + PINNED, message id {pm.message_id}")
    else:
        posted = await bot.send_message(
            config.CHANNEL_ID, PINNED, parse_mode="HTML", reply_markup=_buttons()
        )
        await bot.pin_chat_message(config.CHANNEL_ID, posted.message_id, disable_notification=True)
        print(f"\u2705 NAV SENT + PINNED, message id {posted.message_id}")
    sys.stdout.flush()
    import os
    os._exit(0)  # old aiogram session.close() can hang; work is committed


asyncio.run(main())
