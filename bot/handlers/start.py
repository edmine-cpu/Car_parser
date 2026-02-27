import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    URLInputFile,
)
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.config import settings
from bot.db import Favorite, Request, async_session
from bot.services.parser import fetch_offer_detail, format_remaining

router = Router()
logger = logging.getLogger(__name__)

MAX_OFFERS = 10
MAX_PHOTOS = 10

# In-memory cache: offer_id -> (url, title, image_url)
_offer_cache: dict[str, tuple[str, str, str]] = {}

# Relay-chat state: manager_id -> {user_id, user_name, offer_title, request_type, offer_url}
_active_chat: dict[int, dict] = {}
# Users currently in an active relay conversation
_users_in_chat: set[int] = set()
# Reverse mapping: user_id -> manager_id who is chatting with them
_user_to_manager: dict[int, int] = {}

# Persistent keyboard shown to manager during an active relay conversation
_mgr_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/end_chat"), KeyboardButton(text="/who")],
        [KeyboardButton(text="Замовлення"), KeyboardButton(text="Уточнення")],
    ],
    resize_keyboard=True,
)


def _is_manager(user_id: int) -> bool:
    return user_id in settings.manager_ids


def start_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Наявнi автiвки", callback_data="cars_available"),
            InlineKeyboardButton(text="Показати обранi", callback_data="cars_favorites"),
        ],
    ]
    if _is_manager(user_id):
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
    if _is_manager(message.from_user.id):
        await message.answer("Панель менеджера:", reply_markup=_mgr_keyboard)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"<code>{message.from_user.id}</code>", parse_mode="HTML")


# ── Car listing ────────────────────────────────────────────


@router.callback_query(lambda c: c.data == "cars_available")
async def cb_cars_available(callback: CallbackQuery) -> None:
    await callback.answer()
    msg = callback.message

    from bot.services.poller import TWELVE_HOURS, cached_offers
    offers = [o for o in cached_offers if o.auction_end_seconds < TWELVE_HOURS]

    if not offers:
        await msg.answer("Наразi немає авто iз завершенням аукцiону менше нiж за 12 годин.")
        return

    for offer in offers:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Переглянути авто", callback_data=f"detail:{offer.id}")],
        ])
        remaining = format_remaining(offer.auction_end_seconds)
        caption = (
            f"<b>{offer.title}</b>\n"
            f"ID: {offer.id}\n"
            f"Рiк: {offer.year}\n"
            f"Пробiг: {offer.mileage} km\n"
            f"Залишилось: {remaining}"
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
        req = Request(
            user_id=user.id,
            user_name=name,
            username=user.username or "",
            offer_id=offer_id,
            offer_title=offer_title,
            offer_url=offer_url,
            request_type=request_type,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        request_db_id = req.id

    # Notify all managers
    type_label = "Замовлення" if request_type == "order" else "Уточнення деталей"
    manager_text = (
        f"{'🛒' if request_type == 'order' else '❓'} <b>{type_label}</b>\n\n"
        f"Авто: {offer_title}\n"
        f"ID: {offer_id}\n"
        f"Посилання: {offer_url}\n\n"
        f"Клiєнт: {name}{username_str}\n"
        f"ID: <code>{user.id}</code>"
    )
    reply_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вiдповiсти", callback_data=f"reply:{request_db_id}")],
    ])
    sent = False
    for mgr_id in settings.manager_ids:
        try:
            await callback.bot.send_message(
                mgr_id,
                manager_text,
                parse_mode="HTML",
                reply_markup=reply_kb,
            )
            sent = True
        except Exception as e:
            logger.error("Failed to notify manager %s: %s", mgr_id, e)

    if not sent:
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
    if not _is_manager(callback.from_user.id):
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
        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Вiдповiсти", callback_data=f"reply:{req.id}")],
        ])
        await callback.message.answer(text, parse_mode="HTML", reply_markup=reply_kb)


