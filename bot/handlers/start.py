from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import db
import keyboards
import render
from handlers import add as add_h
from handlers import boost as boost_h
from handlers import profile as profile_h

router = Router()

if config.FOCUS_DEAL == "rent" and config.FOCUS_ROOMS == "1":
    WELCOME = (
        "\U0001F3E0 <b>Găsește Casa</b> — однушки в аренду в Кишинёве, прямо в Telegram.\n"
        "Только от хозяев, без агентов и без комиссии: контакт на каждой карточке.\n\n"
        "\U0001F447 Жми кнопку:\n"
        "🔍 <b>Ищу однушку</b> → «Поиск» или «📱 Открыть доску однушек»\n"
        "\u2795 <b>Сдаю свою</b> → «Сдам однушку» (бесплатно, 2 минуты)"
    )
else:
    WELCOME = (
        "\U0001F3E0 <b>Găsește Casa</b> — квартиры и дома в Кишинёве прямо в Telegram.\n"
        "Комиссию не берём вообще: ищешь сам, контакт на каждой карточке.\n\n"
        "\U0001F447 Выбери, зачем зашёл, и жми кнопку:\n"
        "🔍 <b>Ищу жильё</b> → «Поиск» или «📱 Открыть доску»\n"
        "\u2795 <b>Сдаю / продаю</b> → «Разместить объект» (бесплатно, 2 минуты)"
    )

if config.CHANNEL_URL:
    WELCOME += f"\n\U0001F4E3 Лента новых объектов — ещё и в канале: {config.CHANNEL_URL}"


async def show_menu(target: Message, first_name: str | None = None) -> None:
    text = WELCOME if first_name else "Выбери, что нужно 👇"
    await target.answer(text, parse_mode="HTML", reply_markup=keyboards.main_menu())


@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, state: FSMContext) -> None:
    """t.me/gaseste_casa_bot?start=<payload> - remember where the user came from.

    A `share_<id>` payload means the user arrived from a forwarded listing card: land them straight
    on that object (with its contact/share buttons), then the menu. This is the receiving end of the
    referral loop - every shared card is an advert that opens the product one tap before 'разместить своё'.
    `add` / `boost_<id>` come from the Mini App buttons (openTelegramLink) and go straight into the
    wizard / boost offer - ?start= cannot carry a colon, so these use underscores.
    """
    payload = (message.text or "").partition(" ")[2]
    u = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    await db.log_event("start", user_id=u["id"], meta=payload[:64])
    if payload.startswith("share_"):
        try:
            listing_id = int(payload.split("share_", 1)[1])
        except ValueError:
            listing_id = None
        if listing_id:
            row = await db.get_listing(listing_id)
            if row and row["status"] == "active":
                await db.inc_views(listing_id)
                await render.send_listing(message.bot, message.chat.id, row, owner=False)
    if payload == "add":
        await add_h.begin(message, state)
        return
    if payload == "my":
        # Phase A of the Mini App plan: the app's «Мои» tab sends the user here until Phase B
        # makes it native inside the app.
        await profile_h.render_my_listings(message, message.from_user)
        return
    if payload.startswith("boost_"):
        try:
            listing_id = int(payload.split("boost_", 1)[1])
        except ValueError:
            listing_id = None
        if listing_id:
            await boost_h.offer_flow(message, message.from_user, listing_id)
            return
    await show_menu(message, message.from_user.first_name)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    u = await db.get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name
    )
    await db.log_event("start", user_id=u["id"])
    await show_menu(message, message.from_user.first_name)


@router.message(F.text.startswith("/startapp boost:"))
async def deep_boost(message: Message) -> None:
    """Deep link from the Mini App: /startapp boost:<listing_id>."""
    try:
        listing_id = int(message.text.split("boost:", 1)[1].split()[0])
    except (ValueError, IndexError):
        return
    await boost_h.offer_flow(message, message.from_user, listing_id)


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await show_menu(message)


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    """Telegram shows this in the command list - the shortest path from any chat to the wizard."""
    await add_h.begin(message, state)


@router.callback_query(F.data == "menu:home")
async def cb_home(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer("Выбери, что нужно 👇", reply_markup=keyboards.main_menu())


@router.callback_query(F.data == "menu:help")
async def cb_help(cb: CallbackQuery) -> None:
    await cb.answer()
    text = (
        "<b>Как это работает</b>\n\n"
        "\U0001F50D <b>Поиск</b> — найти аренду или продажу.\n"
        "\u2795 <b>Разместить объект</b> — добавить своё объявление (бесплатно).\n"
        "\U0001F4CB <b>Мои объявления</b> — управлять своими объектами и поднимать их.\n\n"
        "\U0001F680 <b>Boost</b> — платное поднятие объявления в топ выдачи.\n"
        f"Оплата: {keyboards.rails_line()}.\n\n"
        "Объявления проходят короткую модерацию."
    )
    await cb.message.answer(text, parse_mode="HTML", reply_markup=keyboards.back_menu())


@router.callback_query(F.data == "menu:add")
async def cb_add(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await add_h.begin(cb.message, state)


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message) -> None:
    # a dead "@support" handle is itself a scam signal: only name a contact that actually exists.
    # the fallback router forwards plain-text DMs to the admins, so "this chat" is a real line.
    where = config.CONTACT_URL or "напиши сообщение прямо в этот чат"
    await message.answer(
        f"\U0001F4B3 Вопросы и возвраты по оплате (перевод MDL / Telegram Stars): {where} — "
        "передадим модератору.",
        reply_markup=keyboards.back_menu(),
    )


@router.callback_query(F.data == "cancel")
async def cb_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer("Отменено")
    await cb.message.answer("Действие отменено.", reply_markup=keyboards.main_menu())
