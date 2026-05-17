"""Scheduled subscriber notifications.

Wakes at 21:00 Europe/Kyiv each day and reports the count of new offers
seen since the previous slot (24h window). Relies on OfferSnapshot.updated_at
behaving as 'first-seen-at': the poller's on_conflict_do_update does not
touch updated_at, and SQLAlchemy's onupdate hook is bypassed by the
dialect-specific on_conflict_do_update path."""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import func, select

from bot.db import OfferSnapshot, User, async_session

logger = logging.getLogger(__name__)

KYIV = ZoneInfo("Europe/Kyiv")
SLOT_HOURS = (21,)


def _next_slot(now: datetime) -> datetime:
    now_kyiv = now.astimezone(KYIV)
    today = now_kyiv.date()
    for h in SLOT_HOURS:
        candidate = datetime(today.year, today.month, today.day, h, 0, tzinfo=KYIV)
        if candidate > now_kyiv:
            return candidate
    t = today + timedelta(days=1)
    return datetime(t.year, t.month, t.day, SLOT_HOURS[0], 0, tzinfo=KYIV)


def _prev_slot(slot: datetime) -> datetime:
    s = slot.astimezone(KYIV)
    idx = SLOT_HOURS.index(s.hour)
    if idx == 0:
        d = s.date() - timedelta(days=1)
        return datetime(d.year, d.month, d.day, SLOT_HOURS[-1], 0, tzinfo=KYIV)
    return datetime(s.year, s.month, s.day, SLOT_HOURS[idx - 1], 0, tzinfo=KYIV)


async def _count_new_offers(start: datetime, end: datetime) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(OfferSnapshot).where(
                OfferSnapshot.updated_at >= start,
                OfferSnapshot.updated_at < end,
            )
        )
        return int(result.scalar() or 0)


async def _broadcast(bot: Bot, text: str) -> list[int]:
    from bot.services.poller import subscribers
    chat_ids: set[int] = set(subscribers)
    try:
        async with async_session() as session:
            result = await session.execute(select(User.chat_id))
            chat_ids.update(int(x) for x in result.scalars().all())
    except Exception as e:
        logger.error("Failed to load users for broadcast, falling back to in-memory subscribers: %s", e)
    recipients = list(chat_ids)
    for user_id in recipients:
        try:
            await bot.send_message(user_id, text)
        except Exception as e:
            logger.warning("User notify failed for %s: %s", user_id, e)
    return recipients


async def notify_new_cars_loop(bot: Bot) -> None:
    while True:
        now = datetime.now(tz=KYIV)
        slot = _next_slot(now)
        delay = (slot - now).total_seconds()
        logger.info("Next manager notify slot: %s (in %.0fs)", slot.isoformat(), delay)
        await asyncio.sleep(delay)

        prev = _prev_slot(slot)
        hours = int(round((slot - prev).total_seconds() / 3600))
        try:
            count = await _count_new_offers(prev, slot)
        except Exception as e:
            logger.error("notify count query failed: %s", e)
            continue

        text = f"За останні {hours} часов було додано {count} авто."
        recipients = await _broadcast(bot, text)
        logger.info("Notified %d subscribers: %s", len(recipients), text)
