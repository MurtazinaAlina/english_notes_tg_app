"""
Различные вспомогательные функции, общие для разных модулей.
"""
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Type, Sequence

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.custom_bot_class import Bot
from app.utils.paginator import Paginator, pages
from app.database.db import DataBase
from app.database.models import WordPhrase, Topic, Notes
from app.keyboards.inlines import get_kbds_with_topic_btns
from app.settings import (PLUG_TEMPLATE, PATTERN_CONTEXT_EXAMPLE, KEYWORDS_FOR_RE_SEND_MSG, SYSTEM_SHEETS, SENDER_EMAIL,
                          SMTP_SERVER, SMTP_PORT, SENDER_PASSWORD)
from app.common.msg_templates import note_msg_template


# ПРОВЕРКИ И ВАЛИДАЦИЯ

# Валидация названия темы Topic.name
def validate_topic_name(topic_name: str) -> bool:
    """
    Функция для валидации названия темы Topic.name. Проверка, что пользовательский ввод не входит в список названий,
    зарезервированных для системных листов.

    :param topic_name: Название темы, введенное пользователем
    :return: True, если название валидно, False в противном случае
    """
    if topic_name in SYSTEM_SHEETS:
        return False
    return True


# Валидация Context.example
def validate_context_example(example_text: str) -> bool:
    """
    Функция для валидации Context.example по паттерну, установленному в settings.py.

    :param example_text: Пример для проверки
    :return: True, если пример валиден, False в противном случае
    """
    if not re.match(PATTERN_CONTEXT_EXAMPLE, example_text):
        return False
    return True


# Проверяем, что пользователь аутентифицирован
async def check_if_authorized(callback: types.CallbackQuery, bot: Bot, chat_id: int) -> bool:
    """
    Функция проверяет, что пользователь аутентифицирован в боте (по наличию bot.auth_user_id).

    :param callback: Callback-запрос для всплывающего уведомления (если пользователь не аутентифицирован)
    :param bot: Объект бота
    :param chat_id: ID чата
    :return: True, если пользователь аутентифицирован, False в противном случае (+ всплывающее уведомление)
    """
    if not bot.auth_user_id.get(chat_id):
        await callback.answer('⚠️ Упс! Сначала необходимо авторизоваться', show_alert=True)
        return False
    return True


# Проверка, что у пользователя есть созданные темы Topic в словаре.
async def check_if_user_has_topics(bot: Bot, chat_id: int, session: AsyncSession) -> bool:
    """
    Проверка, что у пользователя есть созданные темы Topic в словаре.

    :param bot: Объект бота
    :param chat_id: ID чата
    :param session: Пользовательская сессия
    :return: True, если у пользователя есть темы, иначе False
    """
    # Проверяем, есть ли у пользователя темы
    if bot.auth_user_id.get(chat_id):
        user = await DataBase.get_user_by_id(session, bot.auth_user_id.get(chat_id))
        if not user.topics:
            await bot.auxiliary_msgs['cbq'][chat_id].answer(
                '⚠️ У пользователя нет тем!\nСоздайте хотя бы одну тему в разделе "Словарь"/"Управление темами"',
                show_alert=True
            )
            return False
    return True


# Проверка, что у пользователя есть записи WordPhrase в словаре
async def check_if_words_exist(bot: Bot, chat_id: int, session: AsyncSession) -> bool:
    """
    Проверка, что у пользователя есть записи WordPhrase в словаре

    :param bot: Объект бота
    :param chat_id: ID чата
    :param session: Пользовательская сессия
    :return: True, если у пользователя есть записи, иначе False
    """
    if not await DataBase.check_if_user_has_words(session, bot.auth_user_id.get(chat_id)):
        await bot.auxiliary_msgs['cbq'][chat_id].answer('У вас нет записей!', show_alert=True)
        return False
    return True


# УДАЛЕНИЕ СООБЩЕНИЙ ИЗ ЧАТА

