import asyncio
import logging

from aiogram import Bot, Dispatcher
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from bot.config import settings
from bot.db import Base, Favorite, ManualCar, Request, User, async_session, engine
from bot.handlers.add_car import router as add_car_router
from bot.handlers.start import router as start_router
from bot.services.notifier import notify_new_cars_loop
from bot.services.poller import poll_new_offers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _backfill_users() -> None:
    try:
        async with async_session() as session:
            ids: set[int] = set()
            for col in (Favorite.user_id, Request.user_id, ManualCar.added_by):
                result = await session.execute(select(col).distinct())
                ids.update(int(x) for x in result.scalars().all() if x is not None)
            if not ids:
                logger.info("User backfill: no historical users found")
                return
            stmt = pg_insert(User).values([{"chat_id": cid} for cid in ids])
            stmt = stmt.on_conflict_do_nothing(index_elements=["chat_id"])
            await session.execute(stmt)
            await session.commit()
            logger.info("User backfill: upserted %d candidates", len(ids))
    except Exception as e:
        logger.error("User backfill failed: %s", e)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _backfill_users()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(add_car_router)
    dp.include_router(start_router)

    # Start background poller
    asyncio.create_task(poll_new_offers(bot))
    asyncio.create_task(notify_new_cars_loop(bot))

    logger.info("Starting bot…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
