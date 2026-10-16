"""Storefront for Telegram search: titles, descriptions and the posts that make an empty
channel look alive.

Idempotent - safe to re-run after every copy change:
  1. channel title + description (this is what Telegram search indexes for the channel);
  2. bot short + long description (indexed for the bot - it used to advertise Stars as the
     payment, while the real rail is a 100 MDL MIA transfer);
  3. three "how it works" posts. Message ids are remembered in
     /opt/imobiliare/data/channel_series.json, so a re-run EDITS them in place instead of
     spamming the feed. If Telegram refuses the edit (old message), a fresh one is posted.

Run: cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python /tmp/seo_channel.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config

STATE = "/opt/imobiliare/data/channel_series.json"

TITLE = "Однушка.md · Аренда и продажа Кишинёва"

SHORT = "Аренда и продажа жилья в Кишинёве: поиск и объявления без комиссии."

LONG = (
    "«Однушка.md» — объявления об аренде и продаже недвижимости в Кишинёве и пригородах.\n\n"
    "• Поиск по сделке, типу, комнатам, району и цене\n"
    "• Подать объявление — бесплатно, за 2 минуты\n"
    "• Контакт владельца напрямую, без комиссии и посредников\n"
    "• Поднять в топ (Boost) — 100 MDL переводом MIA или 400 Stars\n\n"
    "Свежие объекты выходят в канал: @gaseste_casa"
)

TAGS = "#аренда #продажа #кишинёв #chirie #безкомиссии"


def _btns(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def _add(text: str = "\u2795 Подать объявление") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=f"{config.BOT_URL}?start=add")


# Order of the list = order of posting, so the feed reads top->bottom as HELP, EXAMPLE,
# CONCIERGE (the newest post sits directly under the pinned one).
POSTS: list[tuple[str, str, InlineKeyboardMarkup]] = [
    (
        "concierge",
        "\U0001F4EC <b>Не хочешь заполнять формы?</b>\n\n"
        "Пришли <b>фото + район + цену</b> — оформим объявление за тебя.\n"
        "Телефон остаётся твой, клиенты звонят напрямую. Комиссии нет.\n\n"
        "Как попасть сюда:\n"
        "\u2022 напиши боту обычным сообщением — передадим модератору\n"
        "\u2022 или пришли фото прямо в ответ на этот пост\n\n"
        "\U0001F381 Первым 10, кто выложит, — поднятие в топ в подарок.\n\n" + TAGS,
        _btns(
            [InlineKeyboardButton(text="\u270D\ufe0f Написать боту", url=config.BOT_URL)],
            [_add("\u2795 Подать самому")],
        ),
    ),
    (
        "example",
        "\U0001F9E9 <b>Так выглядит карточка</b> — пример оформления, "
        "не настоящее объявление:\n\n"
        "<b>#42 · Аренда · Квартира</b>\n"
        "1 комн. \u00b7 38 м\u00b2 \u00b7 4/9 этаж\n"
        "\U0001F4CD Ботаника\n"
        "\U0001F4B0 <b>350 EUR</b>\n\n"
        "Светлая, рядом парк и рынок. Мебель и техника есть.\n\n"
        "\U0001F4DE телефон виден прямо на карточке, \U0001F4AC кнопки «Написать» — "
        "один тап и человек у тебя в личке.\n"
        "Карточку можно переслать — кнопки останутся.\n\n"
        "Прислал фото + район + цену — карточку соберём сами.\n\n" + TAGS,
        _btns(
            [_add("\u2795 Разместить своё объявление")],
            [InlineKeyboardButton(text="\U0001F50D Искать объект", url=config.BOT_URL)],
        ),
    ),
    (
        "help",
        "\u2795 <b>Как подать объявление за 2 минуты</b>\n\n"
        "1\uFE0F\u20E3 открой бота \u2192 «\u2795 Разместить объект»\n"
        "2\uFE0F\u20E3 сделка и тип (чипами), район, цена, фото, контакт\n"
        "3\uFE0F\u20E3 короткая модерация \u2192 объект уже в поиске и здесь\n\n"
        "Бесплатно: ни платы за пост, ни комиссии с арендатора. "
        "Площадь, этаж и описание можно пропустить — на публикацию влияют район, цена и фото.\n\n"
        "\U0001F381 Первым 10, кто выложит, — поднятие в топ в подарок.\n\n" + TAGS,
        _btns(
            [_add()],
            [InlineKeyboardButton(text="\U0001F50D Искать жильё", url=config.BOT_URL)],
        ),
    ),
]


async def _silent(coro):
    try:
        return await coro, None
    except Exception as e:
        return None, e


async def main() -> None:
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    _, err = await _silent(bot.set_chat_title(config.CHANNEL_ID, TITLE))
    print("title:", "ok" if err is None else err)
    _, err = await _silent(bot.set_chat_description(config.CHANNEL_ID, config_description()))
    print("channel description:", "ok" if err is None else err)
    _, err = await _silent(bot.set_my_short_description(SHORT))
    print("bot short:", "ok" if err is None else err)
    _, err = await _silent(bot.set_my_description(LONG))
    print("bot description:", "ok" if err is None else err)

    state: dict = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE, encoding="utf-8"))
        except Exception:
            state = {}

    # CONCIERGE first, HELP last: the newest post is what a visitor reads under the pin
    for key, text, kb in POSTS:
        mid = state.get(key)
        if mid:
            _, err = await _silent(bot.edit_message_text(
                text, chat_id=config.CHANNEL_ID, message_id=mid, reply_markup=kb))
            if err is None:
                print(f"{key}: edited #{mid}")
                continue
            if "not modified" in str(err):
                print(f"{key}: unchanged #{mid}")
                continue
            print(f"{key}: edit failed ({err}) -> reposting")
        msg = await bot.send_message(config.CHANNEL_ID, text, reply_markup=kb)
        state[key] = msg.message_id
        print(f"{key}: posted #{msg.message_id}")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print("state:", state)
    await bot.session.close()


def config_description() -> str:
    # Telegram caps a channel description at 255 characters: keywords first, link last.
    text = (
        "Аренда и продажа квартир и домов в Кишинёве и пригородах — без комиссии и без платы "
        "за объявление. Chirie Chișinău · контакт владельца на каждой карточке. "
        "#аренда #продажа #кишинёв #chirie\nПодать объявление: " + config.BOT_URL
    )
    if len(text) > 255:
        raise SystemExit(f"description too long: {len(text)} > 255")
    return text


asyncio.run(main())
