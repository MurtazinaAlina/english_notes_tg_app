"""
Обработка действий с AI-ассистентом GIGACHAT
"""
from aiogram import Router, F, types
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_gigachat import GigaChat

from app.common.fsm_classes import GigaAiFSM
from app.common.tools import clear_auxiliary_msgs_in_chat
from app.filters.custom_filters import ChatTypeFilter
from app.utils.custom_bot_class import Bot
from app.settings import GIGA_SYSTEM_PROMPT


# Создаём роутер для приватного чата бота с пользователем
giga_router = Router()

# Настраиваем фильтр, что строго приватный чат
giga_router.message.filter(ChatTypeFilter(['private']))


# Отправка пользовательского запроса в GIGACHAT и отправка ответа в чат бота
@giga_router.message(GigaAiFSM.text_input, F.text)
async def giga_chat_get_response(message: types.Message, bot: Bot, giga_chat: GigaChat) -> None:
    """
    Обработка действий с AI-ассистентом GIGACHAT.
    Отправка пользовательского запроса в GIGACHAT и отправка ответа в чат бота.

    :param message: Сообщение с запросом от пользователя
    :param bot: Объект бота
    :param giga_chat: Объект GIGACHAT, взаимодействующий с API GIGACHAT SBER
    :return: None
    """

    # Сохраняем сообщение во вспомогательном хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Формируем промпт
    prompt = [
        SystemMessage(content=GIGA_SYSTEM_PROMPT),
        HumanMessage(content=message.text),
    ]

    # Обращаемся к GigaChat и отправляем ответ в чат бота
    try:
        response = giga_chat.invoke(prompt)
        msg = await message.answer(response.content)
        bot.auxiliary_msgs['user_msgs'][message.chat.id].append(msg)

    except Exception as e:
        msg = await message.answer("⚠️ Упс! Произошла ошибка при обращении к GigaChat. Попробуйте ещё раз.")
        bot.auxiliary_msgs['user_msgs'][message.chat.id].append(msg)
        print(f"Ошибка: {e}")


# Очистка чата от истории сообщений
@giga_router.callback_query(GigaAiFSM.text_input, F.data == 'clear_chat')
async def clear_chat(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Очистка чата от сообщений.

    :param callback: CallbackQuery-запрос формата 'clear_chat'
    :param bot: Объект бота
    :return: None
    """
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)
