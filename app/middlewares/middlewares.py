"""
Middleware
"""
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from langchain_gigachat import GigaChat
from sqlalchemy.ext.asyncio import async_sessionmaker


# Middleware для подключения к БД, который будет сохранять объект сессии
class DataBaseSession(BaseMiddleware):
    """ Middleware для подключения к БД, который будет сохранять объект сессии. """

    def __init__(self, session_pool: async_sessionmaker) -> None:
        self.Session = session_pool                 # Сессия БД

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,                  # Событие-триггер для срабатывания - ЛЮБОЕ
            data: Dict[str, Any]                    # Данные, которые слои пробрасывают друг другу
    ) -> Any:
        async with self.Session() as session:
            data['session'] = session               # объект (!) асинхронной сессии

            # Теперь в КАЖДОМ обработчике в параметре 'session' будет доступна асинхронная сессия с БД
            return await handler(event, data)


# Middleware для подключения к GigaChat, который будет сохранять объект созданного чата
class GigaChatMiddleware(BaseMiddleware):
    """ Middleware для подключения к GigaChat, который будет сохранять объект созданного чата. """

    def __init__(self, giga_chat: GigaChat) -> None:
        self.giga_chat = giga_chat

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        data['giga_chat'] = self.giga_chat
        return await handler(event, data)