# Удалить последнее сообщение из хранилища пользовательских сообщений bot.auxiliary_msgs['user_msgs'][chat_id]
async def delete_last_message(bot: Bot, chat_id: int) -> None:
    """
    Функция удаляет последнее сообщение из хранилища bot.auxiliary_msgs['user_msgs'][chat_id].

    :param bot: Объект бота
    :param chat_id: ID чата
    :return: None
    """
    msg = bot.auxiliary_msgs['user_msgs'][chat_id].pop()
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
    except (Exception, ):
        pass


# Удалить информационное сообщение, сохраненное под ключом info_msg в state
async def delete_info_message(bot: Bot, chat_id: int, state_data: dict) -> None:
    """
    Удалить информационное сообщение, сохраненное под ключом info_msg в state.

    :param bot: Объект бота
    :param chat_id: ID чата
    :param state_data: Данные контекста состояния FSM
    :return: None
    """
    info_msg = state_data.get('info_msg')
    if info_msg:
        try:
            await bot.delete_message(chat_id, info_msg.message_id)
        except (Exception,) as e:
            print(e)


# Очистить вспомогательные сообщения в пользовательском чате бота
async def clear_auxiliary_msgs_in_chat(bot: Bot, chat_id: int, edit_context: bool = False,
                                       only_examples: bool = False) -> None:
    """
    Очистить вспомогательные сообщения в чате бота.\n
    Функция удаляет сообщения, сохраненные в:
     - bot.auxiliary_msgs['user_msgs'][chat_id],
     - bot.auxiliary_msgs['example_msgs'][chat_id]
     - и (опционально) в bot.auxiliary_msgs['add_or_edit_word'][chat_id].

    :param bot: Объект бота
    :param chat_id: ID чата
    :param edit_context: Флаг, указывающий на запрет удаления сообщений bot.auxiliary_msgs['add_or_edit_word'][chat_id]
                        (при редактировании слова, удаление сообщений с примерами контекста без обработки других групп).
                        При установке edit_context=True, bot.auxiliary_msgs['add_or_edit_word'][chat_id] НЕ очищается.
    :param only_examples: Флаг, указывающий на удаление ТОЛЬКО сообщений bot.auxiliary_msgs['example_msgs'][chat_id].
    :return: None
    """

    # Если установлен флаг "Удалить только примеры", удаляем их и выходим из функции
    if only_examples:
        for msg in bot.auxiliary_msgs['example_msgs'][chat_id]:
            try:
                await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
            except (Exception, ):
                pass
        bot.auxiliary_msgs['example_msgs'][chat_id] = []
        return

    # Удаление сообщений из хранилища bot.auxiliary_msgs['user_msgs'][chat_id]
    for msg in bot.auxiliary_msgs['user_msgs'][chat_id]:
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except (Exception, ):
            pass
    bot.auxiliary_msgs['user_msgs'][chat_id] = []

    # Удаление сообщений из хранилища bot.auxiliary_msgs['example_msgs'][chat_id]
    for msg in bot.auxiliary_msgs['example_msgs'][chat_id]:
        try:
            await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
        except (Exception, ):
            pass
    bot.auxiliary_msgs['example_msgs'][chat_id] = []

    # Удаление сообщений из хранилища bot.auxiliary_msgs['add_or_edit_word'][chat_id]
    if not edit_context:                                        # Если не установлен запрет на удаление из edit_context
        for msg in bot.auxiliary_msgs['add_or_edit_word'][chat_id].values():
            try:
                await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
            except (Exception, ):
                pass

    # Удаление сообщения со статистикой тестирования
    try:
        await bot.delete_message(chat_id=chat_id, message_id=bot.auxiliary_msgs['statistic_msg'][chat_id].message_id)
    except (Exception, ):
        pass


