"""SEO seeds: put our Telegram surfaces into global search for the semantic core (SEO.md).

Telegram's global search indexes ONLY title + @username + description - not post text. The bot's
@handle is permanent, so the real leverage is: (a) per-language bot names, (b) a keyword-loaded
description, (c) extra public channels, one per cluster of the core.

Hard platform limit (verified on prod against aiogram 3.31 / Bot API): a bot CANNOT create a
channel and CANNOT add a human to one - CreateChatPublisher/AddChatMember do not exist. So this
script does everything the API can do and prints precisely the one manual step per cluster.

What it does:
  1. bot: setMyName for default + "ru" + "ro" (Telegram shows the name matching the user's
     language, and both are indexed), short description (search result line), long description
     (both languages, keyword-loaded);
  2. every channel id listed in /opt/imobiliare/data/seo_channels.json: title + description +
     ONE pinned welcome post with a tracked deep link (?start=seo_<key> -> events.start.meta,
     so /traffic shows which surface brings people). Re-runs EDIT in place, never duplicate.

Run on prod:  cd /opt/imobiliare/bot && /opt/imobiliare/venv/bin/python /tmp/seo_seeds.py
Register a channel the human created (either its -100 id or its public handle):
  ... seo_seeds.py rent=@chirie_kvartire_md  |  ... seo_seeds.py ro=-1002345678901
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/opt/imobiliare/bot")

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config

STATE = "/opt/imobiliare/data/seo_channels.json"

# ---- cluster copy: the single source of truth (mirror of SEO.md) ----
# Brand stays first (users read the name in the chat list); the keywords follow in the same
# indexed string. Bot name limit is 64 chars - asserted below.
BOT_NAME_RU = "Однушка.md · Аренда однушек Кишинёв без комиссии"
BOT_NAME_RO = "Odnushka.md · Chirie 1 Cameră Chișinău fără comision"
BOT_SHORT = "Снять однушку в Кишинёве у хозяина, без комиссии. · Chirie 1 cameră Chișinău."
BOT_LONG = (
    "«Однушка.md» — доска однушек в аренду в Кишинёве, прямо в Telegram.\n\n"
    "• Снять квартиру или однушку: фильтр по району, цене и сделке\n"
    "• Подать объявление бесплатно за 2 минуты; контакт хозяина на карточке\n"
    "• Без комиссии и посредников; поднять в топ — 100 MDL MIA или 400 Stars\n"
    "• Напиши боту район и бюджет — подберём и следим за новыми\n\n"
    "Chirie în Chișinău · Garsonieră · 1 cameră · fără comision, direct proprietar · "
    "Botanica, Râșcani, Buiucani, Centru."
)

# One entry = one search cluster the family should own. Title <=60, description <=255 chars.
# "wire": "channel" means the surface is ALREADY public and wired in .env (CHANNEL_ID) - no
# manual step for it. "welcome": None means identity only (pin_roles.py owns that channel's pinned
# navigation post, so we do not fight over the pin).
CLUSTERS = [
    {
        "key": "board",
        "lang": "ru",
        "wire": "channel",
        "title": "Однушки в Кишинёве · Аренда без комиссии",
        "desc": (
            "Снять однушку в Кишинёве напрямую у хозяина, без комиссии и посредников. "
            "Аренда однушки: Botanica, Râșcani, Centru, Budva, Buiucani. "
            "Подать объявление бесплатно: "
        ),
        "welcome": None,
        "cta": "🔍 Смотреть однушки",
        "handle": "@gaseste_casa (уже публичный)",
    },
    {
        "key": "rent",
        "lang": "ru",
        "title": "Снять Квартиру в Кишинёве · Без Посредников",
        "desc": (
            "Аренда квартир в Кишинёве без посредников: однушки, студия, 2 комнаты. "
            "Снять квартиру надолго, контакт хозяина на каждой карточке. "
            "Подача объявления бесплатно: "
        ),
        "welcome": (
            "🔑 <b>Снять квартиру в Кишинёве — без посредников</b>\n\n"
            "Карточки с ценой, районом и прямым контактом хозяина. Комиссии за сделку нет.\n\n"
            "• Фильтр: сделка · комнаты · район · цена\n"
            "• Нашёл — один тап до звонка\n"
            "• Сдаёшь — подай бесплатно за 2 минуты"
        ),
        "cta": "🔍 Открыть поиск",
        "handle": "@chirie_kvartire_md",
    },
    {
        "key": "ro",
        "lang": "ro",
        "title": "Chirie 1 Cameră Chișinău · Garsonieră Direct Proprietar",
        "desc": (
            "Chirie în Chișinău: 1 cameră și garsoniere, preț clar, contact direct cu "
            "proprietarul. Fără comision, fără intermediari. Publici gratuit: "
        ),
        "welcome": (
            "🏠 <b>Chirie în Chișinău — direct cu proprietarul</b>\n\n"
            "Anunțuri de închiriere: 1 cameră, garsonieră, preț și sector. Apasă butonul — "
            "contactul proprietarului e pe card.\n\n"
            "• Fără comision și fără intermediari\n"
            "• Publici gratuit în 2 minute\n"
            "• Caută: sector · preț · 1 cameră"
        ),
        "cta": "🔍 Caută acum",
        "handle": "@chirie_1camera_chisinau",
    },
    {
        "key": "sector",
        "lang": "ro",
        "title": "Chirie Botanica · Râșcani · Buiucani pe Sector",
        "desc": (
            "Apartamente de închiriat pe sectoare: chirie Botanica, Râșcani, Buiucani, "
            "Centru, Ciocana. Alege sectorul și bugetul. Publicare gratuită: "
        ),
        "welcome": (
            "📍 <b>Chirie pe sectoare — alege raionul tău</b>\n\n"
            "Botanica, Râșcani, Buiucani, Centru, Ciocana — filtrezi după sector și buget, "
            "restul face bota.\n\n"
            "• Caută: <b>sector · preț · 1 cameră</b>\n"
            "• Publici gratuit: <b>fără comision</b>\n"
            "• Nu răsfoiești: scrie botului sectorul și prețul"
        ),
        "cta": "📍 Alege sectorul",
        "handle": "@chirie_sector_chisinau",
    },
]


async def _silent(coro):
    try:
        return await coro, None
    except Exception as e:
        return None, e


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


async def main() -> None:
    if not config.BOT_URL:
        raise SystemExit("BOT_URL empty - set BOT_USERNAME in .env (deep links need the handle)")
    for c in CLUSTERS:
        assert len(c["title"]) <= 60, f"{c['key']}: title {len(c['title'])} > 60 chars"
        full = c["desc"] + config.BOT_URL
        assert len(full) <= 255, f"{c['key']}: desc {len(full)} > 255 chars"
    assert len(BOT_NAME_RU) <= 64 and len(BOT_NAME_RO) <= 64, "bot name > 64 chars"
    assert len(BOT_SHORT) <= 120, f"bot short {len(BOT_SHORT)} > 120 chars"
    assert len(BOT_LONG) <= 512, f"bot long {len(BOT_LONG)} > 512 chars"

    state = load_state()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # 0. register channels passed on the command line (key=@publichandle or key=-100...)
    keys = {c["key"] for c in CLUSTERS}
    for arg in sys.argv[1:]:
        key, _, ref = arg.partition("=")
        if key not in keys:
            raise SystemExit(f"unknown cluster key: {key} (use {'/'.join(sorted(keys))})")
        ref = ref.strip()
        if ref.startswith("-") or ref.isdigit():
            chat_id = int(ref) if ref.startswith("-") else int("-100" + ref)
        else:  # resolve a public handle - works only once the bot is an admin of that channel
            chat = await bot.get_chat(ref if ref.startswith("@") else "@" + ref)
            chat_id = chat.id
            print(f"{key}: resolved {ref} -> {chat_id}")
        state.setdefault(key, {})["chat_id"] = chat_id

    # 1. bot identity - indexed for the bot, shown per language
    for lang, name in (("", BOT_NAME_RU), ("ru", BOT_NAME_RU), ("ro", BOT_NAME_RO)):
        _, err = await _silent(bot.set_my_name(name=name, language_code=lang))
        print(f"bot name[{lang or 'default'}]:", "ok" if err is None else err)
    _, err = await _silent(bot.set_my_short_description(BOT_SHORT))
    print("bot short:", "ok" if err is None else err)
    _, err = await _silent(bot.set_my_description(BOT_LONG))
    print("bot long:", "ok" if err is None else err)

    # 2. channel surfaces: the wired one from .env, the rest once a human creates them
    me = await bot.get_me()
    deep = f"https://t.me/{me.username}?start="
    wired, missing = [], []

    for c in CLUSTERS:
        if c.get("wire") == "channel":
            cid, entry = config.CHANNEL_ID, {}
            if not cid:
                print(f"{c['key']}: CHANNEL_ID not set - channel is hidden, skipped")
                continue
        else:
            entry = state.get(c["key"]) or {}
            cid = entry.get("chat_id")
            if not cid:
                missing.append(c)
                continue
        wired.append(c["key"])

        _, err = await _silent(bot.set_chat_title(cid, c["title"]))
        print(f"{c['key']} title:", "ok" if err is None else err)
        _, err = await _silent(bot.set_chat_description(cid, c["desc"] + config.BOT_URL))
        print(f"{c['key']} desc:", "ok" if err is None else err)
        if c["welcome"] is None:
            # identity only - pin_roles.py owns this channel's pinned navigation post
            continue

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=c["cta"], url=deep + "seo_" + c["key"])],
            [InlineKeyboardButton(text="➕ Подать / Publică gratuit", url=deep + "add")],
        ])
        mid = entry.get("msg_id")
        posted = False
        if mid:
            _, err = await _silent(bot.edit_message_text(
                c["welcome"], chat_id=cid, message_id=mid, reply_markup=kb))
            if err is None:
                print(f"{c['key']}: welcome edited #{mid}")
            elif "not modified" in str(err):
                print(f"{c['key']}: welcome unchanged #{mid}")
            else:
                print(f"{c['key']}: edit failed ({err}) -> reposting")
                posted = True
        else:
            posted = True
        if posted:
            msg = await bot.send_message(cid, c["welcome"], reply_markup=kb)
            entry["msg_id"] = msg.message_id
            state[c["key"]] = entry
            print(f"{c['key']}: welcome posted #{msg.message_id}")
            try:
                await bot.pin_chat_message(cid, msg.message_id)
            except TelegramBadRequest as e:
                print(f"{c['key']}: pin failed ({e})")

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print("\nbot surfaces: ok")
    print("channels wired:", wired or "none")
    if missing:
        print("\nMANUAL STEP (Bot API cannot create a channel - verified on prod):")
        for c in missing:
            print(f"  [{c['key']} / {c['lang']}] {c['title']}")
            print("      1) Telegram -> new channel, any name (we rename it below)")
            print(f"      2) add @{me.username} as administrator (rights: post messages)")
            print(f"      3) channel type -> Public -> handle {c['handle']}")
            print(f"      4) register it here:  python seo_seeds.py {c['key']}={c['handle']}")
        print("  then re-run: the script sets the title, the description and the pinned welcome.")
    print("✅ SEO SEEDS OK")
    await bot.session.close()


asyncio.run(main())
