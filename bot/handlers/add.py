"""Submission wizard - 3 steps, per the mockups (Stage 5).

  Шаг 1 · Сделка и комнаты   (deal + rooms, type is preset to apartment for the cold start)
  Шаг 2 · Район и цена        (district chip, then a typed price)
  Шаг 3 · Фото и телефон      (up to 10 photos; the caption carries phone + a couple of lines)
  → Проверь объявление        (one review screen, then straight to moderation)

The old flow had five numbered screens plus optional detail pages (area / floor / features /
extra channels). The redesign folds every free-form detail into the description the owner
types on step 3, so the DB columns stay (schema only grows) but the form is short enough to
finish on a phone in under a minute. State lives in sqlite (fsm_sqlite), so a restart in the
middle of a submission resumes instead of starting over.
"""
import html
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message)

import config
import db
import keyboards
from constants import DEAL_LABELS, DISTRICTS, PTYPE_LABELS

router = Router()

# the linear spine "Назад" walks; the phone sub-page is folded under media.
SPINE = ("specs", "district_price", "media", "confirm")


class AddListing(StatesGroup):
    specs = State()
    district_price = State()
    media = State()
    phone = State()
    confirm = State()


# --- small helpers -----------------------------------------------------------

def _chip(label: str, active: bool, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=(label + " ✓") if active else label, callback_data=cb)


def _num(txt: str) -> str:
    """Keep only digits/separators: '450 eur', '450€', '450 €' -> '450'."""
    return re.sub(r"[^\d.,]", "", txt or "")


# a phone is 9+ digits, optionally starting with '+': +373 69 123 456, 060123456.
# separators are spaces/parens/dots/dashes only - NOT a newline, so a caption like
# "+373 69 123 456\n54 м²" stops the number at the line break and keeps "54 м²" as text.
_PHONE_RE = re.compile(r"\+?\d[\d().\- \t]{7,}\d")


def _extract_phone(text: str) -> tuple[str | None, str]:
    """Pull the first phone-looking token out of a caption and return (phone, rest_of_text).
    The rest is what becomes the description, so the owner can paste everything in one line."""
    m = _PHONE_RE.search(text or "")
    if not m:
        return None, (text or "").strip()
    raw = m.group(0)
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 9:
        return None, (text or "").strip()
    phone = ("+" + digits) if raw.strip().startswith("+") else digits
    rest = (text[:m.start()] + text[m.end():]).strip(" ,.\n")
    return phone[:25], rest


def _cancel_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="\u2716\uFE0F Отмена", callback_data="cancel")


def _back_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="\u2B05\uFE0F Назад", callback_data="add:back")


# --- step prompts ------------------------------------------------------------

def _p_specs(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    deal = DEAL_LABELS.get(data.get("deal"), "—")
    r = data.get("rooms")
    rooms = "—" if not r else ("3+" if r >= 3 else str(r))
    preview = f"{deal} · {rooms} комн. · квартира"
    miss = []
    if not data.get("deal"):
        miss.append("сделку")
    if not data.get("rooms"):
        miss.append("число комнат")
    hint = ("👉 Выбери: " + " и ".join(miss)) if miss else "Всё выбрано → «Далее ➡️»"
    text = (f"<b>Шаг 1 из 3 · Сделка и комнаты</b>\n\n"
            f"<i>{html.escape(preview)}</i>\n{hint}")
    rooms_row = [
        _chip("3+" if r == 3 else str(r), data.get("rooms") == r, f"rooms:{r}")
        for r in (1, 2, 3)
    ]
    rows = [
        [_chip("\U0001F3E0 Аренда", data.get("deal") == "rent", "deal:rent"),
         _chip("\U0001F4B0 Продажа", data.get("deal") == "sale", "deal:sale")],
        rooms_row,
        [InlineKeyboardButton(text="Далее \u27A1\uFE0F", callback_data="add:next")],
        [_cancel_btn()],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_district_price(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    d = data.get("district")
    price = data.get("price")
    lines = ["<b>Шаг 2 из 3 · Район и цена</b>", "", "Выбери район, потом напиши цену в евро."]
    status = []
    status.append(f"Район: <b>{html.escape(d)}</b> \u2713" if d else "Район: —")
    status.append(f"Цена: <b>{price} €</b> \u2713" if price else "Цена: напиши числом 👇")
    lines.append("\n".join(status))
    text = "\n".join(lines)
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for name in DISTRICTS:
        pair.append(_chip(name, d == name, f"district:{name}"))
        if len(pair) == 3:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_back_btn(), _cancel_btn()])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="\u2705 Готово", callback_data="add:done")],
        [_back_btn(), _cancel_btn()],
    ])


