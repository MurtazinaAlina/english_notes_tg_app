"""
Обработка действий в разделе "Словарь".
WORD_PHRASES - обработчики действий с записями слов/фраз в БД.

INFO:

1. ПОИСК СЛОВА - ключ для поиска записывается в атрибуте бота bot.word_search_keywords[<chat_id>].
2. Также при выборе фильтра по теме, в state добавляется ключ "selected_topic_id" с id выбранной темы.
   Таким образом фильтрация сохраняется и при поиске слова и после его отмены.

ОЗВУЧИВАНИЕ СЛОВ/ПРИМЕРОВ.
1. Озвучка делается через модель Edge TTS (Microsoft Azure Voices).
2. При выборе прослушивания генерируется и отправляется в чат голосовое сообщение с озвучкой.
3. При запросе прослушивания нового слова неактуальные аудио автоматически удаляются из чата.

РЕДАКТИРОВАНИЕ записей WordPhrase.
1. При выборе редактирования в FSMContext добавляется ключ "word_to_update" с редактируемым объектом WordPhrase.
2. При редактировании новое значение, введённое пользователем, записывается в контекст с ключом по имени
   атрибута таблицы.
3. Сообщение с новым значением от пользователя удаляется и переотправляется ботом с указанием шага:
   "Новая тема: {new_topic_name}"
4. Это системное сообщение сохраняется в словарь bot.auxiliary_msgs['add_or_edit_word'][chat_id] с ключом
   по имени ШАГА состояния State. Это позволяет удалять именно это сообщение при шаге назад
5. При отмене редактирования происходит автоматическое перенаправление на последнюю просмотренную страницу словаря.
   Дополнительный обработчик не требуется, переход прописан в callback_data кнопки отмены редактирования.
6. Ключ для фильтра по темам при редактировании сохраняется в bot.topic_search_keywords[chat_id], НЕ в FSMContext.
7. Поиск темы. Отправка сообщения с запросом ввода ключа поиска темы и отмена ввода обрабатываются в topic_actions.py:
   find_topic_by_matches_ask_keywords          - запрос ключевого слова поиска темы
   cancel_find_topic                           - отмена поиска темы
8. Редактирование примеров вынесено в context_examples_actions.py
"""
import os
import re
import time
from typing import BinaryIO

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details
from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.keyboards.inlines import get_inline_btns, get_pagination_btns, add_new_or_edit_word_main_btns, \
    get_kbds_with_navi_header_btns
from app.utils.custom_bot_class import Bot
from app.utils.menu_processing import vocabulary
from app.utils.xsl_tools import export_vcb_data_to_xls_file, import_data_from_xls_file
from app.utils.paginator import Paginator, pages
from app.utils.tts import speak_text, clear_audio_examples_from_chat
from app.common.tools import re_send_msg_with_step, get_upd_word_and_cancel_page_from_context, get_topic_kbds_helper, \
    check_if_words_exist, get_word_phrase_caption_formatting, clear_auxiliary_msgs_in_chat, try_alert_msg, \
    modify_callback_data
from app.common.msg_templates import word_msg_template, oops_with_error_msg_template, oops_try_again_msg_template, \
    word_validation_not_passed_msg_template
from app.common.fsm_classes import WordPhraseFSM, TopicFSM, ImportXlsFSM
from app.settings import PER_PAGE_VOCABULARY, PLUG_TEMPLATE, PATTERN_WORD


# Создаём роутер для приватного чата бота с пользователем
vocabulary_router = Router()

# Настраиваем фильтр, что строго приватный чат
vocabulary_router.message.filter(ChatTypeFilter(['private']))


