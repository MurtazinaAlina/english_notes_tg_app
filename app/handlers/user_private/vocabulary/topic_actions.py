"""
Обработка действий в разделе "Словарь".
TOPICS - обработчики действий с темами.

INFO:
1. Содержит универсальные обработчики действий с темами.
   Для разграничения действий контроллеров используются ключи в state FSMContext.
"""
from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DataBase
from app.database.models import Topic
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter, IsKeyNotInStateFilter
from app.keyboards.inlines import get_inline_btns, get_pagination_btns
from app.utils.custom_bot_class import Bot
from app.handlers.user_private.menu_processing import vocabulary
from app.utils.paginator import pages, Paginator
from app.common.fsm_classes import TopicFSM, WordPhraseFSM
from app.common.tools import clear_auxiliary_msgs_in_chat, get_topic_info_for_caption, try_alert_msg, \
    modify_callback_data, validate_topic_name, delete_last_message
from app.common.msg_templates import topic_msg_template, oops_with_error_msg_template, oops_try_again_msg_template, \
    action_cancelled_msg_template
from app.handlers.user_private.tests_actions import tests_ask_select_topic
from app.handlers.user_private.add_word_phrase_actions import add_word_ask_topic
from app.settings import PER_PAGE_TOPICS


# Создаём роутер для приватного чата бота с пользователем
topic_router = Router()

# Настраиваем фильтр, что строго приватный чат
topic_router.message.filter(ChatTypeFilter(['private']))


# ПОИСК ТЕМЫ

