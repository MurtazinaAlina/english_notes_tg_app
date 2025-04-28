"""
Обработка действий с преобразованием введённого текста в аудио.
"""
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.fsm_classes import SpeakingFSM
from app.common.tools import clear_auxiliary_msgs_in_chat
from app.filters.custom_filters import ChatTypeFilter
from app.utils.custom_bot_class import Bot
from app.utils.tts import speak_text


# Создаём роутер для приватного чата бота с пользователем
speaking_router = Router()

# Настраиваем фильтр, что строго приватный чат
speaking_router.message.filter(ChatTypeFilter(['private']))


# Преобразование введённого текста в аудио файл
@speaking_router.message(SpeakingFSM.text_input, F.text)
async def speaking_text_input(message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Преобразование введённого текста в аудио файл.

    :param message: Входящий текст
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    text = message.text
    await speak_text(text, bot, message.chat.id, is_with_title=True, autodelete=False, state=state, session=session)
    await bot.delete_message(message.chat.id, message.message_id)

    # INFO: на выходе остаётся SpeakingFSM.text_input, можно продолжать вводить фрагменты текста для получения аудио


# Очистка чата от аудио файлов
@speaking_router.callback_query(SpeakingFSM.text_input, F.data == 'clear_chat')
async def clear_chat(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Очистка чата от аудио файлов.

    :param callback: CallbackQuery-запрос формата 'clear_chat'
    :param bot: Объект бота
    :return: None
    """
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # INFO: на выходе остаётся SpeakingFSM.text_input, можно продолжать вводить фрагменты текста для получения аудио
