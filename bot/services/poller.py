import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, URLInputFile

from bot.services.parser import OfferItem, fetch_offers

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds

# Set of known offer IDs (populated on first poll)
_seen_ids: set[str] = set()

# Cached latest offers (updated every poll)
cached_offers: list[OfferItem] = []

# Set of subscribed user chat IDs
subscribers: set[int] = set()


def _card_keyboard(offer: OfferItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Переглянути авто", callback_data=f"detail:{offer.id}")],
    ])


def _card_caption(offer: OfferItem) -> str:
    return (
        f"<b>{offer.title}</b>\n"
        f"ID: {offer.id} ({offer.source})\n"
        f"Рiк: {offer.year}\n"
        f"Пробiг: {offer.mileage} km\n"
        f"Завершення: {offer.auction_end}"
    )


async def _send_offer(bot: Bot, chat_id: int, offer: OfferItem) -> None:
    caption = _card_caption(offer)
    keyboard = _card_keyboard(offer)
    try:
        if offer.image_url:
            photo = URLInputFile(offer.image_url)
            await bot.send_photo(chat_id, photo=photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.warning("Failed to send offer %s to %s: %s", offer.id, chat_id, e)


async def poll_new_offers(bot: Bot) -> None:
    """Background task: fetch offers every minute, send new ones to subscribers."""
    global _seen_ids

    global _seen_ids, cached_offers

    # First run: populate cache and seen IDs without sending
    try:
        cached_offers = await fetch_offers()
        _seen_ids = {o.id for o in cached_offers if o.id}

        from bot.handlers.start import _offer_cache
        for o in cached_offers:
            _offer_cache[o.id] = (o.url, o.title, o.image_url)

        logger.info("Poller initialized with %d offers", len(_seen_ids))
    except Exception as e:
        logger.error("Poller init failed: %s", e)

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            offers = await fetch_offers()
        except Exception as e:
            logger.error("Poll failed: %s", e)
            continue

        # Update cache
        cached_offers = offers

        from bot.handlers.start import _offer_cache
        for o in offers:
            _offer_cache[o.id] = (o.url, o.title, o.image_url)

        new_offers = [o for o in offers if o.id and o.id not in _seen_ids]
        if not new_offers:
            continue

        logger.info("Found %d new offers", len(new_offers))

        for o in new_offers:
            _seen_ids.add(o.id)

        # Send new ones to all subscribers
        for chat_id in list(subscribers):
            for offer in new_offers:
                await _send_offer(bot, chat_id, offer)
