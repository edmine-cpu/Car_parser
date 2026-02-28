import asyncio
import logging

from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, URLInputFile
from sqlalchemy import select

from bot.db import Favorite, ManualCar, async_session
from bot.services.parser import OfferItem, fetch_offers, format_remaining

logger = logging.getLogger(__name__)

POLL_INTERVAL = 180  # 3 minutes
TWELVE_HOURS = 12 * 3600
THREE_HOURS = 3 * 3600

# Prevent concurrent HTTP fetches
_fetch_lock = asyncio.Lock()

# Set of known offer IDs (populated on first poll)
_seen_ids: set[str] = set()

# Cached latest offers (updated every poll)
cached_offers: list[OfferItem] = []

# Set of subscribed user chat IDs
subscribers: set[int] = set()

# offer_id -> already sent the 12h notification
_notified_12h: set[str] = set()

# "user_id:offer_id" -> already sent the 3h favorite notification
_notified_3h: set[str] = set()


async def _load_manual_cars() -> list[OfferItem]:
    """Load active manual cars from DB and convert to OfferItem."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ManualCar).where(ManualCar.is_active == True)
            )
            cars = result.scalars().all()
    except Exception as e:
        logger.error("Failed to load manual cars: %s", e)
        return []

    items = []
    now = datetime.now(timezone.utc)
    for car in cars:
        diff = (car.auction_end - now).total_seconds()
        auction_end_seconds = max(0, int(diff))
        if auction_end_seconds <= 0:
            continue
        items.append(OfferItem(
            id=f"manual_{car.id}",
            title=car.title,
            year=car.year,
            mileage=car.mileage,
            auction_end=car.auction_end.strftime("%Y-%m-%d %H:%M:%S"),
            url=car.url or "",
            image_url=car.image_url or "",
            source="Менеджер",
            auction_end_seconds=auction_end_seconds,
        ))
    return items


def _card_keyboard(offer: OfferItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Переглянути авто", callback_data=f"detail:{offer.id}")],
    ])


def _card_caption(offer: OfferItem) -> str:
    remaining = format_remaining(offer.auction_end_seconds)
    return (
        f"<b>{offer.title}</b>\n"
        f"ID: {offer.id} ({offer.source})\n"
        f"Рiк: {offer.year}\n"
        f"Пробiг: {offer.mileage} km\n"
        f"Залишилось: {remaining}"
    )


async def _send_offer(bot: Bot, chat_id: int, offer: OfferItem) -> None:
    caption = _card_caption(offer)
    keyboard = _card_keyboard(offer)
    try:
        if offer.image_url:
            is_manual = offer.id.startswith("manual_")
            if is_manual:
                await bot.send_photo(chat_id, photo=offer.image_url, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            else:
                photo = URLInputFile(offer.image_url)
                await bot.send_photo(chat_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.warning("Failed to send offer photo %s to %s: %s, falling back to text", offer.id, chat_id, e)
        try:
            await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e2:
            logger.warning("Fallback text also failed for %s to %s: %s", offer.id, chat_id, e2)


async def _check_favorites_3h(bot: Bot, notify: bool = True) -> None:
    """Check favorites and send 3h warnings. If notify=False, only populate _notified_3h."""
    if not cached_offers:
        return

    offers_map = {o.id: o for o in cached_offers}

    try:
        async with async_session() as session:
            result = await session.execute(select(Favorite))
            favs = result.scalars().all()
    except Exception as e:
        logger.error("Failed to load favorites for 3h check: %s", e)
        return

    for fav in favs:
        offer = offers_map.get(fav.offer_id)
        if offer is None:
            continue
        if offer.auction_end_seconds >= THREE_HOURS:
            continue

        key = f"{fav.user_id}:{fav.offer_id}"
        if key in _notified_3h:
            continue
        _notified_3h.add(key)

        if not notify:
            continue

        remaining = format_remaining(offer.auction_end_seconds)
        caption = (
            f"⏰ <b>До завершення аукціону залишилось менше 3 годин!</b>\n\n"
            f"<b>{offer.title}</b>\n"
            f"ID: {offer.id}\n"
            f"Залишилось: {remaining}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Переглянути авто", callback_data=f"detail:{offer.id}")],
        ])
        try:
            await bot.send_message(fav.user_id, caption, parse_mode="HTML", reply_markup=keyboard)
            logger.info("Sent 3h warning to user %s for offer %s", fav.user_id, fav.offer_id)
        except Exception as e:
            logger.warning("3h fav notify failed for user %s: %s", fav.user_id, e)


async def _fetch_offers_safe() -> list[OfferItem]:
    async with _fetch_lock:
        return await fetch_offers()


async def poll_new_offers(bot: Bot) -> None:
    """Background task: fetch offers every 3 minutes, send notifications to subscribers."""
    global _seen_ids, cached_offers

    # First run: populate cache and seen IDs without sending 12h notifications
    try:
        cached_offers = await _fetch_offers_safe()

        # Merge manual cars from DB
        manual = await _load_manual_cars()
        cached_offers = cached_offers + manual
        cached_offers.sort(key=lambda o: o.auction_end_seconds)

        _seen_ids = {o.id for o in cached_offers if o.id}

        # Mark all currently <12h parsed offers as already notified (avoid spam on restart)
        # Manual cars are NOT marked — they should get notified after restart
        for o in cached_offers:
            if o.auction_end_seconds < TWELVE_HOURS and not o.id.startswith("manual_"):
                _notified_12h.add(o.id)

        from bot.handlers.start import _offer_cache
        for o in cached_offers:
            _offer_cache[o.id] = (o.url, o.title, o.image_url)

        # Mark 3h favorites without sending (avoid spam on restart)
        await _check_favorites_3h(bot, notify=False)

        logger.info("Poller initialized with %d offers (%d under 12h)",
                    len(_seen_ids),
                    sum(1 for o in cached_offers if o.auction_end_seconds < TWELVE_HOURS))
    except Exception as e:
        logger.error("Poller init failed: %s", e)

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            offers = await _fetch_offers_safe()
        except Exception as e:
            logger.error("Poll failed: %s", e)
            continue

        try:
            # Merge manual cars from DB
            manual = await _load_manual_cars()
            offers = offers + manual
            offers.sort(key=lambda o: o.auction_end_seconds)

            cached_offers = offers

            from bot.handlers.start import _offer_cache
            for o in offers:
                _offer_cache[o.id] = (o.url, o.title, o.image_url)
                _seen_ids.add(o.id)

            # Send 12h notifications for offers that just crossed the 12h threshold
            for offer in offers:
                if offer.auction_end_seconds < TWELVE_HOURS and offer.id not in _notified_12h:
                    _notified_12h.add(offer.id)
                    logger.info("12h alert for offer %s (%s)", offer.id, offer.auction_end)
                    for chat_id in list(subscribers):
                        await _send_offer(bot, chat_id, offer)

            # Check favorites for 3h notifications
            await _check_favorites_3h(bot, notify=True)
        except Exception as e:
            logger.error("Post-fetch processing failed: %s", e)
