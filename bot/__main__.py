import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import settings
from bot.db import Base, engine
from bot.handlers.add_car import router as add_car_router
from bot.handlers.start import router as start_router
from bot.services.poller import poll_new_offers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(add_car_router)
    dp.include_router(start_router)

    # Start background poller
    asyncio.create_task(poll_new_offers(bot))

    logger.info("Starting bot…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
