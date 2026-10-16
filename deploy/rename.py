#!/usr/bin/env python3
"""Rebrand the Telegram surfaces under one name: "Однушка.md".

Single, idempotent tool for the identity that Telegram indexes and a user sees in the chat list:
  - bot display name           (set_my_name for the default + "ru")
  - bot short + long description (search-indexed; states the one-bedroom rental wedge)
  - channel title              ONLY when the channel is wired up (skipped while it is hidden)

Note on the handle: the @username (@gaseste_casa_bot) cannot be changed via the Bot API - it is
permanent and only the display name moves. Deep links therefore keep the old handle; that is fine
and what users actually read is the display name.

Run on prod from /opt/imobiliare/bot:  /opt/imobiliare/venv/bin/python rename.py
"""
import asyncio

from aiogram import Bot

import config

BOT_TITLE = "Однушка.md · Аренда однушек в Кишинёве"
CHANNEL_TITLE = "Однушка.md · Лента однушек"
BOT_SHORT = "Аренда однушек в Кишинёве: поиск и подача без комиссии, от хозяев."
BOT_LONG = (
    "«Однушка.md» — доска однушек в аренду в Кишинёве, прямо в Telegram.\n\n"
    "• Только однушки и аренда — без шума «2/3 комнаты, дома, продажа»\n"
    "• Подать объявление — бесплатно, за 2 минуты\n"
    "• Контакт хозяина напрямую, без комиссии и посредников\n"
    "• Не хочешь листать — напиши район и бюджет: подберём и следим за новыми\n\n"
    "Chirie Chișinău · garsonieră · поиск и объявления без комиссии."
)


async def main() -> None:
    bot = Bot(token=config.BOT_TOKEN)

    me = await bot.get_me()
    print("bot name was:", me.first_name)
    await bot.set_my_name(name=BOT_TITLE, language_code="")
    await bot.set_my_name(name=BOT_TITLE, language_code="ru")
    await bot.set_my_short_description(short_description=BOT_SHORT)
    await bot.set_my_description(description=BOT_LONG)
    me = await bot.get_me()
    print("bot name now:", me.first_name)

    if config.CHANNEL_ID:
        chat = await bot.get_chat(config.CHANNEL_ID)
        print("channel title was:", chat.title)
        await bot.set_chat_title(config.CHANNEL_ID, title=CHANNEL_TITLE)
        chat = await bot.get_chat(config.CHANNEL_ID)
        print("channel now:  ", chat.title)
    else:
        print("channel hidden (CHANNEL_ID=None) - skipping channel title")

    ok = me.first_name == BOT_TITLE
    print("✅ RENAMED OK" if ok else "⚠️ check failed")
    await bot.session.close()


asyncio.run(main())
