import html
import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import config
import db
import keyboards
import lemon

log = logging.getLogger("chiriebot.boost")

router = Router()


async def offer_flow(target: Message, from_user, listing_id: int) -> None:
    """Show boost offer. Works for callback messages and /startapp deep links."""
    row = await db.get_listing(listing_id)
    if not row:
        await target.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(from_user.id, from_user.username, from_user.first_name)
    if row["user_id"] != user["id"]:
        await target.answer("Поднять можно только своё объявление.")
        return
    # A buyer who already pressed «Я перевёл» must see a waiting room, not a second cashier:
    # re-offering rails here invites a double transfer and a double claim.
    if await db.find_boost(listing_id, "manual_pending"):
        await target.answer(
            f"⏳ <b>Платёж по объявлению #{listing_id} на проверке.</b>\n\n"
            "Модератор сверит поступление и объявление само поднимется в топ — придёт "
            "сообщение. Повторно переводить не нужно.",
            parse_mode="HTML",
            reply_markup=keyboards.main_menu(),
        )
        return
    await db.log_event("boost_click", user_id=user["id"], listing_id=listing_id)
    credits = user["boost_credits"] if "boost_credits" in user.keys() else 0
    note = ""
    if row["boosted_until"]:
        note += f"\n\n⚡ Boost уже активен до <b>{str(row['boosted_until'])[:10]}</b> — новый продлит срок."
    if config.CARD_ENABLED:
        note += ("\n\nКартой — оплата на защищённой странице банка, Boost включается сам, "
                 "подтверждение менеджером не нужно.")
    if credits > 0:
        phrase = "подарочное поднятие" if credits % 10 == 1 and credits % 100 != 11 else (
            "подарочных поднятия" if 2 <= credits % 10 <= 4 and not 12 <= credits % 100 <= 14
            else "подарочных поднятий")
        note = (f"\n\n\U0001F381 У тебя <b>{credits}</b> {phrase} — "
                "жми «Поднять бесплатно»." + note)
    text = (
        f"\U0001F680 <b>Boost объявления #{listing_id}</b>\n\n"
        f"Твоё объявление будет показываться <b>первым</b> в поиске и выделяться \u2B50 "
        f"в течение <b>{config.BOOST_DAYS} дней</b>.\n\n"
        + keyboards.boost_pitch() + note
    )
    await target.answer(text, parse_mode="HTML", reply_markup=keyboards.boost_kb(listing_id, credits))


@router.callback_query(F.data.startswith("gift:"))
async def gift_raise(cb: CallbackQuery, bot: Bot) -> None:
    """Spend one founders-gift credit: same top placement as a paid Boost, zero money."""
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row or row["status"] != "active":
        await cb.message.answer("Объявление не найдено или не активно.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Поднять можно только своё объявление.")
        return
    if not await db.spend_gift_credit(user["id"]):
        await cb.message.answer(f"Подарочных поднятий больше нет. Boost — {keyboards.rails_line()}.")
        return
    await db.boost_listing(listing_id, config.BOOST_DAYS)
    await db.log_boost(listing_id, user["id"], 0, config.BOOST_DAYS, status="gift")
    await db.log_event("gift_used", user_id=user["id"], listing_id=listing_id)
    left = (await db.get_user_by_tg_id(cb.from_user.id))["boost_credits"]
    await cb.message.answer(
        f"\U0001F381 Подарок сработал: объявление <b>#{listing_id}</b> в топе на "
        f"<b>{config.BOOST_DAYS} дней</b>, без денег.\nОсталось подарочных поднятий: <b>{left}</b>",
        parse_mode="HTML",
        reply_markup=keyboards.main_menu(),
    )
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, f"\U0001F381 Подарочный Boost: #{listing_id} "
                                          f"(остаток у владельца {left})")
        except Exception:
            pass


