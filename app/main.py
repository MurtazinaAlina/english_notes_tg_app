"""
Основной файл с запуском приложения.
"""
import os
import asyncio

from aiogram import Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

from app.handlers.user_private.user_private import user_private_router
from app.handlers.user_private.giga_ai import giga_router
from app.handlers.user_private.speaking_actions import speaking_router
from app.handlers.user_private import add_word_phrase_actions, auth_actions
from app.handlers.user_private.vocabulary.topic_actions import topic_router
from app.handlers.user_private.vocabulary.note_actions import note_router
from app.handlers.user_private.tests_actions import tests_router
from app.handlers.user_private.vocabulary import vocabulary_actions
from app.handlers.user_group import user_group_router
from app.middlewares.middlewares import DataBaseSession, GigaChatMiddleware
from app.database.db import DataBase
from app.utils.gigachat_assistant import create_gigachat_assistant
from app.utils.scheduler import schedule_tasks
from app.utils.custom_bot_class import Bot
from app.common.bot_commands import private


# Создаём бот
bot = Bot(
    token=os.getenv('BOT_TOKEN'),                                       # Токен бота
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)             # Тип форматирования текста
)

# Создаём объект для управления БД
db = DataBase()

# Создаём Gigachat ассистента
giga_chat = create_gigachat_assistant()

# Создаём диспетчер обработки + подключаем к нему роутеры
dp = Dispatcher()
dp.include_router(auth_actions.auth_router)
dp.include_router(user_private_router)
dp.include_router(topic_router)
dp.include_router(vocabulary_actions.vocabulary_router)
dp.include_router(note_router)
dp.include_router(add_word_phrase_actions.word_phrase_router)
dp.include_router(tests_router)
dp.include_router(speaking_router)
dp.include_router(giga_router)
dp.include_router(user_group_router)

# Регистрируем Middleware на диспетчер
# dp.update.outer_middleware(SomeMiddleware())
dp.update.middleware(DataBaseSession(db.session_maker))
dp.update.middleware(GigaChatMiddleware(giga_chat))


async def on_startup():
    """ Действия при запуске бота. """
    await db.create_db()                                    # Создание/обновление таблиц


async def on_shutdown():
    """ Действия при завершении работы бота. """
    print('========== on_shutdown')


async def main():
    """ Функция запуска бота. """

    # Установка меню команд
    await bot.set_my_commands(
        commands=private, scope=types.BotCommandScopeAllPrivateChats()
    )
    # await bot.delete_my_commands(scope=types.BotCommandScopeAllPrivateChats())

    # Запускаем планировщик в фоновом режиме
    asyncio.create_task(schedule_tasks(db))

    # Запускаем диспетчер и бот
    dp.startup.register(on_startup)                                                       # Функции при старте бота
    dp.shutdown.register(on_shutdown)                                                     # Функции при завершении бота
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())     # Все типы триггеров


if __name__ == '__main__':
    asyncio.run(main())
