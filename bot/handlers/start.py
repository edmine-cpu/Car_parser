import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    URLInputFile,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.config import settings
from bot.db import Favorite, Request, async_session
from bot.services.parser import fetch_offer_detail, fetch_offers

router = Router()
logger = logging.getLogger(__name__)

MAX_OFFERS = 10
MAX_PHOTOS = 10

# In-memory cache: offer_id -> (url, title, image_url)
_offer_cache: dict[str, tuple[str, str, str]] = {}


def start_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Наявнi автiвки", callback_data="cars_available"),
            InlineKeyboardButton(text="Показати обранi", callback_data="cars_favorites"),
        ],
    ]
    if user_id == settings.MANAGER_ID:
        rows.append([
            InlineKeyboardButton(text="Замовлення", callback_data="mgr_orders"),
            InlineKeyboardButton(text="Уточнення", callback_data="mgr_questions"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    from bot.services.poller import subscribers
    subscribers.add(message.chat.id)
    await message.answer("Оберiть дiю:", reply_markup=start_keyboard(message.from_user.id))


# ── Car listing ────────────────────────────────────────────


@router.callback_query(lambda c: c.data == "cars_available")
async def cb_cars_available(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback.message
    await msg.answer("Завантаження...")

    try:
        offers = await fetch_offers()
    except Exception as e:
        logger.error("Failed to fetch offers: %s", e)
        await msg.answer("Не вдалося завантажити автiвки. Спробуйте пiзнiше.")
        return

    if not offers:
        await msg.answer("Наразi немає доступних автiвок.")
        return

    for offer in offers[:MAX_OFFERS]:
        _offer_cache[offer.id] = (offer.url, offer.title, offer.image_url)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Переглянути авто", callback_data=f"detail:{offer.id}")],
        ])
        caption = (
            f"<b>{offer.title}</b>\n"
            f"ID: {offer.id}\n"
            f"Рiк: {offer.year}\n"
            f"Пробiг: {offer.mileage} km\n"
            f"Завершення: {offer.auction_end}"
        )
        try:
            if offer.image_url:
                photo = URLInputFile(offer.image_url)
                await msg.answer_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            else:
                await msg.answer(caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.warning("Send failed for %s: %s", offer.title, e)
            await msg.answer(caption, parse_mode="HTML", reply_markup=keyboard)


# ── Car detail ─────────────────────────────────────────────


@router.callback_query(lambda c: c.data and c.data.startswith("detail:"))
async def cb_offer_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback.message
    offer_id = callback.data.removeprefix("detail:")
    cached = _offer_cache.get(offer_id)
    if not cached:
        await msg.answer("Лот не знайдено. Спробуйте оновити список.")
        return
    url, _, _ = cached

    try:
        detail = await fetch_offer_detail(url)
    except Exception as e:
        logger.error("Failed to fetch detail %s: %s", url, e)
        await msg.answer("Не вдалося завантажити деталi. Спробуйте пiзнiше.")
        return

    if not detail:
        await msg.answer("Деталi не знайдено.")
        return

    caption = (
        f"<b>{detail.title}</b>\n"
        f"\U0001f1e8\U0001f1edАВТО Зi ШВЕЙЦАРIЇ\n\n"
        f"☑️Рiк випуску: {detail.year}\n"
        f"☑️Вид палива: {detail.fuel}\n"
        f"☑️Об'єм двигуна: {detail.engine}\n"
        f"☑️Пробiг: {detail.mileage}\n"
        f"☑️Коробка передач: {detail.transmission}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Замовити авто", callback_data=f"order:{offer_id}")],
        [InlineKeyboardButton(text="Уточнити деталi", callback_data=f"ask:{offer_id}")],
        [InlineKeyboardButton(text="Додати в обранi", callback_data=f"fav:{offer_id}")],
        [InlineKeyboardButton(text="Назад до списку", callback_data="cars_available")],
    ])

    photos = detail.photos[:MAX_PHOTOS]
    if photos:
        media = [InputMediaPhoto(media=URLInputFile(p)) for p in photos]
        media[0] = InputMediaPhoto(media=URLInputFile(photos[0]), caption=caption, parse_mode="HTML")
        try:
            await msg.answer_media_group(media=media)
        except Exception as e:
            logger.warning("Media group failed: %s", e)
            try:
                await msg.answer_photo(photo=URLInputFile(photos[0]), caption=caption, parse_mode="HTML")
            except Exception:
                await msg.answer(caption, parse_mode="HTML")

    await msg.answer(
        "Якщо вам подобається авто, натиснiть кнопку нижче 👇",
        reply_markup=keyboard,
    )


# ── Favorites ──────────────────────────────────────────────


@router.callback_query(lambda c: c.data and c.data.startswith("fav:"))
async def cb_add_fav(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    offer_id = callback.data.removeprefix("fav:")

    cached = _offer_cache.get(offer_id)
    if not cached:
        await callback.message.answer("Лот не знайдено. Спробуйте оновити список.")
        return
    url, title, image_url = cached

    async with async_session() as session:
        stmt = (
            pg_insert(Favorite)
            .values(
                user_id=user_id,
                offer_id=offer_id,
                title=title,
                url=url,
                image_url=image_url,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "offer_id"])
        )
        await session.execute(stmt)
        await session.commit()

    await callback.message.answer("⭐ Додано в обранi!")


