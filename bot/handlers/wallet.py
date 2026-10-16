"""Balance wallet: money flows IN here, services spend it.

Top-up is a lump sum over MIA: the buyer reports a transfer, an admin ticks it, and only then do
the leu land in users.balance_mdl (credit_topup is idempotent, so a double tick cannot mint twice).
Money IN is the only revenue row we write - a top-up is real leu arriving. Spending the wallet on
a service (the "Срочно" badge here; renewal/boost elsewhere) is an EVENT, not a new payment, or the
MDL revenue line would count the same leu once coming and once going.

Everything here is harmless when config.PAID_ENABLED=0: the menu just shows a live price list, and
the paid actions refuse. The board stays free until the supply is real.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
import db
import keyboards

log = logging.getLogger("chiriebot.wallet")
router = Router()


def _is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


async def _balance_line(user_row) -> str:
    bal = await db.get_balance(user_row["id"])
    return (f"\U0001F4B3 <b>Кошелёк</b>\n\nБаланс: <b>{bal} леев</b>\n\n"
            "Пополни одной суммой — неделя однушки, поднятие, «Срочно» и подписка на уведомления "
            "списываются с баланса сразу, без отдельных переводов на каждую мелочь.")


@router.callback_query(F.data == "wallet:menu")
async def wallet_menu_cb(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    await cb.message.answer(await _balance_line(user), parse_mode="HTML",
                            reply_markup=keyboards.wallet_menu_kb(user["id"]))


@router.message(Command("balance"))
async def balance_cmd(message: Message) -> None:
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username,
                                       message.from_user.first_name)
    await message.answer(await _balance_line(user), parse_mode="HTML",
                         reply_markup=keyboards.wallet_menu_kb(user["id"]))


@router.callback_query(F.data.startswith("tup:"))
async def start_topup(cb: CallbackQuery) -> None:
    """Open a pending top-up for the chosen lump sum and show the MIA instructions."""
    raw = cb.data.split(":", 1)[1]
    if not raw.isdigit():
        await cb.answer("Непонятная сумма.", show_alert=True)
        return
    amount = int(raw)
    if amount <= 0:
        await cb.answer("Сумма должна быть больше нуля.", show_alert=True)
        return
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    topup_id = await db.create_topup(user["id"], cb.from_user.id, amount)
    await db.log_event("topup_started", user_id=user["id"], meta=f"{topup_id}:{amount}")
    await cb.message.answer(keyboards.topup_pitch(topup_id, amount), parse_mode="HTML",
                            reply_markup=keyboards.topup_sent_kb(topup_id))


@router.callback_query(F.data.startswith("tuppaid:"))
async def claim_sent(cb: CallbackQuery, bot: Bot) -> None:
    """The buyer says the transfer is out. We do not credit yet - we hand the admin a tick-list."""
    topup_id = int(cb.data.split(":", 1)[1])
    row = await db.get_topup(topup_id)
    await cb.answer()
    if not row or row["status"] != "pending":
        await cb.message.answer("Эта заявка уже обработана.")
        return
    if str(row["tg_id"]) != str(cb.from_user.id):
        await cb.message.answer("Это не твоя заявка на пополнение.")
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.message.answer(
        f"\U0001F4E4 Принято. Сверяем поступление — как только увидим перевод на <b>{row['amount_mdl']}</b> "
        "леев, они сразу лягут на баланс. Придёт сообщение.", parse_mode="HTML",
        reply_markup=keyboards.main_menu())
    who = f"@{cb.from_user.username}" if cb.from_user.username else f"tg:{row['tg_id']}"
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin,
                f"\U0001F4B3 <b>Пополнение ждёт подтверждения</b>\n"
                f"{who} шлёт <b>{row['amount_mdl']} MDL</b> в кошелёк.\n"
                f"Комментарий к платежу: <b>{keyboards.topup_reference(topup_id)}</b>\n"
                f"topup_id={topup_id}",
                parse_mode="HTML",
                reply_markup=keyboards.topup_review_kb(topup_id))
        except Exception:
            pass


@router.callback_query(F.data.startswith("tupok:"))
async def approve_topup(cb: CallbackQuery, bot: Bot) -> None:
    """Admin saw the money: credit the wallet (idempotent) and book the only revenue row."""
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    topup_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    row = await db.credit_topup(topup_id)
    if not row:
        await cb.message.answer("Заявка не найдена или уже зачислена.")
        return
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    # real leu arrived -> this is the revenue record (currency MDL, counted by stats)
    await db.log_payment(user_id=row["user_id"], listing_id=None, purpose="topup", stars=0,
                         currency="MDL", telegram_charge_id=f"topup:{topup_id}",
                         provider_charge_id=None, amount=float(row["amount_mdl"]))
    await db.log_event("topup_paid", user_id=row["user_id"], meta=str(row["amount_mdl"]))
    bal = await db.get_balance(row["user_id"])
    await cb.message.answer(f"\u2705 Пополнение #{topup_id}: <b>{row['amount_mdl']}</b> леев зачислено. "
                            f"Баланс: <b>{bal}</b> леев.", parse_mode="HTML")
    try:
        await bot.send_message(row["tg_id"],
                               f"\U0001F4B3 Баланс пополнен на <b>{row['amount_mdl']}</b> леев. "
                               f"Сейчас на кошельке <b>{bal}</b> леев.", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("tupno:"))
async def reject_topup(cb: CallbackQuery, bot: Bot) -> None:
    if not _is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    topup_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    row = await db.get_topup(topup_id)
    if not await db.reject_topup(topup_id):
        await cb.message.answer("Заявка уже обработана.")
        return
    await cb.message.answer(f"\u274C Пополнение #{topup_id} отклонено (денег на счёте нет).")
    if row:
        try:
            await bot.send_message(row["tg_id"],
                                   "\u26A0️ Деньги не нашли на счёте. Проверь реквизиты и комментарий "
                                   f"<b>{keyboards.topup_reference(topup_id)}</b> и нажми «Я отправил» снова.",
                                   parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("hl:"))
async def buy_highlight(cb: CallbackQuery) -> None:
    """Owner spends the wallet to light the "Срочно" badge for HIGHLIGHT_DAYS. A wallet spend is
    an event, never a payment row - the leu were already booked as revenue when they came in."""
    if not config.PAID_ENABLED:
        await cb.answer("Выделение недоступно на бесплатной доске.", show_alert=True)
        return
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row or row["status"] != "active":
        await cb.message.answer("Выделить можно только активное объявление.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Выделить можно только своё объявление.")
        return
    price = config.PRICE_HIGHLIGHT_MDL
    if not await db.spend_balance(user["id"], price):
        bal = await db.get_balance(user["id"])
        await cb.message.answer(
            f"\U0001F525 «Срочно» стоит <b>{price}</b> леев, на балансе <b>{bal}</b>.\nПополни кошелёк 👇",
            parse_mode="HTML", reply_markup=keyboards.wallet_menu_kb(user["id"]))
        return
    await db.feature_listing(listing_id, config.HIGHLIGHT_DAYS)
    await db.log_event("highlight_paid", user_id=user["id"], listing_id=listing_id, meta=str(price))
    await cb.message.answer(
        f"\U0001F525 Объявление <b>#{listing_id}</b> горит «Срочно» ближайшие <b>{config.HIGHLIGHT_DAYS} "
        "дней</b> — оно идёт перед остальными в поиске и в ленте.", parse_mode="HTML",
        reply_markup=keyboards.main_menu())


@router.callback_query(F.data == "alerts:sub")
async def subscribe_alerts_cb(cb: CallbackQuery) -> None:
    """Buy a month of saved-search pushes from the wallet. Same rule as every other service: a
    spend is an event, the leu were already revenue on the way in."""
    if not config.PAID_ENABLED:
        await cb.answer("Подписка недоступна на бесплатной доске.", show_alert=True)
        return
    await cb.answer()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    price = config.PRICE_ALERTS_MDL
    if not await db.spend_balance(user["id"], price):
        bal = await db.get_balance(user["id"])
        disp = config.ALERTS_DISPLAY or f"{price} леев"
        await cb.message.answer(
            f"\U0001F514 Уведомления по поиску — <b>{disp}</b>/месяц ({price} леев), на балансе "
            f"<b>{bal}</b>.\nПополни кошелёк 👇",
            parse_mode="HTML", reply_markup=keyboards.wallet_menu_kb(user["id"]))
        return
    await db.subscribe_alerts(user["id"], config.ALERTS_MONTH_DAYS)
    await db.log_event("alerts_paid", user_id=user["id"], meta=str(price))
    await cb.message.answer(
        f"\U0001F514 Уведомления о новых однушках активны ещё на <b>{config.ALERTS_MONTH_DAYS} "
        f"дней</b> (списано <b>{price}</b> леев).", parse_mode="HTML",
        reply_markup=keyboards.main_menu())