@router.callback_query(lambda c: c.data == "mgr_questions")
async def cb_mgr_questions(callback: CallbackQuery) -> None:
    await callback.answer()
    if not _is_manager(callback.from_user.id):
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
        reply_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Вiдповiсти", callback_data=f"reply:{req.id}")],
        ])
        await callback.message.answer(text, parse_mode="HTML", reply_markup=reply_kb)


# ── Relay chat ────────────────────────────────────────────


@router.message(Command("clients"))
async def cmd_clients(message: Message) -> None:
    if not _is_manager(message.from_user.id):
        return

    async with async_session() as session:
        orders_result = await session.execute(
            select(Request)
            .where(Request.request_type == "order")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        orders = orders_result.scalars().all()

        questions_result = await session.execute(
            select(Request)
            .where(Request.request_type == "question")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        questions = questions_result.scalars().all()

    if not orders and not questions:
        await message.answer("Немає запитiв вiд клiєнтiв.")
        return

    # Orders section
    if orders:
        order_buttons = []
        for req in orders:
            label = f"{req.user_name} — {req.offer_title[:30]}"
            order_buttons.append(
                [InlineKeyboardButton(text=label, callback_data=f"pick:{req.id}")]
            )
        order_kb = InlineKeyboardMarkup(inline_keyboard=order_buttons)
        await message.answer("🛒 <b>Замовники:</b>", parse_mode="HTML", reply_markup=order_kb)

    # Questions section
    if questions:
        question_buttons = []
        for req in questions:
            label = f"{req.user_name} — {req.offer_title[:30]}"
            question_buttons.append(
                [InlineKeyboardButton(text=label, callback_data=f"pick:{req.id}")]
            )
        question_kb = InlineKeyboardMarkup(inline_keyboard=question_buttons)
        await message.answer("❓ <b>Уточнення:</b>", parse_mode="HTML", reply_markup=question_kb)


@router.callback_query(lambda c: c.data and c.data.startswith("pick:"))
async def cb_pick_request(callback: CallbackQuery) -> None:
    await callback.answer()
    if not _is_manager(callback.from_user.id):
        return

    request_id = callback.data.removeprefix("pick:")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Написати", callback_data=f"reply:{request_id}"),
            InlineKeyboardButton(text="Закрити", callback_data=f"close_req:{request_id}"),
        ],
    ])
    await callback.message.answer("Оберiть дiю:", reply_markup=kb)


@router.callback_query(lambda c: c.data and c.data.startswith("close_req:"))
async def cb_close_request(callback: CallbackQuery) -> None:
    await callback.answer()
    if not _is_manager(callback.from_user.id):
        return

    request_id = int(callback.data.removeprefix("close_req:"))

    async with async_session() as session:
        await session.execute(
            delete(Request).where(Request.id == request_id)
        )
        await session.commit()

    await callback.message.edit_text("✅ Запит закрито.")


@router.callback_query(lambda c: c.data and c.data.startswith("reply:"))
async def cb_reply_to_user(callback: CallbackQuery) -> None:
    await callback.answer()
    manager_id = callback.from_user.id
    if not _is_manager(manager_id):
        return

    request_id = int(callback.data.removeprefix("reply:"))

    async with async_session() as session:
        result = await session.execute(
            select(Request).where(Request.id == request_id)
        )
        req = result.scalar_one_or_none()

    if not req:
        await callback.message.answer("Запит не знайдено.")
        return

    # Protection: check if another manager is already chatting with this user
    if req.user_id in _users_in_chat:
        other_mgr = _user_to_manager.get(req.user_id)
        if other_mgr and other_mgr != manager_id:
            await callback.message.answer(
                "⚠️ Цей клiєнт вже в розмовi з iншим менеджером."
            )
            return

    # Close previous conversation if this manager is switching to a different user
    old_chat = _active_chat.get(manager_id)
    if old_chat and old_chat["user_id"] != req.user_id:
        old_user_id = old_chat["user_id"]
        _users_in_chat.discard(old_user_id)
        _user_to_manager.pop(old_user_id, None)
        await callback.message.answer(
            f"Попередню розмову з {old_chat['user_name']} завершено."
        )

    _active_chat[manager_id] = {
        "user_id": req.user_id,
        "user_name": req.user_name,
        "offer_title": req.offer_title,
        "offer_url": req.offer_url,
        "request_type": req.request_type,
    }
    _users_in_chat.add(req.user_id)
    _user_to_manager[req.user_id] = manager_id

    type_label = "Замовлення" if req.request_type == "order" else "Уточнення"
    await callback.message.answer(
        f"💬 Розмова з <b>{req.user_name}</b>\n"
        f"Тема: {type_label} — {req.offer_title}\n\n"
        f"Пишiть повiдомлення, воно буде надiслане клiєнту.",
        parse_mode="HTML",
        reply_markup=_mgr_keyboard,
    )


