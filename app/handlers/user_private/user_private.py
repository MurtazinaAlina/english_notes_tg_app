"""
Обработчики роутера приватного чата бота с пользователем.

INFO:
1. Обработчик команды /start
2. Точка входа обработки всех запросов типа MenuCallBack. Перенаправляет в основной обработчик меню.
"""
from aiogram import types, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.filters.custom_filters import ChatTypeFilter
from app.keyboards.inlines import MenuCallBack
from app.database.db import DataBase
from handlers.user_private.menu_processing import get_menu_content, start_page
from app.utils.custom_bot_class import Bot
from app.settings import TEST_TYPES

# Создаём роутер для приватного чата бота с пользователем
user_private_router = Router()

# Настраиваем фильтр, что строго приватный чат
user_private_router.message.filter(ChatTypeFilter(['private']))

# Регистрируем Middleware на роутер (при необходимости)
# user_private_router.message.middleware(SomeMiddleware())


# Обработчик команды /start
@user_private_router.message(CommandStart())
async def start_cmd(message: types.Message, session: AsyncSession, bot: Bot) -> None:
    """
    Обработка команды /start.
    Инициализация в атрибутах бота структур для хранения данных + попытка автоматического log in + стартовая страница.

    :param message: Входящее сообщение с командой /start
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Инициализируем в атрибутах бота словари для хранения различных данных по ключу с ID чата:
    bot.auxiliary_msgs['user_msgs'][message.chat.id] = []               # Вспомогательные сообщения
    bot.auxiliary_msgs['add_or_edit_word'][message.chat.id] = {}        # Сообщения с вводом данных и шагом
    bot.auxiliary_msgs['example_msgs'][message.chat.id] = []            # Сообщения с примерами
    bot.tests_word_navi[message.chat.id] = {}                           # Словарь с историей попыток прохождения тестов
    for test_type in TEST_TYPES:
        bot.tests_word_navi[message.chat.id][test_type] = {
            'history': {},
            'navi_index': 1
        }
    bot.word_search_keywords[message.chat.id] = {}

    # Попытка автоматического log in, если в БД есть привязка ID чата Telegram к пользователю User
    user_chat = await DataBase.get_user_chat(session, message.chat.id)
    if user_chat:
        bot.auth_user_id[message.chat.id] = user_chat.user_id

    # Вызываем основное меню
    media, reply_markup = await start_page(
        bot, session, state=None, callback=None, user_id=bot.auth_user_id.get(message.chat.id, None),
        chat_id=message.chat.id
    )
    await message.answer_photo(media.media, caption=media.caption, reply_markup=reply_markup)


# Точка входа обработки всех MenuCallBack. Стартовое меню и большинство inline-кнопок, пагинации и т.д.
@user_private_router.callback_query(MenuCallBack.filter())
async def user_menu(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Точка входа обработки всех MenuCallBack. Стартовое меню и большинство inline-кнопок, пагинации и т.д.

    В зависимости от callback забирает новый контент для основного сообщения с баннером и клавиатурой.
    Редактирует ранее отправленное сообщение, подставляя новое изображение, описание и кнопки.

    :param callback: Объект MenuCallBack, например, с data "menu:1:vocabulary::1" и т.д.
    :param state: Контекст состояния FSM с различными ключами
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Распаковываем MenuCallBack-объект для корректного доступа к его атрибутам
    callback_data = MenuCallBack.unpack(callback.data)

    # Сохраняем в атрибутах бота основное сообщение, чтобы потом можно было его редактировать
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

    # Получаем новый контент для баннера
    media, reply_markup = await get_menu_content(
        bot=bot, session=session, state=state, level=callback_data.level, menu_name=callback_data.menu_name,
        page=callback_data.page, menu_details=callback_data.menu_details,
        user_id=bot.auth_user_id.get(callback.message.chat.id, None), callback=callback
    )

    # Редактируем основной баннер
    try:
        await callback.message.edit_media(media=media, reply_markup=reply_markup)
    except (Exception, ) as e:
        print(e)
