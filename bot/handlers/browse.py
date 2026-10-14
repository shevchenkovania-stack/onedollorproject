from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
import keyboards
import render
from constants import DEAL_LABELS, DISTRICTS, PTYPE_LABELS

router = Router()
PAGE = 10

# Round price caps per deal: a rent seeker thinks in hundreds of euro, a buyer in tens of
# thousands - one shared list would be nonsense for both sides.
PRICE_BUCKETS = {"rent": [300, 500, 800, 1500], "sale": [30000, 50000, 80000, 150000]}

FILTER_KEYS = ("deal", "ptype", "district", "rooms", "price_max")


class Search(StatesGroup):
    on = State()


def _chip(label: str, active: bool, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=(label + " ✓") if active else label, callback_data=cb)


def _plural(n: int) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return "объявление"
    if m10 in (2, 3, 4) and m100 not in (12, 13, 14):
        return "объявления"
    return "объявлений"


def _summary(data: dict) -> str:
    """One line of the active filters, so the panel is readable without decoding the chips."""
    bits = []
    bits.append(DEAL_LABELS.get(data.get("deal")) if data.get("deal") else "аренда и продажа")
    if data.get("rooms"):
        bits.append(f"{data['rooms']}+ комн." if data["rooms"] >= 3 else f"{data['rooms']} комн.")
    if data.get("ptype"):
        bits.append(PTYPE_LABELS.get(data["ptype"], data["ptype"]).lower())
    if data.get("district"):
        bits.append(data["district"])
    if data.get("price_max"):
        bits.append(f"до {data['price_max']:,}".replace(",", " ") + " €")
    return " · ".join(bits)


def _filters(data: dict) -> dict:
    return {k: data.get(k) for k in FILTER_KEYS}


def _any_filter(data: dict) -> bool:
    return any(_filters(data).values())


async def _render(target: Message, text: str, kb: InlineKeyboardMarkup) -> None:
    """Edit the message the user is looking at instead of stacking new ones.

    A search session started from the menu lives inside ONE message: panel <-> pickers <-
    results all edit each other in place. Only opening a listing (photos) adds messages."""
    try:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
    except Exception:
        pass
    await target.answer(text, parse_mode="HTML", reply_markup=kb)