@router.callback_query(lambda c: c.data == "cars_favorites")
async def cb_cars_favorites(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id

    async with async_session() as session:
        result = await session.execute(
            select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.created_at.desc())
        )
        favs = result.scalars().all()

    if not favs:
        await callback.message.answer("У вас поки немає обраних автiвок.")
        return

    for fav in favs:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Переглянути авто", url=fav.url)],
            [InlineKeyboardButton(text="Видалити з обраних", callback_data=f"unfav:{fav.offer_id}")],
        ])
        caption = f"⭐ <b>{fav.title}</b>\nID: {fav.offer_id}"
        try:
            if fav.image_url:
                photo = URLInputFile(fav.image_url)
                await callback.message.answer_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            else:
                await callback.message.answer(caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            logger.warning("Send fav failed for %s: %s", fav.title, e)
            await callback.message.answer(caption, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(lambda c: c.data and c.data.startswith("unfav:"))
async def cb_remove_fav(callback: CallbackQuery) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    offer_id = callback.data.removeprefix("unfav:")

    async with async_session() as session:
        await session.execute(
            delete(Favorite).where(Favorite.user_id == user_id, Favorite.offer_id == offer_id)
        )
        await session.commit()

    await callback.message.answer("❌ Видалено з обраних.")


# ── Order / Question requests ──────────────────────────────


async def _send_request(callback: CallbackQuery, request_type: str) -> None:
    user = callback.from_user
    offer_id = callback.data.split(":", 1)[1]

    cached = _offer_cache.get(offer_id)
    offer_url, offer_title = (cached[0], cached[1]) if cached else ("", "")

    name = user.full_name or "Невiдомий"
    username_str = f" (@{user.username})" if user.username else ""

    # Save to DB
    async with async_session() as session:
        session.add(Request(
            user_id=user.id,
            user_name=name,
            username=user.username or "",
            offer_id=offer_id,
            offer_title=offer_title,
            offer_url=offer_url,
            request_type=request_type,
        ))
        await session.commit()

    # Notify manager
    type_label = "Замовлення" if request_type == "order" else "Уточнення деталей"
    manager_text = (
        f"{'🛒' if request_type == 'order' else '❓'} <b>{type_label}</b>\n\n"
        f"Авто: {offer_title}\n"
        f"ID: {offer_id}\n"
        f"Посилання: {offer_url}\n\n"
        f"Клiєнт: {name}{username_str}\n"
        f"ID: <code>{user.id}</code>"
    )
    try:
        await callback.bot.send_message(
            settings.MANAGER_ID,
            manager_text,
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Failed to notify manager: %s", e)
        await callback.message.answer("Не вдалося надiслати запит. Спробуйте пiзнiше.")
        return

    await callback.message.answer("✅ Дякуємо! Очiкуйте, вам напишуть.")


@router.callback_query(lambda c: c.data and c.data.startswith("order:"))
async def cb_order(callback: CallbackQuery) -> None:
    await callback.answer()
    await _send_request(callback, "order")


@router.callback_query(lambda c: c.data and c.data.startswith("ask:"))
async def cb_ask(callback: CallbackQuery) -> None:
    await callback.answer()
    await _send_request(callback, "question")


# ── Manager panel ──────────────────────────────────────────


@router.callback_query(lambda c: c.data == "mgr_orders")
async def cb_mgr_orders(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user.id != settings.MANAGER_ID:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Request)
            .where(Request.request_type == "order")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        reqs = result.scalars().all()

    if not reqs:
        await callback.message.answer("Замовлень поки немає.")
        return

    for req in reqs:
        username_str = f" (@{req.username})" if req.username else ""
        text = (
            f"🛒 <b>Замовлення</b>\n"
            f"Авто: {req.offer_title}\n"
            f"ID: {req.offer_id}\n"
            f"Посилання: {req.offer_url}\n\n"
            f"Клiєнт: {req.user_name}{username_str}\n"
            f"ID: <code>{req.user_id}</code>\n"
            f"Дата: {req.created_at:%Y-%m-%d %H:%M}"
        )
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(lambda c: c.data == "mgr_questions")
async def cb_mgr_questions(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.from_user.id != settings.MANAGER_ID:
        return

    async with async_session() as session:
        result = await session.execute(
            select(Request)
            .where(Request.request_type == "question")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        reqs = result.scalars().all()

    if not reqs:
        await callback.message.answer("Запитiв на уточнення поки немає.")
        return

    for req in reqs:
        username_str = f" (@{req.username})" if req.username else ""
        text = (
            f"❓ <b>Уточнення деталей</b>\n"
            f"Авто: {req.offer_title}\n"
            f"ID: {req.offer_id}\n"
            f"Посилання: {req.offer_url}\n\n"
            f"Клiєнт: {req.user_name}{username_str}\n"
            f"ID: <code>{req.user_id}</code>\n"
            f"Дата: {req.created_at:%Y-%m-%d %H:%M}"
        )
        await callback.message.answer(text, parse_mode="HTML")
