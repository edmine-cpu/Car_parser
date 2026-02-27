import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from bot.config import settings
from bot.db import ManualCar, async_session

router = Router()
logger = logging.getLogger(__name__)


def _is_manager(user_id: int) -> bool:
    return user_id in settings.manager_ids


class AddCarFSM(StatesGroup):
    title = State()
    year = State()
    mileage = State()
    fuel = State()
    engine = State()
    transmission = State()
    price = State()
    auction_end = State()
    photo = State()
    url = State()
    confirm = State()


_cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Скасувати", callback_data="cancel_add_car")],
])


# ── Cancel ────────────────────────────────────────────────


@router.callback_query(lambda c: c.data == "cancel_add_car", StateFilter(AddCarFSM))
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("Додавання авто скасовано.")


@router.message(Command("cancel"), StateFilter(AddCarFSM))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Додавання авто скасовано.")


# ── Entry point ───────────────────────────────────────────


@router.callback_query(lambda c: c.data == "mgr_add_car")
async def cb_start_add_car(callback: CallbackQuery, state: FSMContext) -> None:
    if not _is_manager(callback.from_user.id):
        return
    await callback.answer()
    await state.set_state(AddCarFSM.title)
    await callback.message.answer(
        "Додавання авто. Введiть назву (наприклад: BMW 320d F30):",
        reply_markup=_cancel_kb,
    )


@router.message(lambda m: _is_manager(m.from_user.id), F.text == "Додати авто")
async def msg_start_add_car(message: Message, state: FSMContext) -> None:
    await state.set_state(AddCarFSM.title)
    await message.answer(
        "Додавання авто. Введiть назву (наприклад: BMW 320d F30):",
        reply_markup=_cancel_kb,
    )


# ── Step 1: Title ─────────────────────────────────────────


@router.message(AddCarFSM.title, F.text)
async def process_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text.strip())
    await state.set_state(AddCarFSM.year)
    await message.answer("Рiк реєстрацiї (наприклад: 03/2019):", reply_markup=_cancel_kb)


# ── Step 2: Year ──────────────────────────────────────────


@router.message(AddCarFSM.year, F.text)
async def process_year(message: Message, state: FSMContext) -> None:
    await state.update_data(year=message.text.strip())
    await state.set_state(AddCarFSM.mileage)
    await message.answer("Пробiг в км (наприклад: 150000):", reply_markup=_cancel_kb)


# ── Step 3: Mileage ───────────────────────────────────────


@router.message(AddCarFSM.mileage, F.text)
async def process_mileage(message: Message, state: FSMContext) -> None:
    await state.update_data(mileage=message.text.strip())
    await state.set_state(AddCarFSM.fuel)
    await message.answer("Вид палива (наприклад: Diesel):", reply_markup=_cancel_kb)


# ── Step 4: Fuel ──────────────────────────────────────────


@router.message(AddCarFSM.fuel, F.text)
async def process_fuel(message: Message, state: FSMContext) -> None:
    await state.update_data(fuel=message.text.strip())
    await state.set_state(AddCarFSM.engine)
    await message.answer("Об'єм двигуна (наприклад: 1998 ccm):", reply_markup=_cancel_kb)


# ── Step 5: Engine ────────────────────────────────────────


@router.message(AddCarFSM.engine, F.text)
async def process_engine(message: Message, state: FSMContext) -> None:
    await state.update_data(engine=message.text.strip())
    await state.set_state(AddCarFSM.transmission)
    await message.answer("Коробка передач (наприклад: Automatik):", reply_markup=_cancel_kb)


# ── Step 6: Transmission ─────────────────────────────────


@router.message(AddCarFSM.transmission, F.text)
async def process_transmission(message: Message, state: FSMContext) -> None:
    await state.update_data(transmission=message.text.strip())
    await state.set_state(AddCarFSM.price)
    await message.answer("Цiна (наприклад: 15000 EUR):", reply_markup=_cancel_kb)


# ── Step 7: Price ─────────────────────────────────────────


@router.message(AddCarFSM.price, F.text)
async def process_price(message: Message, state: FSMContext) -> None:
    await state.update_data(price=message.text.strip())
    await state.set_state(AddCarFSM.auction_end)
    await message.answer("Дата закiнчення аукцiону (формат: YYYY-MM-DD HH:MM):\nНаприклад: 2026-03-15 14:00", reply_markup=_cancel_kb)


# ── Step 8: Auction end ───────────────────────────────────