@router.message(Command("end_chat"))
async def cmd_end_chat(message: Message) -> None:
    if not _is_manager(message.from_user.id):
        return
    chat_info = _active_chat.pop(message.from_user.id, None)
    if chat_info:
        user_id = chat_info["user_id"]
        _users_in_chat.discard(user_id)
        _user_to_manager.pop(user_id, None)
        await message.answer(f"Розмову з {chat_info['user_name']} завершено.")
    else:
        await message.answer("Немає активної розмови.")


@router.message(Command("who"))
async def cmd_who(message: Message) -> None:
    if not _is_manager(message.from_user.id):
        return
    chat_info = _active_chat.get(message.from_user.id)
    if chat_info:
        url = chat_info.get("offer_url", "")
        url_line = f"\nПосилання: {url}" if url else ""
        await message.answer(
            f"Активна розмова з: <b>{chat_info['user_name']}</b>\n"
            f"Авто: {chat_info['offer_title']}{url_line}",
            parse_mode="HTML",
        )
    else:
        await message.answer("Немає активної розмови.")


@router.message(lambda m: _is_manager(m.from_user.id), F.text == "Замовлення")
async def mgr_btn_orders(message: Message) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Request)
            .where(Request.request_type == "order")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        orders = result.scalars().all()

    if not orders:
        await message.answer("Замовлень поки немає.")
        return

    buttons = []
    for req in orders:
        label = f"{req.user_name} — {req.offer_title[:30]}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"pick:{req.id}")]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🛒 <b>Замовники:</b>", parse_mode="HTML", reply_markup=kb)


@router.message(lambda m: _is_manager(m.from_user.id), F.text == "Уточнення")
async def mgr_btn_questions(message: Message) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Request)
            .where(Request.request_type == "question")
            .order_by(Request.created_at.desc())
            .limit(20)
        )
        questions = result.scalars().all()

    if not questions:
        await message.answer("Запитiв на уточнення поки немає.")
        return

    buttons = []
    for req in questions:
        label = f"{req.user_name} — {req.offer_title[:30]}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f"pick:{req.id}")]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("❓ <b>Уточнення:</b>", parse_mode="HTML", reply_markup=kb)


@router.message(lambda m: _is_manager(m.from_user.id), F.text, ~F.text.startswith("/"))
async def mgr_relay_to_user(message: Message) -> None:
    chat_info = _active_chat.get(message.from_user.id)
    if not chat_info:
        return

    user_id = chat_info["user_id"]
    try:
        await message.bot.send_message(
            user_id,
            message.text,
        )
    except Exception as e:
        logger.error("Failed to relay to user %s: %s", user_id, e)
        await message.reply("❌ Не вдалося надiслати повiдомлення клiєнту.")


@router.message(F.text)
async def user_relay_to_manager(message: Message) -> None:
    user_id = message.from_user.id
    if _is_manager(user_id) or user_id not in _users_in_chat:
        return

    target_mgr = _user_to_manager.get(user_id)
    if not target_mgr:
        return

    await message.bot.send_message(
        target_mgr,
        message.text,
    )
