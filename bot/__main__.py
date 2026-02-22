import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import settings
from bot.handlers.start import router as start_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(start_router)

    logger.info("Starting bot…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
