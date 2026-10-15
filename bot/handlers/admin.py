import asyncio

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, LabeledPrice, Message

import config
import db
import keyboards
import publish
import scheduler

router = Router()


def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS


@router.callback_query(F.data.startswith("mod:"))
async def moderate(cb: CallbackQuery, bot: Bot) -> None:
    if not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    _, action, sid = cb.data.split(":")
    listing_id = int(sid)
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    status = "active" if action == "approve" else "rejected"
    await db.set_listing_status(listing_id, status)
    await db.log_event(f"moderation_{action}", listing_id=listing_id)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"Объявление #{listing_id}: <b>{status}</b>", parse_mode="HTML")
    owner = await db.get_user_by_id(row["user_id"])
    if owner:
        if status == "active":
            note = (f"\u2705 Твоё объявление <b>#{listing_id}</b> одобрено и опубликовано!\n"
                    "Его уже видят в поиске и в канале. Хочешь быть первым? Жми Boost \U0001F680")
            try:
                await publish.publish_listing(bot, row)
            except Exception:
                pass  # publishing to channel/webapp must never block moderation
            # founders gift: the first GIFT_SLOTS publishers get free raises - this is the
            # headline of every outreach message, so it must arrive automatically, not by hand
            if await db.grant_gift_credits(row["user_id"], config.GIFT_BOOSTS, config.GIFT_SLOTS):
                n = config.GIFT_BOOSTS
                word = "поднятие" if n % 10 == 1 and n % 100 != 11 else (
                    "поднятия" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "поднятий")
                note += (f"\n\n\U0001F381 Ты в первых {config.GIFT_SLOTS}: лови <b>{n} {word}</b> "
                         "в топ — кнопка «\U0001F381 Поднять бесплатно» у твоего объявления, без денег.")
        else:
            note = f"\u274C Твоё объявление <b>#{listing_id}</b> отклонено модератором."
        try:
            await bot.send_message(owner["tg_id"], note, parse_mode="HTML", reply_markup=keyboards.main_menu())
        except Exception:
            pass