# Очистить все данные (сообщения в чате, ключи в контексте, атрибуты бота)
async def clear_all_data(bot: Bot, chat_id: int, state: FSMContext) -> None:
    """
    Функция очищает все данные (сообщения в чате, контекст, атрибуты бота).
    Для обработки ситуаций, когда нужен сброс.

    :param bot: Объект бота
    :param chat_id: ID чата
    :param state: Контекст состояния
    :return: None
    """
    try:
        await clear_auxiliary_msgs_in_chat(bot, chat_id)         # Очистить вспомогательные сообщения
        await state.clear()                                      # Очистить контекст состояния
        bot.word_search_keywords[chat_id] = None                 # Очистить ключевые слова поиска WordPhrase
        bot.topic_search_keywords[chat_id] = None                # Очистить ключевые слова поиска Topic
    except (Exception, ):
        pass


# ОТПРАВКА СООБЩЕНИЙ В ЧАТ

# Пробуем отправить системное сообщение из сохраненного callback-ответа.
# Если не получается, просто пропускаем или (опционально) отправляем текстовое сообщение
async def try_alert_msg(bot: Bot, chat_id: int, msg_text: str, if_error_send_msg: bool = False) -> None:
    """
    Функция пробует отправить всплывающее сообщение с переданным текстом из сохраненного ранее callback-ответа.
    При ошибке отправки по умолчанию используется заглушка ошибки pass.

    При указании флага if_error_send_msg, если не удалось отправить всплывающее, то отправляется текстовое сообщение в
    чат с автоматическим удалением через 2 секунды.

    :param bot: Объект бота
    :param chat_id: id чата для отправки сообщения
    :param msg_text: Текст отправляемого сообщения
    :param if_error_send_msg: Флаг для отправки текстового сообщения в чат, если не удалось отправить всплывающее
    :return: None
    """

    # Пробуем отправить всплывающее сообщение
    try:
        await bot.auxiliary_msgs['cbq'][chat_id].answer(msg_text, show_alert=True)

    # Если не получается:
    except (Exception, ):

        # Если требуется оповещение об ошибке, отправляем текстовое сообщение и сохраняем его во вспомогательные
        if if_error_send_msg:
            try:
                msg = await bot.send_message(text=msg_text, chat_id=chat_id)
                bot.auxiliary_msgs['user_msgs'][chat_id].append(msg)
                time.sleep(2)
                await bot.delete_message(chat_id, msg.message_id)
            except (Exception, ) as e:
                print(f'Error in try_alert_msg: {e}')

        # Если не требуется оповещение об ошибке, просто пропускаем
        else:
            pass


# Для редактирования/добавления новых слов WordPhrase
# Функция забирает значение из сообщения пользователя и переотправляет сообщение с пометкой шага, сохраняя в хранилище +
# удаляет оригинальное сообщение
async def re_send_msg_with_step(message: types.Message, bot: Bot, state: FSMContext, msg_text: str) -> None:
    """
    Функция принимает сообщение пользователя, переотправляет текст из него с пометкой шага, сохраняет отправленное
    сообщение в хранилище бота bot.auxiliary_msgs['add_or_edit_word'][chat_id]  + удаляет оригинальное сообщение.

    Для визуализации введённых данных при добавлении/редактировании слов + корректной обработки шага назад.

    :param message: Текстовое сообщение пользователя
    :param bot: Объект бота
    :param state: Контекст состояния FSM
    :param msg_text: Начало системного сообщения с текстовым указанием шага
    :return:
    """

    # Проверяем введённое значение на заглушку
    value = message.text
    if any(word in msg_text.lower() for word in KEYWORDS_FOR_RE_SEND_MSG):
        value = message.text if len(message.text) > 1 else PLUG_TEMPLATE

    # Переотправляем сообщение с добавлением нового текста
    msg = await message.answer(text=f'{msg_text} {value}')

    # Получаем текущее состояние шага для определения названия ключа (формат <Класс StatesGroup>:<шаг State>)
    edit_word_state = await state.get_state()

    # Сохраняем сообщение в хранилище бота c ключом == edit_word_state
    bot.auxiliary_msgs['add_or_edit_word'][message.chat.id][edit_word_state] = msg

    # Удаляем оригинальное сообщение от пользователя
    await bot.delete_message(message.chat.id, message.message_id)


