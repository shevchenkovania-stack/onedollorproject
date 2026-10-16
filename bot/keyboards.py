import html
import re
from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config

RENT = "\U0001F3E0 Аренда"
SALE = "\U0001F4B0 Продажа"
ADD = "\u2795 Разместить объект"
MY = "\U0001F4CB Мои объявления"
SEARCH = "\U0001F50D Поиск"
HELP = "\u2139\uFE0F Помощь"


def add_label() -> str:
    """The 'post a listing' button, worded for the board's focus. Generic when not focused."""
    if config.FOCUS_DEAL == "rent" and config.FOCUS_ROOMS == "1":
        return "\u2795 Сдам однушку"
    if config.FOCUS_DEAL == "rent":
        return "\u2795 Сдам жильё"
    return ADD


def board_label() -> str:
    """Short name of the board for button copy, driven by the focus."""
    if config.FOCUS_DEAL == "rent" and config.FOCUS_ROOMS == "1":
        return "доску однушек"
    return "доску"


# --- price copy, always from config (never a hardcoded number in a label) -----

def listing_price_label() -> str:
    return f"\U0001F501 Продлить · {config.PRICE_LISTING_MDL} лей"


def highlight_price_label() -> str:
    return f"\U0001F525 Срочно · {config.PRICE_HIGHLIGHT_MDL} леев"


def alerts_price_label() -> str:
    display = config.ALERTS_DISPLAY or f"{config.PRICE_ALERTS_MDL} леев"
    return f"\U0001F514 Уведомления · {display}/мес"