# Запрос ввода ключевого текста для фильтра по темам (Универсальный обработчик)
@topic_router.callback_query(F.data.startswith('find_topic_by_matches'))
async def find_topic_by_matches_ask_keywords(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Запрос ввода ключевого текста для фильтра по темам.

    Работает для выбора раздела в словаре, тестированиях, добавления нового слова и редактирования существующего.

    :param callback: Callback-запрос формата "find_topic_by_matches"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Отправляем пользователю информационное сообщение с кнопкой отмены
    msg = await callback.message.answer(
        'Введите текст для поиска темы',
        reply_markup=get_inline_btns(btns={'Отмена ❌': 'cancel_find_topic'})
    )
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Устанавливаем состояние ввода ключевого текста
    await state.set_state(TopicFSM.search_keywords)


# Отмена запроса ввода ключевого текста для фильтра по темам (Универсальный обработчик)
@topic_router.callback_query(F.data == 'cancel_find_topic')
async def cancel_find_topic(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Универсальный обработчик отмены ввода ключевого текста для поиска тем.
    Функция отменяет состояние ввода текста для фильтра и удаляет вспомогательные сообщения.

    Работает для выбора раздела в словаре, тестирований, добавления нового слова и редактирования существующего.

    :param callback: Callback-запрос формата "cancel_find_topic"
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Убираем состояние ввода ключевого текста и чистим чат от информационного сообщения
    await state.set_state(None)
    await delete_last_message(bot, callback.message.chat.id)

    # Обрабатываем кейс добавления нового слова (слетает состояние ввода темы)
    state_data = await state.get_data()

    # Логика при создании новой записи WordPhrase
    if state_data.get('add_new_word_key'):
        await add_word_ask_topic(callback, state, session, bot)

    # Логика при редактировании существующей записи WordPhrase
    if state_data.get('word_to_update'):
        await state.set_state(WordPhraseFSM.topic)


# Применение фильтра по темам при выборе раздела в словаре И добавлении нового слова WordPhrase, тестировании.
# НЕ обрабатывает кейс с редактированием слова. Необходимая функция определяется по наличию ключа в контексте.
@topic_router.message(F.text, StateFilter(TopicFSM.search_keywords), IsKeyNotInStateFilter('word_to_update'))
async def find_topic_by_matches_get_keywords(
        message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Применение фильтра по темам при выборе раздела в словаре и добавлении нового слова WordPhrase.
    Функция обрабатывает введённый пользователем тест для фильтра и заново вызывает функцию запроса темы, но уже с
    применением фильтра для клавиатуры (Записывает его в state под ключом search_keywords=).

    НЕ обрабатывает кейс с редактированием слова.
    Необходимая функция определяется по наличию соответствующего ключа в контексте.

    :param message: Сообщение пользователя с текстом для фильтра
    :param session: Пользовательская сессия
    :param state: Контекст состояния (Строго БЕЗ ключа 'word_to_update', ВОЗМОЖНЫ ключи 'add_new_word_key', 'test_type')
    :param bot: Объект бота
    :return: None
    """

    # Забираем ключ для фильтра и добавляем его в контекст
    search_keywords = message.text
    await state.update_data(search_keywords=search_keywords)

    # Сохраняем сообщение во вспомогательные и чистим чат
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # Обрабатываем кейс добавления нового слова
    data = await state.get_data()
    if data.get('add_new_word_key'):
        await add_word_ask_topic(bot.auxiliary_msgs['cbq'][message.chat.id], state, session, bot)
        return

    # Обрабатываем кейс поиска темы при тестировании
    if data.get('test_type'):
        await tests_ask_select_topic(
            callback=bot.auxiliary_msgs['cbq'][message.chat.id], state=state, session=session, bot=bot
        )
        return

    # Обрабатываем кейс выбора раздела в словаре для просмотра записей слов:

    # Обновляем клавиатуру
    media, kbds = await vocabulary(
        bot, session, state=state, level=2, menu_details='select_topic',
        callback=bot.auxiliary_msgs['cbq'][message.chat.id]
    )
    try:
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=bot.auxiliary_msgs['cbq_msg'][message.chat.id].caption, reply_markup=kbds
        )
    except TelegramBadRequest:
        pass

    # Сбрасываем состояние ввода
    await state.set_state(None)


# Отмена фильтра по темам для "Словарь" - "Выбрать тему".
# НЕ обрабатывает отмену фильтра по теме в добавлении и редактировании, тестированиях.
@topic_router.callback_query(
    F.data.contains('cancel_find_topic_by_matches'),
    IsKeyNotInStateFilter('word_to_update', 'add_new_word_key', 'test_type'))
async def cancel_find_topic_by_matches_vcb(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Отмена фильтра по темам для "Словарь" - "Выбрать тему".
    НЕ обрабатывает отмену фильтра по теме в добавлении и редактировании.

    :param callback: Callback-запрос формата "cancel_find_topic_by_matches"
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    await callback.answer('⚠️ Фильтр по теме отменен!', show_alert=True)

    # Убираем из контекста значение ключа с фильтром по теме
    await state.update_data(search_keywords=None)

    # Обновляем клавиатуру
    media, kbds = await vocabulary(
        bot, session, state=state, level=2, menu_details='select_topic',
        callback=bot.auxiliary_msgs['cbq'][callback.message.chat.id]
    )
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
            caption=bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].caption, reply_markup=kbds
        )
    except TelegramBadRequest:
        pass

    # Убираем состояние ввода
    await state.set_state(None)


# ПРОСМОТР ВСЕХ ТЕМ

# Просмотр всех тем пользователя, с inline-кнопками редактирования и удаления
@topic_router.callback_query(F.data.contains('edit_or_delete_topic'), StateFilter(None))
async def show_all_topics(callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Просмотр всех тем пользователя, с пагинацией и доступом к редактированию и удалению.
    (Выбор раздела 'Редактировать/удалить темы 📝').

    :param callback: Callback-запрос формата "edit_or_delete_topic" или "vcb:edit_or_delete_topic:{page}"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :return: None
    """

    # Определяем номер текущей страницы
    if callback.data.startswith('vcb'):                 # Если это callback из MenuCallback, то забираем номер страницы
        page = int(callback.data.split(':')[-1])
        await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)    # Чистим чат от записей предыдущих страниц
    else:
        page = 1

    # Записываем в контекст callback.data текущей страницы (для возврата на страницу после редактирования/отмены)
    await state.update_data(show_topics_cbq=callback.data)

    # Определяем список с темами для текущей страницы
    all_user_topics = await DataBase.get_all_topics(session, bot.auth_user_id[callback.message.chat.id])
    all_user_topics = list(reversed(all_user_topics))
    paginator = Paginator(all_user_topics, page=page, per_page=PER_PAGE_TOPICS)
    current_page_topics: list = paginator.get_page()

    # Выводим в чат темы с описанием и inline-кнопками для редактирования и удаления
    for topic in current_page_topics:
        msg = await callback.message.answer(
            text=topic_msg_template.format(
                topic=topic.name, created=topic.created, updated=topic.updated, words_total=len(topic.word_phrases)
            ),
            reply_markup=get_inline_btns(
                btns={
                    'Изменить 🖌': f'update_topic_{topic.id}',
                    'Удалить 🗑': f'delete_topic_{topic.id}',
                })
        )
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Выводим информационное сообщение с пагинацией и сохраняем его во вспомогательные
    msg_text = '<b>Всего тем:</b> {topics_total}\n<b>Показаны темы:</b> {first_topic} - {last_topic}'
    topic_info_for_caption = await get_topic_info_for_caption(
        all_user_topics, current_page_topics, page, PER_PAGE_TOPICS
    )
    kbds_pagi = get_pagination_btns(page=page, pagination_btns=pages(paginator), menu_details='edit_or_delete_topic')
    msg = await callback.message.answer(text=msg_text.format(**topic_info_for_caption), reply_markup=kbds_pagi)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# УДАЛЕНИЕ ТЕМ

# Удаление темы - ШАГ 1, запрос подтверждения
@topic_router.callback_query(F.data.startswith('delete_topic_'))
async def delete_topic_ask_confirm(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Удаление темы - ШАГ 1, запрос подтверждения.

    :param callback: Callback-запрос формата "delete_topic_{Topic.id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом 'show_topics_cbq' для возврата к последней просмотренной странице
    :param bot: Объект бота
    :return: None
    """

    # Получаем идентификатор темы из callback-запроса и находим тему в БД
    topic_id_to_delete = int(callback.data.replace('delete_topic_', ''))
    topic = await DataBase.get_topic_by_id(session, topic_id_to_delete)

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о странице для возврата при отмене действия
    state_data = await state.get_data()
    show_topics_cbq = state_data.get('show_topics_cbq')

    # Отправляем пользователю информационное сообщение с запросом подтверждения действия или отмены
    msg_text = f'⚠️ Вы действительно хотите удалить тему <b>"{topic.name}"</b>?'
    btns = {'Удалить 🗑': f'confirm_delete_topic_{topic_id_to_delete}', 'Отмена ❌': show_topics_cbq}
    kbds = get_inline_btns(btns=btns)
    msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Удаление темы - ШАГ 2, получение подтверждения и удаление темы из БД
@topic_router.callback_query(F.data.startswith('confirm_delete_topic_'))
async def delete_topic_get_confirm(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Удаление темы - ШАГ 2, получение подтверждения и удаление темы из БД.

    :param callback: Callback-запрос формата "confirm_delete_topic_{Topic.id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом 'show_topics_cbq' для возврата к последней просмотренной странице
    :param bot: Объект бота
    :return: None
    """

    # Получаем идентификатор темы из callback-запроса
    topic_id_to_delete = int(callback.data.replace('confirm_delete_topic_', ''))

    # Удаляем тему из базы данных
    try:
        is_deleted = await DataBase.delete_topic_by_id(session, topic_id_to_delete)
    except Exception as e:
        is_deleted = None
        await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)

    # Выводим сообщение пользователю с результатом и удаляем информационное сообщение
    if is_deleted:
        await callback.answer(f'✅ Тема "{is_deleted}" удалена!', show_alert=True)
        await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)

        # Забираем из контекста данные о странице для возврата при завершении действия
        state_data = await state.get_data()
        show_topics_cbq = state_data.get('show_topics_cbq')

        # Возвращаемся к последней просмотренной странице
        modified_callback = await modify_callback_data(callback, show_topics_cbq)
        await show_all_topics(modified_callback, bot, session, state)

    # При ошибке выводим сообщение пользователю
    else:
        await callback.answer(text=oops_try_again_msg_template, show_alert=True)


# СОЗДАНИЕ ТЕМ

# Создание новой темы. Запрос названия темы
@topic_router.callback_query(F.data == 'add_new_topic')
async def create_topic_ask_name(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Запуск создания новой темы. Запрос названия темы.

    :param callback: Callback-запрос формата "add_new_topic"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Чистим чат от предыдущих сообщений
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)
    if TopicFSM.updating_info_message_with_cancel:                          # Если было переключение с редактирования
        try:
            await bot.delete_message(
                chat_id=TopicFSM.updating_info_message_with_cancel.chat.id,
                message_id=TopicFSM.updating_info_message_with_cancel.message_id
            )
        except (Exception, ):
            pass

    # Если в контексте есть тема для редактирования, то сбрасываем состояние
    data = await state.get_data()
    if data.get('topic_to_update_key'):
        await state.clear()

    # Сохраняем баннер для редактирования и callback
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Запрашиваем название новой темы и сохраняем сообщение
    msg = await callback.message.answer(
        text='Введите название новой темы: ',
        reply_markup=get_inline_btns(btns={'Отмена ❌': 'cancel_create_topic'}, sizes=(1, ))
    )
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ввода названия темы
    await state.set_state(TopicFSM.name)


# Отмена создания новой темы
@topic_router.callback_query(F.data == 'cancel_create_topic')
async def cancel_create_topic(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Отмена создания новой темы.

    :param callback: Callback-запрос формата "cancel_create_topic"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)        # Отправка информационного сообщения
    await state.clear()                                                          # Сброс состояния
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)            # Удаление вспомогательных сообщений


