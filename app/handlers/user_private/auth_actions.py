"""
Обработка действий с auth и профилем пользователя.
Sign in, Log in, Log out, Reset password; настройки профиля - выбор голоса для озвучивания и скорости речи,
статистика пользователя (просмотр + отправка сводного .xls-файла с попытками Attempt и отчётами Report), выгрузка
всей базы данных пользователя: словарь + заметки + отчёты.

INFO:
1. Для разделения контроллеров при обработке одинаковых состояний AuthFSM используется фильтр IsKeyInStateFilter.
   При запросе sign in в FSMContext сохраняется ключ is_sign_in=True (до завершения регистрации или отмены).
2. Смена пароля делается по кнопке "Забыл пароль" при log in. Токен для сброса отправляется на почту пользователя,
   действителен в течение 10 минут. Через 10 минут удаляется из БД (настроен планировщик задач с очисткой таблицы).
3. При прохождении аутентификации пользователь сохраняется в bot.auth_user_id[message.chat.id] = User.id. По наличию
   записи в этом атрибуте и проверяется аутентификация.
4. Также после успешной аутентификации связка User.id и ID чата записывается в БД. При последующих запусках приложения
   пользователь будет определяться автоматически на основе этой записи для используемого чата. Запись сохраняется
   в течение 90 дней. Если пользователь выходит из учётной записи и проходит аутентификацию с новыми данными - старая
   запись будет удалена и автоматическая аутентификация будет срабатывать по новым данным (Таблица UserChat).
5. Обработка входа в профиль осуществляется через menu_processing.py
6. При входе в профиль (или завершении регистрации Sign in) в state сохраняется ключ user=<User object> для доступа
   к данным.
7. При регистрации нового пользователя автоматически создаётся запись в таблице с настройками пользователя
   UserSettings, данные там проставляются по умолчанию, доступны для редактирования в профиле.
8. В профиле пользователя доступны настройки воспроизведения аудио (голос и скорость речи). В режиме 'random' при
   создании каждого аудио файла выбирается случайный голос из списка.
9. В разделе со статистикой помимо просмотра доступна выгрузка отчёта в .xls-файле (сводного .xls-файла с попытками
   Attempt и отчётами Report) и выгрузка всей базы данных (словарь + заметки + отчёты).
"""
import os
import re

from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details as bnr
from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.keyboards.inlines import get_auth_btns, get_inline_btns, get_kbds_with_navi_header_btns, get_pagination_btns
from app.common.fsm_classes import UserSettingsFSM, AuthFSM
from app.common.tools import try_alert_msg, clear_all_data, update_user_chat_data, clear_auxiliary_msgs_in_chat
from common.msg_templates import report_msg_template, oops_with_error_msg_template, action_cancelled_msg_template, \
    oops_try_again_msg_template
from app.utils.menu_processing import auth_page, start_page
from common.tools import send_email_reset_psw_token
from app.utils.custom_bot_class import Bot
from app.utils.paginator import Paginator, pages
from app.utils.tts import speak_text
from app.utils.tts_voices import all_voices_en_US_ShortName_list
from app.utils.xsl_tools import export_statistic_data_to_xls, export_all_user_data_to_xls
from app.settings import PER_PAGE_STAT_REPORTS, PATTERN_EMAIL, MIN_USER_PSW_LENGTH, PATTERN_SPEECH_RATE, \
    PER_PAGE_VOICE_SAMPLES, VOICE_SAMPLES_TEXT


# Создаём роутер для приватного чата бота с пользователем
auth_router = Router()

# Настраиваем фильтр, что строго приватный чат
auth_router.message.filter(ChatTypeFilter(['private']))


# SIGN IN - Регистрация нового пользователя

