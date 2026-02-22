import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    URLInputFile,
)

from bot.services.parser import fetch_offers

router = Router()
logger = logging.getLogger(__name__)


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Наявні автівки", callback_data="cars_available"),
            InlineKeyboardButton(text="Показати обрані", callback_data="cars_favorites"),
        ],
        [
            InlineKeyboardButton(text="Запросити менеджера", callback_data="request_manager"),
        ],
    ])


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Оберіть дію:", reply_markup=start_keyboard())


@router.callback_query(lambda c: c.data == "cars_available")
async def cb_cars_available(callback: CallbackQuery) -> None:
    await callback.answer()

    msg = callback.message
    await msg.answer("💬 Наявні автівки\n\nЗавантаження...")

    try:
        offers = await fetch_offers()
    except Exception as e:
        logger.error("Failed to fetch offers: %s", e)
        await msg.answer("Не вдалося завантажити автівки. Спробуйте пізніше.")
        return

    if not offers:
        await msg.answer("Наразі немає доступних автівок.")
        return

    for offer in offers:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Переглянути авто", url=offer.detail_url)],
        ])
        try:
            photo = URLInputFile(offer.image_url)
            await msg.answer_photo(photo=photo, caption=offer.title, reply_markup=keyboard)
        except Exception as e:
            logger.warning("Send failed for %s: %s", offer.title, e)
            try:
                await msg.answer(offer.title, reply_markup=keyboard)
            except Exception:
                await msg.answer(offer.title)


@router.callback_query(lambda c: c.data == "cars_favorites")
async def cb_cars_favorites(callback: CallbackQuery) -> None:
    await callback.answer("Обрані — скоро буде!")


@router.callback_query(lambda c: c.data == "request_manager")
async def cb_request_manager(callback: CallbackQuery) -> None:
    await callback.answer("Запит менеджеру — скоро буде!")