# ФОРМИРОВАНИЕ СЛОВАРЕЙ ДЛЯ РАСПАКОВКИ / ДАННЫХ ДЛЯ .format() И КЛАВИАТУР

# Формирование строки со списком примеров для отображения
def join_examples_in_unordered_list(some_obj: Notes | WordPhrase) -> str:
    """
    Функция формирует строку со списком примеров заметки/слова по заданному шаблону.

    :param some_obj: Объект Notes или WordPhrase
    :return: Строка для отображения со списком примеров использования
    """
    examples = None
    if some_obj.__class__.__name__ == 'WordPhrase':
        examples = some_obj.context
    elif some_obj.__class__.__name__ == 'Notes':
        examples = some_obj.examples
    return '- ' + '\n- '.join([i.example for i in examples])


# Формирование словаря с данными для формирования баннеров редактирования записи слова/фразы из объекта WordPhrase
async def get_word_phrase_caption_formatting(word_phrase_obj: WordPhrase | Type[WordPhrase]) -> dict:
    """
    Формирование словаря с готовыми данными для формирования баннеров редактирования записи WordPhrase.
    Функция принимает объект WordPhrase и возвращает словарь с данными для распаковки в .format()

    :param word_phrase_obj: Объект WordPhrase
    :return: Словарь с данными для распаковки в .format()
    """
    return {
        'word_id': word_phrase_obj.id,
        'word': word_phrase_obj.word,
        'topic': word_phrase_obj.topic.name,
        'transcription': word_phrase_obj.transcription,
        'translate': word_phrase_obj.translate,
        'context': '\n' + join_examples_in_unordered_list(word_phrase_obj),
        'created': word_phrase_obj.created,
        'updated': word_phrase_obj.updated,
    }


# Формирование словаря с данными о темах для отображения в описании баннера
async def get_topic_info_for_caption(
        all_topics: Sequence[Topic], current_page_topics: list[Topic], page: int, per_page: int) -> dict[str, int]:
    """
    Формирование словаря с данными о темах для отображения в описании баннера (для распаковки в .format()).

    Возвращает словарь с topics_total: int, first_topic: int, last_topic: int.

    :param all_topics: Все темы пользователя с учётом фильтра
    :param current_page_topics: Список тем на текущей странице
    :param page: Номер текущей страницы
    :param per_page: Количество отображаемых тем на странице
    :return: Словарь с данными для распаковки в .format()
    """
    topics_total: int = len(all_topics)
    first_topic: int = ((page - 1) * per_page) + 1
    last_topic: int = len(current_page_topics) - 1 + first_topic

    return {
        'topics_total': topics_total,                       # Всего тем, шт. (с учётом фильтра)
        'first_topic': first_topic,                         # Номер первой отображаемой темы
        'last_topic': last_topic                            # Номер последней отображаемой темы
    }