# Регистрация нового пользователя, Шаг 1 - Запрос логина (email).
@auth_router.callback_query(F.data.contains('sign_in_app'), StateFilter(None))
async def sign_in_ask_email(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Начало регистрации нового пользователя. Запрос логина (email).

    :param callback: Callback запрос формата "menu:1:auth:sign_in_app:1"
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет состояние на AuthFSM.email, изменяет баннер
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Изменение баннера и клавиатуры
    media, reply_markup = await auth_page(session, 'sign_in_app')
    try:
        await callback.message.edit_media(media=media, reply_markup=reply_markup)
    except (Exception, ) as e:
        print(e)

    # Сохраняем баннер для редактирования, клавиатуру
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message
    bot.reply_markup_save[callback.message.chat.id] = reply_markup

    # Добавляем значение is_sign_in в контекст состояния для дальнейшей фильтрации
    await state.update_data(is_sign_in=True)

    # Меняем состояние
    await state.set_state(AuthFSM.email)


# Регистрация нового пользователя, Шаг 2 - запрос пароля.
@auth_router.message(AuthFSM.email, F.text, IsKeyInStateFilter('is_sign_in'))
async def sign_in_get_login_wait_psw(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Продолжение регистрации нового пользователя - запрос пароля.

    :param message: Текстовое сообщение с email
    :param state: Контекст состояния с ключом is_sign_in=True
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет состояние на AuthFSM.password, изменяет баннер
    """

    # Проверяем валидность введённого email. При некорректном вводе сообщаем об этом и выходим из функции
    if not re.match(PATTERN_EMAIL, message.text):
        msg_text = '⚠️ Упс! Почта введена некорректно. Повторите ввод.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Записываем полученную почту в контекст
    await state.update_data(email=message.text)

    # Сохраняем объект сообщения для дальнейшего удаления
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Редактируем баннер
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=bnr.sign_in_step_2, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Меняем состояние
    await state.set_state(AuthFSM.password)


# Регистрация нового пользователя, Шаг 3 - запрос подтверждения пароля.
@auth_router.message(AuthFSM.password, F.text, IsKeyInStateFilter('is_sign_in'))
async def sign_in_get_psw_wait_confirm(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Продолжение регистрации нового пользователя - запрос подтверждения пароля.

    :param message: Текстовое сообщение с паролем
    :param state: Контекст состояния с ключом is_sign_in=True
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет состояние на AuthFSM.confirm_password, изменяет баннер
    """

    # Проверяем валидность введённого пароля. При некорректном вводе сообщаем об этом и выходим из функции
    if len(message.text) < MIN_USER_PSW_LENGTH:
        msg_text = f'⚠️ Упс! Пароль должен содержать не менее {MIN_USER_PSW_LENGTH} символов. Повторите ввод.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Записываем введённый пароль в переменную
    psw = message.text
    AuthFSM.psw_first_input = psw

    # Удаляем сообщение с паролем из чата и переотправляем его скрытым + сохраняем во вспомогательные
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    message = await message.answer(f"Ваш пароль: ||{psw}||\n\n *Подтвердите пароль повторным вводом*",
                                   parse_mode=ParseMode.MARKDOWN_V2)
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Редактируем баннер
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=bnr.sign_in_step_3, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Устанавливаем состояние ввода подтверждения пароля
    await state.set_state(AuthFSM.confirm_password)


# Регистрация нового пользователя, Шаг 4 - подтверждение пароля, создание пользователя в БД.
@auth_router.message(AuthFSM.confirm_password, F.text, IsKeyInStateFilter('is_sign_in'))
async def sign_in_get_confirm_psw(message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Регистрация нового пользователя в системе, завершение, создание нового пользователя User в БД.
    Фиксирует привязку пользователя к ID чата в БД.

    :param message: Текстовое сообщение с подтверждением пароля
    :param state: Контекст состояния с ключом is_sign_in=True
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но создаёт нового пользователя User в БД, аутентифицирует его в системе.
    """

    # Проверяем, что подтверждение сошлось с первым вводом. Выходим из обработчика при ошибке
    if message.text != AuthFSM.psw_first_input:
        msg_text = '⚠️ Упс! Пароль для подтверждения отличается от введённого ранее. Повторите ввод.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Записываем пароль в контекст и удаляем сообщение
    await state.update_data(password=message.text)
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Забираем данные из контекста и создаем нового пользователя
    data = await state.get_data()
    try:
        new_user = await DataBase.create_user(session, data)
    except (Exception, ):
        msg_text = ('⚠️ Упс! Что-то пошло не так. '
                    'Попробуйте ещё раз или восстановите пароль, если учётная запись уже есть.')
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # При успехе авторизуем его в системе
    if new_user:
        await try_alert_msg(bot, message.chat.id, f'Пользователь {data["email"]} успешно зарегистрирован!')
        bot.auth_user_id[message.chat.id] = new_user.id

        # Редактируем баннер
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=bnr.sign_in_step_4.format(email=data['email']), reply_markup=get_auth_btns(profile=True)
        )

        # Чистим состояние, атрибуты, удаляем временные сообщения
        await clear_all_data(bot, message.chat.id, state)
        AuthFSM.psw_first_input = None

        # Добавляем в контекст ключ user с созданным объектом User для доступа к данным при работе в профиле
        await state.update_data(user=new_user)

        # Обновляем данные привязки чата к пользователю
        await update_user_chat_data(session, message.chat.id, new_user.id)

    # При неудаче создания пользователя User сообщаем об этом
    else:
        msg_text = ('⚠️ Упс! Что-то пошло не так. '
                    'Попробуйте ещё раз или восстановите пароль, если учётная запись уже есть.')
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

        # INFO: Состояние FSM не меняется, продолжается ожидание ввода подтверждения пароля


# Отмена регистрации пользователя - возврат в начало регистрации
@auth_router.callback_query(F.data == 'cancel_auth', StateFilter('*'), IsKeyInStateFilter('is_sign_in'))
async def cancel_sign_in(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Отмена регистрации пользователя и откат в начало регистрации.

    :param callback: Callback запрос формата "cancel_auth"
    :param state: Контекст состояния с ключом is_sign_in=True
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но меняет состояние на AuthFSM.email, баннер, сбрасывает контекст
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Чистим состояние, атрибуты, удаляем временные сообщения
    await clear_all_data(bot, callback.message.chat.id, state)
    AuthFSM.psw_first_input = None

    # Редактируем баннер, откатывая на начало регистрации
    media, reply_markup = await auth_page(session, 'sign_in_app')
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_media(media=media, reply_markup=reply_markup)

    # Сохраняем клавиатуру
    bot.reply_markup_save[callback.message.chat.id] = reply_markup

    # Добавляем значение is_sign_in в контекст состояния для дальнейшей фильтрации
    await state.update_data(is_sign_in=True)

    # Устанавливаем состояние ввода email
    await state.set_state(AuthFSM.email)


# LOG OUT - выход пользователя из учётной записи

# Выход из учётной записи - ШАГ 1, запрос подтверждения
@auth_router.callback_query(F.data.contains('log_out_ask_confirm'))
async def log_out_ask_confirm(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Отправка сообщения для подтверждения выхода из учётной записи.

    :param callback: Callback запрос формата "menu:1:auth:log_out_ask_confirm:1"
    :param bot: Объект бота
    :return: None
    """

    # Формируем сообщение
    msg_text = '⚠️ Вы уверены, что хотите выйти из профиля?'
    btns = {
        'Подтвердить выход ➡': 'logout_get_confirm',
        'Отмена ❌': 'cancel_log_out',
    }
    kbds = get_inline_btns(btns=btns, sizes=(1,))

    # Отправляем в чат сообщение с запросом подтверждения выхода из профиля
    msg = await bot.send_message(chat_id=callback.message.chat.id, text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Выход из учётной записи - отмена, возврат на страницу профиля
@auth_router.callback_query(F.data == 'cancel_log_out')
async def cancel_log_out(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Отмена попытки выхода из учётной записи и откат в начало аутентификации.

    :param callback: Callback запрос формата "cancel_log_out"
    :param bot: Объект бота
    :return: None
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Чистим состояние, атрибуты, удаляем временные сообщения
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)


# Выход из учётной записи - ШАГ 2, подтверждение, выход, возврат на главную страницу
@auth_router.callback_query(F.data.contains('logout_get_confirm'))
async def log_out_get_confirm(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Обработка подтверждения выхода из учётной записи. Возврат на стартовую страницу.

    :param callback: Callback запрос по нажатию inline-кнопки
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет баннер и клавиатуру.
    """

    # Снятие отметки id пользователя в боте и оповещение
    bot.auth_user_id[callback.message.chat.id] = None
    await callback.answer('⚠️ Вы вышли из учётной записи!', show_alert=True)

    # Возврат на главную страницу, редактирование баннера
    media, reply_markup = await start_page(bot, session, state, callback, bot.auth_user_id[callback.message.chat.id])
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_media(media=media, reply_markup=reply_markup)


# LOG IN - вход пользователя в учётную запись

# Вход в учётную запись - ШАГ 1, запрос логина
@auth_router.callback_query(F.data.contains('log_in_app'), StateFilter(None))
async def log_in_start_ask_email(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Начало аутентификации пользователя. Запрос логина (email).

    :param callback: Callback запрос формата "menu:1:auth:log_in_app:1"
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет баннер и клавиатуру, состояние
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Редактируем баннер и клавиатуру
    media, reply_markup = await auth_page(session, 'log_in_app')
    await callback.message.edit_media(media=media, reply_markup=reply_markup)

    # Сохраняем баннер для редактирования, клавиатуру
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message
    bot.reply_markup_save[callback.message.chat.id] = reply_markup

    # Устанавливаем состояние ввода email
    await state.set_state(AuthFSM.email)


# Вход в учётную запись - ШАГ 2, запрос пароля
@auth_router.message(AuthFSM.email, F.text)
async def log_in_get_email_ask_psw(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Продолжение аутентификации пользователя - запрос пароля.

    :param message: Сообщение с логином (email)
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но меняет баннер, состояние, отправляет запрос пароля
    """

    # Проверяем валидность введённого email. При некорректном вводе сообщаем об этом и выходим из функции
    if not re.match(PATTERN_EMAIL, message.text):
        msg_text = '⚠️ Упс! Почта введена некорректно. Повторите ввод.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Записываем в контекст введённую почту, сохраняем полученное сообщение
    await state.update_data(email=message.text)
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=bnr.log_in_step_2, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Устанавливаем состояние ввода пароля
    await state.set_state(AuthFSM.password)


# Вход в учётную запись - ШАГ 3, аутентификация пользователя в системе
@auth_router.message(AuthFSM.password, F.text)
async def log_in_get_psw(message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Завершение аутентификации пользователя, вход в систему.
    Фиксирует привязку пользователя к ID чата в БД.

    :param message: Сообщение с паролем
    :param state: Контекст состояния
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но аутентифицирует пользователя, меняет баннер, чистит состояние и сообщения
    """

    # Записываем введённый пароль в контекст
    psw = message.text
    await state.update_data(password=psw)

    # Пробуем найти пользователя по переданным email и паролю
    data = await state.get_data()
    user_id: int = await DataBase.get_user_id_by_data(session, data)

    # Если пользователь найден в БД:
    if user_id:

        # Сохраняем id пользователя в боте для отметки пройденной аутентификации
        bot.auth_user_id[message.chat.id] = user_id

        # Обновляем данные привязки чата к пользователю
        await update_user_chat_data(session, message.chat.id, user_id)

        # Чистим состояние, атрибуты, удаляем временные сообщения
        await clear_all_data(bot, message.chat.id, state)

        # Отправляем сообщение об успешном входе
        msg_text = f'✅ Вы вошли в систему как {data["email"]}!'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

        # Переходим на главную страницу, редактируем баннер и клавиатуру
        media, reply_markup = await start_page(bot, session, state, bot.auxiliary_msgs['cbq'][message.chat.id], user_id)
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_media(media=media, reply_markup=reply_markup)

    # Если пользователь не найден в БД, отправляем сообщение об ошибке
    else:
        msg_text = '⚠️ Упс! Неверный email или пароль. Попробуйте ввести пароль ещё раз.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Удаляем сообщение с паролем
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


# Отмена попытки входа в учётную запись
@auth_router.callback_query(F.data == 'cancel_auth', StateFilter('*'))
async def cancel_log_in(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Отмена попытки аутентификации пользователя и откат в начало аутентификации.

    :param callback: Callback запрос формата "cancel_auth"
    :param state: Контекст состояния (БЕЗ ключа is_sign_in)
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но меняет баннер, клавиатуру, чистит контекст и сообщения
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Чистим состояние, атрибуты, удаляем временные сообщения
    await clear_all_data(bot, callback.message.chat.id, state)

    # Редактируем баннер, клавиатуру
    media, reply_markup = await auth_page(session, 'log_in_app')
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_media(media=media, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass

    # Сохраняем клавиатуру, callback
    bot.reply_markup_save[callback.message.chat.id] = reply_markup
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Устанавливаем состояние ввода email
    await state.set_state(AuthFSM.email)


# RESET PASSWORD - сброс и переустановка пароля пользователя

# Сброс пароля - ШАГ 1: отправка письма с ключом сброса и запрос его ввода
@auth_router.callback_query(F.data.contains('reset_password'))
async def reset_password_start(
        callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Сброс пароля - ШАГ 1. Формирование ключа для сброса пароля, отправка его в письме и запрос ввода.

    :param callback: Callback запрос по нажатию inline-кнопки
    :param state: Контекст состояния
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но формирует и отправляет ключ для сброса пароля, меняет состояние
    """

    # Проверяем, есть ли в контексте есть почта
    data = await state.get_data()
    if data.get('email'):

        # Редактируем баннер и клавиатуру
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
            caption=bnr.new_psw_step_1, reply_markup=bot.reply_markup_save[callback.message.chat.id]
        )

        # Если токен существует, то отправляем его, если нет, то генерируем и отправляем
        psw_token = await DataBase.get_token_pass_reset_by_email(session, data['email'])
        if psw_token:
            send_email_reset_psw_token(to_email=data['email'], reset_token=psw_token)
        else:
            reset_object = await DataBase.create_token_reset_psw(session, data)
            send_email_reset_psw_token(to_email=reset_object.email, reset_token=reset_object.reset_token)

        # Отправляем сообщение с инструкцией
        await callback.answer('✅ На указанный email отправлено письмо с ключом для сброса пароля.\n '
                              'Введите полученный ключ в течение 10 минут', show_alert=True)

        # Устанавливаем состояние ввода ключа сброса
        await state.set_state(AuthFSM.reset_pass_token)

    # Если в контексте нет почты
    else:
        await callback.answer('⚠️ Сначала введите свой email, затем нажмите "Забыл пароль"', show_alert=True)


# Сброс пароля - ШАГ 2: проверка ключа сброса и запрос нового пароля
@auth_router.message(AuthFSM.reset_pass_token, F.text)
async def reset_password_get_token_ask_new_psw(
        message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Сброс пароля - ШАГ 2: проверка ключа сброса и запрос нового пароля.

    :param message: Сообщение с ключом подтверждения сброса из письма
    :param state: Контекст состояния
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но сверяет полученный ключ с тем что в базе, меняет состояние
    """

    # Записываем введённый ключ в контекст, сохраняем сообщение
    entered_token = message.text
    await state.update_data(reset_pass_token=entered_token)
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Получаем ключ из БД для указанной почты
    data = await state.get_data()
    supposed_token: str | None = await DataBase.get_token_pass_reset_by_email(session, data['email'])

    # Если введённый ключ не совпадает с тем что в базе, отправляем сообщение и выходим из обработчика.
    # Состояние не меняется, можно сразу повторить попытку ввода
    if supposed_token != entered_token:
        msg_text = '⚠️ Упс! Неверный ключ. Попробуйте ввести ключ ещё раз'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Если ключ совпадает с тем что в базе, редактируем баннер
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=bnr.new_psw_step_2, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Устанавливаем состояние ввода нового пароля
    await state.set_state(AuthFSM.new_password)


# Сброс пароля - ШАГ 3: запрос подтверждения нового пароля
@auth_router.message(AuthFSM.new_password, F.text)
async def reset_password_get_new_psw_ask_confirm(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Сброс пароля - ШАГ 3: запрос подтверждения нового пароля.

    :param message: Сообщение с новым паролем
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: Функция ничего не возвращает, но редактирует баннер, отправляет запрос, меняет состояние
    """

    # Записываем введённый новый пароль в переменную для дальнейшей сверки
    new_psw = message.text
    AuthFSM.psw_first_input = new_psw

    # Удаляем сообщение с паролем из чата и переотправляем его скрытым
    msg = await message.answer(
        f"Новый пароль: ||{new_psw}||\n\n *Подтвердите пароль повторным вводом*", parse_mode=ParseMode.MARKDOWN_V2
    )
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(msg)
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Редактируем баннер
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=bnr.new_psw_step_3, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Устанавливаем состояние ввода подтверждения нового пароля
    await state.set_state(AuthFSM.confirm_new_password)


# Сброс пароля - ШАГ 4: подтверждение нового пароля, изменение хеша пароля в БД
@auth_router.message(AuthFSM.confirm_new_password, F.text)
async def reset_password_get_new_psw_confirm(
        message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Сброс пароля - ШАГ 4: подтверждение нового пароля, изменение хеша пароля в БД.

    :param message: Сообщение с подтверждением нового пароля
    :param state: Контекст состояния
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: Функция ничего не возвращает, но сверяет пароли, записывает новый пароль в БД, чистит состояние и сообщения
    """

    # Записываем введённый подтверждённый пароль
    new_psw_confirm = message.text

    # При несовпадении с первым вводом отправляем сообщение и выходим из обработчика
    if new_psw_confirm != AuthFSM.psw_first_input:
        msg_text = '⚠️ Упс! Пароль для подтверждения отличается от введённого ранее. Повторите ввод'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Если пароль подтверждён, записываем его и забираем все данные из контекста
    await state.update_data(password=new_psw_confirm)
    data = await state.get_data()

    # Пробуем изменить пароль у пользователя
    user = await DataBase.user_change_password(session, data)

    # Если смена пароля прошла успешно:
    if user:

        # Редактируем баннер и клавиатуру
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=bnr.new_psw_step_4.format(email=data['email']), reply_markup=get_auth_btns(profile=True)
        )

        # Обновляем id авторизованного пользователя, входим в систему
        bot.auth_user_id[message.chat.id] = user.id

        # Чистим состояние, атрибуты, удаляем временные сообщения
        await clear_all_data(bot, message.chat.id, state)
        AuthFSM.psw_first_input = None

        # Записываем пользователя в контекст
        await state.update_data(user=user)

    # Если смена пароля не удалась, отправляем сообщение и сохраняем его
    else:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)

    # Удаляем сообщение с паролем из чата
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


# USER SETTINGS

# Страница профиля с отображением настроек пользователя и доступом к редактированию
@auth_router.callback_query(F.data == 'user_settings', IsKeyInStateFilter('user'))
async def show_user_settings(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Страница профиля с отображением настроек пользователя и доступом к редактированию.

    :param callback: Callback-запрос формата "user_settings"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """

    # Забираем данные о пользователе из контекста
    state_data = await state.get_data()
    user = state_data.get('user')

    # Забираем из БД информацию о настройках пользователя
    settings_data = await DataBase.get_user_settings(session, user.id)

    # Формируем клавиатуру настроек профиля
    btns = {
        '🔹 Изменить скорость речи': 'change_speech_rate',
        '🔹 Изменить голос': 'change_voice',
    }
    kbds = get_kbds_with_navi_header_btns(btns=btns, level=2, menu_name='auth', sizes=(2, 1))
    bot.reply_markup_save[callback.message.chat.id] = kbds

    # Редактируем баннер и клавиатуру
    caption = bnr.user_profile_settings.format(
        email=user.email, speech_rate=settings_data.speech_rate, voice=settings_data.voice
    )
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)


# Изменить скорость речи - ШАГ 1, запрос ввода нового значения скорости
@auth_router.callback_query(F.data == 'change_speech_rate', IsKeyInStateFilter('user'))
async def change_speech_rate_ask_value(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Изменение настроек скорости воспроизведения речи, ШАГ 1. Запрос ввода нового значения скорости.

    :param callback: Callback-запрос формата "change_speech_rate"
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Отправляем сообщение с инструкцией и кнопкой отмены изменений
    msg_text = ("<b>Изменение скорости речи</b>\n\n"
                "Скорость устанавливается в процентах: от '-100%'(очень медленно) до '+100%'(очень быстро).\n\n" 
                "Введите желаемое значение <i>(без '%', только знак и число, без пробела)</i>\n"
                "Для нормального воспроизведения речи установите значение '+0'.\n\n"
                )
    kbds = get_inline_btns(btns={'Отмена ❌': 'cancel_user_settings'})
    msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ввода скорости речи
    await state.set_state(UserSettingsFSM.speech_rate)


# Универсальный обработчик отмены изменений настроек профиля.
@auth_router.callback_query(F.data == 'cancel_user_settings', IsKeyInStateFilter('user'))
async def cancel_user_settings_update(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Универсальный обработчик отмены изменений настроек профиля. Сбрасывает состояния ввода и удаляет сообщения из чата.

    :param callback: Callback-запрос формата "cancel_user_settings"
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """
    await state.set_state(None)                                                     # Сбрасываем state
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)               # Удаляем вспомогательные сообщения


# Изменить скорость речи - ШАГ 2, обработка ввода нового значения
@auth_router.message(UserSettingsFSM.speech_rate, IsKeyInStateFilter('user'))
async def change_speech_rate_get_value(
        message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Изменение настроек скорости воспроизведения речи, ШАГ 2. Обработка ввода нового значения.

    :param message: Текстовое сообщение с новой скоростью
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем сообщение во вспомогательном словаре
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Забираем данные о пользователе из контекста
    state_data = await state.get_data()
    user = state_data.get('user')

    # Проверяем корректность ввода данных, если некорректно - выходим из обработчика
    if not re.match(PATTERN_SPEECH_RATE, message.text):
        msg_text = '⚠️ Упс! Некорректные данные. Повторите ввод'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    # Обновляем значение в таблице с настройками
    is_updated = await DataBase.update_user_settings(session, user.id, speech_rate=f'{message.text}%')

    # При успехе отправляем уведомление и обновляем баннер
    if is_updated:
        await try_alert_msg(bot, message.chat.id, f'✅ Настройки изменены!')
        settings_data = await DataBase.get_user_settings(session, user.id)
        caption = bnr.user_profile_settings.format(
            email=user.email, speech_rate=settings_data.speech_rate, voice=settings_data.voice
        )
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=caption, reply_markup=bot.reply_markup_save[message.chat.id]
        )

    # При ошибке отправляем уведомление
    else:
        msg_text = oops_try_again_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Сбрасываем состояние ввода и удаляем вспомогательные сообщения
    await state.set_state(None)
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)


# Изменить голос - ШАГ 1, отправка образцов голосов озвучки для выбора
@auth_router.callback_query(F.data.contains('change_voice'), IsKeyInStateFilter('user'))
async def change_voice_ask_value(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Изменение голоса озвучки аудио - ШАГ 1, отправка образцов голосов озвучки для выбора.

    :param callback: Callback-запрос формата "change_voice", "change_voice_page_<page_number>"
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Определяем страницу пагинации.
    if '_page_' in callback.data:                   # Если callback был вызван из пагинации, забираем страницу из него
        page = int(callback.data.split('_')[-1])
    else:                                           # Иначе устанавливаем страницу 1
        page = 1

    # Настраиваем пагинацию и получаем срез голосов для текущей страницы
    per_page = PER_PAGE_VOICE_SAMPLES
    paginator = Paginator(list(all_voices_en_US_ShortName_list), page=page, per_page=per_page)
    voices_on_current_page: list = paginator.get_page()

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    #  Отправляем информационное сообщение с инструкцией, установкой разноголосой озвучки и кнопкой отмены
    info_msg_text = (f'<b>Изменение голоса.\n\n</b>Вы можете установить случайный выбор для озвучки разными голосами '
                     f'или выберите конкретный голос из списка ниже:\n')
    btns = {'✨ Установить разноголосую озвучку': 'apply_voice:random', 'Отмена ❌': 'cancel_user_settings'}
    kbds = get_inline_btns(btns=btns, sizes=(1, ))
    msg = await callback.message.answer(text=info_msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Отправляем сообщения с образцами голосов текущей страницы
    for voice in voices_on_current_page:
        msg = await callback.message.answer(
            text=voice,
            reply_markup=get_inline_btns(btns={
                '🎧 Прослушать': f'play_voice:{voice}',
                'Применить ✅': f'apply_voice:{voice}',
            })
        )
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Отправляем информационное сообщение с пагинацией
    voices_count = len(all_voices_en_US_ShortName_list)                         # Всего голосов
    first_voice: int = ((page - 1) * per_page) + 1                              # Номер первой отображаемой записи
    last_voice: int = len(voices_on_current_page) - 1 + first_voice             # Номер последней отображаемой записи
    pagi_kbds = get_pagination_btns(page=page, pagination_btns=pages(paginator), custom_cb_data='change_voice')
    info_msg_text = f'<b>Всего записей:</b> {voices_count}\n' \
                    f'<b>Показаны записи:</b> {first_voice} - {last_voice}'
    msg = await callback.message.answer(text=info_msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Изменить голос - ШАГ 1.5, прослушать образец голоса
@auth_router.callback_query(F.data.contains('play_voice:'), IsKeyInStateFilter('user'))
async def play_voice_sample(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Изменение голоса озвучки аудио - прослушивание образца голоса.
    Функция отправляет в чат образец аудио (автоматически удаляется через 15 секунд).

    :param callback: Callback-запрос формата "play_voice:<voice_name>"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """

    # Забираем название озвучки из callback
    voice_name = callback.data.split(':')[1]

    # Отправляем аудио с образцом в чат
    text_to_speak = f'{VOICE_SAMPLES_TEXT}. {voice_name.split("-")[-1]}'
    await speak_text(
        text_to_speak, bot, callback.message.chat.id, is_with_title=False,
        state=state, session=session, test_voice=voice_name
    )


# Изменить голос - ШАГ 2, подтверждение нового голоса
@auth_router.callback_query(F.data.contains('apply_voice:'), IsKeyInStateFilter('user'))
async def change_voice_get_value(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Изменение голоса озвучки аудио - ШАГ 2. Подтверждение выбора голоса, сохранение нового голоса в базе.

    :param callback: Callback-запрос формата "apply_voice:<voice_name>"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """

    # Забираем название озвучки из callback
    voice_name = callback.data.split(':')[1]

    # Забираем данные о пользователе из контекста
    state_data = await state.get_data()
    user = state_data.get('user')

    # Обновляем значение голоса в таблице с настройками UserSettings
    try:
        is_updated = await DataBase.update_user_settings(session, user.id, voice=voice_name)
    except (Exception, ) as e:
        await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)
        return

    # При успехе отправляем уведомление, обновляем описание баннера и очищаем чат
    if is_updated:
        await callback.answer(text='✅ Настройки изменены!', show_alert=True)
        await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

        settings_data = await DataBase.get_user_settings(session, user.id)
        caption = bnr.user_profile_settings.format(
            email=user.email, speech_rate=settings_data.speech_rate, voice=settings_data.voice
        )
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
            caption=caption, reply_markup=bot.reply_markup_save[callback.message.chat.id]
        )
    else:
        await callback.answer(text=oops_try_again_msg_template, show_alert=True)


# STATISTIC

# Просмотр отчётов прохождения тестов + выгрузка отчетов статистики и общей базы данных пользователя
@auth_router.callback_query(F.data.startswith('statistic_page_'), IsKeyInStateFilter('user'))
async def show_statistic_reports(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Просмотр отчётов прохождения тестов + выгрузка отчетов статистики и общей базы данных пользователя.

    :param callback: Callback-запрос формата "statistic_page_<page_number>"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключами user и all_user_reports(при пагинации)
    :param bot: Объект бота
    :return: None
    """

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о пользователе и отчётах (при пагинации)
    state_data = await state.get_data()
    user = state_data.get('user')
    all_user_reports = state_data.get('all_user_reports')

    # Загружаем в контекст информацию о callback текущей страницы (для обработки возврата)
    await state.update_data(show_statistic_reports_cbq=callback.data)

    # Обновляем описание баннера и клавиатуру
    caption = bnr.user_profile_stat.format(email=user.email)
    btns = {
        'Сформировать отчёт .xls 📊': 'create_statistic_report',
        'Выгрузить базу пользователя .xls 💾': 'export_all_user_data'
    }
    kbds = get_kbds_with_navi_header_btns(level=2, menu_name='auth', btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception,) as e:
        print(e)

    # При входе в раздел забираем отчёты из БД и сохраняем их в контекст под ключом all_user_reports=
    if not all_user_reports:
        all_user_reports = await DataBase.get_user_reports(session, user_id=user.id, is_desc=True)
        await state.update_data(all_user_reports=all_user_reports)

    # Определяем страницу пагинации из callback
    page = int(callback.data.split('_')[-1])

    # Настраиваем пагинацию
    per_page = PER_PAGE_STAT_REPORTS
    paginator = Paginator(list(all_user_reports), page=page, per_page=per_page)
    reports_on_current_page: list = paginator.get_page()

    # Создаём пользовательский счетчик отчётов для отображения (ID из БД общее для всех Users)
    reports_total = len(all_user_reports)
    user_report_id = reports_total - (per_page * (page - 1))

    # Отправляем информационное сообщение с пагинацией
    first_report: int = ((page - 1) * per_page) + 1
    last_report: int = len(reports_on_current_page) - 1 + first_report
    msg_text = f'<b>Всего отчётов:</b> {reports_total}\n<b>Показаны отчёты:</b> {first_report} - {last_report}\n'
    pagi_kbds = get_pagination_btns(pagination_btns=pages(paginator), page=page, custom_cb_data='statistic')
    msg = await callback.message.answer(text=msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Отправляем сообщения с отчётами
    for report in reports_on_current_page:
        topic_name = report.topic_name if report.topic_name else '-'
        msg = await callback.message.answer(
            text=report_msg_template.format(
                report_number=user_report_id, report_date=report.created, test_type=report.test_type,
                topic_name=topic_name, total_attempts=report.total_attempts, correct_attempts=report.correct_attempts,
                total_words=report.total_words, result_percentage=report.result_percentage)
        )
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
        user_report_id -= 1

    # Дублируем информационное сообщение с пагинацией
    msg = await callback.message.answer(text=msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Сформировать и отправить отчёт .xls со статистикой или полную базу пользователя(словарь + заметки + отчёты)
@auth_router.callback_query(F.data.startswith('create_statistic_report') | F.data.startswith('export_all_user_data'),
                            IsKeyInStateFilter('user'))
async def create_statistic_report(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Сформировать и отправить отчёт .xls со статистикой отчётов Report и попыток прохождения тестов Attempt.

    :param callback: Callback-запрос формата "create_statistic_report"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключами user, all_user_reports и show_statistic_reports_cbq
    :param bot: Объект бота
    :return: None
    """

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о пользователе, отчётах Report и callback_data для возврата на предыдущую страницу
    state_data = await state.get_data()
    user = state_data.get('user')
    all_user_reports = state_data.get('all_user_reports')
    show_statistic_reports_cbq = state_data.get('show_statistic_reports_cbq')

    # Создаём отчёт .xls со статистикой
    all_user_reports = list(all_user_reports)
    all_user_reports.reverse()

    # Формируем необходимый xlsx-файл в зависимости от запроса в callback
    path_to_file = ''
    if callback.data == 'create_statistic_report':
        path_to_file = await export_statistic_data_to_xls(session, user.id, all_user_reports)
    elif callback.data == 'export_all_user_data':
        chat_id = callback.message.chat.id
        path_to_file = await export_all_user_data_to_xls(session, bot, chat_id, user.id, all_user_reports)

    # Отправляем отчёт в чат
    with open(path_to_file, 'rb') as f:
        data_file = types.BufferedInputFile(f.read(), filename=os.path.basename(path_to_file))
    kbds = get_inline_btns(btns={'Вернуться к просмотру отчётов?': show_statistic_reports_cbq})
    msg = await bot.send_document(chat_id=callback.message.chat.id, document=data_file, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Удаляем xlsx-файл из системы после отправки
    os.remove(path_to_file)