# Создание новой темы по заданному названию.
# НЕ обрабатывает, если в контексте есть тема для редактирования
@topic_router.message(TopicFSM.name, F.text, IsKeyNotInStateFilter('topic_to_update_key'))
async def create_topic_finish(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Создание новой темы по заданному названию.

    :param message: Текстовое сообщение с названием новой темы
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем сообщение с названием темы во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Делаем валидацию названия
    if not validate_topic_name(message.text):
        msg_text = '⚠️ Недопустимое название темы! Введите новое'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем полученное название в переменную
    data = {'name': message.text}

    # Создаем новую тему в базе данных
    topic = await DataBase.create_topic(session, data, user_id=bot.auth_user_id[message.chat.id])

    # Отправляем системное сообщение пользователю при любом результате
    if topic:
        await try_alert_msg(bot, message.chat.id, f'✅ Тема "{topic.name}" создана!', if_error_send_msg=True)
    else:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)

    # Чистим контекст, вспомогательные сообщения
    await state.clear()
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)


# РЕДАКТИРОВАНИЕ ТЕМ

# Редактирование темы. Запрос нового названия темы
@topic_router.callback_query(F.data.startswith('update_topic_'), StateFilter(None))
async def update_topic_start_process(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot)\
        -> None:
    """
    Редактирование темы. Запрос нового названия темы

    :param callback: Callback-запрос формата "update_topic_{id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Получаем id выбранной темы
    topic_id = int(callback.data.replace('update_topic_', ''))

    # Получаем объект Topic выбранной темы из базы
    topic_to_update = await DataBase.get_topic_by_id(session, topic_id)
    if not topic_to_update:
        await callback.answer('⚠️ Тема не найдена!', show_alert=True)
        return

    # Пробросим полученный объект изменяемой темы в контекст под ключом "topic_to_update_key"
    await state.update_data(topic_to_update_key=topic_to_update)

    # Запоминаем объект редактируемого сообщения, чтобы потом его отредактировать в онлайне
    TopicFSM.editing_message = callback.message

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Отправляем сообщение с запросом нового названия и кнопкой отмены действия
    TopicFSM.updating_info_message_with_cancel = await callback.message.answer(
        f'Введите новое название темы для <b>"{topic_to_update.name}"</b>: ',
        reply_markup=get_inline_btns(btns={'Отмена ❌': 'cancel_update_topic'}, sizes=(1, ))
    )

    # Переходим в состояние запроса названия темы
    await state.set_state(TopicFSM.name)


# Отмена редактирования темы
@topic_router.callback_query(F.data == 'cancel_update_topic')
async def cancel_update_topic(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Отмена редактирования темы.

    :param callback: Callback-запрос формата "cancel_update_topic"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    await callback.answer(action_cancelled_msg_template, show_alert=True)       # Отправка информационного сообщения
    await state.set_state(None)                                                  # Сброс состояния
    await bot.delete_message(                                                    # Удаление информационного сообщения
        chat_id=TopicFSM.updating_info_message_with_cancel.chat.id,
        message_id=TopicFSM.updating_info_message_with_cancel.message_id
    )


# Завершение редактирования темы
@topic_router.message(TopicFSM.name, F.text, IsKeyInStateFilter('topic_to_update_key'))
async def update_topic_finish(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Редактирование темы, завершение.

    :param message: Текстовое сообщение с новым названием темы
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем сообщение с новым названием темы во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Делаем валидацию названия
    if not validate_topic_name(message.text):
        msg_text = '⚠️ Недопустимое название темы! Введите новое'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем полученное название в переменную
    data = {'name': message.text}

    # Получаем объект темы из контекста
    context = await state.get_data()
    updating_topic: Topic = context.get('topic_to_update_key')

    # Обновляем название темы в базе данных
    try:
        success = await DataBase.update_topic_by_id(session, updating_topic.id, data)
    except Exception as e:
        success = False
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # При успехе:
    if success:

        # Загружаем обновленные данные темы
        updated_topic = await DataBase.get_topic_by_id(session, updating_topic.id)

        try:
            # Отправляем системное сообщение пользователю
            msg_text = f'✅ Тема изменена на "{updated_topic.name}"!'
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

            # Удаляем информационное сообщение + сообщение с темой
            await bot.delete_message(
                chat_id=TopicFSM.updating_info_message_with_cancel.chat.id,
                message_id=TopicFSM.updating_info_message_with_cancel.message_id
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            # Редактируем сообщение с изменённой темой
            await bot.edit_message_text(
                text=topic_msg_template.format(
                    topic=updated_topic.name,
                    words_total=len(updated_topic.word_phrases),
                    created=updated_topic.created,
                    updated=updated_topic.updated
                ),
                chat_id=TopicFSM.editing_message.chat.id, message_id=TopicFSM.editing_message.message_id,
                reply_markup=get_inline_btns(btns={
                    'Изменить 🖌': f'update_topic_{updating_topic.id}',
                    'Удалить 🗑': f'delete_topic_{updating_topic.id}'})
            )

        # При возникновении ошибок оповещаем пользователя
        except Exception as e:
            msg_text = oops_with_error_msg_template.format(error=str(e))
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Чистим контекст
    await state.set_state(None)