async def _panel_msg(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = await db.count_search_listings(**_filters(data))
    price_line = (f"💶 Цена: до {data['price_max']:,} €".replace(",", " ")
                  if data.get("price_max") else "💶 Цена: без лимита")
    text = (
        "🔍 <b>Поиск объектов</b>\n\n"
        f"Фильтры: <i>{_summary(data)}</i>\n"
        f"Найдено: <b>{n}</b>"
    )
    rows = [
        [
            _chip("🏠 Аренда", data.get("deal") == "rent", "sf:deal:rent"),
            _chip("💰 Продажа", data.get("deal") == "sale", "sf:deal:sale"),
            _chip("👀 Всё", not data.get("deal"), "sf:deal:all"),
        ],
        [
            _chip("🛏 Все", not data.get("rooms"), "sf:rooms:all"),
            _chip("1", data.get("rooms") == 1, "sf:rooms:1"),
            _chip("2", data.get("rooms") == 2, "sf:rooms:2"),
            _chip("3+", data.get("rooms") == 3, "sf:rooms:3"),
        ],
        [
            _chip("Квартира", data.get("ptype") == "apartment", "sf:ptype:apartment"),
            _chip("Дом", data.get("ptype") == "house", "sf:ptype:house"),
            _chip("Комната", data.get("ptype") == "room", "sf:ptype:room"),
            _chip("Все типы", not data.get("ptype"), "sf:ptype:all"),
        ],
        [InlineKeyboardButton(
            text=f"📍 Район: {data.get('district') or 'любой'}",
            callback_data="sf:pick:district")],
        [InlineKeyboardButton(text=price_line, callback_data="sf:pick:price")],
        [InlineKeyboardButton(text=f"🔍 Показать {n} {_plural(n)}", callback_data="sf:go")],
        [InlineKeyboardButton(text="✖️ Сбросить фильтры", callback_data="sf:reset")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _district_msg(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    text = "📍 <b>Район</b> — выбери один:"
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for d in DISTRICTS:
        pair.append(_chip(d, data.get("district") == d, f"sf:dist:{d}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([_chip("🌍 Любой район", not data.get("district"), "sf:dist:all")])
    rows.append([InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="sf:panel")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _price_msg(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Pickers are deal-aware: with no deal chosen both groups show and picking one sets the
    deal too - a price cap without a deal is meaningless (400 kills every sale, 50000 kills
    every rental), so the ambiguity is resolved by the tap itself."""
    deal = data.get("deal")
    text = "💶 <b>Цена до</b> — выбери порог:"
    rows: list[list[InlineKeyboardButton]] = []
    for dkey, header in (("rent", "🏠 Аренда — до"), ("sale", "💰 Продажа — до")):
        if deal and deal != dkey:
            continue
        if not deal:
            rows.append([InlineKeyboardButton(text=header, callback_data="noop")])
        rows.append([
            _chip(f"{v:,} €".replace(",", " "), data.get("price_max") == v, f"sf:price:{dkey}:{v}")
            for v in PRICE_BUCKETS[dkey]
        ])
    rows.append([_chip("💶 Без лимита", not data.get("price_max"), "sf:price:x:none")])
    rows.append([InlineKeyboardButton(text="⬅️ К фильтрам", callback_data="sf:panel")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _results_msg(data: dict) -> tuple[str, InlineKeyboardMarkup]:
    n = await db.count_search_listings(**_filters(data))
    if n == 0:
        text = (
            "🔍 <b>Ничего не нашлось</b>\n"
            f"Фильтры: <i>{_summary(data)}</i>\n\n"
            "Измени фильтры или добавь своё объявление — размещение бесплатное."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Изменить фильтры", callback_data="sf:panel")],
            [InlineKeyboardButton(text="➕ Разместить объект", callback_data="menu:add")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="menu:home")],
        ])
        return text, kb

    page = int(data.get("page", 0))
    pages = (n - 1) // PAGE + 1
    page = max(0, min(page, pages - 1))
    rows = await db.search_listings(**_filters(data), limit=PAGE, offset=page * PAGE)
    text = (
        f"🔍 Найдено <b>{n}</b> — <i>{_summary(data)}</i>"
        + (f"\nСтраница {page + 1}/{pages}" if pages > 1 else "")
    )
    kb_rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=render.listing_button_text(r), callback_data=f"view:{r['id']}")]
        for r in rows
    ]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data="page:prev"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page + 1 < pages:
            nav.append(InlineKeyboardButton(text="➡️", callback_data="page:next"))
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="🔍 Фильтры", callback_data="sf:panel")])
    kb_rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="menu:home")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


async def _show_panel(target: Message, state: FSMContext) -> None:
    await state.set_state(Search.on)
    text, kb = await _panel_msg(await state.get_data())
    await _render(target, text, kb)


async def _show_results(target: Message, state: FSMContext) -> None:
    await state.set_state(Search.on)
    text, kb = await _results_msg(await state.get_data())
    await _render(target, text, kb)


async def _update(target: Message, state: FSMContext) -> None:
    """After any filter change we are back on the panel (the edit target is the picker we came
    from - one message, no matter how many times the user flips filters)."""
    text, kb = await _panel_msg(await state.get_data())
    await _render(target, text, kb)


# --- entry -----------------------------------------------------------------

@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    """The command Telegram itself suggests: /search opens the filter panel in a fresh message."""
    await state.clear()
    await state.set_state(Search.on)
    text, kb = await _panel_msg({})
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "menu:search")
async def start_search(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.answer()
    await _show_panel(cb.message, state)


@router.callback_query(Search.on, F.data == "sf:panel")
async def back_to_panel(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _show_panel(cb.message, state)


# --- chips: value updates --------------------------------------------------

@router.callback_query(Search.on, F.data.startswith("sf:deal:"))
async def f_deal(cb: CallbackQuery, state: FSMContext) -> None:
    v = cb.data.split(":", 2)[2]
    await state.update_data(deal=None if v == "all" else v, page=0)
    await cb.answer()
    await _update(cb.message, state)


@router.callback_query(Search.on, F.data.startswith("sf:ptype:"))
async def f_ptype(cb: CallbackQuery, state: FSMContext) -> None:
    v = cb.data.split(":", 2)[2]
    await state.update_data(ptype=None if v == "all" else v, page=0)
    await cb.answer()
    await _update(cb.message, state)


@router.callback_query(Search.on, F.data.startswith("sf:rooms:"))
async def f_rooms(cb: CallbackQuery, state: FSMContext) -> None:
    v = cb.data.split(":", 2)[2]
    await state.update_data(rooms=None if v == "all" else int(v), page=0)
    await cb.answer()
    await _update(cb.message, state)


# --- pickers ---------------------------------------------------------------

@router.callback_query(Search.on, F.data == "sf:pick:district")
async def pick_district(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    text, kb = await _district_msg(await state.get_data())
    await _render(cb.message, text, kb)


@router.callback_query(Search.on, F.data == "sf:pick:price")
async def pick_price(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    text, kb = await _price_msg(await state.get_data())
    await _render(cb.message, text, kb)


@router.callback_query(Search.on, F.data.startswith("sf:dist:"))
async def f_dist(cb: CallbackQuery, state: FSMContext) -> None:
    v = cb.data.split(":", 2)[2]
    await state.update_data(district=None if v == "all" else v, page=0)
    await cb.answer()
    await _update(cb.message, state)


@router.callback_query(Search.on, F.data.startswith("sf:price:"))
async def f_price(cb: CallbackQuery, state: FSMContext) -> None:
    # sf:price:<deal|x>:<cap|none> - the deal travels with the cap, so a pick made while
    # "Всё" was active does not silently filter against the wrong side of the market
    _, _, deal_part, value = cb.data.split(":", 3)
    upd: dict = {"price_max": None if value == "none" else int(value), "page": 0}
    if deal_part != "x":
        upd["deal"] = deal_part
    await state.update_data(**upd)
    await cb.answer()
    await _update(cb.message, state)


@router.callback_query(Search.on, F.data == "sf:reset")
async def f_reset(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(deal=None, ptype=None, district=None, rooms=None, price_max=None, page=0)
    await cb.answer("Фильтры сброшены")
    await _update(cb.message, state)


# --- results ---------------------------------------------------------------

@router.callback_query(Search.on, F.data == "sf:go")
async def f_go(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _show_results(cb.message, state)


@router.callback_query(Search.on, F.data.in_({"page:next", "page:prev"}))
async def paginate(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = int(data.get("page", 0))
    page = page + 1 if cb.data == "page:next" else max(0, page - 1)
    await state.update_data(page=page)
    await cb.answer()
    await _show_results(cb.message, state)


@router.callback_query(F.data == "back:search")
async def back_to_results(cb: CallbackQuery, state: FSMContext) -> None:
    """'К списку' from a listing card. No state filter: a card can outlive the session
    (deep link, profile, moderation), and an old card should still land somewhere sane."""
    data = await state.get_data()
    await state.set_state(Search.on)
    await cb.answer()
    if _any_filter(data):
        text, kb = await _results_msg(data)
    else:
        text, kb = await _panel_msg(data)
    await _render(cb.message, text, kb)


# --- unchanged: open a listing / take the contact --------------------------

@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data.startswith("view:"))
async def view_listing(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row:
        await cb.message.answer("Объявление не найдено.")
        return
    user = await db.get_or_create_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name)
    is_owner = row["user_id"] == user["id"]
    if not is_owner and row["status"] != "active":
        await cb.message.answer("Объявление недоступно.")
        return
    if not is_owner:
        await db.inc_views(listing_id)
        await db.log_event("listing_view", user_id=user["id"], listing_id=listing_id)
    await render.send_listing(cb.bot, cb.message.chat.id, row, owner=is_owner)


@router.callback_query(F.data.startswith("contact:"))
async def contact(cb: CallbackQuery) -> None:
    listing_id = int(cb.data.split(":", 1)[1])
    row = await db.get_listing(listing_id)
    await cb.answer()
    if not row:
        return
    phone = row["phone"]
    if phone:
        await cb.message.answer(
            f"\U0001F4DE Контакт: <code>{render.esc(phone)}</code>\n"
            "Нажми на номер, чтобы скопировать.",
            parse_mode="HTML",
        )
    else:
        username = None
        cur = await db.get_user_by_id(row["user_id"])
        if cur:
            username = cur["username"]
        if username:
            await cb.message.answer(f"Контакт: https://t.me/{username}")
        else:
            await cb.message.answer("Контакт не указан.")
    await db.log_event("contact_click", user_id=None, listing_id=listing_id)
