#!/usr/bin/env python3
"""First-glance clarity for the channel, the standard way: a short 'what is this / what to do'
pinned post with tappable buttons, plus a channel description.

Idempotent: if a post is already pinned, it is EDITED in place (no duplicate clutter); only when
nothing is pinned does it send a fresh one and pin it. Safe to re-run whenever the text changes.

Run on prod from /opt/imobiliare/bot:  /opt/imobiliare/venv/bin/python pin_roles.py
"""
import asyncio
import sys

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config

DESCRIPTION = (
    "Аренда и продажа квартир и домов в Кишинёве и пригородах — без комиссии и без платы "
    "за объявление. Chirie Chișinău · контакт владельца на каждой карточке. "
    "#аренда #продажа #кишинёв #chirie\nПодать объявление: "
) + config.BOT_URL

# intent first, two lines, then buttons - a newcomer maps 'I'm looking' vs 'I'm posting' at a glance
PINNED = (
    "\U0001F4CD <b>Găsește Casa</b> — здесь выходит каждый новый объект: квартиры и дома "
    "в Кишинёве, аренда и продажа.\n\n"
    "\U0001F50D Ты <b>ищешь</b> → открыл карточку, контакт прямо на ней — договариваешься напрямую.\n"
    "\u2795 Ты <b>сдаёшь / продаёшь</b> → подай объявление в боте. Бесплатно, 2 минуты.\n\n"
    "Комиссии нет ни с кого. Жми кнопку \U0001F447\n\n"
    "#аренда #продажа #кишинёв #chirie #безкомиссии"
)


def _buttons() -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="\U0001F50D Искать жильё", url=config.BOT_URL),
        InlineKeyboardButton(text="\u2795 Подать объявление", url=f"{config.BOT_URL}?start=add"),
    ]]
    if config.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="\U0001F4F1 Открыть доску со всеми", url=config.WEBAPP_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def main() -> None:
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest
    bot = Bot(token=config.BOT_TOKEN)
    try:
        await bot.set_chat_description(config.CHANNEL_ID, description=DESCRIPTION[:255])
    except TelegramBadRequest as e:
        # Telegram errors when the text is already identical - harmless on re-run
        if "not modified" not in str(e):
            raise

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
