\
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import (
    add_employee, add_minus_item, close_minus_item, finish_daily_check,
    get_daily_reports, get_missing_today, get_month_summary,
    get_open_minus_items, get_or_create_daily_check,
    get_period_rows, get_user_employee, init_db, list_employees,
    set_alignment, set_has_minuses,
    set_user_employee
)
from excel_export import create_month_excel

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent").strip()
REMINDER_TIME = os.getenv("REMINDER_TIME", "20:00").strip()

ALLOWED_USER_IDS = {
    int(x.strip()) for x in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
ADMIN_USER_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}

PRODUCTS_PATH = Path(__file__).with_name("products.json")
router = Router()
bot_instance: Bot | None = None


class Flow(StatesGroup):
    choosing_employee = State()
    adding_employee = State()
    choosing_shift = State()
    choosing_existing_action = State()
    waiting_alignment = State()
    waiting_has_minuses = State()
    choosing_category = State()
    choosing_product = State()
    waiting_product = State()
    waiting_quantity = State()
    choosing_countermeasures = State()
    waiting_reason = State()
    waiting_responsible = State()
    waiting_comment = State()
    choosing_case_status = State()
    waiting_closure_comment = State()
    choosing_open_item = State()
    waiting_close_existing_comment = State()
    waiting_add_more = State()


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def today_str() -> str:
    return now_local().date().isoformat()


def allowed(user_id: int | None) -> bool:
    return bool(user_id) and (not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS)


def is_admin(user_id: int | None) -> bool:
    return bool(user_id) and (not ADMIN_USER_IDS or user_id in ADMIN_USER_IDS)



def progress_header(
    step: int,
    total: int,
    title: str,
    employee_name: str | None = None,
    shift: str | None = None,
) -> str:
    filled = "●" * step
    empty = "○" * max(total - step, 0)
    lines = [
        f"<b>{title}</b>",
        f"<code>{filled}{empty}</code>  Шаг {step} из {total}",
    ]
    if employee_name:
        lines.append(f"👤 {employee_name}")
    if shift:
        lines.append(f"🕒 {shift}")
    return "\n".join(lines)


def existing_check_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить позиции",
                    callback_data="existing:continue",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="existing:cancel",
                )
            ],
        ]
    )


def main_menu(employee_name: str | None = None) -> ReplyKeyboardMarkup:
    employee_btn = f"👤 Я: {employee_name}" if employee_name else "👤 Выбрать себя"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=employee_btn)],
            [KeyboardButton(text="🧾 Начать проверку дня")],
            [
                KeyboardButton(text="📋 Отчёт за сегодня"),
                KeyboardButton(text="📊 Отчёт за месяц"),
            ],
            [KeyboardButton(text="🔴 Открытые позиции")],
            [KeyboardButton(text="📥 Скачать Excel за месяц")],
            [KeyboardButton(text="❌ Отменить заполнение")],
        ],
        resize_keyboard=True,
    )


def yes_no(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}:yes"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}:no"),
    ]])



def alignment_continue_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🟢 Продолжить",
                    callback_data="alignment_continue",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="alignment_back",
                )
            ],
        ]
    )


def shifts_keyboard() -> InlineKeyboardMarkup:
    values = ["1 смена", "2 смена", "3 смена", "Общая проверка"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=x, callback_data=f"shift:{x}")]
        for x in values
    ])


def employees_keyboard(employees: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=e["name"], callback_data=f"employee:{e['id']}")]
        for e in employees
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить новое имя", callback_data="employee:add")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


COUNTERMEASURES = [
    "Проверка выравнивания",
    "Проверка движения товара",
    "Сверка движения",
    "Проверка закрытости накладных",
    "Сверка движения с накладной",
    "Проверка приёмки товара по видео",
    "Проверка склада остатков",
]

REASONS = [
    "Ошибка в выравнивании",
    "Пересорт (Официант)",
    "Пересорт (Кассир)",
    "Не пробитие официантами",
    "Не пробитие кассирами",
    "Не садится продажа",
    "Неправильная приёмка",
    "Ошибка при списании",
    "Прочее",
    "Причина не найдена",
]


