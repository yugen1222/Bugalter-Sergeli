\
from __future__ import annotations

import asyncio
import calendar
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from database import (
    add_minus_item,
    finish_daily_check,
    get_daily_report,
    get_month_summary,
    get_or_create_daily_check,
    init_db,
    reset_daily_check,
    set_alignment,
    set_has_minuses,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent").strip()
ALLOWED_USER_IDS = {
    int(value.strip())
    for value in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if value.strip().isdigit()
}

router = Router()


class MinusFlow(StatesGroup):
    waiting_alignment = State()
    waiting_has_minuses = State()
    waiting_item_name = State()
    waiting_quantity = State()
    waiting_reason_found = State()
    waiting_reason = State()
    waiting_comment = State()
    waiting_add_more = State()


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def today_str() -> str:
    return now_local().date().isoformat()


def user_display_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return "Неизвестный пользователь"
    return user.full_name or user.username or str(user.id)


def is_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧾 Начать проверку дня")],
            [
                KeyboardButton(text="📋 Отчёт за сегодня"),
                KeyboardButton(text="📊 Отчёт за месяц"),
            ],
            [KeyboardButton(text="❌ Отменить заполнение")],
        ],
        resize_keyboard=True,
    )


def yes_no_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}:yes"),
                InlineKeyboardButton(text="❌ Нет", callback_data=f"{prefix}:no"),
            ]
        ]
    )


def reasons_keyboard() -> InlineKeyboardMarkup:
    reasons = [
        ("🍽 Официант не пробил", "waiter_not_punched"),
        ("🏷 Неправильно оценили товар", "wrong_valuation"),
        ("🗑 Ошибка списания", "writeoff_error"),
        ("🔄 Пересорт", "resort"),
        ("📥 Ошибка приёмки", "acceptance_error"),
        ("✍️ Другая причина", "other"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title, callback_data=f"reason:{code}")]
            for title, code in reasons
        ]
    )


REASON_LABELS = {
    "waiter_not_punched": "Официант не пробил",
    "wrong_valuation": "Неправильно оценили товар",
    "writeoff_error": "Ошибка списания",
    "resort": "Пересорт",
    "acceptance_error": "Ошибка приёмки",
    "other": "Другая причина",
}


def skip_comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить комментарий", callback_data="comment:skip")]
        ]
    )