# Вывод записей слов/фраз в чат с пагинацией и доступом к редактированию/удалению
@vocabulary_router.callback_query(F.data.contains('select_all_words') | F.data.contains('select_topic_id_'))
async def show_vocabulary_words(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Просмотр записей слов/фраз из таблицы WordPhrase (всех или по конкретной теме).
    Функция отображает страницу с соответствующими номеру страницы записями из БД.

    :param callback: Callback запрос по нажатию inline-кнопки. Варианты для 1-й страницы пагинации и последующих:
                    С фильтром по теме:   "select_topic_id_{topic_id}", "vcb:select_topic_id_{topic_id}:{page_number}",
                    Все записи:           "select_all_words", "vcb:select_all_words:{page_number}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Чистим контекст и вспомогательные сообщения (на случай отмены/завершения редактирования)
    await state.set_state(None)
    await state.update_data(word_to_update=None)    # Сбрасываем слово для редактирования на случай перехода по отмене
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Записываем в контекст значение callback текущей страницы, чтобы возвращаться после редактирования или при отмене
    await state.update_data(page_address=callback.data)

    # Проверяем, что у пользователя есть записи
    if not await check_if_words_exist(bot, callback.message.chat.id, session):
        return

    # Устанавливаем фильтр по теме и menu_details для отображения записей в пагинации, записываем тему (если выбрана):

    # Если это обработка выбранной пользователем темы
    if 'select_topic_id_' in callback.data:

        # Если это callback пагинации: "vcb:select_topic_id_{topic_id}::{page_number}"
        if callback.data.startswith('vcb'):
            filter_topic_id = callback.data.split(':')[1].replace('select_topic_id_', '')

        # Если это 1-я страница выбранной пользователем темы (Callback "select_topic_id_{topic_id}")
        else:
            filter_topic_id = int(callback.data.replace('select_topic_id_', ''))

            # Записываем в контекст id выбранной темы под ключом selected_topic_id=
            await state.update_data(selected_topic_id=filter_topic_id)

            # Если это сообщение с баннером, записываем его (только для первой страницы в пагинации)
            if callback.message.photo:
                bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

        menu_details = f'select_topic_id_{filter_topic_id}'
        topic = await DataBase.get_topic_by_id(session, filter_topic_id)
        topic_if_selected = topic.name                      # Название темы для отображения в сообщении с пагинацией

    # Если это просмотр всех записей WordPhrase (без фильтрации по темам)
    else:
        filter_topic_id = None
        menu_details = 'select_all_words'
        topic_if_selected = 'Не выбрана'

        # Если это сообщение с баннером, записываем его (только для первой страницы в пагинации)
        if callback.data == 'select_all_words' and callback.message.photo:
            bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

    # Получаем изображение для баннера и клавиатуру
    media, kbds = await vocabulary(
        bot, session, state=state, level=2, menu_details='show_word_phrases', callback=callback
    )

    # Редактируем баннер и клавиатуру. При пагинации баннер не меняется, обрабатываем исключение.
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_media(
            media=media, reply_markup=kbds
        )
    except (Exception, ):
        pass

    # Определяем страницу пагинации.
    if callback.data.startswith('vcb'):             # Если callback был VocabularyCallBack, забираем страницу из него
        page = int(callback.data.split(':')[-1])
    else:                                           # Если callback не был VocabularyCallBack, устанавливаем страницу 1
        page = 1

    # Если выбрана тема, получаем ее id из контекста (на случай обработки поиска/отмены поиска слова)
    state_data = await state.get_data()
    filter_topic_exists = state_data.get('selected_topic_id')
    if filter_topic_exists:
        filter_topic_id = filter_topic_exists

    # Получаем записи WordPhrase пользователя для текущей страницы пагинации с учётом всех фильтров
    all_user_vocab = await DataBase.get_user_word_phrases(
        session, user_id=bot.auth_user_id.get(callback.message.chat.id), topic_id=filter_topic_id,
        search_keywords=bot.word_search_keywords[callback.message.chat.id]
    )
    paginator = Paginator(list(all_user_vocab), page=page, per_page=PER_PAGE_VOCABULARY)
    words_on_current_page: list = paginator.get_page()

    # Формируем информационное сообщение с пагинацией
    user_words_count = len(all_user_vocab)                          # Всего записей WordPhrase (или в выбранной теме)
    first_word: int = ((page - 1) * PER_PAGE_VOCABULARY) + 1        # Номер первой отображаемой записи
    last_word: int = len(words_on_current_page) - 1 + first_word    # Номер последней отображаемой записи
    pagi_kbds = get_pagination_btns(page=page, pagination_btns=pages(paginator), menu_details=menu_details)
    info_msg_text = f'<b>Тема:</b> {topic_if_selected}\n' \
                    f'<b>Всего записей:</b> {user_words_count}\n' \
                    f'<b>Показаны записи:</b> {first_word} - {last_word}'

    # Отправляем информационное сообщение с пагинацией
    msg = await callback.message.answer(text=info_msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Выводим записи WordPhrase текущей страницы + управление каждой в инлайне. Сохраняем сообщения во вспомогательные
    for word_phrase in words_on_current_page:
        word_phrase_caption: dict = await get_word_phrase_caption_formatting(word_phrase)
        msg = await callback.message.answer(
            text=word_msg_template.format(**word_phrase_caption),
            reply_markup=get_inline_btns(
                btns={
                    'Изменить 🖌': f'update_word_{word_phrase.id}',
                    'Удалить 🗑': f'delete_word_{word_phrase.id}',
                    '🎧 Слово': f'speak_word_{word_phrase.id}',
                    '🎧 Примеры': f'speak_example_{word_phrase.id}',
                }
            )
        )
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Дублируем информационное сообщение с пагинацией
    msg = await callback.message.answer(text=info_msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# ПОИСК СЛОВА/ФРАЗЫ WordPhrase

# Поиск слова/фразы по ключевому слову - запрос текста для поиска
@vocabulary_router.callback_query(F.data.startswith('search_word_phrase_by_keyword'))
async def search_word_phrase_ask_keyword(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Поиск слова/фразы по ключевому слову - запрос текста для поиска.

    :param callback: Callback-запрос формата "search_word_phrase_by_keyword"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Чистим вспомогательные сообщения в чате
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Получаем адрес последней просмотренной страницы
    data = await state.get_data()
    cancel_page_address = data.get('page_address')

    # Выводим информационное сообщение для ввода ключа поиска.
    # При отмене по кнопке - возвращаемся на последнюю просмотренную страницу, дополнительный обработчик не требуется
    msg = await callback.message.answer(
        'Введите текст для поиска слова/фразы', reply_markup=get_inline_btns(btns={'Отмена ❌': cancel_page_address})
    )

    # Сохраняем вспомогательное сообщение и callback
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Устанавливаем состояние ввода ключевого слова для поиска
    await state.set_state(WordPhraseFSM.search_keywords)


# Поиск слова/фразы по ключевому слову - обработка ввода ключевого слова
@vocabulary_router.message(F.text, StateFilter(WordPhraseFSM.search_keywords))
async def search_word_phrase_get_keyword(message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Поиск слова/фразы. Обработка ввода ключевого слова.

    :param message: Текстовое сообщение с ключевым словом
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Устанавливаем введенное ключевое слово в атрибут бота
    bot.word_search_keywords[message.chat.id] = message.text

    # Сохраняем сообщение во вспомогательные
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Вызываем обработчик просмотра слов/фраз в базе
    await show_vocabulary_words(
        session=session, state=state, bot=bot, callback=bot.auxiliary_msgs['cbq'][message.chat.id]
    )


# Отмена поиска слова/фразы по ключевому слову в интерфейсе inline-меню
@vocabulary_router.callback_query(F.data == 'cancel_search_word_phrase')
async def cancel_search_word_phrase(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Отмена поиска слова/фразы по ключевому слову в интерфейсе меню. Возврат к просмотру всех записей базы слов/темы.

    :param callback: Callback-запрос формата "cancel_search_word_phrase"
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Убираем значение ключевого слова в боте
    bot.word_search_keywords[callback.message.chat.id] = None

    # Оповещаем пользователя
    await callback.answer('⚠️ Поиск слова/фразы отменен!', show_alert=True)

    # Вызываем обработчик просмотра слов/фраз в базе
    await show_vocabulary_words(
        session=session, state=state, bot=bot, callback=bot.auxiliary_msgs['cbq'][callback.message.chat.id]
    )


# ОЗВУЧИВАНИЕ СЛОВ/ПРИМЕРОВ

# Прослушивание звучания слова/фразы
@vocabulary_router.callback_query(F.data.startswith('speak_word_'))
async def speak_word_aloud(callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Прослушать звучание слова/фразы WordPhrase. Функция отправляет в чат бота аудио сообщение.

    :param callback: Callback-запрос формата "speak_word_<WordPhrase.id>"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :return: None
    """

    # Получаем слово/фразу из базы
    word_id = int(callback.data.split('_')[-1])
    word_phrase = await DataBase.get_word_phrase_by_id(session, word_id)

    # Определяем, нужен ли заголовок для аудио (не указывается для тестирования)
    state_data = await state.get_data()
    is_with_title = False if state_data.get('test_type') else True

    # Удаляем аудио сообщения с примерами предыдущих слов
    await clear_audio_examples_from_chat(state, bot, callback, state_data, word_id)

    # Отправляем в чат аудио файл со словом/фразой
    await speak_text(
        str(word_phrase.word), bot, callback.message.chat.id, is_with_title, autodelete=False,
        state=state, session=session
    )


# Прослушивание звучания примеров слова/фразы, заметки
@vocabulary_router.callback_query(F.data.startswith('speak_example_') | F.data.startswith('speak_note_example_'))
async def speak_example_aloud(
        callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Прослушать звучание примеров слова/фразы, заметки. Функция отправляет в чат бота аудио сообщения с примерами.

    :param callback: Callback-запрос формата "speak_example_<WordPhrase.id>" или "speak_note_example_<Notes.id>"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с (опционально) ключом "test_type" (при тестировании)
    :return: None
    """

    # Определяем, нужен ли заголовок для аудио (не указывается для тестирования)
    state_data = await state.get_data()
    is_with_title = False if state_data.get('test_type') else True

    # Получаем идентификатор слова/фразы/заметки
    entity_id = int(callback.data.split('_')[-1])

    # Получаем примеры заметки
    if callback.data.startswith('speak_note_example_'):
        note = await DataBase.get_note_by_id(session, entity_id)
        context = note.examples

    # Получаем примеры слова/фразы
    else:
        word_phrase = await DataBase.get_word_phrase_by_id(session, entity_id)
        context = word_phrase.context

    # Удаляем аудио сообщения с примерами предыдущих слов
    await clear_audio_examples_from_chat(state, bot, callback, state_data, entity_id)

    # Если есть примеры Context, отправляет в чат аудио с ними
    if context:
        for example in context:
            await speak_text(
                example.example, bot, callback.message.chat.id, is_with_title, autodelete=False,
                state=state, session=session
            )


# ИМПОРТ/ЭКСПОРТ ДАННЫХ СЛОВАРЯ ИЗ/В .XLS ФАЙЛ

# Выбор действия экспорта/импорта сводной таблицы словаря
@vocabulary_router.callback_query(F.data == 'xls_actions')
async def xls_actions(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Выбор действия экспорта/импорта сводной таблицы словаря.

    :param callback: Callback-запрос формата "xls_actions"
    :param bot: Объект бота
    :return: None
    """

    # Убираем все предыдущие сообщения из чата
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Редактируем клавиатуру
    btns = {
        'Создать сводный .xls файл 📑': 'send_xls_wb',
        'Импортировать данные из .xlsx 💾': 'import_data_from_xlsx_wb',
    }
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    await callback.message.edit_caption(caption=banners_details.vcb_descrptn_xls, reply_markup=kbds)


# Генерирование и отправка сводной xlsx таблицы с данными словаря и заметок в чат
@vocabulary_router.callback_query(F.data.startswith('send_xls_wb'))
async def send_xls_wb(callback: types.CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """
    Генерирование и отправка сводной xlsx таблицы с данными словаря и заметок в чат

    :param callback: Callback-запрос формата "send_xls_wb"
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Создаём сводный xlsx-файл с данными словаря и заметок пользователя
    file_path: str = await export_vcb_data_to_xls_file(session, bot, callback.message.chat.id)

    # Преобразуем файл для отправки и скидываем его в чат
    with open(file_path, "rb") as file:
        data_file = types.BufferedInputFile(file.read(), filename=os.path.basename(file_path))
    msg = await bot.send_document(chat_id=callback.message.chat.id, document=data_file)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Удаляем xlsx-файл из системы после отправки
    os.remove(file_path)


# Импортирование данных из .xlsx файла - ШАГ 1, запрос файла
@vocabulary_router.callback_query(F.data == 'import_data_from_xlsx_wb')
async def import_data_from_xlsx_ask_file(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Импортировать данные из .xlsx файла - ШАГ 1, запрос файла.

    :param callback: Callback-запрос формата "import_data_from_xlsx_wb"
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Отправляем информационное сообщение с кнопкой отмены в чат
    kbds = get_inline_btns(btns={'Отмена ❌': 'import_data_cancel'})
    msg_text = 'Отправьте в чат .xlsx-файл с данными словаря <b>в формате идентичном сводной таблице</b>.'
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ожидания отправки файла .xls
    await state.set_state(ImportXlsFSM.xls_file)


# Отмена импорта данных из .xlsx файла
@vocabulary_router.callback_query(F.data == 'import_data_cancel')
async def import_data_cancel(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Функция для отмены импорта данных из .xlsx. Сбрасывает состояние ожидания отправки файла и чистит чат.

    :param callback: Callback-запрос "import_data_cancel"
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """
    await callback.answer('⚠️ Импорт данных отменён!', show_alert=True)
    await state.set_state(None)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)


# Импортирование данных из .xlsx файла - ШАГ 2, загрузка данных в БД из полученного файла
@vocabulary_router.message(ImportXlsFSM.xls_file, F.document)
async def import_data_from_xlsx_get_file(message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Импортировать данные из .xlsx файла - ШАГ 2, загрузка данных в БД из полученного файла.

    :param message: Входящий документ в формате .xlsx с данными словаря для загрузки
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем сообщение во вспомогательные
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Проверяем тип документа. При несоответствии выводим предупреждение и выходим из функции
    if not message.document.file_name.lower().endswith(('.xls', '.xlsx')):
        msg_text = '⚠️Упс! Поддерживаются только .xls и .xlsx файлы. Отправьте документ верного формата.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Скачиваем файл во временный буфер
    data_file: BinaryIO | None = await bot.download(message.document)
    if data_file is None:
        msg_text = '⚠️Упс! Файл не скачан. Попробуйте отправить файл ещё раз.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Отправляем системное сообщение об ожидании загрузки. Оно будет удаляться в конце импорта/при ошибке
    info_msg = await bot.send_message(chat_id=message.chat.id, text='⏳ Импорт данных займёт некоторое время...')

    # Импортируем данные из .xlsx-файла в БД
    added = None
    try:
        added = await import_data_from_xls_file(session, bot, message.chat.id, data_file)
    except Exception as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    await bot.delete_message(chat_id=message.chat.id, message_id=info_msg.message_id)
    if type(added) is int:
        msg_text = f'✅ Загружено/обновлено записей: {added}'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        time.sleep(3)
        await state.set_state(None)
        await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # Если данные не импортированы, выводим предупреждение. Состояние ожидания отправки файла остаётся (!)
    else:
        msg_text = '⚠️Упс! Данные не импортированы. Попробуйте отправить файл ещё раз.'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)


# УДАЛЕНИЕ СЛОВА/ФРАЗЫ WordPhrase

# Удаление слова/фразы - ШАГ 1, запрос подтверждения
@vocabulary_router.callback_query(F.data.startswith('delete_word_'))
async def delete_word_phrase_ask_for_confirmation(callback: types.CallbackQuery, session: AsyncSession,
                                                  state: FSMContext, bot: Bot) -> None:
    """
    Удаление слова/фразы WordPhrase из базы.

    :param callback: Callback-запрос формата "delete_word_{word_id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом "page_address" с адресом текущей страницы просмотра записей
    :param bot: Объект бота
    :return: None
    """

    # Получаем идентификатор слова/фразы из callback и объект слова/фразы из БД
    word_id = int(callback.data.split('_')[-1])
    word_phrase_obj = await DataBase.get_word_phrase_by_id(session, word_id)

    # Забираем из контекста данные для кнопки отмены действия
    state_data = await state.get_data()

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Отправляем пользователю информационное сообщение с запросом подтверждения действия или отмены
    msg_text = f'⚠️ Вы действительно хотите удалить запись <b>"{word_phrase_obj.word}"</b>?'
    btns = {'Удалить 🗑': f'confirm_delete_word_{word_id}', 'Отмена ❌': state_data.get('page_address')}
    kbds = get_inline_btns(btns=btns)
    msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Удаление слова/фразы - ШАГ 2, подтверждение получено, удаляем из БД
@vocabulary_router.callback_query(F.data.startswith('confirm_delete_word_'))
async def delete_word_phrase_get_confirmation(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext,
                                              bot: Bot) -> None:
    """
    Удаление слова/фразы WordPhrase из базы.

    :param callback: Callback-запрос формата "confirm_delete_word_{word_id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом "page_address" с адресом текущей страницы просмотра записей
    :param bot: Объект бота
    :return: None
    """

    # Получаем идентификатор слова/фразы из callback
    word_id = int(callback.data.split('_')[-1])

    # Удаляем слово/фразу из базы
    try:
        is_del = await DataBase.delete_word_phrase(session, word_id)
        await callback.answer('✅ Запись удалена!', show_alert=True)

    except Exception as e:
        is_del = False
        await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)

    # Если удаление прошло успешно, возвращаемся к последней просмотренной странице
    if is_del:

        # Забираем из контекста данные о странице для возврата при завершении действия
        state_data = await state.get_data()
        show_words_cbq = state_data.get('page_address')

        # Возвращаемся к последней просмотренной странице
        modified_callback = await modify_callback_data(callback, show_words_cbq)
        await show_vocabulary_words(modified_callback, session, state, bot)

        # При ошибке выводим сообщение пользователю
    else:
        await callback.answer(text=oops_try_again_msg_template, show_alert=True)


# РЕДАКТИРОВАНИЕ записей WordPhrase - UPDATE

# Редактирование - ШАГ НАЗАД
@vocabulary_router.callback_query(
    StateFilter('*'), F.data == 'add_or_edit_word_step_back', IsKeyInStateFilter('word_to_update'))
async def edit_word_step_back(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    ШАГ НАЗАД. Возврат к предыдущему шагу редактирования слова/фразы WordPhrase.

    :param callback: Callback-запрос формата "add_or_edit_word_step_back"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param bot: Объект бота
    :return: None
    """

    # Получаем данные из контекста
    data = await state.get_data()
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)
    first_topic, last_topic, total_topics = data.get('topic_info_for_caption', (None, None, None))

    # Формируем описание баннера
    caption_formatting_dict = await get_word_phrase_caption_formatting(word_to_update)
    caption_formatting_dict = {
        **caption_formatting_dict,
        'first_topic': first_topic,
        'last_topic': last_topic,
        'topics_total': total_topics
    }

    # Определяем текущий шаг состояния
    current_state = await state.get_state()
    previous = None

    # Проверяем каждое из состояний класса на соответствие текущему из FSM
    for step in WordPhraseFSM.__all_states__:
        if step.state == current_state:         # step.state  отображается как WordPhraseFSM:word  <класс>:<состояние>

            # При совпадении устанавливаем предыдущее состояние (которое записали ранее, на предыдущей итерации)
            await state.set_state(previous)
            await callback.answer('⚠️ Вы вернулись к предыдущему шагу!', show_alert=True)

            # Удаляем сообщение со значением из отменённого шага
            try:
                await bot.delete_message(
                    chat_id=callback.from_user.id,
                    message_id=bot.auxiliary_msgs['add_or_edit_word'][callback.message.chat.id][
                        previous.state].message_id
                )
            except Exception as e:
                print(e)

            # Очищаем вспомогательные сообщения КРОМЕ лежащих в bot.auxiliary_msgs['edit_word'] (Для удаления примеров)
            await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id, edit_context=True)

            # Формируем клавиатуру предыдущего шага
            if step.state == 'WordPhraseFSM:word':
                kbds = bot.markup_user_topics[callback.message.chat.id]         # Для выбора темы клавиатура сохранена

            else:                                                       # Для других шагов клавиатуру формируем
                # Из предыдущего шага определяем название атрибута word_to_update для создания клавиатуры
                attr_name = previous.state.split(':')[-1]
                edit_value = getattr(word_to_update, attr_name)         # Получаем значение атрибута для вывода

                # Формируем клавиатуру
                btns = {'Редактировать значение ✏️': edit_value}
                kbds = add_new_or_edit_word_main_btns(
                    pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
                )

            # Редактируем баннер и клавиатуру
            await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
                caption=(WordPhraseFSM.edit_word_caption[previous.state].format(**caption_formatting_dict)),
                reply_markup=kbds
            )
            break

        # Сохраняем шаг как предыдущий при несовпадении состояния
        previous = step


# Редактирование - ШАГ 1: пропуск или выбор новой темы
@vocabulary_router.callback_query(F.data.startswith('update_word_'))
async def update_word_phrase_ask_topic(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 1, запрос темы.
    Функция позволяет пропустить шаг или выбрать новую тему Topic.

    Если применялся фильтр по темам, то ключ для поиска сохранён в bot.topic_search_keywords[chat_id].

    :param callback: Callback-запрос формата:
                    "update_word_{word_id}" ИЛИ "update_word_{word_id}_page_{page_number}" при пагинации
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Получаем данные из контекста
    word_to_update, cancel_page_address = await get_upd_word_and_cancel_page_from_context(state)
    word_id = word_to_update.id if word_to_update else None

    # Если это первичный вызов обработчика по кнопке "Изменить"
    if callback.data.startswith('update_word_'):

        # Получаем слово/фразу по id из callback по N элемента
        word_id = int(callback.data.split('_')[2])
        word_to_update = await DataBase.get_word_phrase_by_id(session, word_id)

        # Если слово/фраза не найдено, выходим из обработчика
        if word_to_update is None:
            await callback.answer('⚠️ Слово/фраза не найдены!', show_alert=True)
            return

        # Записываем изменяемое слово/фразу в контекст состояния
        await state.update_data(word_to_update=word_to_update)

    # Получаем номер текущей страницы
    if '_page_' in callback.data:
        page = int(callback.data.split('_')[-1])
    else:
        page = 1

    # Настраиваем параметры пагинации
    per_page = 4

    # Формируем клавиатуру и информацию о темах для баннера
    search_key = bot.topic_search_keywords.get(callback.message.chat.id)
    topic_name_prefix = 'updated_word_topic_'
    pass_btn = {'Оставить текущую тему ▶': f'updated_word_topic_{str(word_to_update.topic_id)}'}  # Кнопка текущей темы
    kbds, topic_info_for_caption = await get_topic_kbds_helper(
        bot, chat_id=callback.message.chat.id, session=session, level=2, menu_name='vocabulary',
        menu_details=f'update_word_{word_id}', topic_name_prefix=topic_name_prefix, search_key=search_key, page=page,
        per_page=per_page, cancel_possible=True, cancel_page_address=cancel_page_address, sizes=(2, 1, 2),
        pass_btn=pass_btn
    )

    # Формируем описание баннера для дальнейшего изменения
    caption = banners_details.update_word_step_1.format(
            word=word_to_update.word, topic=word_to_update.topic.name, **topic_info_for_caption
        )

    # Записываем данные о темах в контекст - для корректного отображения в ШАГ НАЗАД
    await state.update_data(topic_info_for_caption=(topic_info_for_caption.values()))

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)

    # Сохраняем клавиатуру и callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback
    bot.markup_user_topics[callback.message.chat.id] = kbds                              # Для перехода "шаг назад"

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Переходим в состояние выбора темы
    await state.set_state(WordPhraseFSM.topic)


# Редактирование - ШАГ 1.5: ПРИМЕНЕНИЕ фильтра по темам
@vocabulary_router.message(F.text, StateFilter(TopicFSM.search_keywords), IsKeyInStateFilter('word_to_update'))
async def find_topic_by_matches_get_keywords(
        message: types.Message, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - Обработка фильтра по темам.
    Функция сохраняет введенное пользователем ключевое слово в атрибут бота и вызывает обработчик выбора темы
    при редактировании записи WordPhrase.

    :param message: Текстовое сообщение с ключевым словом для фильтра
    :param session: Пользовательская сессия
    :param state: Контекст состояния (только TopicFSM.search_keywords и при наличии атрибута word_to_update)
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем введенное ключевое слово
    bot.topic_search_keywords[message.chat.id] = message.text

    # Сохраняем сообщение во вспомогательные
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Вызываем обработчик выбора темы при редактировании записи WordPhrase
    await update_word_phrase_ask_topic(bot.auxiliary_msgs['cbq'][message.chat.id], session, state, bot)


# Редактирование - ШАГ 1.5: ОТМЕНА фильтра по темам
@vocabulary_router.callback_query(F.data == 'cancel_find_topic_by_matches', IsKeyInStateFilter('word_to_update'))
async def cancel_find_topic_by_matches(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - Обработка отмены фильтра по темам.
    Функция убирает значение ключевого слова в атрибуте бота и вызывает обработчик выбора темы при редактировании
    записи WordPhrase.

    :param callback: Callback-запрос формата "cancel_find_topic_by_matches"
    :param session: Пользовательская сессия
    :param state: Контекст состояния (обязательно наличие атрибута word_to_update)
    :param bot: Объект бота
    :return: None
    """
    await callback.answer('⚠️ Фильтр по теме отменен!', show_alert=True)

    # Удаляем ключевое слово из атрибута бота
    bot.topic_search_keywords[callback.message.chat.id] = None

    # Вызываем обработчик выбора темы при редактировании записи WordPhrase
    await update_word_phrase_ask_topic(callback, session, state, bot)


# Редактирование - ШАГ 2: тема получена, запрос слова / пропуска шага со словом
@vocabulary_router.callback_query(WordPhraseFSM.topic, IsKeyInStateFilter('word_to_update'))
async def update_word_get_topic_ask_word(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 2, получение темы и запрос слова/фразы.
    Функция позволяет запросить новое написание слова/фразы или пропустить шаг.

    :param callback: Callback запрос формата:
                    "updated_word_topic_{topic_id}" (и для заново выбранной темы, и для пропуска шага)
    :param session: Пользовательская сессия
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Получаем id выбранной темы из callback и находим тему
    topic_id = int(callback.data.replace('updated_word_topic_', ''))
    new_topic = await DataBase.get_topic_by_id(session=session, topic_id=topic_id)

    # Если тема изменилась, отправляем сообщение в чат и сохраняем его во вспомогательные с шагом в виде ключа
    if new_topic.id != word_to_update.topic_id:
        msg = await callback.message.answer(text=f'<b>Новая тема:</b> {new_topic.name}')
        edit_word_state = await state.get_state()                           # Получаем состояние для названия ключа
        bot.auxiliary_msgs['add_or_edit_word'][callback.message.chat.id][edit_word_state] = msg

    # Записываем id выбранной темы в контекст состояния
    await state.update_data(topic_id=topic_id)

    # Редактируем баннер и клавиатуру
    btns = {'Редактировать значение ✏️': word_to_update.word}
    kbds = add_new_or_edit_word_main_btns(
        pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await callback.message.edit_caption(
        caption=banners_details.update_word_step_2.format(**caption), reply_markup=kbds
    )

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Переходим в состояние ввода слова
    await state.set_state(WordPhraseFSM.word)


# Редактирование - ШАГ 3: отредактированное слово получено, запрос транскрипции / пропуска шага с транскрипцией
@vocabulary_router.message(WordPhraseFSM.word, IsKeyInStateFilter('word_to_update'))
async def update_word_get_word_ask_transcription(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 3, получение отредактированного слова/фразы и запрос транскрипции.
    Функция позволяет запросить новую транскрипцию или пропустить шаг.

    :param message: Сообщение пользователя с отредактированным словом/фразой
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Делаем валидацию введённого значения
    if not re.match(PATTERN_WORD, message.text):
        msg_text = word_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем слово/фразу в контекст состояния
    await state.update_data(word=message.text)

    # Переотправка сообщения с новыми данными с пометкой шага
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Новое значение:</b>')

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Редактируем баннер и клавиатуру
    btns = {'Редактировать значение ✏️': word_to_update.transcription}
    kbds = add_new_or_edit_word_main_btns(
        pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.update_word_step_3.format(**caption), reply_markup=kbds
    )

    # Сохраняем клавиатуру
    bot.reply_markup_save[message.chat.id] = kbds

    # Переходим в состояние ввода транскрипции
    await state.set_state(WordPhraseFSM.transcription)


# Редактирование - ШАГ 3 VAR: этап редактирования слова пропущен, запрос транскрипции / пропуска шага с транскрипцией
@vocabulary_router.callback_query(WordPhraseFSM.word, IsKeyInStateFilter('word_to_update'), F.data.contains('pass'))
async def update_word_pass_word_ask_transcription(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 3 VAR: этап редактирования слова пропущен, запрос транскрипции.
    Функция позволяет запросить новую транскрипцию или пропустить шаг.

    :param callback: Callback запрос формата "pass"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Записываем в контекст состояния атрибут word
    await state.update_data(word=word_to_update.word)

    # Редактируем баннер и клавиатуру
    btns = {'Редактировать значение ✏️': word_to_update.transcription}
    kbds = add_new_or_edit_word_main_btns(
        pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_step_3.format(**caption), reply_markup=kbds
    )

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Переходим в состояние ввода транскрипции
    await state.set_state(WordPhraseFSM.transcription)


# Редактирование - ШАГ 4: транскрипция получена, запрос перевода / пропуска шага с переводом
@vocabulary_router.message(WordPhraseFSM.transcription, IsKeyInStateFilter('word_to_update'))
async def update_word_get_transcription_ask_translation(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 4, получение транскрипции и запрос перевода.
    Функция позволяет запросить новый перевод или пропустить шаг.

    :param message: Сообщение пользователя с отредактированной транскрипцией
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Переотправка сообщения с новыми данными с пометкой шага
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Новая транскрипция:</b>')

    # Записываем транскрипцию в контекст состояния
    transcription = message.text if len(message.text) > 1 else PLUG_TEMPLATE
    await state.update_data(transcription=transcription)

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Редактируем баннер и клавиатуру
    btns = {'Редактировать значение ✏️': word_to_update.translate}
    kbds = add_new_or_edit_word_main_btns(
        pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.update_word_step_4.format(**caption), reply_markup=kbds
    )

    # Переходим в состояние ввода перевода
    await state.set_state(WordPhraseFSM.translate)


# Редактирование - ШАГ 4 VAR: этап редактирования транскрипции пропущен, запрос перевода / пропуска шага с переводом
@vocabulary_router.callback_query(
    WordPhraseFSM.transcription, IsKeyInStateFilter('word_to_update'), F.data.contains('pass'))
async def update_word_pass_transcription_ask_translate(callback: types.CallbackQuery, state: FSMContext, bot: Bot) \
        -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 4 VAR: этап редактирования транскрипции пропущен, запрос перевода.
    Функция позволяет запросить новый перевод или пропустить шаг.

    :param callback: Callback запрос формата "pass"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Записываем в контекст состояния атрибут transcription
    await state.update_data(transcription=word_to_update.transcription)

    # Редактируем баннер и клавиатуру
    btns = {'Редактировать значение ✏️': word_to_update.translate}
    kbds = add_new_or_edit_word_main_btns(
        pass_step=True, level=2, sizes=(2, 1, 1, 2), cancel_page_address=cancel_page, btns=btns
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_step_4.format(**caption), reply_markup=kbds
    )

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Переходим в состояние ввода перевода
    await state.set_state(WordPhraseFSM.translate)


# Редактирование - ШАГ 5: перевод получен, запрос подтверждения изменений
@vocabulary_router.message(WordPhraseFSM.translate, IsKeyInStateFilter('word_to_update'))
async def update_word_get_translate_ask_confirm(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 5, получение перевода и запрос подтверждения изменений.

    :param message: Сообщение пользователя с отредактированным переводом
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Переотправка сообщения с новыми данными с пометкой шага
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Новый перевод:</b>')

    # Записываем слово в контекст состояния
    translate = message.text if len(message.text) > 1 else PLUG_TEMPLATE
    await state.update_data(translate=translate)

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Редактируем баннер и клавиатуру
    btns = {'Подтвердить изменения ✅': 'pass'}
    kbds = add_new_or_edit_word_main_btns(btns=btns, level=2, sizes=(2, 1, 2), cancel_page_address=cancel_page)
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.update_word_step_5.format(**caption), reply_markup=kbds
    )

    # Переходим в состояние ввода контекста
    await state.set_state(WordPhraseFSM.context)


# Редактирование - ШАГ 5 VAR: этап редактирования перевода пропущен, запрос подтверждения изменений
@vocabulary_router.callback_query(
    WordPhraseFSM.translate, IsKeyInStateFilter('word_to_update'), F.data.contains('pass'))
async def update_word_pass_translate_ask_confirm(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы WordPhrase - шаг 5 VAR: пропуск перевода и запрос подтверждения изменений.

    :param callback: Callback запрос формата "pass"
    :param state: Контекст состояния
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Записываем в контекст состояния атрибут transcription
    await state.update_data(translate=word_to_update.translate)

    # Редактируем баннер и клавиатуру
    btns = {'Подтвердить изменения ✅': 'pass'}
    kbds = add_new_or_edit_word_main_btns(btns=btns, level=2, sizes=(2, 1, 2), cancel_page_address=cancel_page)
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_step_5.format(**caption), reply_markup=kbds
    )

    # Переходим в состояние ввода контекста
    await state.set_state(WordPhraseFSM.context)


# Редактирование - ШАГ 6: подтверждение изменений WordPhrase, запрос завершения / дальнейших действий с примерами
# Здесь же обработка отмены добавления нового примера Context
@vocabulary_router.callback_query(WordPhraseFSM.context, IsKeyInStateFilter('word_to_update'),
                                  F.data.contains('pass') | F.data.contains('edit_word_cancel_add_example'))
async def update_word_wait_context(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Редактирование слова/фразы WordPhrase. Запрос завершения или дальнейших действий с примерами Context.
    Здесь же обработка отмены добавления нового примера Context.

    :param callback: Callback-запрос формата "pass" или "edit_word_cancel_add_example"
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Забираем все введённые данные
    data: dict = await state.get_data()
    editing_word = data.get('word_to_update')
    word_id = data.get('word_to_update').id
    cancel_page_address = data.get('page_address')

    # Если нажата кнопка "Подтвердить изменения", редактируем в БД запись WordPhrase и отправляем сообщение пользователю
    if callback.data == 'pass':

        # Проверяем, внесены ли изменения пользователем в запись
        have_changes = False
        for key, value in inspect(editing_word).attrs.items():
            if key in ('topic_id', 'word', 'transcription', 'translate'):
                if value.value != data.get(key):
                    have_changes = True
        if not have_changes:
            await callback.answer('⚠️ Упс! В записи не было изменений', show_alert=True)

        # Если внесены изменения, редактируем в БД запись WordPhrase
        else:
            is_updated = False
            updating_dict = {
                'topic_id': data['topic_id'],
                'word': data['word'],
                'transcription': data['transcription'],
                'translate': data['translate']
            }
            try:
                is_updated = await DataBase.update_word_phrase(session, word_id, updating_dict)
                await callback.answer('✅ Запись обновлена!', show_alert=True)
            except (Exception, ) as e:
                await callback.answer(oops_with_error_msg_template.format(error=e), show_alert=True)

            # Обновляем данные в ключе word_to_update в контексте (для дальнейшей работы с примерами)
            if is_updated:
                word_to_update = await DataBase.get_word_phrase_by_id(session, word_id)
                await state.update_data(word_to_update=word_to_update)

    # Забираем слово с обновленными данными
    word_with_new_date = await DataBase.get_word_phrase_by_id(session, word_id)

    # Обновляем баннер и клавиатуру
    btns = {
        'Вернуться к записям 📖': cancel_page_address,
        'Добавить ещё пример ➕': f'add_more_examples_to_word_{word_id}',
        'Управление примерами 📝': 'edit_context',
        'Редактировать заново ✏️': f'update_word_{word_id}',
    }
    kbds = add_new_or_edit_word_main_btns(level=2, btns=btns, cancel_possible=False, sizes=(2, 1, 1))
    bot.reply_markup_save[callback.message.chat.id] = kbds
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_with_new_date)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_step_6.format(**caption), reply_markup=kbds
    )

    # Очищаем вспомогательные сообщения.
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # INFO: на выходе остаётся состояние WordPhraseFSM.context, IsKeyInStateFilter('word_to_update') на случай, если
    #       нужно будет дальше работать с примерами Context