@router.callback_query(F.data.startswith("manapp:") | F.data.startswith("manrej:"))
async def manual_review(cb: CallbackQuery, bot: Bot) -> None:
    """Admin confirms a manual MDL transfer (manapp) or rejects it (manrej)."""
    if not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    action, sid = cb.data.split(":", 1)
    boost_id = int(sid)
    boost = await db.get_boost(boost_id)
    await cb.answer()
    if not boost:
        await cb.message.answer("Заявка не найдена (возможно, уже обработана).")
        return
    if boost["status"] != "manual_pending":
        await cb.message.answer(f"Заявка уже в статусе «{boost['status']}».")
        return
    listing_id = boost["listing_id"]
    owner = await db.get_user_by_id(boost["user_id"])
    await cb.message.edit_reply_markup(reply_markup=None)

    if action == "manapp":
        # order_ref ties the boost to the payment row that paid for it, so a later
        # "money went back" (chargeback via the bank) can be traced to the boost to switch off.
        await db.set_boost_status(boost_id, "manual", order_ref=f"manual:{boost_id}")
        await db.boost_listing(listing_id, boost["days"])
        # boosts.stars holds the agreed MDL price for manual claims (set at claim time)
        amount_mdl = float(boost["stars"] or config.BOOST_MDL)
        await db.log_payment(user_id=boost["user_id"], listing_id=listing_id, purpose="boost_manual",
                             stars=0, currency="MDL", telegram_charge_id=f"manual:{boost_id}",
                             provider_charge_id=None, amount=amount_mdl)
        await db.log_event("boost_manual_paid", user_id=boost["user_id"], listing_id=listing_id,
                           meta=str(int(amount_mdl)))
        await cb.message.answer(f"✅ Boost #{listing_id} активирован на {boost['days']} дней. "
                                f"Сумма: {int(amount_mdl)} MDL.",
                                # the tick that keeps the books honest if this money goes back by hand
                                reply_markup=keyboards.manual_refund_kb(boost_id, listing_id))
        if owner:
            try:
                await bot.send_message(
                    owner["tg_id"],
                    f"\U0001F4B0 Платёж получен, спасибо!\nОбъявление <b>#{listing_id}</b> "
                    f"поднято в топ на <b>{boost['days']} дней</b> \U0001F680",
                    parse_mode="HTML", reply_markup=keyboards.main_menu(),
                )
            except Exception:
                pass
    else:
        await db.set_boost_status(boost_id, "manual_rejected")
        await cb.message.answer(f"❌ Платёж по заявке #{boost_id} отклонён.")
        if owner:
            try:
                await bot.send_message(
                    owner["tg_id"],
                    "⚠️ Деньги не нашли на счёте. Проверь реквизиты и комментарий к переводу "
                    f"и нажми «Я перевёл» ещё раз по объявлению #{listing_id}.",
                    reply_markup=keyboards.main_menu(),
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("manref:") | F.data.startswith("manrefyes:"))
async def manual_refund(cb: CallbackQuery, bot: Bot) -> None:
    """MIA has no revoke API: the admin transfers the money back in his banking app and books it here.

    Two taps on purpose - this writes money out of the revenue line and takes a paying customer out
    of the top of the board, so it must never fire on a stray thumb.
    """
    if not is_admin(cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    action, sid = cb.data.split(":", 1)
    boost_id = int(sid)
    await cb.answer()
    boost = await db.get_boost(boost_id)
    if not boost:
        await cb.message.answer("Заявка не найдена (возможно, уже обработана).")
        return
    if action == "manref":
        if boost["status"] != "manual":
            await cb.message.answer(
                f"Заявка #{boost_id} в статусе «{boost['status']}» — возвращать нечего.")
            return
        await cb.message.answer(
            f"\U0001F4B8 Подтверди возврат: заявка #{boost_id}, "
            f"{boost['stars']} MDL, объявление #{boost['listing_id']}.\n\n"
            "Нажми «Да», только если деньги уже ушли обратно покупателю. "
            "После этого сумма перестанет считаться выручкой, а Boost снимется с топа.",
            reply_markup=keyboards.manual_refund_confirm_kb(boost_id, boost["listing_id"]),
        )
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    result = await scheduler.refund_manual_claim(bot, boost_id)
    await cb.message.answer(result)
    for admin in config.ADMIN_IDS:
        if admin == cb.from_user.id:
            continue
        try:
            await bot.send_message(
                admin,
                f"\u21a9\ufe0f Возврат по переводу: заявка #{boost_id}, "
                f"объявление #{boost['listing_id']}.\n{result}\nОформил: @{cb.from_user.username or cb.from_user.id}",
            )
        except Exception:
            pass


@router.message(Command("stats"))
async def stats(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    s = await db.stats()
    text = (
        "<b>Статистика</b>\n"
        f"Пользователи: {s['users']}\n"
        f"Объявлений: {s['listings']} (активных {s['active']}, на модерации {s['pending']})\n"
        f"Boost'ов: {s['boosts']}\n"
        f"Платежей: {s['payments']} (тестовых: {s['test_payments']})\n"
        f"REAL_REVENUE Картой 💳: <b>{s['revenue_card']:.2f}</b> (не применено карточных заказов: {s['card_unapplied']})\n"
        f"REAL_REVENUE MDL \U0001F4B0: <b>{s['revenue_mdl']}</b>\n"
        f"REAL_REVENUE Stars \u2B50: <b>{s['revenue_stars']}</b>\n"
        # both lines are "money that needs a human": a refund we may have to react to,
        # and a transfer the buyer already sent but nobody has ticked yet
        f"Возвратов/чарджбеков: {s['refunds']}\n"
        f"Переводов ждут подтверждения: {s['manual_waiting']}\n"
        f"🎁 Подарок выдан: {s['gift_holders']} чел., поднятий в карманах: {s['gift_credits']}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("admin"))
async def admin_help(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "Админ-команды:\n"
        "/stats — статистика\n"
        "/pending — объявления на модерации\n"
        "/nudge — сразу предложить Boost тем, кто не в топе\n"
        "/broadcast <текст> — рассылка всем пользователям бота",
    )


@router.message(Command("nudge"))
async def nudge_now(message: Message, bot: Bot) -> None:
    """Force a nudge pass without waiting for the scheduler."""
    if not is_admin(message.from_user.id):
        return
    n = await scheduler.nudge_unboosted(bot)
    await message.answer(f"Предложений Boost отправлено: {n}")


@router.message(Command("broadcast"))
async def broadcast(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    text = (message.text or "").partition("\n")[2].strip()
    if not text:
        await message.answer("Использование: /broadcast <текст>")
        return
    ids = [tg for tg in await db.all_user_tg_ids() if tg not in config.ADMIN_IDS]
    ok = 0
    for tg in ids:
        try:
            await bot.send_message(tg, text, parse_mode="HTML", reply_markup=keyboards.main_menu())
            ok += 1
        except Exception:
            pass  # blocked the bot
        await asyncio.sleep(0.05)
    await db.log_event("broadcast", user_id=message.from_user.id, meta=f"{ok}/{len(ids)}")
    await message.answer(f"Рассылка готова: доставлено {ok} из {len(ids)}.")


@router.message(Command("pending"))
async def pending(message: Message) -> None:
    if not is_admin(message.from_user.id):
        return
    cur = await db._db().execute("SELECT * FROM listings WHERE status='pending' ORDER BY created_at LIMIT 20")
    pend = await cur.fetchall()
    if not pend:
        await message.answer("Нет объявлений на модерации.")
        return
    for r in pend:
        await message.answer(
            f"#{r['id']} {r['deal']} · {r['ptype']} · {r['district']} · {r['price']}{r['currency']}",
            reply_markup=keyboards.moderation_kb(r["id"]),
        )