def countermeasures_keyboard(selected: set[int]) -> InlineKeyboardMarkup:
    rows = []
    for index, title in enumerate(COUNTERMEASURES):
        mark = "☑️" if index in selected else "⬜"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {title}",
                callback_data=f"counter:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="✅ Готово", callback_data="counter:done"),
        InlineKeyboardButton(text="🧹 Очистить", callback_data="counter:clear"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reason_keyboard() -> InlineKeyboardMarkup:
    icons = ["⚖️", "🍽", "💳", "🍽", "💳", "🔎", "📥", "🗑", "✍️", "❓"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{icons[i]} {reason}", callback_data=f"reason:{i}")]
        for i, reason in enumerate(REASONS)
    ])



def case_status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔴 Оставить открытой",
                    callback_data="case_status:open",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Закрыть сейчас",
                    callback_data="case_status:closed",
                )
            ],
        ]
    )


def open_items_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        title = (
            f"#{item['id']} • {item['item_name']} • "
            f"{float(item['quantity']):g} шт."
        )
        rows.append([
            InlineKeyboardButton(
                text=title[:60],
                callback_data=f"close_item:{item['id']}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="close_item:cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def load_products() -> dict[str, list[str]]:
    try:
        data = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(category): [str(item) for item in items]
                for category, items in data.items()
                if isinstance(items, list)
            }
    except Exception:
        pass
    return {}


def categories_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=category, callback_data=f"category:{index}")]
        for index, category in enumerate(load_products().keys())
    ]
    rows.append([
        InlineKeyboardButton(
            text="🔎 Написать название вручную",
            callback_data="category:manual",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def products_keyboard(category_index: int) -> InlineKeyboardMarkup:
    product_map = load_products()
    categories = list(product_map.keys())
    if category_index < 0 or category_index >= len(categories):
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data="product_back")
        ]])

    products = product_map[categories[category_index]]
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"catalog_product:{category_index}:{index}")]
        for index, name in enumerate(products)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К категориям", callback_data="product_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_search_keyboard(query: str) -> InlineKeyboardMarkup | None:
    q = query.strip().lower()
    matches: list[str] = []
    for items in load_products().values():
        for item in items:
            if q in item.lower() and item not in matches:
                matches.append(item)

    matches = matches[:10]
    if not matches:
        return None

    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"search_product:{index}")]
        for index, name in enumerate(matches)
    ]
    rows.append([
        InlineKeyboardButton(
            text=f"Использовать: {query[:35]}",
            callback_data="search_product:typed",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def access_denied(event: Message | CallbackQuery) -> bool:
    uid = event.from_user.id if event.from_user else None
    if allowed(uid):
        return False
    if isinstance(event, CallbackQuery):
        await event.answer("Нет доступа", show_alert=True)
    else:
        await event.answer("⛔ У вас нет доступа к этому боту.")
    return True


async def ensure_employee(message: Message) -> dict | None:
    employee = await get_user_employee(message.from_user.id)
    if employee:
        return employee
    employees = await list_employees()
    await message.answer(
        "Сначала выберите себя. Это нужно, потому что выравнивание делают несколько сотрудников.",
        reply_markup=employees_keyboard(employees),
    )
    return None


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    if await access_denied(message):
        return
    await state.clear()
    employee = await get_user_employee(message.from_user.id)
    await message.answer(
        "Здравствуйте! 👋\n\n"
        "Бот фиксирует ежедневное выравнивание, минусы, причины и ответственных.",
        reply_markup=main_menu(employee["name"] if employee else None),
    )
    if not employee:
        employees = await list_employees()
        await state.set_state(Flow.choosing_employee)
        await message.answer("Кто сегодня заполняет?", reply_markup=employees_keyboard(employees))


@router.message(F.text.startswith("👤"))
async def choose_self(message: Message, state: FSMContext) -> None:
    if await access_denied(message):
        return
    employees = await list_employees()
    await state.set_state(Flow.choosing_employee)
    await message.answer("Выберите своё имя:", reply_markup=employees_keyboard(employees))


@router.callback_query(Flow.choosing_employee, F.data.startswith("employee:"))
async def employee_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()
    value = callback.data.split(":", 1)[1]

    if value == "add":
        await state.set_state(Flow.adding_employee)
        await callback.message.edit_text("Напишите имя и фамилию сотрудника:")
        return

    employee_id = int(value)
    employees = await list_employees()
    employee = next((e for e in employees if e["id"] == employee_id), None)
    if not employee:
        await callback.message.edit_text("Имя не найдено. Откройте меню заново.")
        return

    await set_user_employee(callback.from_user.id, employee_id)
    await state.clear()
    await callback.message.edit_text(f"✅ Вы выбрали: <b>{employee['name']}</b>")
    await callback.message.answer("Главное меню:", reply_markup=main_menu(employee["name"]))


@router.message(Flow.adding_employee)
async def add_employee_name(message: Message, state: FSMContext) -> None:
    if await access_denied(message):
        return
    name = " ".join((message.text or "").strip().split())
    if len(name) < 3:
        await message.answer("Напишите полное имя.")
        return
    employee_id = await add_employee(name)
    await set_user_employee(message.from_user.id, employee_id)
    await state.clear()
    await message.answer(
        f"✅ Имя <b>{name}</b> добавлено и выбрано.",
        reply_markup=main_menu(name),
    )


@router.message(F.text == "🧾 Начать проверку дня")
async def begin_check(message: Message, state: FSMContext) -> None:
    if await access_denied(message):
        return

    employee = await ensure_employee(message)
    if not employee:
        await state.set_state(Flow.choosing_employee)
        return

    await state.clear()
    await state.update_data(employee=employee, items=[])
    await state.set_state(Flow.choosing_shift)
    await message.answer(
        progress_header(
            1,
            6,
            "Новая проверка",
            employee_name=employee["name"],
        )
        + "\n\nВыберите смену. Каждая смена сохраняется отдельно:",
        reply_markup=shifts_keyboard(),
    )


@router.callback_query(Flow.choosing_shift, F.data.startswith("shift:"))
async def shift_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return

    await callback.answer()
    shift = callback.data.split(":", 1)[1]
    data = await state.get_data()
    employee = data["employee"]

    check_id, already_existed = await get_or_create_daily_check(
        telegram_user_id=callback.from_user.id,
        employee_id=employee["id"],
        employee_name=employee["name"],
        check_date=today_str(),
        shift=shift,
    )

    await state.update_data(
        check_id=check_id,
        shift=shift,
        items=[],
    )

    if already_existed:
        await state.set_state(Flow.choosing_existing_action)
        await callback.message.edit_text(
            progress_header(
                2,
                6,
                "Эта смена уже сохранена",
                employee_name=employee["name"],
                shift=shift,
            )
            + "\n\nРанее внесённые данные останутся на месте.\n"
              "Можно добавить новые минусовые позиции:",
            reply_markup=existing_check_keyboard(),
        )
        return

    await state.set_state(Flow.waiting_alignment)
    await callback.message.edit_text(
        progress_header(
            2,
            6,
            "Выравнивание",
            employee_name=employee["name"],
            shift=shift,
        )
        + "\n\nВыравнивание выполнено?",
        reply_markup=yes_no("alignment"),
    )


@router.callback_query(
    Flow.choosing_existing_action,
    F.data.in_({"existing:continue", "existing:cancel"}),
)
async def existing_action(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    data = await state.get_data()
    employee = data["employee"]

    if callback.data == "existing:cancel":
        await state.clear()
        await callback.message.edit_text("Действие отменено. Старые данные сохранены.")
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu(employee["name"]),
        )
        return

    await state.set_state(Flow.choosing_category)
    await callback.message.edit_text(
        progress_header(
            4,
            6,
            "Добавление позиции",
            employee_name=employee["name"],
            shift=data["shift"],
        )
        + "\n\nВыберите категорию товара:",
        reply_markup=categories_keyboard(),
    )


@router.callback_query(Flow.waiting_alignment, F.data.in_({"alignment:yes", "alignment:no"}))
async def alignment_answer(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()
    data = await state.get_data()

    if callback.data == "alignment:no":
        await set_alignment(data["check_id"], False, completed=True)
        employee = data["employee"]
        await state.clear()
        await callback.message.edit_text("❌ Выравнивание не выполнено. Запись сохранена.")
        await callback.message.answer("Главное меню:", reply_markup=main_menu(employee["name"]))
        return

    await set_alignment(data["check_id"], True)
    await callback.message.edit_text(
        progress_header(
            3,
            6,
            "Подтверждение выравнивания",
            employee_name=data["employee"]["name"],
            shift=data["shift"],
        )
        + "\n\n✅ Выравнивание отмечено как выполненное.\n"
          "Нажмите «Продолжить», чтобы перейти дальше.",
        reply_markup=alignment_continue_keyboard(),
    )


@router.callback_query(
    F.data.in_({"alignment_continue", "alignment_back"})
)
async def alignment_continue_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    if await access_denied(callback):
        return

    await callback.answer()
    data = await state.get_data()

    if callback.data == "alignment_back":
        await state.set_state(Flow.waiting_alignment)
        await callback.message.edit_text(
            progress_header(
                2,
                6,
                "Выравнивание",
                employee_name=data["employee"]["name"],
                shift=data["shift"],
            )
            + "\n\nВыравнивание выполнено?",
            reply_markup=yes_no("alignment"),
        )
        return

    await state.set_state(Flow.waiting_has_minuses)
    await callback.message.edit_text(
        progress_header(
            4,
            6,
            "Результат выравнивания",
            employee_name=data["employee"]["name"],
            shift=data["shift"],
        )
        + "\n\nЕсть позиции, которые вышли в минус?",
        reply_markup=yes_no("has_minuses"),
    )


@router.callback_query(Flow.waiting_has_minuses, F.data.in_({"has_minuses:yes", "has_minuses:no"}))
async def minuses_answer(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()
    data = await state.get_data()

    if callback.data == "has_minuses:no":
        await set_has_minuses(data["check_id"], False, completed=True)
        employee = data["employee"]
        await state.clear()
        await callback.message.edit_text("✅ Минусов нет. День успешно закрыт.")
        await callback.message.answer("Главное меню:", reply_markup=main_menu(employee["name"]))
        return

    await set_has_minuses(data["check_id"], True)
    await state.set_state(Flow.choosing_category)
    await callback.message.edit_text(
        progress_header(
            4,
            6,
            "Минусовая позиция",
            employee_name=data["employee"]["name"],
            shift=data["shift"],
        )
        + "\n\nВыберите категорию товара:",
        reply_markup=categories_keyboard(),
    )


@router.callback_query(Flow.choosing_category, F.data.startswith("category:"))
async def category_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()
    value = callback.data.split(":", 1)[1]

    if value == "manual":
        await state.set_state(Flow.waiting_product)
        await callback.message.edit_text(
            "Напишите название товара или первые буквы.\n"
            "Например: <code>напо</code>"
        )
        return

    category_index = int(value)
    categories = list(load_products().keys())
    if category_index >= len(categories):
        await callback.message.edit_text("Категория не найдена.")
        return

    await state.update_data(category_index=category_index)
    await state.set_state(Flow.choosing_product)
    await callback.message.edit_text(
        f"Категория: <b>{categories[category_index]}</b>\n\nВыберите товар:",
        reply_markup=products_keyboard(category_index),
    )


@router.callback_query(Flow.choosing_product, F.data == "product_back")
async def product_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(Flow.choosing_category)
    await callback.message.edit_text(
        "Выберите категорию товара:",
        reply_markup=categories_keyboard(),
    )


@router.callback_query(Flow.choosing_product, F.data.startswith("catalog_product:"))
async def catalog_product_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()

    _, category_value, product_value = callback.data.split(":")
    category_index = int(category_value)
    product_index = int(product_value)

    product_map = load_products()
    categories = list(product_map.keys())
    if category_index >= len(categories):
        await callback.message.edit_text("Категория не найдена.")
        return

    items = product_map[categories[category_index]]
    if product_index >= len(items):
        await callback.message.edit_text("Товар не найден.")
        return

    product = items[product_index]
    await state.update_data(current_product=product)
    await state.set_state(Flow.waiting_quantity)
    await callback.message.edit_text(
        f"Товар: <b>{product}</b>\n\nСколько единиц вышло в минус?"
    )


@router.message(Flow.waiting_product)
async def product_search(message: Message, state: FSMContext) -> None:
    if await access_denied(message):
        return

    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите минимум 2 буквы.")
        return

    matches: list[str] = []
    for items in load_products().values():
        for item in items:
            if query.lower() in item.lower() and item not in matches:
                matches.append(item)
    matches = matches[:10]

    await state.update_data(typed_product=query, search_matches=matches)
    keyboard = product_search_keyboard(query)

    if keyboard:
        await message.answer(
            "Выберите найденный товар или используйте введённое название:",
            reply_markup=keyboard,
        )
        return

    await state.update_data(current_product=query)
    await state.set_state(Flow.waiting_quantity)
    await message.answer(
        f"Товар: <b>{query}</b>\n\nСколько единиц вышло в минус?"
    )


@router.callback_query(Flow.waiting_product, F.data.startswith("search_product:"))
async def searched_product_selected(callback: CallbackQuery, state: FSMContext) -> None:
    if await access_denied(callback):
        return
    await callback.answer()

    value = callback.data.split(":", 1)[1]
    data = await state.get_data()

    if value == "typed":
        product = data["typed_product"]
    else:
        matches = data.get("search_matches", [])
        index = int(value)
        if index >= len(matches):
            await callback.message.edit_text("Товар не найден.")
            return
        product = matches[index]

    await state.update_data(current_product=product)
    await state.set_state(Flow.waiting_quantity)
    await callback.message.edit_text(
        f"Товар: <b>{product}</b>\n\nСколько единиц вышло в минус?"
    )


@router.message(Flow.waiting_quantity)
async def quantity(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        await message.answer("Введите количество числом. Например: <code>6</code>")
        return
    if value <= 0:
        await message.answer("Количество должно быть больше нуля.")
        return

    data = await state.get_data()
    await state.update_data(
        current_quantity=value,
        selected_countermeasures=[],
    )
    await state.set_state(Flow.choosing_countermeasures)
    await message.answer(
        progress_header(
            5,
            7,
            "Контрмеры",
            employee_name=data["employee"]["name"],
            shift=data["shift"],
        )
        + f"\n\n📦 <b>{data['current_product']}</b> — минус <b>{value:g} шт.</b>"
          "\n\nОтметьте все проверки, которые были выполнены. "
          "Можно выбрать несколько пунктов:",
        reply_markup=countermeasures_keyboard(set()),
    )


@router.callback_query(
    Flow.choosing_countermeasures,
    F.data.startswith("counter:"),
)
async def countermeasure_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("selected_countermeasures", []))

    if action == "clear":
        selected.clear()
    elif action == "done":
        if not selected:
            await callback.answer(
                "Выберите минимум одну выполненную проверку.",
                show_alert=True,
            )
            return
        await state.set_state(Flow.waiting_reason)
        chosen = [COUNTERMEASURES[i] for i in sorted(selected)]
        await callback.message.edit_text(
            progress_header(
                6,
                7,
                "Итоговая причина",
                employee_name=data["employee"]["name"],
                shift=data["shift"],
            )
            + "\n\nПроверено:\n• "
            + "\n• ".join(chosen)
            + "\n\nТеперь выберите <b>одну итоговую причину</b>:",
            reply_markup=reason_keyboard(),
        )
        return
    else:
        index = int(action)
        if index in selected:
            selected.remove(index)
        else:
            selected.add(index)

    await state.update_data(selected_countermeasures=sorted(selected))
    await callback.message.edit_reply_markup(
        reply_markup=countermeasures_keyboard(selected)
    )


@router.callback_query(Flow.waiting_reason, F.data.startswith("reason:"))
async def reason_selected(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    index = int(callback.data.split(":", 1)[1])
    if index < 0 or index >= len(REASONS):
        await callback.answer("Причина не найдена.", show_alert=True)
        return

    reason = REASONS[index]
    unresolved = reason == "Причина не найдена"
    await state.update_data(
        reason_found=not unresolved,
        current_reason=reason,
        current_status="unresolved" if unresolved else "resolved",
        responsible_name=None,
    )

    if unresolved:
        await state.set_state(Flow.waiting_comment)
        await callback.message.edit_text(
            "❓ <b>Причина пока не найдена</b>\n\n"
            "Напишите, что уже проверили или где ещё нужно искать.\n"
            "Чтобы пропустить, отправьте <code>-</code>"
        )
        return

    await state.set_state(Flow.waiting_responsible)
    await callback.message.edit_text(
        f"✅ Итоговая причина: <b>{reason}</b>\n\n"
        "Напишите имя ответственного сотрудника.\n"
        "Если конкретного сотрудника нет, отправьте <code>-</code>"
    )


@router.message(Flow.waiting_responsible)
async def responsible(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    await state.update_data(responsible_name=None if value == "-" else value)
    await state.set_state(Flow.waiting_comment)
    await message.answer(
        "Напишите короткий комментарий о том, что произошло.\n"
        "Чтобы пропустить, отправьте <code>-</code>"
    )


@router.message(Flow.waiting_comment)
async def choose_case_status(message: Message, state: FSMContext) -> None:
    comment = (message.text or "").strip()
    comment = None if comment == "-" else comment
    await state.update_data(current_comment=comment)
    await state.set_state(Flow.choosing_case_status)

    data = await state.get_data()
    await message.answer(
        "Какой статус у этой минусовой позиции?\n\n"
        f"📦 <b>{data['current_product']}</b> — "
        f"<b>{data['current_quantity']:g} шт.</b>\n"
        f"⚠️ Причина: <b>{data['current_reason']}</b>",
        reply_markup=case_status_keyboard(),
    )


async def persist_current_item(
    state: FSMContext,
    case_status: str,
    closure_comment: str | None = None,
) -> dict:
    data = await state.get_data()

    selected_indexes = data.get("selected_countermeasures", [])
    selected_countermeasures = [
        COUNTERMEASURES[i]
        for i in selected_indexes
        if 0 <= i < len(COUNTERMEASURES)
    ]

    item = {
        "item_name": data["current_product"],
        "quantity": data["current_quantity"],
        "reason_found": data["reason_found"],
        "reason": data["current_reason"],
        "countermeasures": selected_countermeasures,
        "responsible_name": data.get("responsible_name"),
        "comment": data.get("current_comment"),
        "status": data["current_status"],
        "case_status": case_status,
        "closure_comment": closure_comment,
    }

    item_id = await add_minus_item(
        daily_check_id=data["check_id"],
        item_name=item["item_name"],
        quantity=item["quantity"],
        reason_found=item["reason_found"],
        reason=item["reason"],
        countermeasures=item["countermeasures"],
        responsible_name=item["responsible_name"],
        comment=item["comment"],
        status=item["status"],
        case_status=item["case_status"],
        closure_comment=item["closure_comment"],
    )
    item["id"] = item_id

    items = data.get("items", [])
    items.append(item)
    await state.update_data(
        items=items,
        current_product=None,
        current_quantity=None,
        selected_countermeasures=[],
        current_comment=None,
    )
    return item


async def show_saved_item(
    message: Message,
    state: FSMContext,
    item: dict,
) -> None:
    await state.set_state(Flow.waiting_add_more)
    status_text = (
        "🟢 Закрыта"
        if item["case_status"] == "closed"
        else "🔴 Открыта"
    )
    await message.answer(
        "✅ <b>Случай сохранён</b>\n\n"
        f"📦 {item['item_name']} — <b>{item['quantity']:g} шт.</b>\n"
        f"🔎 Контрмер проверено: <b>{len(item['countermeasures'])}</b>\n"
        f"⚠️ Причина: <b>{item['reason']}</b>\n"
        f"📌 Статус позиции: <b>{status_text}</b>\n\n"
        "В аналитике это считается как:\n"
        "• <b>1 случай ошибки</b>\n"
        f"• <b>{item['quantity']:g} единиц товара</b>\n\n"
        "Добавить ещё позицию?",
        reply_markup=yes_no("more"),
    )


@router.callback_query(
    Flow.choosing_case_status,
    F.data.in_({"case_status:open", "case_status:closed"}),
)
async def case_status_selected(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    if callback.data == "case_status:closed":
        await state.set_state(Flow.waiting_closure_comment)
        await callback.message.edit_text(
            "🟢 <b>Закрытие позиции</b>\n\n"
            "Напишите, как позиция была закрыта.\n"
            "Например: деньги получены с кассира, накладная исправлена.\n\n"
            "Чтобы пропустить комментарий, отправьте <code>-</code>"
        )
        return

    item = await persist_current_item(state, case_status="open")
    await callback.message.edit_text(
        "🔴 Позиция сохранена как открытая."
    )
    await show_saved_item(callback.message, state, item)


@router.message(Flow.waiting_closure_comment)
async def closure_comment_handler(
    message: Message,
    state: FSMContext,
) -> None:
    comment = (message.text or "").strip()
    comment = None if comment == "-" else comment
    item = await persist_current_item(
        state,
        case_status="closed",
        closure_comment=comment,
    )
    await show_saved_item(message, state, item)


@router.message(F.text == "🔴 Открытые позиции")
async def open_positions_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if await access_denied(message):
        return

    items = await get_open_minus_items(limit=30)
    if not items:
        await message.answer("🟢 Открытых позиций сейчас нет.")
        return

    await state.set_state(Flow.choosing_open_item)
    await state.update_data(open_items=items)
    await message.answer(
        f"🔴 <b>Открытые позиции: {len(items)}</b>\n\n"
        "Нажмите на позицию, которую нужно закрыть:",
        reply_markup=open_items_keyboard(items),
    )


@router.callback_query(
    Flow.choosing_open_item,
    F.data.startswith("close_item:"),
)
async def choose_open_item_to_close(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    value = callback.data.split(":", 1)[1]

    if value == "cancel":
        employee = await get_user_employee(callback.from_user.id)
        await state.clear()
        await callback.message.edit_text("Закрытие отменено.")
        await callback.message.answer(
            "Главное меню:",
            reply_markup=main_menu(employee["name"] if employee else None),
        )
        return

    item_id = int(value)
    data = await state.get_data()
    items = data.get("open_items", [])
    item = next((x for x in items if x["id"] == item_id), None)
    if not item:
        await callback.message.edit_text("Позиция уже не найдена.")
        await state.clear()
        return

    await state.update_data(closing_item_id=item_id)
    await state.set_state(Flow.waiting_close_existing_comment)
    await callback.message.edit_text(
        "🟢 <b>Закрытие открытой позиции</b>\n\n"
        f"📦 {item['item_name']} — <b>{float(item['quantity']):g} шт.</b>\n"
        f"⚠️ Причина: {item['reason']}\n\n"
        "Напишите, как позиция была закрыта.\n"
        "Чтобы пропустить, отправьте <code>-</code>"
    )


@router.message(Flow.waiting_close_existing_comment)
async def close_existing_item_handler(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    comment = (message.text or "").strip()
    comment = None if comment == "-" else comment

    closed = await close_minus_item(
        data["closing_item_id"],
        closure_comment=comment,
    )
    employee = await get_user_employee(message.from_user.id)
    await state.clear()

    if closed:
        await message.answer(
            "🟢 Позиция успешно перенесена в закрытые.",
            reply_markup=main_menu(employee["name"] if employee else None),
        )
    else:
        await message.answer(
            "Эта позиция уже была закрыта.",
            reply_markup=main_menu(employee["name"] if employee else None),
        )


@router.callback_query(Flow.waiting_add_more, F.data.in_({"more:yes", "more:no"}))
async def add_more(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()

    if callback.data == "more:yes":
        await state.set_state(Flow.choosing_category)
        await callback.message.edit_text(
            "Выберите категорию следующего товара:",
            reply_markup=categories_keyboard(),
        )
        return

    await finish_daily_check(data["check_id"])
    employee = data["employee"]
    items = data.get("items", [])
    unresolved = sum(1 for x in items if x["status"] == "unresolved")
    await state.clear()
    await callback.message.edit_text(
        f"✅ День закрыт.\n\n"
        f"Сотрудник: <b>{employee['name']}</b>\n"
        f"Смена: <b>{data['shift']}</b>\n"
        f"Минусовых позиций: <b>{len(items)}</b>\n"
        f"Причина не найдена: <b>{unresolved}</b>"
    )
    await callback.message.answer("Главное меню:", reply_markup=main_menu(employee["name"]))


@router.message(F.text == "📋 Отчёт за сегодня")
async def today_report(message: Message) -> None:
    employee = await ensure_employee(message)
    if not employee:
        return

    reports = await get_daily_reports(employee["id"], today_str())
    if not reports:
        await message.answer("Сегодня ещё нет сохранённых проверок.")
        return

    lines = [
        f"📋 <b>Проверки за {today_str()}</b>",
        f"👤 {employee['name']}",
        "",
    ]

    for report in reports:
        check = report["check"]
        items = report["items"]

        lines.extend(
            [
                f"<b>🕒 {check['shift']}</b>",
                f"Выравнивание: {'✅' if check['alignment_done'] else '❌'}",
                f"Статус: {'✅ Закрыто' if check['completed'] else '⏳ Не завершено'}",
            ]
        )

        if not items:
            lines.append("Минусовых позиций нет.")
        else:
            for index, item in enumerate(items, start=1):
                lines.append(
                    f"{index}. <b>{item['item_name']}</b> — "
                    f"{item['quantity']:g} шт.\n"
                    f"   Причина: {item['reason']}\n"
                    f"   Позиция: "
                    f"{'🟢 закрыта' if item.get('case_status') == 'closed' else '🔴 открыта'}"
                )

        lines.append("")

    await message.answer("\n".join(lines))


def month_range() -> tuple[datetime, str, str]:
    now = now_local()
    last = calendar.monthrange(now.year, now.month)[1]
    return now, f"{now.year:04d}-{now.month:02d}-01", f"{now.year:04d}-{now.month:02d}-{last:02d}"


@router.message(F.text == "📊 Отчёт за месяц")
async def month_report(message: Message) -> None:
    employee = await ensure_employee(message)
    if not employee:
        return
    now, start, end = month_range()
    s = await get_month_summary(start, end, employee["id"])
    t, m = s["totals"], s["minus_totals"]
    lines = [
        f"📊 <b>Отчёт {employee['name']} за {now.strftime('%m.%Y')}</b>",
        "",
        f"Дней с записями: <b>{t.get('total_days') or 0}</b>",
        f"Выравнивание выполнено: <b>{t.get('aligned_days') or 0}</b>",
        f"Минусовых позиций: <b>{m.get('positions_count') or 0}</b>",
        f"Общий минус: <b>{float(m.get('quantity_sum') or 0):g}</b>",
        f"Причина не найдена: <b>{m.get('unresolved_count') or 0}</b>",
        f"🔴 Открытых позиций: <b>{m.get('open_count') or 0}</b>",
        f"🟢 Закрытых позиций: <b>{m.get('closed_count') or 0}</b>",
    ]
    if s["top_items"]:
        lines += ["", "🔥 <b>Топ товаров</b>"]
        for x in s["top_items"]:
            lines.append(f"• {x['item_name']} — {float(x['quantity_sum']):g} шт.")
    if s["reasons"]:
        lines += ["", "📌 <b>Причины</b>"]
        for x in s["reasons"]:
            lines.append(f"• {x['reason']} — {x['cases_count']} случаев")
    if s["shifts"]:
        lines += ["", "🕒 <b>По сменам</b>"]
        for x in s["shifts"]:
            lines.append(f"• {x['shift'] or 'Не указана'} — {x['cases_count']} позиций")
    await message.answer("\n".join(lines))


@router.message(F.text == "📥 Скачать Excel за месяц")
async def excel_report(message: Message) -> None:
    if await access_denied(message):
        return
    now, start, end = month_range()
    rows = await get_period_rows(start, end)
    path = create_month_excel(rows, now.year, now.month)
    await message.answer_document(
        FSInputFile(path),
        caption=f"📊 Общий отчёт за {now.strftime('%m.%Y')}"
    )
    try:
        path.unlink()
    except OSError:
        pass


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить заполнение")
async def cancel(message: Message, state: FSMContext) -> None:
    employee = await get_user_employee(message.from_user.id)
    await state.clear()
    await message.answer(
        "Заполнение отменено.",
        reply_markup=main_menu(employee["name"] if employee else None),
    )


async def send_daily_reminder() -> None:
    if bot_instance is None:
        return
    missing = await get_missing_today(today_str())
    if not missing:
        return

    names = ", ".join(x["name"] for x in missing)
    text = (
        "⏰ <b>Напоминание о выравнивании</b>\n\n"
        f"Сегодня ещё не закрыли проверку:\n{names}"
    )

    recipients = ADMIN_USER_IDS or ALLOWED_USER_IDS
    for user_id in recipients:
        try:
            await bot_instance.send_message(user_id, text)
        except Exception:
            logging.exception("Не удалось отправить напоминание пользователю %s", user_id)


@router.message()
async def fallback(message: Message) -> None:
    employee = await get_user_employee(message.from_user.id)
    await message.answer(
        "Выберите действие кнопкой.",
        reply_markup=main_menu(employee["name"] if employee else None),
    )


async def main() -> None:
    global bot_instance
    if not BOT_TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN")

    logging.basicConfig(level=logging.INFO)
    await init_db()

    bot_instance = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    hour, minute = (int(x) for x in REMINDER_TIME.split(":"))
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(send_daily_reminder, "cron", hour=hour, minute=minute)
    scheduler.start()

    await bot_instance.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить заполнение"),
    ])
    await dp.start_polling(bot_instance)


if __name__ == "__main__":
    asyncio.run(main())
