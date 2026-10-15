import html
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                            KeyboardButton, Message, ReplyKeyboardMarkup)

import config
import db
import keyboards
from constants import DEAL_LABELS, DISTRICTS, PTYPE_LABELS, ROOMS

router = Router()

# Linear spine of the wizard - "Назад" walks it one step back, keeping everything already
# answered. The optional detail screens (area/floor/description/phone) share ONE step in
# the numbering, so the wizard honestly says 5 instead of counting every screen.
STEP_ORDER = ("specs", "district", "price", "area", "floor", "description", "features", "phone", "contacts", "photos", "confirm")

STEP_LABEL = {
    "specs": "Шаг 1/5 · Характеристики",
    "district": "Шаг 2/5 · Район",
    "price": "Шаг 3/5 · Цена",
    "area": "Шаг 4/5 · Детали (необязательно)",
    "floor": "Шаг 4/5 · Детали (необязательно)",
    "description": "Шаг 4/5 · Описание (необязательно)",
    "features": "Шаг 4/5 · Удобства (необязательно)",
    "phone": "Шаг 4/5 · Детали (необязательно)",
    "contacts": "Шаг 4/5 · Доп. контакты (необязательно)",
    "photos": "Шаг 5/5 · Фото",
    "confirm": "Проверь и опубликуй",
}

# "Пропустить" on an optional step always means "one step forward"
SKIP_NEXT = {"area": "floor", "floor": "description", "description": "features",
             "features": "phone", "phone": "contacts", "contacts": "photos"}


class AddListing(StatesGroup):
    specs = State()
    district = State()
    price = State()
    area = State()
    floor = State()
    description = State()
    features = State()
    phone = State()
    contacts = State()
    contacts_ask = State()
    photos = State()
    confirm = State()


def _chip(label: str, active: bool, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=(label + " ✓") if active else label, callback_data=cb)


def _num(txt: str) -> str:
    """Оставить только цифры и разделители: '450 eur', '450€', '450 €' -> '450'.
    Люди вписывают валюту и символы в поле цены - не надо гонять их с начала."""
    return re.sub(r"[^\d.,]", "", txt or "")


def _cancel_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="✖️ Отмена", callback_data="cancel")


def _back_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Назад", callback_data="add:back")


