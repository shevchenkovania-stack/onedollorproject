"""Periodic jobs. Everything that can silently lose money runs here.

The inbox job is what makes card money real: Lemon Squeezy posts the order to the Mini App
process, that process only stores it, and here (single writer) it becomes a boost + payment row.
`apply_refunds` is the same money going back - a refund that leaves the revenue line and the top of
the board is the difference between a bookkeeper and a person who finds out from a chargeback.
`check_manual_claims` re-pushes unpaid MIA claims to the admins (a missed confirmation looks exactly
like a customer who never paid), `notify_expired_boosts` sells the same Boost a second time, and
`check_webhook_silence` notices when the tunnel moved and the till went quiet.
Deterministic by design - plain SQL, plain arithmetic, no LLM, no heuristics.
"""
import asyncio
import html
import logging
import time

from aiogram import Bot

import config
import db
import keyboards

log = logging.getLogger("chiriebot.scheduler")
CARD_TICK_SECONDS = 60  # how often the card payment inbox is checked
SILENCE_MINUTES = 15    # a paid checkout reaches us in seconds; longer silence = broken webhook
SILENCE_ALERT_COOLDOWN = 1800  # do not DM the same warning every minute
_silence_alert_at = 0.0
# A buyer who says "I sent the MIA transfer" is waiting for money we can already see in the bank.
# If no admin has ticked it within this window, the claim is reminded - a missed confirmation is a
# lost sale that looks exactly like a customer who never paid.
MANUAL_REMIND_HOURS = 6  # a claim is reminded at most once per this window (dedup lives in events)

_DEAL_LABEL = {"rent": "\u0410\u0440\u0435\u043d\u0434\u0430", "sale": "\u041f\u0440\u043e\u0434\u0430\u0436\u0430"}


def _alert_match(a, listing) -> bool:
    """Pure rule, no DB: does a freshly live ad satisfy one saved search? Mirrors api_listings:
    deal / exact district (case-insensitive) / rooms bucket (3 means 3+) / price ceiling / free text.
    A None on the alert means 'any' for that field."""
    if a["deal"] and a["deal"] != listing["deal"]:
        return False
    if a["district"]:
        if (listing["district"] or "").strip().lower() != str(a["district"]).strip().lower():
            return False
    if a["rooms"]:
        lr = listing["rooms"]
        if lr is None:
            return False
        if a["rooms"] >= 3:
            if lr < 3:
                return False
        elif lr != a["rooms"]:
            return False
    if a["price_max"] and (listing["price"] or 0) > a["price_max"]:
        return False
    if a["query"]:
        hay = ((listing["district"] or "") + " " + (listing["description"] or "")).lower()
        if str(a["query"]).lower() not in hay:
            return False
    return True


async def notify_search_subscribers(bot: Bot, listing) -> int:
    """Push a just-approved ad to everyone watching a matching search (screen 08).

    Owner never receives their own ad; a subscriber already past this id is skipped, and the id is
    only advanced on a real delivery, so a blocked chat can't silently consume the alert."""
    sent = 0
    lid = listing["id"]
    for a in await db.all_search_alerts():
        if a["user_id"] == listing["user_id"] or a["last_listing_id"] >= lid:
            continue
        if not _alert_match(a, listing):
            continue
        # Paid board: a saved search only delivers while its subscription is live. The DB check is
        # short-circuited behind the flag, so the free board (PAID_ENABLED=0) still pushes to every
        # watcher and the alert clock is never consulted.
        if config.PAID_ENABLED and not await db.is_alerts_subscribed(a["user_id"]):
            continue
        deal = _DEAL_LABEL.get(listing["deal"], listing["deal"] or "")
        dist = listing["district"] or "\u2014"
        cur = listing["currency"] or ""
        price = listing["price"]
        rooms = f" \u00b7 {listing['rooms']} \u043a\u043e\u043c\u043d." if listing["rooms"] else ""
        cap = (f"\U0001F514 <b>\u041d\u043e\u0432\u043e\u0435 \u043f\u043e \u0442\u0432\u043e\u0435\u043c\u0443 \u043f\u043e\u0438\u0441\u043a\u0443</b>\n"
               f"{deal}{rooms} \u00b7 \U0001F4CD {dist}\n"
               f"\U0001F4B8 {price} {cur}")
        if config.WEBAPP_URL:
            # web_app (not plain url): only a Mini App launch carries Telegram's initData,
            # so the viewer can open the card AND, if they're the owner, submit without a 401.
            btn = {"text": "\U0001F441 \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0435",
                   "web_app": {"url": config.WEBAPP_URL.rstrip("/") + f"/#listing={lid}"}}
        else:
            btn = {"text": "\U0001F441 \u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043e\u0431\u044a\u044f\u0432\u043b\u0435\u043d\u0438\u0435",
                   "callback_data": f"view:{lid}"}
        try:
            await bot.send_message(a["tg_id"], cap, parse_mode="HTML",
                                   reply_markup={"inline_keyboard": [[btn]]})
        except Exception as e:  # blocked / deleted chat: leave last_listing_id so we can retry a newer ad
            log.info("search alert #%s -> tg %s skipped: %s", a["id"], a["tg_id"], e)
            continue
        await db.touch_search_alert(a["id"], lid)
        sent += 1
    if sent:
        await db.log_event("search_alert_push", listing_id=lid, meta=str(sent))
    return sent