@router.message(AddCarFSM.auction_end, F.text)
async def process_auction_end(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
        dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        await message.answer("Невiрний формат. Введiть дату у форматi: YYYY-MM-DD HH:MM\nНаприклад: 2026-03-15 14:00", reply_markup=_cancel_kb)
        return
    if dt <= datetime.now(timezone.utc):
        await message.answer("Дата повинна бути у майбутньому.", reply_markup=_cancel_kb)
        return
    await state.update_data(auction_end=dt.isoformat())
    await state.set_state(AddCarFSM.photo)
    await message.answer("Надiшлiть фото авто або напишiть 'skip':", reply_markup=_cancel_kb)


# ── Step 9: Photo ─────────────────────────────────────────


@router.message(AddCarFSM.photo, F.photo)
async def process_photo_file(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    await state.update_data(image_file_id=photo.file_id)
    await state.set_state(AddCarFSM.url)
    await message.answer("Посилання на авто (або 'skip'):", reply_markup=_cancel_kb)


@router.message(AddCarFSM.photo, F.text)
async def process_photo_skip(message: Message, state: FSMContext) -> None:
    text = message.text.strip().lower()
    if text != "skip":
        await message.answer("Надiшлiть фото або напишiть 'skip':", reply_markup=_cancel_kb)
        return
    await state.update_data(image_file_id=None)
    await state.set_state(AddCarFSM.url)
    await message.answer("Посилання на авто (або 'skip'):", reply_markup=_cancel_kb)


# ── Step 10: URL ──────────────────────────────────────────


@router.message(AddCarFSM.url, F.text)
async def process_url(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    url = None if text.lower() == "skip" else text
    await state.update_data(url=url)

    data = await state.get_data()
    auction_dt = datetime.fromisoformat(data["auction_end"])

    summary = (
        f"<b>Пiдсумок:</b>\n\n"
        f"Назва: {data['title']}\n"
        f"Рiк: {data['year']}\n"
        f"Пробiг: {data['mileage']} km\n"
        f"Паливо: {data['fuel']}\n"
        f"Двигун: {data['engine']}\n"
        f"КПП: {data['transmission']}\n"
        f"Цiна: {data['price']}\n"
        f"Аукцiон до: {auction_dt:%Y-%m-%d %H:%M} UTC\n"
        f"Фото: {'так' if data.get('image_file_id') else 'нi'}\n"
        f"Посилання: {url or 'нi'}"
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Зберегти", callback_data="confirm_add_car"),
            InlineKeyboardButton(text="Скасувати", callback_data="cancel_add_car"),
        ],
    ])
    await state.set_state(AddCarFSM.confirm)
    await message.answer(summary, parse_mode="HTML", reply_markup=confirm_kb)


# ── Step 11: Confirm ──────────────────────────────────────


@router.callback_query(lambda c: c.data == "confirm_add_car", StateFilter(AddCarFSM.confirm))
async def cb_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    auction_end_dt = datetime.fromisoformat(data["auction_end"])

    async with async_session() as session:
        car = ManualCar(
            title=data["title"],
            year=data["year"],
            mileage=data["mileage"],
            fuel=data.get("fuel", ""),
            engine=data.get("engine", ""),
            transmission=data.get("transmission", ""),
            price=data.get("price", ""),
            auction_end=auction_end_dt,
            image_url=data.get("image_file_id"),
            url=data.get("url"),
            added_by=callback.from_user.id,
        )
        session.add(car)
        await session.commit()
        await session.refresh(car)

    await state.clear()
    await callback.message.edit_text(
        f"{callback.message.text}\n\nАвто '{data['title']}' додано! (ID: manual_{car.id})"
    )


# ── My cars list ──────────────────────────────────────────


@router.callback_query(lambda c: c.data == "mgr_my_cars")
async def cb_mgr_my_cars(callback: CallbackQuery) -> None:
    if not _is_manager(callback.from_user.id):
        return
    await callback.answer()

    async with async_session() as session:
        result = await session.execute(
            select(ManualCar)
            .where(ManualCar.added_by == callback.from_user.id, ManualCar.is_active == True)
            .order_by(ManualCar.created_at.desc())
        )
        cars = result.scalars().all()

    if not cars:
        await callback.message.answer("У вас немає доданих авто.")
        return

    for car in cars:
        remaining = ""
        now = datetime.now(timezone.utc)
        diff = (car.auction_end - now).total_seconds()
        if diff > 0:
            from bot.services.parser import format_remaining
            remaining = f"\nЗалишилось: {format_remaining(int(diff))}"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Видалити", callback_data=f"del_manual:{car.id}")],
        ])
        await callback.message.answer(
            f"<b>{car.title}</b>\n"
            f"ID: manual_{car.id}\n"
            f"Аукцiон до: {car.auction_end:%Y-%m-%d %H:%M}{remaining}",
            parse_mode="HTML",
            reply_markup=kb,
        )


# ── Delete manual car ─────────────────────────────────────


@router.callback_query(lambda c: c.data and c.data.startswith("del_manual:"))
async def cb_delete_manual_car(callback: CallbackQuery) -> None:
    if not _is_manager(callback.from_user.id):
        return
    await callback.answer()
    car_id = int(callback.data.removeprefix("del_manual:"))

    async with async_session() as session:
        result = await session.execute(
            select(ManualCar).where(ManualCar.id == car_id)
        )
        car = result.scalar_one_or_none()
        if car and car.added_by == callback.from_user.id:
            car.is_active = False
            await session.commit()
            await callback.message.edit_text("Авто видалено.")
        else:
            await callback.message.answer("Авто не знайдено.")