def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text=SEARCH, callback_data="menu:search")],
        [InlineKeyboardButton(text=add_label(), callback_data="menu:add")],
        [InlineKeyboardButton(text=MY, callback_data="menu:my")],
    ]
    if config.PAID_ENABLED:
        # a wallet button only makes sense once the board charges; when PAID_ENABLED=0 the
        # horse stays free and there is nothing to top up.
        kb.append([InlineKeyboardButton(text="\U0001F4B3 Кошелёк · пополнить", callback_data="wallet:menu")])
    kb.append([InlineKeyboardButton(text=HELP, callback_data="menu:help")])
    if config.WEBAPP_URL:
        kb.insert(1, [InlineKeyboardButton(text=f"\U0001F4F1 Открыть {board_label()}", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    if config.CHANNEL_URL:
        kb.append([InlineKeyboardButton(text="\U0001F4E3 Канал с объектами", url=config.CHANNEL_URL)])
    share = share_url()
    if share:
        kb.append([InlineKeyboardButton(text="\U0001F4E4 Пригласить друзей", url=share)])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def share_url(listing_id: int | None = None) -> str | None:
    """A native Telegram forward link - the engine that makes promotion run without hands.

    A URL button opens the user's own share sheet with a message pre-typed; they pick chats in one
    tap. Every shared card carries a bot deep link (`?start=share_<id>`), so whoever taps it lands on
    that exact object and is a whisker from posting their own. Attribution stays in
    `events.start.meta`, so we can see which listing actually spreads.
    """
    if not config.BOT_URL:
        return None
    target = f"{config.BOT_URL}?start=share_{listing_id}" if listing_id else config.BOT_URL
    if listing_id:
        text = (f"Объект #{listing_id} на Однушка.md — квартиры и дома в Кишинёве: "
                "без комиссии, контакты на карточке, размещение бесплатное")
    else:
        text = ("Однушка.md — бесплатная доска аренды и продажи жилья в Кишинёве прямо в Telegram: "
                "объявление публикуется за 2 минуты")
    return "https://t.me/share/url?url=" + quote(target, safe="") + "&text=" + quote(text)


def listing_kb(listing_id: int, owner: bool, boosted: bool,
               contacts: dict | None = None) -> InlineKeyboardMarkup:
    rows = []
    if owner:
        rows.append([InlineKeyboardButton(text="\U0001F680 Поднять (Boost)", callback_data=f"boost:{listing_id}")])
    rows.append([InlineKeyboardButton(text="\U0001F4DE Показать контакт", callback_data=f"contact:{listing_id}")])
    # public channels the owner was ready to share: one tap -> the app opens. no reveal needed.
    rows.extend(contact_buttons(contacts))
    share = share_url(listing_id)
    if share:
        rows.append([InlineKeyboardButton(text="\U0001F4E4 Поделиться объектом", url=share)])
    if owner:
        # Stage 4: the owner can close out their own ad - sold / extend / delete (all reversible
        # via Продлить, since archiving only takes it off the board and keeps the row).
        extend_label = "\U0001F501 Продлить"
        if config.PAID_ENABLED:
            extend_label = listing_price_label()
        rows.append([
            InlineKeyboardButton(text="\u2705 Сдано", callback_data=f"sold:{listing_id}"),
            InlineKeyboardButton(text=extend_label, callback_data=f"extend:{listing_id}"),
            InlineKeyboardButton(text="\U0001F5D1 Удалить", callback_data=f"delete:{listing_id}"),
        ])
        if config.PAID_ENABLED:
            # the paid highlight: one tap buys the "Срочно" badge (spent from the wallet).
            rows.append([InlineKeyboardButton(text=highlight_price_label(),
                                              callback_data=f"hl:{listing_id}")])
    if not owner:
        # every opened card is a recruitment moment: a viewer who is looking at someone
        # else's listing is one tap from being an owner. Put the pitch right before they
        # leave the card.
        rows.append([InlineKeyboardButton(text="\u2795 Разместить своё", callback_data="menu:add")])
    rows.append([InlineKeyboardButton(text="\u2b05\uFE0F К списку", callback_data="back:search")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# every channel the owner may attach to a listing -> (button label, url builder).
# NOTE: Telegram inline URL buttons only accept http(s):// and tg:// - viber://, tel: and
# intent:// are rejected server-side with "Unsupported URL protocol" and take the whole card
# down. so every builder here must return an https:// deeplink that the client can open.
_CONTACT_CHANNELS = {
    "telegram":  ("\U0001F4AC Telegram",  lambda v: _as_url(v, "https://t.me/", strip_at=True)),
    "instagram": ("\U0001F4F7 Instagram", lambda v: _as_url(v, "https://instagram.com/", strip_at=True)),
    "facebook":  ("\U0001F4D8 Facebook",  lambda v: _as_url(v, "https://facebook.com/", strip_at=True)),
    "whatsapp":  ("\U0001F7E2 WhatsApp",  lambda v: "https://wa.me/" + re.sub(r"\D", "", str(v))),
    "site":      ("\U0001F517 Сайт",       lambda v: _as_url(v, "https://")),
}


def _as_url(v: str, prefix: str, strip_at: bool = False) -> str:
    s = str(v).strip()
    if s.startswith("http"):
        return s
    if strip_at:
        s = s.lstrip("@/")
    return prefix + s


def _safe_url(u: str) -> str | None:
    """Return u only if Telegram will accept it as an inline URL button.
    Stale viber:// rows already saved in the DB are silently filtered here so a bad contact
    cannot take a card / channel post / Mini App render down."""
    if not u:
        return None
    if not (u.startswith("https://") or u.startswith("http://") or u.startswith("tg://")):
        return None
    return u


def contact_buttons(contacts: dict | None) -> list[list[InlineKeyboardButton]]:
    """URL buttons for owner-supplied public channels. Unknown keys and unsupported
    protocols are skipped silently so a stale dict never breaks a card render."""
    out: list[list[InlineKeyboardButton]] = []
    for key, val in (contacts or {}).items():
        pair = _CONTACT_CHANNELS.get(key)
        if not pair or not val:
            continue
        label, build = pair
        try:
            url = build(val)
        except Exception:
            continue
        url = _safe_url(url)
        if url:
            out.append([InlineKeyboardButton(text=label, url=url)])
    return out


def usd(cents: int) -> str:
    """Price label for the card rail (Lemon Squeezy store currency is USD)."""
    return f"${cents / 100:.2f}"


def transfer_word() -> str:
    """How we call the MDL rail: MIA (BNM instant payments) once a phone number is configured."""
    return "MIA" if config.PAY_PHONE else "переводом"


def transfer_button() -> str:
    """Button label for the MDL rail, capitalised the way a button deserves."""
    return f"MIA {config.BOOST_MDL} MDL" if config.PAY_PHONE else f"Перевод {config.BOOST_MDL} MDL"


def pay_lines(listing_id: int) -> list:
    """Fields the buyer enters in their banking app, in the order the bank asks for them.
    The 'Boost #N' line is the comment we look for in the bank statement, so it is always there."""
    lines = []
    if config.PAY_PHONE:
        lines.append(config.PAY_PHONE)
    if config.PAY_NAME:
        lines.append(config.PAY_NAME)
    if config.PAY_INFO:
        lines.append(config.PAY_INFO)
    lines.append(f"Boost #{listing_id}")
    return lines


def pay_block(listing_id: int) -> str:
    """Tap-to-copy block: Telegram copies the whole <code> when the user taps it, so nobody
    retypes a phone number and nobody mistypes the comment we match payments by."""
    return "<code>" + html.escape("\n".join(pay_lines(listing_id))) + "</code>"


def manual_pitch(listing_id: int) -> str:
    """The whole MDL payment instruction, written for a person holding a Moldovan banking app."""
    price = config.BOOST_MDL
    if config.PAY_PHONE:
        who = f", сверь имя получателя: <b>{html.escape(config.PAY_NAME)}</b>." if config.PAY_NAME else \
              ", сверь имя получателя на экране подтверждения."
        return (
            f"\u26A1 <b>Boost за {price} MDL</b> (объявление #{listing_id}, {config.BOOST_DAYS} дней)\n\n"
            "1. В приложении банка открой <b>MIA \u00b7 Мгновенные платежи</b> \u2192 «Перевод P2P».\n"
            f"2. Введи номер получателя и сумму <b>{price} MDL</b>{who}\n"
            "3. Комментарием к переводу напиши текст из блока ниже \u2014 по нему мы найдём "
            "твой платёж за секунды.\n\n"
            f"{pay_block(listing_id)}\n\n"
            "Перевод приходит мгновенно, комиссии для переводящего нет. Как только отправишь \u2014 "
            "нажми «Я перевёл»."
        )
    details = html.escape(config.PAY_INFO) if config.PAY_INFO else "реквизиты пришлёт модератор"
    return (
        f"\U0001F4B0 <b>Boost за {price} MDL</b> (объявление #{listing_id}, {config.BOOST_DAYS} дней)\n\n"
        f"1. Переведи <b>{price} MDL</b>: {details}\n"
        f"   В комментарии укажи номер объявления <b>#{listing_id}</b>.\n"
        "2. Нажми «Я перевёл» \u2014 модератор проверит поступление и поднимет объявление.\n\n"
        "Обычно подтверждение занимает несколько минут в рабочее время."
    )


def manual_reference(listing_id: int) -> str:
    """What the admin must spot in the bank statement for this claim."""
    return f"Boost #{listing_id}"


def rails_line() -> str:
    """Payment options in one line: '$5.00 💳 картой / 100 MDL MIA / 400 Stars ⭐'."""
    opts = []
    if config.CARD_ENABLED:
        opts.append(f"{usd(config.CARD_PRICE_CENTS)} \U0001F4B3 картой")
    if config.BOOST_MDL:
        opts.append(f"{config.BOOST_MDL} MDL \U0001F4B0 {transfer_word()}")
    # Stars is an optional rail: BOOST_STARS=0 hides it everywhere (bug #8). MIA is the primary rail.
    if config.BOOST_STARS:
        opts.append(f"{config.BOOST_STARS} Stars \u2B50")
    return " / ".join(opts)


def boost_pitch() -> str:
    """One sentence with every wired-up payment rail. Single source for all price copy."""
    return (f"\U0001F680 <b>Boost</b> \u2014 {rails_line()}: объявление поднимается на первое "
            f"место в поиске на <b>{config.BOOST_DAYS} дней</b>.")


def boost_kb(listing_id: int, gift_credits: int = 0) -> InlineKeyboardMarkup:
    """All available payment rails, cheapest friction first - a free gift raise is always first."""
    rows = []
    if gift_credits > 0:
        # founders gift: free top-raises for the first publishers, spends no money and
        # writes no payments row - the boost is booked with status 'gift'
        rows.append([InlineKeyboardButton(
            text=f"\U0001F381 Поднять бесплатно (осталось {gift_credits})",
            callback_data=f"gift:{listing_id}")])
    if config.PAID_ENABLED and config.BOOST_MDL:
        # the wallet is the lowest-friction rail: money is already in, no transfer to chase, the
        # top spot lights up on the tap itself.
        rows.append([InlineKeyboardButton(
            text=f"\u26A1 Из кошелька · {config.BOOST_MDL} леев",
            callback_data=f"boostwallet:{listing_id}")])
    if config.CARD_ENABLED:
        # hosted checkout: no card attached to Telegram and no acquiring contract needed
        rows.append([InlineKeyboardButton(text=f"\U0001F4B3 Картой {usd(config.CARD_PRICE_CENTS)}",
                                          callback_data=f"card:{listing_id}")])
    if config.BOOST_MDL:
        # instant transfer in MDL: many sellers in MD have no card attached to Telegram, so they
        # cannot buy Stars at all (PROVIDER_ACCOUNT_INVALID), and there is no acquiring contract
        rows.append([InlineKeyboardButton(text=f"\U0001F4B0 {transfer_button()}",
                                          callback_data=f"manual:{listing_id}")])
    # Stars rail is hidden when BOOST_STARS=0 (bug #8); the free gift / card / MIA rails above stay.
    if config.BOOST_STARS:
        rows.append([InlineKeyboardButton(text=f"\u2B50 Оплатить {config.BOOST_STARS} Stars",
                                          callback_data=f"boostpay:{listing_id}")])
    rows.append([InlineKeyboardButton(text="\u2b05\uFE0F Назад", callback_data=f"view:{listing_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def card_pay_kb(url: str, listing_id: int) -> InlineKeyboardMarkup:
    """Opens the Lemon Squeezy checkout in the in-app browser."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001F517 Открыть оплату картой", url=url)],
        [InlineKeyboardButton(text="\u2b05\uFE0F Назад", callback_data=f"view:{listing_id}")],
    ])


def manual_kb(listing_id: int, mdl: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"\U0001F4B0 Я перевёл {mdl} MDL", callback_data=f"manualpaid:{listing_id}")],
        [InlineKeyboardButton(text="\u2b05\uFE0F Назад", callback_data=f"view:{listing_id}")],
    ])


def manual_review_kb(boost_id: int, listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="\u2705 Деньги пришли", callback_data=f"manapp:{boost_id}"),
            InlineKeyboardButton(text="\u274C Не вижу платежа", callback_data=f"manrej:{boost_id}"),
        ],
        [InlineKeyboardButton(text="\U0001F441 Объявление", callback_data=f"view:{listing_id}")],
    ])


def manual_refund_kb(boost_id: int, listing_id: int) -> InlineKeyboardMarkup:
    """Offered to the admin right after a manual transfer was approved.

    MIA has no revoke API: returning the money means opening the banking app and transferring it
    back by hand. This button is where the books learn about it, so the refunded MDL stops being
    counted as revenue and the buyer stops living in the top of the board for free.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u21A9\uFE0F Деньги вернули — снять Boost",
                              callback_data=f"manref:{boost_id}")],
        [InlineKeyboardButton(text="\U0001F441 Объявление", callback_data=f"view:{listing_id}")],
    ])


def manual_refund_confirm_kb(boost_id: int, listing_id: int) -> InlineKeyboardMarkup:
    """A money action never fires on the first tap."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 Да, я перевёл деньги обратно",
                              callback_data=f"manrefyes:{boost_id}"),
         InlineKeyboardButton(text="\u2b05\uFE0F Отмена", callback_data=f"view:{listing_id}")],
    ])


# --- wallet / top-up keyboards ------------------------------------------------

def wallet_menu_kb(balance: int) -> InlineKeyboardMarkup:
    """Pick a lump sum to move into the wallet. Big presets on purpose: one MIA transfer buys
    weeks of renewals, so the admin reconciles rarely instead of once per leu."""
    rows, pair = [], []
    for a in (config.TOPUP_PRESETS or [30, 50, 100]):
        pair.append(InlineKeyboardButton(text=f"\U0001F4B0 {a} леев", callback_data=f"tup:{a}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text=alerts_price_label(), callback_data="alerts:sub")])
    rows.append([InlineKeyboardButton(text="\u2b05\uFE0F Меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_reference(topup_id: int) -> str:
    """The comment the buyer writes in their banking app so we can find this exact top-up."""
    return f"Topup #{topup_id}"


def topup_pitch(topup_id: int, amount: int) -> str:
    """How to send a lump sum, written for a person holding a Moldovan banking app."""
    lines = []
    if config.PAY_PHONE:
        lines.append(config.PAY_PHONE)
    if config.PAY_NAME:
        lines.append(config.PAY_NAME)
    if config.PAY_INFO:
        lines.append(config.PAY_INFO)
    lines.append(topup_reference(topup_id))
    block = "<code>" + html.escape("\n".join(lines)) + "</code>"
    if config.PAY_PHONE:
        return (f"\U0001F4B3 <b>Пополнить кошелёк на {amount} леев</b>\n\n"
                "1. В приложении банка открой <b>MIA · Мгновенные платежи</b> → «Перевод P2P».\n"
                f"2. Введи номер получателя и сумму <b>{amount} MDL</b>.\n"
                f"3. Комментарием напиши блок ниже — по нему мы найдём твой платёж.\n\n{block}\n\n"
                "Деньги приходят мгновенно. Как отправишь — нажми «Я отправил».")
    details = html.escape(config.PAY_INFO) if config.PAY_INFO else "реквизиты пришлёт модератор"
    return (f"\U0001F4B3 <b>Пополнить кошелёк на {amount} леев</b>\n\n"
            f"Переведи <b>{amount} MDL</b>: {details}\n"
            f"Комментарий: <b>{topup_reference(topup_id)}</b>.\n\n"
            "Когда отправишь — нажми «Я отправил», модератор проверит и зачислит леев на баланс.")


def topup_sent_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 Я отправил", callback_data=f"tuppaid:{topup_id}")],
        [InlineKeyboardButton(text="\u2b05\uFE0F Отмена", callback_data="wallet:menu")],
    ])


def topup_review_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="\u2705 Деньги пришли", callback_data=f"tupok:{topup_id}"),
            InlineKeyboardButton(text="\u274C Не вижу платежа", callback_data=f"tupno:{topup_id}"),
        ],
    ])


def moderation_kb(listing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="\u2705 Одобрить", callback_data=f"mod:approve:{listing_id}"),
            InlineKeyboardButton(text="\u274C Отклонить", callback_data=f"mod:reject:{listing_id}"),
        ],
        [InlineKeyboardButton(text="\U0001F441 Показать", callback_data=f"view:{listing_id}")],
    ])


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\U0001F3E0 Меню", callback_data="menu:home")],
    ])