async def nudge_unboosted(bot: Bot) -> int:
    """Offer a Boost to owners of live, currently unboosted listings."""
    rows = await db.listings_to_nudge(config.NUDGE_AFTER_DAYS, config.NUDGE_COOLDOWN_DAYS)
    sent = 0
    for row in rows:
        owner = await db.get_user_by_id(row["user_id"])
        if not owner:
            await db.mark_nudged(row["id"])
            continue
        rooms = f"{row['rooms']}-комн. " if row["rooms"] else ""
        text = (
            f"\u23F3 Твоё объявление <b>#{row['id']}</b> "
            f"({rooms}{row['district'] or '—'} \u00B7 {row['price']} {row['currency']}) "
            "в базе, но сейчас не в топе.\n"
            f"Просмотров: <b>{row['views']}</b>\n\n"
            + keyboards.boost_pitch()
        )
        try:
            await bot.send_message(
                owner["tg_id"], text, parse_mode="HTML",
                reply_markup=keyboards.boost_kb(row["id"]),
            )
        except Exception as e:  # blocked / deleted chat: don't retry forever
            log.info("nudge skipped #%s (user %s): %s", row["id"], owner["tg_id"], e)
        await db.mark_nudged(row["id"])
        await db.log_event("boost_nudge", user_id=owner["id"], listing_id=row["id"])
        sent += 1
    return sent


async def apply_card_orders(bot: Bot) -> int:
    """Credit Boost for card orders the provider reported as paid.

    The webhook only stores rows; this is the single place where card money becomes a boost.
    Idempotent by the applied flag, so a restart or a repeated delivery can never pay twice.
    """
    applied = 0
    for row in await db.card_orders_to_apply():
        listing = await db.get_listing(row["listing_id"])
        buyer = await db.get_user_by_tg_id(row["tg_id"]) if row["tg_id"] else None
        if not listing or not buyer:
            log.warning("card order %s: no listing #%s or user %s, left unapplied",
                        row["order_id"], row["listing_id"], row["tg_id"])
            await db.log_event("card_order_unmatched", listing_id=row["listing_id"],
                               meta=row["order_id"])
            continue
        is_test = bool(row["test_mode"])
        amount = row["amount_cents"] / 100
        await db.boost_listing(row["listing_id"], config.BOOST_DAYS)
        await db.log_boost(row["listing_id"], buyer["id"], 0, config.BOOST_DAYS, status="paid",
                           order_ref=f"ls:{row['order_id']}")
        await db.log_payment(user_id=buyer["id"], listing_id=row["listing_id"],
                             purpose="boost_card_test" if is_test else "boost_card",
                             stars=0, currency=row["currency"], telegram_charge_id=None,
                             provider_charge_id=row["order_id"], amount=amount, is_test=is_test)
        await db.mark_card_order_applied(row["id"])
        await db.log_event("boost_card_paid", user_id=buyer["id"], listing_id=row["listing_id"],
                           meta=f"{row['order_id']} {amount} {row['currency']}")
        tag = "\U0001F9EA Тестовый платёж принят. " if is_test else "\u2705 Оплата картой получена! "
        try:
            await bot.send_message(
                buyer["tg_id"],
                f"{tag}Объявление <b>#{row['listing_id']}</b> поднято в топ на "
                f"<b>{config.BOOST_DAYS} дней</b> \U0001F680",
                parse_mode="HTML", reply_markup=keyboards.main_menu(),
            )
        except Exception as e:
            log.info("card notice skipped for %s: %s", buyer["tg_id"], e)
        for admin in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin,
                    f"\U0001F4B3 <b>Новый платёж</b>\nBoost #{row['listing_id']}: "
                    f"{amount:.2f} {row['currency']} картой (Lemon Squeezy)"
                    + ("\n⚠️ тестовый режим — денег по этому заказу нет" if is_test else ""),
                )
            except Exception:
                pass
        applied += 1
    return applied


