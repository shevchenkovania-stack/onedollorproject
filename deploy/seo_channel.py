"""Channel storefront posts: the three cards that make an empty channel look alive.

Idempotent: message ids are remembered in /opt/imobiliare/data/channel_series.json, so a re-run
EDITS them in place instead of spamming the feed. If Telegram refuses the edit (old message), a
fresh one is posted.

Titles and descriptions are NOT this file's job - `seo_seeds.py` is the single source for every
indexed name/description (bot + channels). Keeping them here used to silently revert the SEO core
on the next run of the other tool.

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

TAGS = "#аренда #однушки #кишинёв #chirie #безкомиссии"


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
    if not config.CHANNEL_ID:
        raise SystemExit("CHANNEL_ID not set - the channel is hidden, nothing to post")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

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


asyncio.run(main())