# Формирование клавиатуры с выбором темы + словаря с информацией о темах на странице для описания баннера
async def get_topic_kbds_helper(
        bot: Bot, chat_id: int, session: AsyncSession, level: int, menu_name: str, menu_details: str,
        topic_name_prefix: str, search_key: str | None, page: int, per_page: int = 4, cancel_possible: bool = False,
        cancel_page_address: str | None = None, sizes: tuple[int, ...] | None = None, pass_btn: dict | None = None
) -> tuple[InlineKeyboardMarkup, dict[str, int]]:
    """
    Функция для формирования клавиатуры с выбором темы и словаря с информацией о темах на странице для описания баннера.
    Возвращает готовую клавиатуру и словарь с данными для распаковки в .format().

    :param bot: Объект бота
    :param chat_id: ID чата
    :param session: Пользовательская сессия
    :param level: Уровень глубины меню (для обработки кнопки "Назад ⬅️" в хедере)
    :param menu_name: Название меню (для формирования callback_data MenuCallBack)
    :param menu_details: Детали меню (для формирования callback_data MenuCallBack)
    :param topic_name_prefix: Текст для формирования необходимой callback_data выбранной темы
    :param search_key: Ключ для фильтра по названию темы
    :param page: Номер текущей страницы пагинации
    :param per_page: Количество отображаемых тем на странице. По умолчанию 4
    :param cancel_possible: Добавление кнопок отмены действия. По умолчанию - False
    :param cancel_page_address: Данные callback_data при отмене действия. По умолчанию - None
    :param sizes: Размеры клавиатуры, кортеж с перечислением, сколько кнопок разместить в каждой строке (если
                 необходима кастомизация). По умолчанию - None (для применения размеров по умолчанию).
    :param pass_btn: Кнопка пропуска шага (при редактировании WordPhrase) для распаковки в btns. По умолчанию - None
    :returns: Кортеж с готовой клавиатурой и словарем с данными для распаковки в .format()
    """

    # Получаем список всех тем пользователя с учётом наличия фильтра
    all_topics = await DataBase.get_all_topics(session, user_id=bot.auth_user_id.get(chat_id), search_key=search_key)

    # Системка, если темы не найдены
    if len(all_topics) == 0:
        await try_alert_msg(bot, chat_id, 'Темы не найдены')

    # Добавляем пагинацию и получаем срез нужной страницы с темами
    paginator = Paginator(list(all_topics), page=page, per_page=per_page)
    current_page_topics: list = paginator.get_page()

    # Получаем словарь с информацией о темах на странице
    topic_info_for_caption: dict = await get_topic_info_for_caption(all_topics, current_page_topics, page, per_page)

    # Настраиваем флаги для формирования кнопок поиска тем/отмены поиска
    search_cancel = True if search_key else False
    search_possible = False if search_key else True

    # Формируем кнопки с темами текущей страницы
    btns: dict = {topic.name: f'{topic_name_prefix}{str(topic.id)}' for topic in current_page_topics}

    # Добавляем кнопку пропуска (опционально)
    if pass_btn:
        btns: dict = {**pass_btn, **btns}

    # Формируем кнопки пагинации
    pagination_btns: dict = pages(paginator)

    # Если переданы размеры, добавляем их в словарь для последующей распаковки
    sizes_dict = dict()
    if sizes:
        sizes_dict['sizes'] = sizes

    # Формируем итоговую клавиатуру
    kbds: InlineKeyboardMarkup = get_kbds_with_topic_btns(
        level=level, btns=btns, page=page, pagination_btns=pagination_btns, menu_name=menu_name,
        menu_details=menu_details, search_possible=search_possible, search_cancel=search_cancel,
        cancel_possible=cancel_possible, cancel_page_address=cancel_page_address, **sizes_dict
    )
    return kbds, topic_info_for_caption


# РАЗНОЕ

# Создать CallbackQuery-объект с необходимым callback.data на базе другого callback
async def modify_callback_data(callback: types.CallbackQuery, new_callback_data: str) -> types.CallbackQuery:
    """
    Функция создает CallbackQuery-объект с необходимым callback.data на базе другого callback (если требуется
    принудительно вызвать обработчик, требующий обязательный callback и с логикой, зависящей от callback.data).

    :param callback: Callback-запрос с любыми данными
    :param new_callback_data: Новый callback.data
    :return: CallbackQuery-объект с новым callback.data
    """
    new_callback = types.CallbackQuery(
        id=callback.id,
        from_user=callback.from_user,
        message=callback.message,
        chat_instance=callback.chat_instance,
        data=new_callback_data,
    )
    return new_callback