async def apply_refunds(bot: Bot) -> int:
    """Money the provider took back has to leave both the revenue line and the top of the board.

    Idempotent by `payments.refunded_at`, so a repeated order_refunded delivery can never strip the
    same boost twice. `partial_refund` is deliberately ignored: the money we keep is real, so the
    boost stays. A refund only takes the top spot away when nothing newer paid for it.
    """
    done = 0
    for row in await db.payments_refunds():
        await db.mark_payment_refunded(row["payment_id"])
        boost = await db.find_boost_by_ref(f"ls:{row['order_id']}")
        took_top = False
        if boost:
            latest = await db.latest_boost_for_listing(row["listing_id"])
            if latest and latest["id"] == boost["id"]:
                await db.unboost_listing(row["listing_id"])
                took_top = True
            await db.set_boost_status(boost["id"], "refunded")
        await db.log_event("boost_refunded", user_id=row["user_id"], listing_id=row["listing_id"],
                           meta=f"{row['order_id']} {row['amount']} {row['currency']} "
                                f"{row['order_status']}")
        log.warning("refund processed: order %s, listing %s, top taken: %s",
                    row["order_id"], row["listing_id"], took_top)
        owner = await db.get_user_by_id(row["user_id"])
        if owner:
            try:
                await bot.send_message(
                    owner["tg_id"],
                    f"\U0001F4B8 Провайдер сообщил о возврате по объявлению <b>#{row['listing_id']}</b> "
                    f"({row['amount']:.2f} {row['currency']})."
                    + (" Топ отключён." if took_top else "")
                    + " Если это ошибка — напиши /paysupport.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        for admin in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin,
                    f"\u26a0\ufe0f <b>Возврат / чарджбек</b>\nЗаказ <code>{row['order_id']}</code>: "
                    f"{row['amount']:.2f} {row['currency']}, статус {row['order_status']}\n"
                    f"Объявление #{row['listing_id']}, "
                    + ("топ снят" if took_top else "топ не тронут (был позже оплачен снова)"),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        done += 1
    return done


async def refund_manual_claim(bot: Bot, boost_id: int) -> str:
    """Book an MIA/MDL transfer that the admin sent back by hand.

    MIA has no revoke API, so the only honest record is a human tick - and it has to write the same
    three things a card refund writes, otherwise the MDL revenue line quietly keeps money we already
    gave back: the payment gets `refunded_at` (which drops it out of every revenue total and makes a
    second tap a no-op), the boost goes to 'refunded', and the top spot is released when nothing
    newer paid for it.
    """
    boost = await db.get_boost(boost_id)
    if not boost:
        return "Заявка не найдена (возможно, уже обработана)."
    if boost["status"] == "refunded":
        return "Этот платёж уже отмечен как возвращённый — ничего не меняю."
    if boost["status"] != "manual":
        return (f"Возврат оформляется только по подтверждённому платежу, "
                f"а заявка #{boost_id} в статусе «{boost['status']}».")
    listing_id = boost["listing_id"]
    payment = await db.find_payment_by_charge(f"manual:{boost_id}")
    amount = float(payment["amount"]) if payment and payment["amount"] is not None \
        else float(boost["stars"] or config.BOOST_MDL)
    currency = (payment["currency"] if payment else None) or "MDL"
    if payment:
        await db.mark_payment_refunded(payment["id"])
    took_top = False
    latest = await db.latest_boost_for_listing(listing_id)
    if latest and latest["id"] == boost_id:
        await db.unboost_listing(listing_id)
        took_top = True
    await db.set_boost_status(boost_id, "refunded")
    await db.log_event("manual_refunded", user_id=boost["user_id"], listing_id=listing_id,
                       meta=f"{boost_id} {amount:.0f} {currency}")
    log.warning("manual refund booked: boost %s, listing %s, %s %s, top taken: %s",
                boost_id, listing_id, amount, currency, took_top)
    owner = await db.get_user_by_id(boost["user_id"])
    if owner:
        note = (f"\U0001F4B8 Деньги по объявлению <b>#{listing_id}</b> ({amount:.0f} {currency}) "
                "возвращены тебе переводом.")
        note += " Топ отключён." if took_top else " Топ остался: позже ты оплатил Boost снова."
        try:
            await bot.send_message(owner["tg_id"], note, parse_mode="HTML")
        except Exception:
            pass
    return (f"Готово: {amount:.0f} {currency} убраны из выручки"
            + (", топ снят." if took_top else " (топ не тронут — позже был оплачен снова)."))


def is_webhook_silent(link_ago_seconds: int | None, deliveries: int,
                      grace_seconds: int = SILENCE_MINUTES * 60) -> bool:
    """Pure rule, no DB: a payment link is old enough to have been paid, yet nothing arrived.

    Deliberately conservative - one delivery inside the window marks the rail healthy, because a
    false alarm on money code is worse than a missed one (the admin also sees card_unapplied).
    """
    return link_ago_seconds is not None and link_ago_seconds >= grace_seconds and deliveries == 0


async def check_webhook_silence(bot: Bot) -> bool:
    """Notice when Lemon Squeezy stops delivering orders while payment links are out there.

    With a temporary HTTPS tunnel the public URL dies on every restart, so the provider keeps
    posting to a host that no longer answers: the client pays real money, the boost never comes
    and nothing anywhere says "error". Someone has to learn that in minutes, not at month end.
    """
    global _silence_alert_at
    if not config.CARD_ENABLED:
        return False
    ago = await db.card_checkout_ago_seconds()
    if ago is None or ago < SILENCE_MINUTES * 60:
        return False  # nobody was sent to the payment page, or it is too fresh to judge
    deliveries = await db.card_orders_since(SILENCE_MINUTES)
    if not is_webhook_silent(ago, deliveries):
        return False
    if time.monotonic() - _silence_alert_at < SILENCE_ALERT_COOLDOWN:
        return True
    _silence_alert_at = time.monotonic()
    checkouts = await db.card_checkouts_since(SILENCE_MINUTES)
    await db.log_event("card_webhook_silent",
                       meta=f"{checkouts} checkout(s), last link {ago // 60} min ago")
    text = (
        f"\u26a0\ufe0f <b>Касса молчит</b>\n"
        f"Ссылок на оплату за {SILENCE_MINUTES} мин: <b>{checkouts}</b>, доставленных заказов: <b>0</b>.\n"
        f"Последняя ссылка выдана {ago // 60} мин назад.\n\n"
        "Значит одно из трёх: вебхук Lemon Squeezy указывает не на этот адрес, упал туннель/nginx, "
        "или подпись не сходится (см. журнал imobiliare-web).\n"
        f"Текущий публичный адрес: <code>{config.WEBAPP_URL or 'не задан (WEBAPP_URL пуст)'}</code>\n\n"
        "Если клиент при этом платил - деньги у провайдера, но Boost не начислен: проверь вебхук, "
        "попроси у LS повторную доставку и перезапусти оба сервиса."
    )
    log.warning("card webhook silent: %s checkouts, 0 deliveries, last link %ss ago",
                checkouts, ago)
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, text, parse_mode="HTML")
        except Exception:
            pass
    return True