def _skip_kb() -> InlineKeyboardMarkup:
    """Optional step: skip, step back, or cancel - all three always visible."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить ⏭", callback_data="skip:next")],
        [_back_btn()],
        [_cancel_btn()],
    ])


# text buttons on a reply keyboard (their text arrives back as a message, not a callback)
BTN_BACK = "⬅️ Назад"
BTN_CANCEL = "✖️ Отмена"
BTN_SKIP = "Пропустить ⏭"

# states where the user must TYPE something. they get a ReplyKeyboardMarkup instead of
# an InlineKeyboardMarkup so Telegram shows an `input_field_placeholder` hint right inside
# the compose bar - the standard "please fill this in" affordance. cannot be edited in place,
# so _goto answers a fresh message and one_time_keyboard=True hides the keyboard as soon as
# the user replies, letting the next inline step show cleanly.
_REPLY_STEPS = {"price", "area", "floor", "description", "phone", "contacts_ask"}

# where "Назад" on a reply step should land. defaults to the previous step in STEP_ORDER;
# contacts_ask is special because it is a sub-page of contacts, not a spine step.
_BACK_TARGET = {"contacts_ask": "contacts"}


def _ask_kb(placeholder: str, skip: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=([[KeyboardButton(text=BTN_SKIP)]] if skip else []) +
                 [[KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True, one_time_keyboard=True, input_field_placeholder=placeholder,
    )


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_back_btn()], [_cancel_btn()]])


def _specs_missing(data: dict) -> list[str]:
    """Which of the three required step-1 picks are still unset, in tap order."""
    miss = []
    if not data.get("deal"):
        miss.append("Сделка")
    if not data.get("ptype"):
        miss.append("Тип объекта")
    if not data.get("rooms"):
        miss.append("Комнаты")
    return miss


def _p_specs(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    deal = DEAL_LABELS.get(data.get("deal"), "—")
    ptype = PTYPE_LABELS.get(data.get("ptype"), "—")
    r = data.get("rooms")
    rooms = "—" if not r else ("3+" if r >= 3 else str(r))
    miss = _specs_missing(data)
    hint = f"👉 Осталось выбрать: «{miss[0]}»" if miss else "Всё выбрано → жми «Далее ➡️»"
    text = (
        f"<b>{STEP_LABEL['specs']}</b>\n\n"
        "Выбери два пункта — сразу видно здесь ↓\n"
        f"Сделка: <b>{deal}</b> · Тип: <b>{ptype}</b> · Комнаты: <b>{rooms}</b>\n"
        f"{hint}"
    )
    # the cold-start board is 1-2-3+ apartments only; houses and rooms come back once
    # the vertical is ready. matches the search filter simplification in browse.py.
    rooms_row = [
        _chip("3+" if r == 3 else str(r), data.get("rooms") == r, f"rooms:{r}")
        for r in (1, 2, 3)
    ]
    rows = [
        [
            _chip("🏠 Аренда", data.get("deal") == "rent", "deal:rent"),
            _chip("💰 Продажа", data.get("deal") == "sale", "deal:sale"),
        ],
        rooms_row,
        [InlineKeyboardButton(text="Далее ➡️", callback_data="add:next")],
        [_cancel_btn()],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_district(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = (f"<b>{STEP_LABEL['district']}</b>\n\n"
            "Нажми свой район одним тапом — и сразу попросим цену.")
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for d in DISTRICTS:
        pair.append(_chip(d, data.get("district") == d, f"district:{d}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_back_btn(), _cancel_btn()])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_price(data: dict):
    deal = data.get("deal") or "rent"
    tail = "EUR за объект" if deal == "sale" else "EUR в месяц"
    cur = data.get("price")
    now = f"\n\nСейчас: <b>{cur} EUR</b> — новое число заменит." if cur else ""
    text = (
        f"<b>{STEP_LABEL['price']}</b>\n\n"
        f"💶 <b>Напиши цену в поле ввода</b> 👇 и отправь — только число, {tail}.\n"
        f"Пойму и <code>450</code>, и <code>450€</code>, и <code>450 eur</code>.{now}"
    )
    return text, _ask_kb("например: 450")


def _p_area(data: dict):
    hint = f"\n\nСейчас: <b>{data['area']} м²</b>" if data.get("area") else ""
    return (f"<b>{STEP_LABEL['area']}</b>\n\n"
            f"📐 <b>Напиши площадь в м²</b> в поле ввода и отправь 👇. "
            f"Например: <code>40</code>.{hint}"), _ask_kb("например: 40", skip=True)


def _p_floor(data: dict):
    hint = f"\n\nСейчас: <b>{data['floor']}</b>" if data.get("floor") else ""
    return (f"<b>{STEP_LABEL['floor']}</b>\n\n"
            f"🏢 <b>Напиши этаж</b> в поле ввода 👇 — можно «4» или «4/9» (этаж/всего).{hint}"), \
           _ask_kb("например: 3 или 3/9", skip=True)


def _p_description(data: dict):
    hint = ""
    if data.get("description"):
        hint = ("\n\nСейчас:\n<i>" + html.escape(str(data["description"]))[:250] +
                "</i>\n\nНовый текст заменит старый.")
    text = (
        f"<b>{STEP_LABEL['description']}</b>\n\n"
        "📝 <b>Опиши объект в поле ввода</b> 👇 — адрес, ссылку на карту, "
        "срок, свободность, всё что важно. До 900 символов.\n\n"
        "Пример, что реально помогает сдать быстрее:\n"
        "<code>Grădina Botanică 18/1 · карта: https://goo.gl/…\n"
        "1 ком · квартира+living · 3/12 эт · новострой\n"
        "Свободна с 1 ноября\n"
        "750€ от 3 мес · 800€ от 1 мес</code>"
        f"{hint}"
    )
    return text, _ask_kb("опиши объект…", skip=True)


# the 12 features that show up most on Chisinau rental ads. tap-toggles -> stored as list
# of labels in state['features']; s_confirm joins them with ' · ' onto the end of description.
_FEATURES = (
    "🆕 Новостройка",           "🔥 Автономка",
    "❄️ Кондиционер",            "📶 Интернет",
    "🍽 Посудомойка",             "🧺 Стиральная",
    "🛋 С мебелью",              "🚗 Парковка",
    "🛗 Лифт",                    "🐾 Животные ок",
    "👶 Дети ок",                "🏖 Балкон",
)


def _p_features(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    sel = set(data.get("features") or [])
    n = len(sel)
    got = f"\n\nОтмечено: <b>{n}</b>" if n else "\n\nПока ничего не отмечено — можно пропустить."
    text = (
        f"<b>{STEP_LABEL['features']}</b>\n\n"
        "Тапай то, что есть. Повторный тап снимает."
        f"{got}\n\n"
        "«✅ Готово» закончит шаг."
    )
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for f in _FEATURES:
        on = f in sel
        rows_label = ("✓ " if on else "") + f
        pair.append(InlineKeyboardButton(text=rows_label, callback_data=f"feat:{f}"))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="add:features_done")])
    rows.append([_back_btn(), _cancel_btn()])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_phone(data: dict):
    hint = f"\n\nСейчас: <code>{html.escape(str(data['phone']))}</code> — новый заменит." \
        if data.get("phone") else ""
    return (f"<b>{STEP_LABEL['phone']}</b>\n\n"
            f"📞 <b>Напиши телефон</b> в поле ввода 👇, например "
            f"<code>+37360123456</code>. Пропустишь — покажем тот, "
            f"что ты делился боту ранее.{hint}"), _ask_kb("+373…", skip=True)


# every channel the owner may attach to a listing, in the order they appear on the
# picker menu. same keys as keyboards._CONTACT_CHANNELS and db.listing_contacts.
# Viber is intentionally not on the list - Telegram inline URL buttons reject viber://
# with "Unsupported URL protocol", so a Viber row would take the whole card down.
_CONTACT_CHANNELS_UI = (
    ("telegram",  "\U0001F4AC Telegram"),
    ("instagram", "\U0001F4F7 Instagram"),
    ("whatsapp",  "\U0001F7E2 WhatsApp"),
    ("facebook",  "\U0001F4D8 Facebook"),
    ("site",      "\U0001F517 Сайт"),
)

_CONTACT_ASK_HINT = {
    "telegram":  "Telegram — напиши @nickname или ссылку t.me/nickname",
    "instagram": "Instagram — напиши @nickname или ссылку instagram.com/nickname",
    "facebook":  "Facebook — ссылка на профиль facebook.com/…",
    "whatsapp":  "WhatsApp — номер, например +37360123456",
    "viber":     "Viber — номер, например +37368765432",
    "site":      "Сайт — например https://пример.md",
}


def _normalize_contact(key: str, raw: str) -> str | None:
    """Turn what the owner actually typed into a value we can build a URL from.
    Tolerant: '@name', 'name', 'https://t.me/name', 't.me/name' all land on the same result.
    Unknown format returns None - the wizard keeps the old value and re-asks."""
    raw = (raw or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if key in ("whatsapp", "viber"):
        digits = re.sub(r"\D", "", raw)
        return digits if len(digits) >= 7 else None
    if key == "site":
        if raw.startswith("http"):
            return raw
        return ("https://" + raw) if "." in raw else None
    # @handle channels: strip known URL prefixes, then a leading @
    prefixes = {
        "telegram":  ("https://t.me/", "http://t.me/", "t.me/"),
        "instagram": ("https://instagram.com/", "http://instagram.com/", "instagram.com/"),
        "facebook":  ("https://facebook.com/", "http://facebook.com/", "facebook.com/"),
    }.get(key, ())
    for pre in prefixes:
        if low.startswith(pre):
            return raw[len(pre):].strip("/").split("?")[0] or None
    return raw.lstrip("@/") or None


def _p_contacts(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    cur = data.get("contacts") or {}
    # a normal owner does not know what 'tg @nickname' means; they DO know how to
    # tap a button that says 'Telegram'. two channels per row keeps the picker short.
    filled = [f"{label}: <b>{html.escape(str(cur[key]))}</b>"
              for key, label in _CONTACT_CHANNELS_UI if cur.get(key)]
    got = ("\nУже на карточке:\n" + "\n".join(filled) + "\n") if filled else ""
    text = (
        f"<b>{STEP_LABEL['contacts']}</b>\n\n"
        "Это публичные кнопки на карточке — ищущий тапает и пишет тебе сам,\n"
        "без звонка и без «показать телефон». Всё необязательно."
        f"{got}\n\n"
        "Тапни канал → впиши значение → вернёмся сюда.\n"
        "«✅ Готово» - закончить шаг, даже если ничего не добавил."
    )
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for key, label in _CONTACT_CHANNELS_UI:
        v = cur.get(key)
        txt = f"✓ {label}" if v else f"➕ {label}"
        pair.append(InlineKeyboardButton(text=txt, callback_data=f"add_contact:{key}"))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="add:contacts_done")])
    rows.append([_back_btn(), _cancel_btn()])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_contacts_ask(data: dict):
    key = data.get("pending_contact") or "telegram"
    label = dict(_CONTACT_CHANNELS_UI).get(key, key)
    hint = _CONTACT_ASK_HINT.get(key, "Напиши значение:")
    cur = (data.get("contacts") or {}).get(key)
    cur_line = f"\n\nСейчас: <code>{html.escape(str(cur))}</code> — новое заменит." if cur else ""
    placeholder = _CONTACT_ASK_HINT.get(key, "напиши значение…").split("—", 1)[-1].strip() or "напиши значение…"
    text = (f"<b>{label}</b>\n\n"
            f"✍️ <b>{hint}</b> — впиши в поле ввода 👇 и отправь. "
            f"«{BTN_BACK}» вернёт к списку каналов.{cur_line}")
    return text, _ask_kb(placeholder)


def _p_photos(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = len(data.get("photos") or [])
    got = f"\nДобавлено фото: <b>{n}</b> — можешь добавить ещё." if n else ""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="add:photos_done")],
        [InlineKeyboardButton(text="Пропустить ⏭", callback_data="add:photos_skip")],
        [_back_btn()],
        [_cancel_btn()],
    ])
    text = (f"<b>{STEP_LABEL['photos']}</b>\n\n"
            f"Отправь 1–10 фото (каждое — как фото, не файл), затем «Готово»:{got}")
    return text, kb


def _p_confirm(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="add:confirm")],
        [_back_btn()],
        [_cancel_btn()],
    ])
    return _summary(data), kb


PROMPTS = {
    "specs": _p_specs, "district": _p_district, "price": _p_price, "area": _p_area,
    "floor": _p_floor, "description": _p_description, "features": _p_features,
    "phone": _p_phone,
    "contacts": _p_contacts, "contacts_ask": _p_contacts_ask,
    "photos": _p_photos, "confirm": _p_confirm,
}


async def _goto(target: Message, state: FSMContext, step: str) -> None:
    """Move to `step` and turn the message the user is looking at into that step's prompt.
    Button-driven transitions edit in place, so the wizard costs one message per typed
    answer instead of one per screen. Reply-keyboard steps CANNOT be edited in place
    (a reply keyboard is chat-level, not message-level), so they always answer a fresh
    one_time_keyboard message."""
    await state.set_state(getattr(AddListing, step))
    text, kb = PROMPTS[step](await state.get_data())
    if step in _REPLY_STEPS:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)
        return
    try:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except Exception:
        pass
    await target.answer(text, parse_mode="HTML", reply_markup=kb)


async def _text_nav(message: Message, state: FSMContext) -> bool:
    """If the incoming text is a reply-keyboard navigation button, perform it and return
    True so the caller can stop. Handles Назад / Отмена / Пропустить consistently."""
    txt = (message.text or "").strip()
    cur = (await state.get_state() or "").split(":")[-1]
    if txt == BTN_CANCEL:
        await state.clear()
        await message.answer("Отменено.", reply_markup=keyboards.main_menu())
        return True
    if txt == BTN_BACK:
        target = _BACK_TARGET.get(cur)
        if not target and cur in STEP_ORDER:
            idx = STEP_ORDER.index(cur)
            if idx > 0:
                target = STEP_ORDER[idx - 1]
        if target:
            await _goto(message, state, target)
            return True
        return False
    if txt == BTN_SKIP:
        nxt = SKIP_NEXT.get(cur)
        if nxt:
            await _goto(message, state, nxt)
            return True
    return False


async def begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    # cold-start vertical is apartments only: pre-set ptype so the owner does not have to
    # pick a type we still do not really take. House/room submissions come back with S2.
    await state.update_data(photos=[], ptype="apartment")
    await _goto(message, state, "specs")


# --- step 1: specs (deal + type + rooms on one screen) ----------------------

@router.callback_query(AddListing.specs, F.data.startswith("deal:"))
@router.callback_query(AddListing.specs, F.data.startswith("ptype:"))
@router.callback_query(AddListing.specs, F.data.startswith("rooms:"))
async def s_specs(cb: CallbackQuery, state: FSMContext) -> None:
    key, value = cb.data.split(":", 1)
    await state.update_data(**{key: int(value) if key == "rooms" else value})
    await cb.answer()
    await _goto(cb.message, state, "specs")


@router.callback_query(AddListing.specs, F.data == "add:next")
async def s_specs_next(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    miss = _specs_missing(data)
    if miss:
        # say exactly what is still unset and redraw the panel pointing at it - "неясно, чего
        # не хватает" was the bug; a vague "выбери значение" left the user stuck on step 1.
        await cb.answer("Ещё не выбрано: " + ", ".join(miss), show_alert=True)
        await _goto(cb.message, state, "specs")
        return
    await cb.answer()
    await _goto(cb.message, state, "district")


# --- step 2: district -------------------------------------------------------

@router.callback_query(AddListing.district, F.data.startswith("district:"))
async def s_district(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(district=cb.data.split(":", 1)[1])
    await cb.answer()
    await _goto(cb.message, state, "price")


# --- step 3: price (typed) --------------------------------------------------

@router.message(AddListing.price)
async def s_price(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    txt = _num((message.text or "").replace(" ", "")).replace(",", ".")
    try:
        price = int(float(txt))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Не вижу числа. Напиши цену в поле ввода 👇 — например <code>450</code>.",
                             parse_mode="HTML")
        return
    await state.update_data(price=price)
    await _goto(message, state, "area")


# --- step 4: optional details (typed or skipped) ----------------------------

@router.message(AddListing.area)
async def s_area(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    try:
        area = float(_num(message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("Не вижу числа. Напиши площадь в м² — например <code>40</code>.",
                             parse_mode="HTML")
        return
    await state.update_data(area=area)
    await _goto(message, state, "floor")


@router.message(AddListing.floor)
async def s_floor(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    txt = (message.text or "").strip()
    val = None
    floors = None
    try:
        parts = txt.split("/")
        val = int(parts[0])
        if len(parts) > 1:
            floors = int(parts[1])
    except ValueError:
        pass
    await state.update_data(floor=val, floors=floors)
    await _goto(message, state, "description")


@router.message(AddListing.description)
async def s_desc(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    await state.update_data(description=(message.text or "")[:900])
    await _goto(message, state, "features")


@router.callback_query(AddListing.features, F.data.startswith("feat:"))
async def s_features_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":", 1)[1]
    data = await state.get_data()
    cur = list(data.get("features") or [])
    if key in cur:
        cur.remove(key)
    else:
        cur.append(key)
    await state.update_data(features=cur)
    await cb.answer()
    await _goto(cb.message, state, "features")


@router.callback_query(AddListing.features, F.data == "add:features_done")
async def s_features_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _goto(cb.message, state, "phone")


@router.message(AddListing.features)
async def s_features_text(message: Message) -> None:
    await message.answer("Тапай кнопки 👇 — или «✅ Готово» чтобы закончить шаг.")


@router.message(AddListing.phone)
async def s_phone(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    await state.update_data(phone=(message.text or "").strip()[:25])
    await _goto(message, state, "contacts")


@router.message(AddListing.contacts)
async def s_contacts(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Тапни канал кнопкой 👇 — я спрошу значение. Или «✅ Готово», если ничего добавлять не надо."
    )


@router.callback_query(AddListing.contacts, F.data.startswith("add_contact:"))
async def s_contacts_pick(cb: CallbackQuery, state: FSMContext) -> None:
    key = cb.data.split(":", 1)[1]
    await state.update_data(pending_contact=key)
    await cb.answer()
    await _goto(cb.message, state, "contacts_ask")


@router.callback_query(AddListing.contacts, F.data == "add:contacts_done")
async def s_contacts_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _goto(cb.message, state, "photos")


@router.callback_query(AddListing.contacts_ask, F.data == "add:contacts_back")
async def s_contacts_back(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(pending_contact=None)
    await cb.answer()
    await _goto(cb.message, state, "contacts")


@router.message(AddListing.contacts_ask)
async def s_contacts_value(message: Message, state: FSMContext) -> None:
    if await _text_nav(message, state):
        return
    data = await state.get_data()
    key = data.get("pending_contact") or "telegram"
    val = _normalize_contact(key, message.text or "")
    if not val:
        await message.answer(
            "Не понял формат. Например: <code>@nickname</code>, "
            "<code>t.me/nickname</code> или <code>+37360123456</code>.",
            parse_mode="HTML",
        )
        return
    contacts = dict(data.get("contacts") or {})
    contacts[key] = val
    await state.update_data(contacts=contacts, pending_contact=None)
    await _goto(message, state, "contacts")


@router.callback_query(AddListing.area, F.data == "skip:next")
@router.callback_query(AddListing.floor, F.data == "skip:next")
@router.callback_query(AddListing.description, F.data == "skip:next")
@router.callback_query(AddListing.features, F.data == "skip:next")
@router.callback_query(AddListing.phone, F.data == "skip:next")
async def s_skip(cb: CallbackQuery, state: FSMContext) -> None:
    cur = (await state.get_state() or "").split(":")[-1]
    await cb.answer()
    await _goto(cb.message, state, SKIP_NEXT[cur])


# --- step 5: photos ---------------------------------------------------------

@router.message(AddListing.photos, F.photo)
async def s_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    if len(photos) >= 10:
        await message.answer("Максимум 10 фото.")
        return
    photos.append(message.photo[-1].file_id)
    await state.update_data(photos=photos)
    await message.answer(f"Добавлено фото: {len(photos)}. Ещё или «Готово».")


@router.message(AddListing.photos, F.document)
async def s_photo_doc(message: Message) -> None:
    # самая частая случайная дыра: фото уходит как «файл» и мастер молчит
    await message.answer(
        "Это пришло как <b>файл</b>, а мне нужно фото — отправь его без галочки «обычный файл» "
        "(тогда оно встанет в карточку объекта). Или нажми «Пропустить» ⏭.",
        parse_mode="HTML",
    )


@router.message(AddListing.photos)
async def s_photos_other(message: Message) -> None:
    await message.answer("Отправь фото (1–10), затем «Готово». Или сразу «Пропустить» ⏭.")


@router.callback_query(AddListing.photos, F.data == "add:photos_skip")
async def s_photos_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _goto(cb.message, state, "confirm")


@router.callback_query(AddListing.photos, F.data == "add:photos_done")
async def s_photos_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _goto(cb.message, state, "confirm")


# --- back / cancel / text guards --------------------------------------------

@router.callback_query(F.data == "add:back")
async def s_back(cb: CallbackQuery, state: FSMContext) -> None:
    """One step back along the spine, every answered value preserved. Outside the wizard
    (a stale button from an old message) it is a no-op, not a state corruption."""
    cur = (await state.get_state() or "").split(":")[-1]
    if cur not in STEP_ORDER or cur == "specs":
        await cb.answer()
        return
    prev = STEP_ORDER[STEP_ORDER.index(cur) - 1]
    await cb.answer()
    await _goto(cb.message, state, prev)


@router.message(AddListing.specs)
@router.message(AddListing.district)
@router.message(AddListing.contacts)
@router.message(AddListing.confirm)
async def s_need_button(message: Message) -> None:
    # иначе любой текст посреди мастера проваливается в support-fallback и улетает админам
    await message.answer("Выбери вариант кнопкой 👇 — «⬅️ Назад» вернёт шаг назад, «✖️ Отмена» выйдет.")


def _summary(data: dict) -> str:
    deal = DEAL_LABELS.get(data.get("deal"), "?")
    ptype = PTYPE_LABELS.get(data.get("ptype"), "?")
    lines = [
        "<b>Проверь объявление:</b>",
        f"Тип: {deal} · {ptype}",
        f"Комнат: {data.get('rooms')}",
        f"Район: {html.escape(str(data.get('district')))}",
        f"Цена: <b>{data.get('price')} {data.get('currency', 'EUR')}</b>",
    ]
    if data.get("area"):
        lines.append(f"Площадь: {data.get('area')} м²")
    if data.get("floor"):
        floors = f"/{data['floors']}" if data.get("floors") else ""
        lines.append(f"Этаж: {data.get('floor')}{floors}")
    if data.get("description"):
        lines.append(f"Описание: {html.escape(str(data.get('description')))}")
    if data.get("features"):
        lines.append("Удобства: " + " · ".join(data["features"]))
    lines.append(f"Телефон: {html.escape(str(data.get('phone') or '—'))}")
    if data.get("contacts"):
        pretty = ", ".join(f"{k}: {v}" for k, v in data["contacts"].items())
        lines.append(f"Каналы: {html.escape(pretty)}")
    lines.append(f"Фото: {len(data.get('photos') or [])}")
    return "\n".join(lines)


@router.callback_query(AddListing.confirm, F.data == "add:confirm")
async def s_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    # features are appended to the description text as one bullet line. no new DB column:
    # the freeform description already renders identically in bot, channel and Mini App.
    description = data.get("description") or ""
    feats = data.get("features") or []
    if feats:
        description = (description.rstrip() + "\n\n" + " · ".join(feats)).strip()
    listing_id = await db.create_listing(
        user_id=user["id"],
        deal=data.get("deal"),
        ptype=data.get("ptype"),
        rooms=data.get("rooms"),
        price=data.get("price"),
        currency="EUR",
        district=data.get("district"),
        area=data.get("area"),
        floor=data.get("floor"),
        floors=data.get("floors"),
        phone=data.get("phone") or user["phone"],
        description=description,
        photos=data.get("photos", []),
        contacts=data.get("contacts") or {},
    )
    if data.get("phone"):
        await db.set_user_phone(user["id"], data["phone"])
    await db.log_event("listing_created", user_id=user["id"], listing_id=listing_id)
    await state.clear()
    await cb.answer("Отправлено на модерацию")
    text = (
        f"✅ Объявление <b>#{listing_id}</b> отправлено на модерацию.\n"
        "После проверки оно появится в поиске."
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=keyboards.main_menu())
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=keyboards.main_menu())
    await _notify_admins(bot, listing_id)


async def _notify_admins(bot: Bot, listing_id: int) -> None:
    row = await db.get_listing(listing_id)
    if not row:
        return
    caption = f"🆕 <b>Новое объявление на модерацию</b>\n\n" + _summary({
        "deal": row["deal"], "ptype": row["ptype"], "rooms": row["rooms"],
        "district": row["district"], "price": row["price"], "currency": row["currency"],
        "area": row["area"], "floor": row["floor"], "floors": row["floors"],
        "description": row["description"],
        "phone": row["phone"], "photos": db.listing_photos(row),
    })
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, caption, parse_mode="HTML",
                                   reply_markup=keyboards.moderation_kb(listing_id))
        except Exception:
            pass