def format_quantity(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


async def deny_if_needed(event: Message | CallbackQuery) -> bool:
    user_id = event.from_user.id if event.from_user else None
    if is_allowed(user_id):
        return False

    text = "⛔ У вас нет доступа к этому боту."
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
    else:
        await event.answer(text)
    return True


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return

    await state.clear()
    await message.answer(
        "Здравствуйте! 👋\n\n"
        "Этот бот фиксирует ежедневное выравнивание, минусовые позиции "
        "и найденные причины.\n\n"
        "Все записи сохраняются и потом могут использоваться на сайте "
        "для анализа за день, неделю и месяц.",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить заполнение")
async def cancel_handler(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    await state.clear()
    await message.answer("Заполнение отменено.", reply_markup=main_menu())


@router.message(F.text == "🧾 Начать проверку дня")
async def begin_day(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return

    user_id = message.from_user.id
    daily_check_id = await get_or_create_daily_check(
        user_id=user_id,
        user_name=user_display_name(message),
        check_date=today_str(),
    )
    await reset_daily_check(daily_check_id)
    await state.clear()
    await state.update_data(daily_check_id=daily_check_id, saved_items=[])
    await state.set_state(MinusFlow.waiting_alignment)

    await message.answer(
        f"📅 Проверка за <b>{today_str()}</b>\n\n"
        "Выравнивание сегодня выполнено?",
        reply_markup=yes_no_keyboard("alignment"),
    )


@router.callback_query(
    MinusFlow.waiting_alignment,
    F.data.in_({"alignment:yes", "alignment:no"}),
)
async def alignment_answer(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()

    data = await state.get_data()
    daily_check_id = data["daily_check_id"]
    done = callback.data == "alignment:yes"

    if not done:
        await set_alignment(daily_check_id, alignment_done=False, completed=True)
        await state.clear()
        await callback.message.edit_text(
            "❌ Выравнивание не выполнено.\n\n"
            "Запись за сегодняшний день сохранена."
        )
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
        return

    await set_alignment(daily_check_id, alignment_done=True)
    await state.set_state(MinusFlow.waiting_has_minuses)
    await callback.message.edit_text(
        "✅ Выравнивание выполнено.\n\n"
        "После выравнивания появились позиции с минусом?",
        reply_markup=yes_no_keyboard("has_minuses"),
    )


@router.callback_query(
    MinusFlow.waiting_has_minuses,
    F.data.in_({"has_minuses:yes", "has_minuses:no"}),
)
async def has_minuses_answer(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()

    data = await state.get_data()
    daily_check_id = data["daily_check_id"]
    has_minuses = callback.data == "has_minuses:yes"

    if not has_minuses:
        await set_has_minuses(daily_check_id, has_minuses=False, completed=True)
        await state.clear()
        await callback.message.edit_text(
            "✅ Выравнивание выполнено.\n"
            "✅ Минусовых позиций нет.\n\n"
            "День успешно закрыт."
        )
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
        return

    await set_has_minuses(daily_check_id, has_minuses=True)
    await state.set_state(MinusFlow.waiting_item_name)
    await callback.message.edit_text(
        "⚠️ Есть минусовые позиции.\n\n"
        "Напишите точное название первой позиции:"
    )


@router.message(MinusFlow.waiting_item_name)
async def item_name_handler(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    item_name = (message.text or "").strip()
    if len(item_name) < 2:
        await message.answer("Напишите полное название позиции.")
        return

    await state.update_data(current_item_name=item_name)
    await state.set_state(MinusFlow.waiting_quantity)
    await message.answer(
        f"Позиция: <b>{item_name}</b>\n\n"
        "Сколько единиц вышло в минус?\n"
        "Например: <code>2</code> или <code>1.5</code>"
    )


@router.message(MinusFlow.waiting_quantity)
async def quantity_handler(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return

    raw = (message.text or "").strip().replace(",", ".")
    try:
        quantity = float(raw)
    except ValueError:
        await message.answer("Введите количество числом. Например: 2")
        return

    if quantity <= 0 or quantity > 100000:
        await message.answer("Количество должно быть больше нуля.")
        return

    await state.update_data(current_quantity=quantity)
    await state.set_state(MinusFlow.waiting_reason_found)
    await message.answer(
        "Удалось найти, из-за чего появилась эта минусовая позиция?",
        reply_markup=yes_no_keyboard("reason_found"),
    )


@router.callback_query(
    MinusFlow.waiting_reason_found,
    F.data.in_({"reason_found:yes", "reason_found:no"}),
)
async def reason_found_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()

    found = callback.data == "reason_found:yes"
    await state.update_data(current_reason_found=found)

    if not found:
        await state.update_data(
            current_reason="Не нашли причину",
            current_status="unresolved",
        )
        await state.set_state(MinusFlow.waiting_comment)
        await callback.message.edit_text(
            "❌ Причина не найдена.\n\n"
            "Можно написать, что уже проверили или где ещё нужно искать.",
            reply_markup=skip_comment_keyboard(),
        )
        return

    await state.set_state(MinusFlow.waiting_reason)
    await callback.message.edit_text(
        "Выберите найденную причину:",
        reply_markup=reasons_keyboard(),
    )


@router.callback_query(MinusFlow.waiting_reason, F.data.startswith("reason:"))
async def reason_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()

    code = callback.data.split(":", 1)[1]
    reason = REASON_LABELS.get(code)
    if reason is None:
        await callback.answer("Неизвестная причина.", show_alert=True)
        return

    await state.update_data(
        current_reason=reason,
        current_status="resolved",
    )
    await state.set_state(MinusFlow.waiting_comment)
    await callback.message.edit_text(
        f"Причина: <b>{reason}</b>\n\n"
        "Напишите короткий комментарий: что именно произошло, "
        "кто допустил ошибку или какой документ исправили.",
        reply_markup=skip_comment_keyboard(),
    )


async def save_current_item(
    state: FSMContext,
    comment: str | None,
) -> dict:
    data = await state.get_data()

    item = {
        "item_name": data["current_item_name"],
        "quantity": float(data["current_quantity"]),
        "reason_found": bool(data["current_reason_found"]),
        "reason": data["current_reason"],
        "comment": comment,
        "status": data["current_status"],
    }

    await add_minus_item(
        daily_check_id=data["daily_check_id"],
        item_name=item["item_name"],
        quantity=item["quantity"],
        reason_found=item["reason_found"],
        reason=item["reason"],
        comment=item["comment"],
        status=item["status"],
    )

    saved_items = data.get("saved_items", [])
    saved_items.append(item)
    await state.update_data(
        saved_items=saved_items,
        current_item_name=None,
        current_quantity=None,
        current_reason_found=None,
        current_reason=None,
        current_status=None,
    )
    return item


@router.callback_query(MinusFlow.waiting_comment, F.data == "comment:skip")
async def skip_comment_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()
    item = await save_current_item(state, comment=None)
    await state.set_state(MinusFlow.waiting_add_more)
    await callback.message.edit_text(
        f"✅ Сохранено:\n"
        f"<b>{item['item_name']}</b> — {format_quantity(item['quantity'])} шт.\n"
        f"Причина: {item['reason']}\n\n"
        "Добавить ещё одну минусовую позицию?",
        reply_markup=yes_no_keyboard("add_more"),
    )


@router.message(MinusFlow.waiting_comment)
async def comment_handler(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return

    comment = (message.text or "").strip()
    if len(comment) > 1000:
        await message.answer("Комментарий слишком длинный. Максимум 1000 символов.")
        return

    item = await save_current_item(state, comment=comment)
    await state.set_state(MinusFlow.waiting_add_more)
    await message.answer(
        f"✅ Сохранено:\n"
        f"<b>{item['item_name']}</b> — {format_quantity(item['quantity'])} шт.\n"
        f"Причина: {item['reason']}\n"
        f"Комментарий: {comment}\n\n"
        "Добавить ещё одну минусовую позицию?",
        reply_markup=yes_no_keyboard("add_more"),
    )


@router.callback_query(
    MinusFlow.waiting_add_more,
    F.data.in_({"add_more:yes", "add_more:no"}),
)
async def add_more_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(callback):
        return
    await callback.answer()

    if callback.data == "add_more:yes":
        await state.set_state(MinusFlow.waiting_item_name)
        await callback.message.edit_text(
            "Напишите название следующей минусовой позиции:"
        )
        return

    data = await state.get_data()
    daily_check_id = data["daily_check_id"]
    items = data.get("saved_items", [])
    await finish_daily_check(daily_check_id)

    lines = [
        f"📅 <b>{today_str()}</b>",
        "✅ Выравнивание выполнено",
        f"⚠️ Внесено позиций: <b>{len(items)}</b>",
        "",
    ]

    unresolved = 0
    for index, item in enumerate(items, start=1):
        if item["status"] == "unresolved":
            unresolved += 1
        lines.append(
            f"{index}. <b>{item['item_name']}</b> — "
            f"{format_quantity(item['quantity'])} шт.\n"
            f"   Причина: {item['reason']}"
        )

    lines.extend(
        [
            "",
            f"❌ Причина не найдена: <b>{unresolved}</b>",
            "",
            "День успешно сохранён.",
        ]
    )

    await state.clear()
    await callback.message.edit_text("\n".join(lines))
    await callback.message.answer("Главное меню:", reply_markup=main_menu())


@router.message(F.text == "📋 Отчёт за сегодня")
async def today_report_handler(message: Message) -> None:
    if await deny_if_needed(message):
        return

    report = await get_daily_report(message.from_user.id, today_str())
    if report is None:
        await message.answer(
            "За сегодня ещё нет записи.\n"
            "Нажмите «🧾 Начать проверку дня».",
            reply_markup=main_menu(),
        )
        return

    check = report["check"]
    items = report["items"]

    alignment_text = "✅ Выполнено" if check["alignment_done"] else "❌ Не выполнено"
    completed_text = "✅ Закрыт" if check["completed"] else "⏳ Не завершён"

    lines = [
        f"📋 <b>Отчёт за {check['check_date']}</b>",
        f"Выравнивание: {alignment_text}",
        f"Статус дня: {completed_text}",
        "",
    ]

    if not items:
        lines.append("Минусовых позиций не внесено.")
    else:
        unresolved = 0
        for index, item in enumerate(items, start=1):
            if item["status"] == "unresolved":
                unresolved += 1
            lines.append(
                f"{index}. <b>{item['item_name']}</b> — "
                f"{format_quantity(item['quantity'])} шт.\n"
                f"   Причина: {item['reason']}"
                + (f"\n   Комментарий: {item['comment']}" if item["comment"] else "")
            )
        lines.extend(["", f"Не найдена причина: <b>{unresolved}</b>"])

    await message.answer("\n".join(lines), reply_markup=main_menu())


@router.message(F.text == "📊 Отчёт за месяц")
async def month_report_handler(message: Message) -> None:
    if await deny_if_needed(message):
        return

    now = now_local()
    last_day = calendar.monthrange(now.year, now.month)[1]
    month_start = f"{now.year:04d}-{now.month:02d}-01"
    month_end = f"{now.year:04d}-{now.month:02d}-{last_day:02d}"

    summary = await get_month_summary(
        user_id=message.from_user.id,
        month_start=month_start,
        month_end=month_end,
    )

    totals = summary["totals"]
    minus_totals = summary["minus_totals"]

    lines = [
        f"📊 <b>Отчёт за {now.strftime('%m.%Y')}</b>",
        "",
        f"Дней с записями: <b>{totals.get('total_days') or 0}</b>",
        f"Выравнивание выполнено: <b>{totals.get('aligned_days') or 0}</b>",
        f"Закрыто дней: <b>{totals.get('completed_days') or 0}</b>",
        "",
        f"Минусовых позиций: <b>{minus_totals.get('positions_count') or 0}</b>",
        f"Общее количество: <b>{format_quantity(float(minus_totals.get('quantity_sum') or 0))}</b>",
        f"Не найдена причина: <b>{minus_totals.get('unresolved_count') or 0}</b>",
    ]

    if summary["top_items"]:
        lines.extend(["", "🔥 <b>Топ минусовых товаров</b>"])
        for item in summary["top_items"]:
            lines.append(
                f"• {item['item_name']} — "
                f"{format_quantity(float(item['quantity_sum']))} шт. "
                f"({item['cases_count']} случаев)"
            )

    if summary["reasons"]:
        lines.extend(["", "📌 <b>Причины минусов</b>"])
        for reason in summary["reasons"]:
            lines.append(
                f"• {reason['reason']} — {reason['cases_count']} случаев, "
                f"{format_quantity(float(reason['quantity_sum']))} шт."
            )

    await message.answer("\n".join(lines), reply_markup=main_menu())


@router.message()
async def fallback_handler(message: Message) -> None:
    if await deny_if_needed(message):
        return
    await message.answer(
        "Выберите действие кнопкой из меню.",
        reply_markup=main_menu(),
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не указан. Создайте файл .env и вставьте токен бота."
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    await init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть главное меню"),
            BotCommand(command="cancel", description="Отменить заполнение"),
        ]
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