async def check_manual_claims(bot: Bot) -> int:
    """Re-send MIA claims that nobody has confirmed yet.

    The money is already in the bank - only the tick is missing. Without this, a busy admin and a
    silent inbox look exactly like a customer who never paid, and the sale is lost with no trace.
    """
    if not config.BOOST_MDL:
        return 0
    sent = 0
    for row in await db.manual_claims_waiting(MANUAL_REMIND_HOURS):
        if await db.event_recent("manual_remind", str(row["boost_id"]), MANUAL_REMIND_HOURS):
            continue
        expect = (f"с номера <code>{html.escape(row['listing_phone'])}</code>"
                  if row["listing_phone"] else f"от @{row['username'] or row['tg_id']}")
        text = (
            f"\U0001F4E2 <b>Перевод ждёт подтверждения уже {row['waited_hours']} ч</b>\n"
            f"Boost #{row['listing_id']}: <b>{config.BOOST_MDL} MDL</b> "
            f"({keyboards.transfer_word()}), {config.BOOST_DAYS} дней\n"
            f"Объявление: #{row['listing_id']}, {row['district'] or '—'}, "
            f"{row['price']} {row['currency']}\n"
            f"Платёж жди: {expect}\n"
            f"Комментарий к платежу: <b>{keyboards.manual_reference(row['listing_id'])}</b>\n"
            f"boost_id={row['boost_id']}\n\n"
            "Если денег нет - отклони заявку, покупатель получит сообщение и попробует снова."
        )
        for admin in config.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin, text, parse_mode="HTML",
                    reply_markup=keyboards.manual_review_kb(row["boost_id"], row["listing_id"]),
                )
            except Exception:
                pass
        await db.log_event("manual_remind", user_id=row["user_id"], listing_id=row["listing_id"],
                           meta=str(row["boost_id"]))
        sent += 1
    return sent