def _p_media(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = len(data.get("photos") or [])
    phone = data.get("phone")
    bits = [f"Фото: <b>{n}</b>" if n else "Фото: пока нет"]
    bits.append(f"Телефон: <code>{html.escape(phone)}</code>" if phone else "Телефон: не указан")
    text = ("<b>Шаг 3 из 3 · Фото и телефон</b>\n\n"
            "Пришли до 10 фото, в подписи — телефон и пара строк: этаж, площадь, "
            "когда свободна.\n"
            "Можно без фото — просто напиши описание с телефоном.\n\n"
            + " · ".join(bits) + "\n\n«\u2705 Готово», когда закончишь.")
    return text, _media_kb()


def _p_phone(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = ("<b>Шаг 3 из 3 · Телефон</b>\n\n"
            "\U0001F4DE Напиши телефон, например <code>+37360123456</code> — "
            "он будет на карточке.")
    rows = [[_back_btn(), _cancel_btn()]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _p_confirm(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = _summary(data)
    rows = [
        [InlineKeyboardButton(text="\u2705 Отправить на модерацию", callback_data="add:confirm")],
        [InlineKeyboardButton(text="\u270F\uFE0F Изменить", callback_data="add:edit"), _cancel_btn()],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


PROMPTS = {
    "specs": _p_specs, "district_price": _p_district_price, "media": _p_media,
    "phone": _p_phone, "confirm": _p_confirm,
}


def _summary(data: dict) -> str:
    deal = DEAL_LABELS.get(data.get("deal"), "?")
    ptype = PTYPE_LABELS.get(data.get("ptype") or "apartment", "Квартира")
    r = data.get("rooms")
    rooms = "3+" if (r or 0) >= 3 else str(r or "—")
    n = len(data.get("photos") or [])
    headline = f"{deal} · {rooms} комн. · {data.get('district') or '—'} · {data.get('price')} € · {n} фото"
    lines = ["<b>Проверь объявление</b>", headline]
    desc = (data.get("description") or "").strip()
    if desc:
        lines.append("")
        lines.append(html.escape(desc)[:900])
    lines.append("")
    lines.append(f"\U0001F4DE {html.escape(str(data.get('phone') or '—'))}")
    return "\n".join(lines)


# --- navigation --------------------------------------------------------------

async def _goto(target: Message, state: FSMContext, step: str) -> None:
    """Move to `step` and turn the current message into that step's prompt. Editing works
    for button taps (the bot's own message); when we are replying to a message the user just
    typed, the edit fails and we answer a fresh prompt instead."""
    await state.set_state(getattr(AddListing, step))
    text, kb = PROMPTS[step](await state.get_data())
    try:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except Exception:
        pass
    await target.answer(text, parse_mode="HTML", reply_markup=kb)


def _prev_step(cur: str) -> str | None:
    if cur == "phone":
        return "media"
    if cur in SPINE:
        i = SPINE.index(cur)
        return SPINE[i - 1] if i > 0 else None
    return None


async def begin(message: Message, state: FSMContext) -> None:
    await state.clear()
    # cold-start vertical is apartments only: ptype is preset so the owner never picks a
    # type we still do not really take.
    await state.update_data(photos=[], ptype="apartment")
    await _goto(message, state, "specs")


# --- step 1: deal + rooms ----------------------------------------------------

@router.callback_query(AddListing.specs, F.data.startswith("deal:"))
@router.callback_query(AddListing.specs, F.data.startswith("rooms:"))
async def s_specs(cb: CallbackQuery, state: FSMContext) -> None:
    key, value = cb.data.split(":", 1)
    await state.update_data(**{key: int(value) if key == "rooms" else value})
    await cb.answer()
    await _goto(cb.message, state, "specs")


@router.callback_query(AddListing.specs, F.data == "add:next")
async def s_specs_next(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    miss = []
    if not data.get("deal"):
        miss.append("сделку")
    if not data.get("rooms"):
        miss.append("число комнат")
    if miss:
        await cb.answer("Ещё не выбрано: " + " и ".join(miss), show_alert=True)
        return
    await cb.answer()
    await _goto(cb.message, state, "district_price")


@router.message(AddListing.specs)
async def s_specs_text(message: Message) -> None:
    await message.answer("Выбери вариант кнопкой 👇.")


# --- step 2: district (tap) + price (typed) ----------------------------------

@router.callback_query(AddListing.district_price, F.data.startswith("district:"))
async def s_district(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(district=cb.data.split(":", 1)[1])
    await cb.answer("Район выбран — теперь напиши цену.")
    await _goto(cb.message, state, "district_price")


@router.message(AddListing.district_price)
async def s_price(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("district"):
        await message.answer("Сначала выбери район кнопкой 👇.")
        return
    txt = _num((message.text or "").replace(" ", "")).replace(",", ".")
    try:
        price = int(float(txt))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Напиши цену числом — например <code>450</code>.", parse_mode="HTML")
        return
    await state.update_data(price=price)
    await _goto(message, state, "media")


# --- step 3: photos + phone + description (one message) ----------------------

@router.message(AddListing.media, F.photo)
async def s_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    photos = list(data.get("photos") or [])
    if len(photos) < 10:
        photos.append(message.photo[-1].file_id)
        await state.update_data(photos=photos)
    cap = message.caption or ""
    if cap and not data.get("description") and not data.get("phone"):
        phone, rest = _extract_phone(cap)
        await state.update_data(phone=phone, description=rest[:900])
    await message.answer(
        f"Фото: {len(photos)}. Ещё фото или «\u2705 Готово».", reply_markup=_media_kb())


@router.message(AddListing.media, F.document)
async def s_photo_doc(message: Message) -> None:
    await message.answer(
        "Это пришло как <b>файл</b>, а нужно фото — отправь без галочки «обычный файл», "
        "или просто напиши описание с телефоном.", parse_mode="HTML")


@router.message(AddListing.media)
async def s_media_text(message: Message, state: FSMContext) -> None:
    phone, rest = _extract_phone(message.text or "")
    upd = {}
    if phone:
        upd["phone"] = phone
    if rest:
        upd["description"] = rest[:900]
    if upd:
        await state.update_data(**upd)
    await message.answer("Записал. Ещё фото/текст или «\u2705 Готово».", reply_markup=_media_kb())


@router.callback_query(AddListing.media, F.data == "add:done")
async def s_media_done(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await cb.answer()
    if not data.get("phone"):
        await _goto(cb.message, state, "phone")
    else:
        await _goto(cb.message, state, "confirm")


# --- phone sub-page ----------------------------------------------------------

@router.message(AddListing.phone)
async def s_phone(message: Message, state: FSMContext) -> None:
    phone, _ = _extract_phone(message.text or "")
    if not phone:
        await message.answer("Не вижу телефон. Напиши, например <code>+37360123456</code>.",
                             parse_mode="HTML")
        return
    await state.update_data(phone=phone)
    await _goto(message, state, "confirm")


# --- confirm -----------------------------------------------------------------

@router.callback_query(AddListing.confirm, F.data == "add:edit")
async def s_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _goto(cb.message, state, "specs")


@router.message(AddListing.confirm)
async def s_confirm_text(message: Message) -> None:
    await message.answer("Нажми «\u2705 Отправить на модерацию» 👇.")


@router.callback_query(AddListing.confirm, F.data == "add:confirm")
async def s_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    listing_id = await db.create_listing(
        user_id=user["id"],
        deal=data.get("deal"),
        ptype=data.get("ptype") or "apartment",
        rooms=data.get("rooms"),
        price=data.get("price"),
        currency="EUR",
        district=data.get("district"),
        area=data.get("area"),
        floor=data.get("floor"),
        floors=data.get("floors"),
        phone=data.get("phone") or user["phone"],
        description=data.get("description") or "",
        photos=data.get("photos", []),
        contacts=data.get("contacts") or {},
    )
    if data.get("phone"):
        await db.set_user_phone(user["id"], data["phone"])
    await db.log_event("listing_created", user_id=user["id"], listing_id=listing_id)
    await state.clear()
    await cb.answer("Отправлено на модерацию")
    text = (f"\u2705 Объявление <b>#{listing_id}</b> отправлено на модерацию.\n"
            "После проверки оно появится в поиске.")
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=keyboards.main_menu())
    except Exception:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=keyboards.main_menu())
    await _notify_admins(bot, listing_id)


# --- global back / cancel ----------------------------------------------------

@router.callback_query(F.data == "add:back")
async def s_back(cb: CallbackQuery, state: FSMContext) -> None:
    cur = (await state.get_state() or "").split(":")[-1]
    prev = _prev_step(cur)
    await cb.answer()
    if prev:
        await _goto(cb.message, state, prev)


@router.callback_query(F.data == "cancel")
async def s_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    try:
        await cb.message.edit_text("Отменено.", reply_markup=keyboards.main_menu())
    except Exception:
        await cb.message.answer("Отменено.", reply_markup=keyboards.main_menu())


async def _notify_admins(bot: Bot, listing_id: int) -> None:
    row = await db.get_listing(listing_id)
    if not row:
        return
    caption = "\U0001F195 <b>Новое объявление на модерацию</b>\n\n" + _summary({
        "deal": row["deal"], "ptype": row["ptype"], "rooms": row["rooms"],
        "district": row["district"], "price": row["price"],
        "description": row["description"], "phone": row["phone"],
        "photos": db.listing_photos(row),
    })
    for admin in config.ADMIN_IDS:
        try:
            await bot.send_message(admin, caption, parse_mode="HTML",
                                   reply_markup=keyboards.moderation_kb(listing_id))
        except Exception:
            pass
