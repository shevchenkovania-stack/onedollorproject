from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
import db
import keyboards
import render
from constants import DEAL_LABELS, PTYPE_LABELS, STATUS_LABELS

router = Router()


async def render_my_listings(target: Message, from_user) -> None:
    """List the user's own ads. Shared by the menu:my callback and the ?start=my deep link
    (Phase A of the Mini App plan: the app's «Мои» tab hands off to the bot until Phase B)."""
    user = await db.get_or_create_user(from_user.id, from_user.username, from_user.first_name)
    rows = await db.get_user_listings(user["id"])
    if not rows:
        await target.answer(
            "У тебя пока нет объявлений.\nРазместить можно бесплатно \U0001F447",
            reply_markup=keyboards.main_menu(),
        )
        return
    kb_rows = []
    for r in rows:
        label = f"#{r['id']} {DEAL_LABELS.get(r['deal'], r['deal'])} · {PTYPE_LABELS.get(r['ptype'], r['ptype'])} · {r['price']}{r['currency']} · {STATUS_LABELS.get(r['status'], r['status'])}"
        kb_rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"view:{r['id']}")])
    kb_rows.append([InlineKeyboardButton(text="\U0001F3E0 Меню", callback_data="menu:home")])
    await target.answer(
        "Твои объявления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(F.data == "menu:my")
async def my_listings(cb: CallbackQuery) -> None:
    await cb.answer()
    await render_my_listings(cb.message, cb.from_user)


# --- Stage 4: owner closes out / renews their own ad ------------------------

async def _owner_row(cb: CallbackQuery, listing_id: int):
    """Load the ad and make sure the tapper is its owner, else None (with a message sent)."""
    row = await db.get_listing(listing_id)
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return None
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Это не твоё объявление.")
        return None
    return row


@router.callback_query(F.data.startswith("sold:"))
async def sold(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await _owner_row(cb, listing_id)
    await cb.answer()
    if not row:
        return
    await db.archive_listing(listing_id)
    await db.log_event("listing_sold", user_id=row["user_id"], listing_id=listing_id)
    await cb.message.answer(
        f"\u2705 Объявление <b>#{listing_id}</b> отмечено как сдано и снято с доски.\n"
        "Передумал — «Продлить» вернёт его в топ.",
        parse_mode="HTML", reply_markup=keyboards.main_menu(),
    )


@router.callback_query(F.data.startswith("extend:"))
async def extend(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await _owner_row(cb, listing_id)
    await cb.answer()
    if not row:
        return
    # Free board: extend is a free one-tap (unchanged). Paid board: keeping an ad live costs
    # PRICE_LISTING_MDL per LISTING_WEEK_DAYS, drawn from the wallet. A spend is an EVENT, never a
    # payments row - the leu were already booked as revenue when the wallet was topped up.
    if not config.PAID_ENABLED:
        await db.extend_listing(listing_id, config.LISTING_DAYS)
        await db.log_event("listing_extended", user_id=row["user_id"], listing_id=listing_id)
        await cb.message.answer(
            f"\U0001F501 Объявление <b>#{listing_id}</b> снова активно ещё на "
            f"<b>{config.LISTING_DAYS} дней</b>.",
            parse_mode="HTML", reply_markup=keyboards.main_menu(),
        )
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    price = config.PRICE_LISTING_MDL
    if not await db.spend_balance(user["id"], price):
        bal = await db.get_balance(user["id"])
        await cb.message.answer(
            f"\U0001F501 Продление стоит <b>{price}</b> лей за <b>{config.LISTING_WEEK_DAYS} дней</b>. "
            f"На балансе <b>{bal}</b> леев.\nПополни кошелёк 👇",
            parse_mode="HTML", reply_markup=keyboards.wallet_menu_kb(user["id"]))
        return
    await db.extend_listing(listing_id, config.LISTING_WEEK_DAYS)
    await db.log_event("renewal_paid", user_id=user["id"], listing_id=listing_id, meta=str(price))
    await cb.message.answer(
        f"\U0001F501 Объявление <b>#{listing_id}</b> активно ещё на "
        f"<b>{config.LISTING_WEEK_DAYS} дней</b> (списано <b>{price}</b> леев с баланса).",
        parse_mode="HTML", reply_markup=keyboards.main_menu(),
    )


@router.callback_query(F.data.startswith("delete:"))
async def delete(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await _owner_row(cb, listing_id)
    await cb.answer()
    if not row:
        return
    await db.archive_listing(listing_id)
    await db.log_event("listing_deleted", user_id=row["user_id"], listing_id=listing_id)
    await cb.message.answer(
        f"\U0001F5D1 Объявление <b>#{listing_id}</b> убрано с доски.\n"
        "Оно не удалено безвозвратно — «Продлить» вернёт его.",
        parse_mode="HTML", reply_markup=keyboards.main_menu(),
    )