async def notify_expired_boosts(bot: Bot) -> int:
    """Tell an owner whose Boost just ran out: the product worked, now sell it again.

    Repeat purchase is the cheapest revenue in the project, and the moment of losing the top spot
    is the only moment it feels real. Dedup is the `boost_expired` event newer than boosted_until,
    so buying again re-arms the notice.
    """
    sent = 0
    for row in await db.boosts_that_just_expired():
        rooms = f"{row['rooms']}-комн. " if row["rooms"] else ""
        text = (
            f"\u23F1 Твой Boost по объявлению <b>#{row['id']}</b> "
            f"({rooms}{row['district'] or '—'} \u00B7 {row['price']} {row['currency']}) "
            "закончился - объявление снова в общем списке.\n\n"
            + keyboards.boost_pitch()
        )
        try:
            await bot.send_message(row["tg_id"], text, parse_mode="HTML",
                                   reply_markup=keyboards.boost_kb(row["id"]))
        except Exception as e:
            log.info("expiry notice skipped for #%s: %s", row["id"], e)
        await db.log_event("boost_expired", user_id=row["user_id"], listing_id=row["id"],
                           meta=row["boosted_until"])
        sent += 1
    return sent


async def run(bot: Bot) -> None:
    """Sleep first, then work forever. Exceptions never kill the loop."""
    elapsed = 0
    while True:
        await asyncio.sleep(CARD_TICK_SECONDS)
        elapsed += CARD_TICK_SECONDS
        try:
            n = await apply_card_orders(bot)
            if n:
                log.info("card boosts applied: %s", n)
            r = await apply_refunds(bot)
            if r:
                log.warning("refunds processed: %s", r)
            await check_webhook_silence(bot)
            m = await check_manual_claims(bot)
            if m:
                log.info("manual claim reminders: %s", m)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("card apply pass failed")
        if elapsed >= config.NUDGE_INTERVAL_HOURS * 3600:
            elapsed = 0
            try:
                n = await nudge_unboosted(bot)
                if n:
                    log.info("boost nudges sent: %s", n)
                e = await notify_expired_boosts(bot)
                if e:
                    log.info("boost expiry notices: %s", e)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("nudge pass failed")