# Получение word_to_update и page_address данных из контекста
async def get_upd_word_and_cancel_page_from_context(state: FSMContext) -> tuple[WordPhrase, str]:
    """
    Вспомогательная функция для получения данных из контекста при редактировании слова/фразы WordPhrase.
    Получает объект WordPhrase и callback_data для страницы отображения при отмене редактирования.

    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update' и
                    callback_data для страницы отмены редактирования в ключе 'page_address'
    :return: Кортеж (word_to_update, page_address)
    """

    # Получаем данные из контекста
    data = await state.get_data()
    word_to_update = data.get('word_to_update')             # Получаем слово/фразу объект WordPhrase
    cancel_page = data.get('page_address')                  # Получаем callback для страницы отмены редактирования
    return word_to_update, cancel_page


# Обновление данных привязки чата к пользователю (создание новой связки в UserChat, удаление неактуальных)
async def update_user_chat_data(session: AsyncSession, chat_id: int, user_id: int) -> None:
    """
    Обновление данных привязки чата к пользователю (создание новой связки в UserChat, удаление неактуальных привязок).

    :param session: Пользовательская сессия
    :param chat_id: ID чата Telegram
    :param user_id: ID пользователя
    :return: None
    """

    # Создаем запись в таблице UserChat. Если запись уже существует - откатываем транзакцию
    try:
        await DataBase.create_user_chat(session, user_id, chat_id)
    except (Exception,) as e:
        await session.rollback()
        print(e)

    # Проверяем, если ли записи с таким же ID чата и другим пользователем. Если есть - удаляем.
    chat_has_old_user = await DataBase.check_if_chat_attached_to_another_user(session, chat_id, user_id)
    if chat_has_old_user:
        try:
            await DataBase.delete_outdated_user_chats(session, chat_id, user_id)
        except (Exception,) as e:
            await session.rollback()
            print(e)


# Редактирование сообщения с заметкой
async def update_note_msg_data(bot: Bot, chat_id: int, state_data: dict, edited_note: Notes) -> None:
    """
    Функция редактирует сообщение с заметкой после её редактирования.

    :param bot: Объект бота
    :param chat_id: ID чата
    :param state_data: Контекст состояния FSM с ключами:
                        'user_notes' - список всех заметок пользователя,
                        'show_user_notes_cbq' - callback-запрос с номером страницы с заметкой (для "заметка №"),
                        'note_msg' - с редактируемым сообщением с заметкой
    :param edited_note: Объект заметки
    :return: None
    """

    # Забираем из контекста информацию для описания заметки и объект сообщения для редактирования
    user_notes = state_data.get('user_notes')
    page = state_data.get('show_user_notes_cbq').replace('my_notes_page_', '')
    note_msg = state_data.get('note_msg')

    # Формируем новый текст сообщения с заметкой
    examples = join_examples_in_unordered_list(edited_note)
    msg_text = note_msg_template.format(
        page=page, len_user_notes=len(user_notes), note_title=edited_note.title, note_text=edited_note.text,
        examples=examples
    )

    # Редактируем сообщение
    await note_msg.edit_text(msg_text, reply_markup=bot.reply_markup_save[chat_id])


# Отправить письмо пользователю на ранее указанную почту с токеном на сброс пароля
def send_email_reset_psw_token(to_email, reset_token) -> None:
    """
    Отправить письмо пользователю на ранее указанную почту с токеном на сброс пароля.

    :param to_email: Почта получателя
    :param reset_token: Токен для сброса пароля
    :return: Функция ничего не возвращает, только отправляет письмо
    """

    # Создание письма
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = "Сброс пароля для вашего аккаунта"

    # Тело письма с токеном на сброс пароля
    body = f"Ваш токен для сброса пароля: {reset_token}"
    msg.attach(MIMEText(body, 'plain'))

    # Отправка письма
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"Ошибка при отправке письма: {e}")