@router.callback_query(F.data.startswith("boost:"))
async def boost_offer(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    await offer_flow(cb.message, cb.from_user, listing_id)


@router.callback_query(F.data.startswith("boostwallet:"))
async def boost_from_wallet(cb: CallbackQuery) -> None:
    """Instant Boost drawn from the wallet: the leu are already in, so the top spot lights up on
    the tap - no transfer to send, no admin to wait for. A wallet spend is an EVENT, never a
    payments row; the money was booked as revenue when it entered the wallet."""
    if not config.PAID_ENABLED:
        await cb.answer("Кошелёк отключён на бесплатной доске.", show_alert=True)
        return
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Поднять можно только своё объявление.")
        return
    price = config.BOOST_MDL
    if not await db.spend_balance(user["id"], price):
        bal = await db.get_balance(user["id"])
        await cb.message.answer(
            f"\u26A1 Boost стоит <b>{price}</b> леев, на балансе <b>{bal}</b>.\nПополни кошелёк 👇",
            parse_mode="HTML", reply_markup=keyboards.wallet_menu_kb(user["id"]))
        return
    await db.boost_listing(listing_id, config.BOOST_DAYS)
    await db.log_boost(listing_id, user["id"], price, config.BOOST_DAYS, status="wallet")
    await db.log_event("boost_wallet_paid", user_id=user["id"], listing_id=listing_id, meta=str(price))
    await cb.message.answer(
        f"\U0001F680 Объявление <b>#{listing_id}</b> поднято в топ на "
        f"<b>{config.BOOST_DAYS} дней</b> (списано <b>{price}</b> леев с баланса).",
        parse_mode="HTML", reply_markup=keyboards.main_menu())


@router.callback_query(F.data.startswith("boostpay:"))
async def boost_pay(cb: CallbackQuery, bot: Bot) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row:
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Это не твоё объявление.")
        return
    await db.log_event("boost_payment_attempt", user_id=user["id"], listing_id=listing_id)
    await bot.send_invoice(
        chat_id=cb.message.chat.id,
        title=f"Boost #{listing_id}",
        description=f"Поднятие объявления в топ на {config.BOOST_DAYS} дней",
        payload=f"boost:{listing_id}",
        provider_token="",
        currency=config.STARS_CURRENCY,
        prices=[LabeledPrice(label="Boost", amount=config.BOOST_STARS)],
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery) -> None:
    await q.answer(ok=True)


@router.callback_query(F.data.startswith("card:"))
async def card_offer(cb: CallbackQuery) -> None:
    """Rail 3: hosted card checkout. Lemon Squeezy is the merchant of record, we only get told."""
    listing_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    if not config.CARD_ENABLED:
        await cb.message.answer("Оплата картой сейчас недоступна.")
        return
    row = await db.get_listing(listing_id)
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Поднять можно только своё объявление.")
        return
    await db.log_event("card_click", user_id=user["id"], listing_id=listing_id)
    try:
        checkout_id, url = await lemon.create_checkout(listing_id, cb.from_user.id)
    except Exception as e:
        log.warning("LS checkout failed for #%s: %s", listing_id, e)
        await cb.message.answer("Платёжная страница временно недоступна — попробуй перевод MDL "
                                "или Stars \u2B50, или напиши в поддержку.")
        return
    await db.log_event("card_checkout", user_id=user["id"], listing_id=listing_id, meta=checkout_id)
    await cb.message.answer(
        f"\U0001F4B3 <b>Оплата картой {keyboards.usd(config.CARD_PRICE_CENTS)}</b> "
        f"(объявление #{listing_id}, {config.BOOST_DAYS} дней в топе)\n\n"
        "1. Нажми кнопку \u2014 откроется защищённая страница оплаты Visa/Mastercard.\n"
        "2. После оплаты бот <b>сам</b> поднимет объявление и напишет сюда \u2014 ничего "
        "подтверждать вручную не нужно.\n\n"
        "Ссылка одноразовая, действует 3 часа.",
        parse_mode="HTML",
        reply_markup=keyboards.card_pay_kb(url, listing_id),
    )


@router.callback_query(F.data.startswith("manual:"))
async def manual_offer(cb: CallbackQuery) -> None:
    """Second payment rail: card/bank transfer in MDL, confirmed manually."""
    listing_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    if not config.BOOST_MDL:
        await cb.message.answer("Оплата переводом сейчас недоступна.")
        return
    row = await db.get_listing(listing_id)
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Поднять можно только своё объявление.")
        return
    await db.log_event("manual_click", user_id=user["id"], listing_id=listing_id)
    await cb.message.answer(keyboards.manual_pitch(listing_id), parse_mode="HTML",
                            reply_markup=keyboards.manual_kb(listing_id, config.BOOST_MDL))


@router.callback_query(F.data.startswith("manualpaid:"))
async def manual_claim(cb: CallbackQuery, bot: Bot) -> None:
    """Buyer says the transfer is done -> create a pending record and ask admins."""
    listing_id = int(cb.data.split(":", 1)[1])
    await cb.answer()
    row = await db.get_listing(listing_id)
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    if row["user_id"] != user["id"]:
        await cb.message.answer("Поднять можно только своё объявление.")
        return
    if await db.find_boost(listing_id, "manual_pending"):
        await cb.message.answer("Твой платёж уже на проверке. Ожидай \u23F3")
        return
    # boosts.stars is reused as "agreed amount in the payment currency" for manual claims
    boost_id = await db.log_boost(listing_id, user["id"], config.BOOST_MDL, config.BOOST_DAYS,
                                  status="manual_pending")
    await db.log_event("manual_payment_claim", user_id=user["id"], listing_id=listing_id,
                       meta=str(config.BOOST_MDL))
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(
        "\U0001F4E4 Принято. Проверяю поступление \u2014 как только деньги придут, "
        f"объявление #{listing_id} поднимется в топ и ты получишь сообщение.",
        reply_markup=keyboards.main_menu(),
    )
    for admin in config.ADMIN_IDS:
        try:
            # In the bank statement a MIA transfer is identified by the PAYER'S PHONE, and the
            # owner's phone is already on the listing: so we hand the admin the exact match.
            expect = (f"с номера <code>{html.escape(row['phone'])}</code>" if row["phone"]
                      else f"от @{user['username'] or user['tg_id']}")
            await bot.send_message(
                admin,
                f"\U0001F4B0 <b>Ожидает подтверждения</b>\n"
                f"Boost #{listing_id}: <b>{config.BOOST_MDL} MDL</b> "
                f"({keyboards.transfer_word()}), {config.BOOST_DAYS} дней\n"
                f"Объявление: #{listing_id}, {row['district'] or '—'}, {row['price']} {row['currency']}\n"
                f"Платёж жди: {expect}\n"
                f"Комментарий к платежу: <b>{keyboards.manual_reference(listing_id)}</b>\n"
                f"boost_id={boost_id}",
                parse_mode="HTML",
                reply_markup=keyboards.manual_review_kb(boost_id, listing_id),
            )
        except Exception:
            pass


@router.message(F.successful_payment)
async def on_paid(message: Message, bot: Bot) -> None:
    sp = message.successful_payment
    payload = sp.invoice_payload or ""
    is_test = sp.currency == "XTEST"  # test invoice: chain is real, money is not
    user = await db.get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if payload.startswith("boost:"):
        listing_id = int(payload.split(":", 1)[1])
        days = config.BOOST_DAYS
        await db.boost_listing(listing_id, days)
        await db.log_boost(listing_id, user["id"], sp.total_amount, days, status="paid",
                           order_ref=f"tg:{sp.telegram_payment_charge_id}")
        await db.log_payment(
            user_id=user["id"], listing_id=listing_id,
            purpose="boost_test" if is_test else "boost",
            stars=sp.total_amount, currency=sp.currency,
            telegram_charge_id=sp.telegram_payment_charge_id,
            provider_charge_id=sp.provider_payment_charge_id,
        )
        await db.log_event("test_paid" if is_test else "boost_paid",
                           user_id=user["id"], listing_id=listing_id, meta=str(sp.total_amount))
        tag = "\U0001F9EA Тестовый платёж принят. " if is_test else "\u2705 Оплата получена! "
        await message.answer(
            f"{tag}Объявление <b>#{listing_id}</b> поднято на <b>{days} дней</b> \U0001F680",
            parse_mode="HTML",
            reply_markup=keyboards.main_menu(),
        )
        for admin in config.ADMIN_IDS:
            try:
                label = "ТЕСТ (XTEST)" if is_test else "Telegram Stars"
                await bot.send_message(
                    admin,
                    f"\U0001F4B0 <b>Новый платёж</b>\nBoost #{listing_id}: {sp.total_amount} {label}",
                )
            except Exception:
                pass
    else:
        await message.answer("\u2705 Платёж получен. Спасибо!", reply_markup=keyboards.main_menu())
