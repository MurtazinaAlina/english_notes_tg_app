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
1. При выборе редактирования в FSMContext добавляется ключ "word_to_update" с редактируемым объектом WordPhrase, он
   используется для ветвления логики контроллеров.
2. При отмене редактирования происходит автоматическое перенаправление на последнюю просмотренную страницу словаря.
   Дополнительный обработчик не требуется, переход прописан в callback_data кнопки отмены редактирования.
3. Ключ для фильтра по темам при редактировании сохраняется в bot.topic_search_keywords[chat_id], НЕ в FSMContext.
4. Поиск темы. Отправка сообщения с запросом ввода ключа поиска темы и отмена ввода обрабатываются в topic_actions.py:
   find_topic_by_matches_ask_keywords          - запрос ключевого слова поиска темы
   cancel_find_topic                           - отмена поиска темы
5. При редактировании примера в контекст добавляется ключ 'editing_context_obj' с редактируемым объектом Context.
"""
import os
import re
import time
from typing import BinaryIO

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details
from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter, IsKeyNotInStateFilter
from app.keyboards.inlines import get_inline_btns, get_pagination_btns, get_kbds_with_navi_header_btns
from app.handlers.user_private.menu_processing import vocabulary
from app.utils.custom_bot_class import Bot
from app.utils.xsl_tools import export_vcb_data_to_xls_file, import_data_from_xls_file
from app.utils.paginator import Paginator, pages
from app.utils.tts import speak_text, clear_audio_examples_from_chat
from app.common.tools import get_upd_word_and_cancel_page_from_context, get_topic_kbds_helper, check_if_words_exist, \
    get_word_phrase_caption_formatting, clear_auxiliary_msgs_in_chat, try_alert_msg, modify_callback_data, \
    validate_context_example
from app.common.msg_templates import word_msg_template, oops_with_error_msg_template, oops_try_again_msg_template, \
    word_validation_not_passed_msg_template, context_validation_not_passed_msg_template, context_example_msg_template
from app.common.fsm_classes import WordPhraseFSM, TopicFSM, ImportXlsFSM
from app.settings import PER_PAGE_VOCABULARY, PATTERN_WORD, PER_PAGE_INLINE_TOPICS


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

# Редактирование слова/фразы - ШАГ 1, вызов основного окна редактирования записи с выбором действия.
# Обработчик также принудительно вызывается после изменения данных, добавления нового примера
@vocabulary_router.callback_query(F.data.startswith('update_word_'))
async def edit_word_phrase_main(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) \
        -> None:
    """
    Редактирование слова/фразы WordPhrase - ШАГ 1, вызов основного окна редактирования с выбором действия.
    Обработчик также принудительно вызывается после изменения данных, добавления нового примера.

    :param callback: CallbackQuery-запрос формата "update_word_{WordPhrase.id}"
    :param state: Контекст состояния FSM с ключом "page_address" с адресом текущей страницы просмотра записей
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Получаем слово/фразу по id из callback
    word_id = int(callback.data.split('_')[-1])
    word_to_update = await DataBase.get_word_phrase_by_id(session, word_id)

    # Если слово/фраза не найдено, выходим из обработчика
    if word_to_update is None:
        await callback.answer('⚠️ Слово/фраза не найдены!', show_alert=True)
        return

    # Записываем редактируемое слово/фразу в контекст состояния
    await state.update_data(word_to_update=word_to_update)

    # Формируем и сохраняем клавиатуру
    _, cancel_page_address = await get_upd_word_and_cancel_page_from_context(state)
    btns = {
        'Тема 🖌': 'edit_word_topic_page_1',
        'Слово/фраза 🖌': 'edit_word:word',
        'Транскрипция 🖌': 'edit_word:transcription',
        'Перевод 🖌': 'edit_word:translate',
        'Примеры 🖌': 'edit_word_examples',
        'Новый пример ➕': 'edit_word_add_new_example',
        'Вернуться к просмотру словаря ⬅': cancel_page_address,
    }
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, menu_name='vocabulary', sizes=(2, 2, 2, 2, 1))
    bot.reply_markup_save[callback.message.chat.id] = kbds

    # Редактируем баннер и клавиатуру
    caption_formatting = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    caption = banners_details.update_word_main.format(**caption_formatting)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)


# Редактирование слова/фразы - отмена ввода данных, возврат к основному меню редактирования записи
@vocabulary_router.callback_query(F.data == 'return_to_edit_word_main', IsKeyInStateFilter('word_to_update'))
async def return_to_edit_word_main(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование слова/фразы - отмена ввода данных, возврат к основному меню редактирования записи.

    :param callback: CallbackQuery-запрос формата "return_to_edit_word_main"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "word_to_update"
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback
    await callback.answer('⚠️ Действие отменено!', show_alert=True)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Если была отмена выбора/поиска темы, возвращаем основную клавиатуру
    state_now = await state.get_state()
    if state_now == 'WordPhraseFSM:topic' or state_now == 'TopicFSM:search_keywords':
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_reply_markup(
            reply_markup=bot.reply_markup_save[callback.message.chat.id]
        )

    # Сбрасываем состояние ввода
    await state.set_state(None)


# Редактирование слова/фразы - изменение слова, транскрипции или перевода, ШАГ 1: запрос новых данных
@vocabulary_router.callback_query(F.data.startswith('edit_word:'), IsKeyInStateFilter('word_to_update'))
async def edit_word_transcription_translate_ask_for_data(callback: types.CallbackQuery, state: FSMContext, bot: Bot) \
        -> None:
    """
    Редактирование слова/фразы - изменение слова, транскрипции или перевода, ШАГ 1: запрос новых данных.

    :param callback: CallbackQuery-запрос формата "edit_word:<атрибут>"
    :param state: Контекст состояния FSM с ключом "word_to_update"
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста информацию о редактируемой записи
    state_data = await state.get_data()
    edited_word_obj = state_data.get('word_to_update')

    # Из callback забираем название редактируемого атрибута
    edited_attr = callback.data.split(':')[-1]

    # Получаем текущее значение атрибута для вывода в клавиатуре
    current_data = getattr(edited_word_obj, edited_attr)

    # Отправляем информационное сообщение с кнопкой отмены и вывода текущих данных
    btns = {
        'Отмена ❌': 'return_to_edit_word_main',
        'Текст сейчас 📝': f'switch_inline_query_current_chat_{current_data}'
    }
    kbds = get_inline_btns(btns=btns)
    attrs_dict = {
        'word': 'слова/фразы',
        'transcription': 'транскрипции',
        'translate': 'перевода'
    }
    msg_text = (f'Введите <b>новое значение {attrs_dict.get(edited_attr)}</b> или нажмите <i>"Текст сейчас 📝"</i> '
                f'для подгрузки в строку ввода текущих данных для удобной корректировки.')
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Определяем требуемое состояние ввода и устанавливаем его
    required_state = getattr(WordPhraseFSM, edited_attr)
    await state.set_state(required_state)


# Редактирование слова/фразы - изменение слова, транскрипции или перевода, ШАГ 2: новые данные получены, обновление в БД
@vocabulary_router.message(StateFilter(WordPhraseFSM.word, WordPhraseFSM.transcription, WordPhraseFSM.translate),
                           IsKeyInStateFilter('word_to_update'))
async def edit_word_get_data_except_topic_or_context(
        message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Редактирование слова/фразы - изменение слова, транскрипции или перевода, ШАГ 2: новые данные получены,
    обновление в БД.

    :param message: Текстовое сообщение с новыми данными
    :param state: Контекст состояния FSM с ключом "word_to_update"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Из контекста забираем название редактируемого атрибута
    current_state = await state.get_state()             # WordPhraseFSM:transcription | WordPhraseFSM:translate | ...
    attr_name = current_state.split(':')[-1]

    # Делаем валидацию введённого значения
    if current_state == WordPhraseFSM.word:
        if not re.match(PATTERN_WORD, message.text):
            msg_text = word_validation_not_passed_msg_template
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
            await bot.delete_message(message.chat.id, message.message_id)
            return

    # Удаляем сообщение с данными от пользователя и информационное сообщение с кнопками
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # Забираем из контекста информацию о редактируемой записи
    state_data = await state.get_data()
    edited_word_obj = state_data.get('word_to_update')

    # Обновляем данные заметки в БД и выводим уведомление
    try:
        is_updated = await DataBase.update_word_phrase(session, edited_word_obj.id, {attr_name: message.text})
    except (Exception, ) as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    if is_updated:
        await try_alert_msg(bot, message.chat.id, '✅ Данные успешно обновлены!', if_error_send_msg=True)
    else:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)

    # Возвращаемся к основному окну редактирования записи
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][message.chat.id], f'update_word_{edited_word_obj.id}'
    )
    await edit_word_phrase_main(modified_callback, state, bot, session)


# Редактирование слова/фразы - изменение темы, ШАГ 1: запрос новой темы.
# Обработчик также принудительно вызывается после применения/отмены фильтра по темам, при пагинации списка тем
@vocabulary_router.callback_query(F.data.startswith('edit_word_topic_page_'), IsKeyInStateFilter('word_to_update'))
async def edit_word_ask_for_topic(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) \
        -> None:
    """
    Редактирование слова/фразы - изменение темы, ШАГ 1: запрос новой темы.
    Обработчик также принудительно вызывается после применения/отмены фильтра по темам, при пагинации списка тем.

    :param callback: CallbackQuery-запрос формата 'edit_word_topic_page_<page_number>'
    :param state: Контекст состояния FSM с ключом "word_to_update"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Забираем из контекста информацию о редактируемой записи
    state_data = await state.get_data()
    edited_word_obj = state_data.get('word_to_update')

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Формируем клавиатуру и информацию о темах
    page = int(callback.data.split('_')[-1])
    per_page = PER_PAGE_INLINE_TOPICS
    search_key = bot.topic_search_keywords.get(callback.message.chat.id)
    topic_name_prefix = 'updated_word_topic_'
    kbds, topic_info_for_caption = await get_topic_kbds_helper(
        bot, chat_id=callback.message.chat.id, session=session, level=2, menu_name='vocabulary',
        menu_details=f'edit_word_topic', topic_name_prefix=topic_name_prefix, search_key=search_key, page=page,
        per_page=per_page, sizes=(2,)
    )

    # Редактируем клавиатуру баннера
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_reply_markup(reply_markup=kbds)

    # Отправляем информационное сообщение с кнопкой отмены и вывода текущих данных
    msg_text = ('Выберите новое значение темы.\n\n'
                'Текущая тема выбрана: <b>"{topic}"</b>\n'
                'Показаны темы {first_topic}-{last_topic} из {topics_total}')
    msg_text = msg_text.format(**topic_info_for_caption, topic=edited_word_obj.topic.name)
    info_msg_kbds = get_inline_btns(btns={'Отмена ❌': 'return_to_edit_word_main'})
    msg = await callback.message.answer(text=msg_text, reply_markup=info_msg_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ввода темы
    await state.set_state(WordPhraseFSM.topic)


# Редактирование слова/фразы - изменение темы, ШАГ 1.5: ПРИМЕНЕНИЕ фильтра по темам
@vocabulary_router.message(F.text, StateFilter(TopicFSM.search_keywords), IsKeyInStateFilter('word_to_update'))
async def edit_word_find_topic_by_matches_get_keywords(
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

    # Возвращаемся к окну редактирования записи - выбор темы
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][message.chat.id], 'edit_word_topic_page_1'
    )
    await edit_word_ask_for_topic(modified_callback, state, bot, session)


# Редактирование слова/фразы - изменение темы, ШАГ 1.5: ОТМЕНА фильтра по темам
@vocabulary_router.callback_query(F.data == 'cancel_find_topic_by_matches', IsKeyInStateFilter('word_to_update'))
async def edit_word_cancel_find_topic_by_matches(
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

    # Возвращаемся к окну редактирования записи - выбор темы
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][callback.message.chat.id], 'edit_word_topic_page_1'
    )
    await edit_word_ask_for_topic(modified_callback, state, bot, session)


# Редактирование слова/фразы - изменение темы, ШАГ 2: ПРИМЕНЕНИЕ выбора темы, обновление в БД
@vocabulary_router.callback_query(WordPhraseFSM.topic, IsKeyInStateFilter('word_to_update'))
async def edit_word_get_new_topic(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы - изменение темы, ШАГ 2: ПРИМЕНЕНИЕ выбора темы, обновление в БД.

    :param callback: Callback-запрос формата "updated_word_topic_{Topic.id}"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом 'word_to_update'
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем данные из контекста
    edited_word_obj, _ = await get_upd_word_and_cancel_page_from_context(state)

    # Получаем id выбранной темы из callback и находим тему
    topic_id = int(callback.data.replace('updated_word_topic_', ''))
    new_topic = await DataBase.get_topic_by_id(session=session, topic_id=topic_id)

    if new_topic.id == edited_word_obj.topic_id:
        await try_alert_msg(bot, callback.message.chat.id, '⚠️ Тема не изменилась!', if_error_send_msg=True)
        return

    # Обновляем данные заметки в БД и выводим уведомление
    try:
        is_updated = await DataBase.update_word_phrase(session, edited_word_obj.id, {'topic_id': topic_id})
    except (Exception, ) as e:
        await try_alert_msg(bot, callback.message.chat.id, oops_with_error_msg_template.format(error=str(e)),
                            if_error_send_msg=True)
        return

    if is_updated:
        await try_alert_msg(bot, callback.message.chat.id, '✅ Данные успешно обновлены!', if_error_send_msg=True)
    else:
        await try_alert_msg(bot, callback.message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)

    # Возвращаемся к основному окну редактирования записи
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][callback.message.chat.id], f'update_word_{edited_word_obj.id}'
    )
    await edit_word_phrase_main(modified_callback, state, bot, session)


# РЕДАКТИРОВАНИЕ - РАБОТА С ПРИМЕРАМИ

# Редактирование слова/фразы - просмотр примеров Context.
# Обработчик также принудительно вызывается после отмены изменений примеров, завершения удаления/редактирования примера
@vocabulary_router.callback_query(F.data == 'edit_word_examples', IsKeyInStateFilter('word_to_update'))
async def edit_word_show_examples(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование слова/фразы - просмотр примеров Context.
    Обработчик также принудительно вызывается после отмены изменений примеров, завершения удаления/редактирования
    примера.

    Функция выводит в чат сообщения с информацией о примерах с доступом к редактированию/удалению.

    :param callback: Callback-запрос формата "edit_word_examples"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем данные из контекста
    word_to_update, _ = await get_upd_word_and_cancel_page_from_context(state)

    # Очищаем сообщения в чате
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Отправляем в чат примеры с inline кнопками редактирования/удаления
    for example in word_to_update.context:
        msg = await callback.message.answer(
            text=context_example_msg_template.format(
                example=example.example, created=example.created, updated=example.updated
            ),
            reply_markup=get_inline_btns(
                btns={'Изменить 🖌': f'update_context_{example.id}', 'Удалить 🗑': f'delete_context_{example.id}'})
        )
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# ДОБАВЛЕНИЕ ПРИМЕРА

# Редактирование WordPhrase - добавить НОВЫЙ пример Context
@vocabulary_router.callback_query(F.data.startswith('edit_word_add_new_example'), IsKeyInStateFilter('word_to_update'))
async def edit_word_add_new_context_ask_text(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Редактирование WordPhrase - добавить НОВЫЙ пример Context.

    :param callback: Callback-запрос формата "edit_word_add_new_example"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Удаляем из чата сообщения с примерами и информационные сообщения (если есть)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Отправляем информационное сообщение с запросом ввода и кнопкой отмены действия
    kbds = get_inline_btns(btns={'Отмена ❌': 'return_to_edit_word_main'})
    msg_text = 'Введите текст <b>нового примера</b>'
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ввода примера к заметке
    await state.set_state(WordPhraseFSM.context)


# Добавление нового введённого примера из add_new_context_ask_text, запрос завершения / дальнейших действий с примерами
@vocabulary_router.message(WordPhraseFSM.context, IsKeyNotInStateFilter('editing_context_obj'),
                           IsKeyInStateFilter('word_to_update'))
async def edit_word_add_new_context_get_text(message: types.Message, state: FSMContext, session: AsyncSession,
                                             bot: Bot) -> None:
    """
    Добавление нового введённого примера Context из add_new_context, запрос завершения/дальнейших действий с примерами.

    :param message: Сообщение пользователя с новым примером Context
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update' и БЕЗ ключа 'editing_context_obj'
                  для ветвления с редактированием примера WordPhrase
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Делаем валидацию ввода, при некорректном значении уведомляем и выходим из функции
    if not validate_context_example(message.text):
        msg_text = context_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Сохраняем сообщение во вспомогательные
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Забираем WordPhrase из контекста
    state_data = await state.get_data()
    word_to_update = state_data.get('word_to_update')

    # Создаём новый пример Context в БД и отправляем уведомление с результатом
    try:
        data = {'context': message.text}
        created_example = await DataBase.create_context_example(session, data, word_id=word_to_update.id)
        if created_example:
            await try_alert_msg(bot, message.chat.id, '✅ Пример успешно добавлен!', if_error_send_msg=True)
    except (Exception,) as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Очищаем вспомогательные сообщения
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # Возвращаемся к основному окну редактирования записи
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][message.chat.id], f'update_word_{word_to_update.id}'
    )
    await edit_word_phrase_main(modified_callback, state, bot, session)


# УДАЛЕНИЕ ПРИМЕРОВ

# Отмена редактирования/удаления примера Context
@vocabulary_router.callback_query(F.data == 'cancel_update_context', IsKeyInStateFilter('word_to_update'))
async def cancel_update_context(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Отмена редактирования/удаления примера Context.
    Удаляет информационное сообщение, сбрасывает значение атрибутов WordPhraseFSM.

    :param callback: Callback-запрос формата "cancel_update_context"
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :return: None
    """
    await callback.answer('⚠️ Действие отменено!', show_alert=True)

    # Удаляем ключ editing_context_obj с объектом Context из FSM
    await state.update_data(editing_context_obj=None)

    # Возвращаемся к основному окну редактирования примеров
    await edit_word_show_examples(callback, state, bot)


# Редактирование WordPhrase, удаление примера Context - ШАГ 1, запрос подтверждения
@vocabulary_router.callback_query(F.data.startswith('delete_context_'), IsKeyInStateFilter('word_to_update'))
async def edit_word_delete_example_ask_confirm(callback: types.CallbackQuery, session: AsyncSession, bot: Bot, ) \
        -> None:
    """
    Редактирование WordPhrase, удаление примера Context - ШАГ 1, запрос подтверждения.

    :param callback: Callback-запрос формата "delete_context_{Context.id}"
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Очищаем вспомогательные сообщения
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Получаем id примера из callback и его объект из БД
    context_id = int(callback.data.replace('delete_context_', ''))
    context_obj = await DataBase.get_context_by_id(session, context_id)

    # Отправляем пользователю информационное сообщение с запросом подтверждения действия или отмены
    msg_text = f'⚠️ Вы действительно хотите удалить пример <b>"{context_obj.example}"</b>?'
    btns = {'Удалить 🗑': f'confirm_delete_context_{context_id}', 'Отмена ❌': 'cancel_update_context'}
    kbds = get_inline_btns(btns=btns)
    info_msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(info_msg)


# Редактирование WordPhrase, удаление примера Context - ШАГ 2, удаление из БД
@vocabulary_router.callback_query(F.data.startswith('confirm_delete_context_'), IsKeyInStateFilter('word_to_update'))
async def edit_word_delete_example_get_confirm(callback: types.CallbackQuery, session: AsyncSession, bot: Bot,
                                               state: FSMContext) -> None:
    """
    УРедактирование WordPhrase, удаление примера Context - ШАГ 2, удаление из БД.

    :param callback: Callback-запрос формата "delete_context_{context_id}"
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом 'word_to_update'
    :return: None
    """

    # Получаем id примера из callback
    context_id = int(callback.data.replace('confirm_delete_context_', ''))

    # Удаляем пример Context из БД
    is_del = False
    try:
        is_del = await DataBase.delete_context_by_id(session, context_id)
    except Exception as e:
        await callback.answer(oops_with_error_msg_template.format(error=str(e)), show_alert=True)

    # При успехе:
    if is_del:
        await callback.answer('✅ Пример удалён', show_alert=True)

        try:
            # Обновляем данные 'word_to_update' в контексте FSM
            state_data = await state.get_data()
            word_to_update = state_data.get('word_to_update')
            word_to_update = await DataBase.get_word_phrase_by_id(session, word_to_update.id)
            await state.update_data(word_to_update=word_to_update)

            # Обновляем описание баннера в основном окне
            caption_formatting = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
            caption = banners_details.update_word_main.format(**caption_formatting)
            await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
                reply_markup=bot.reply_markup_save[callback.message.chat.id], caption=caption)
        except (Exception,):
            pass

        # Возвращаемся к основному окну редактирования примеров
        await edit_word_show_examples(callback, state, bot)


# РЕДАКТИРОВАНИЕ ПРИМЕРОВ

# Редактирование WordPhrase, редактирование примера Context - ШАГ 1, запрос ввода нового текста примера
@vocabulary_router.callback_query(F.data.startswith('update_context_'), IsKeyInStateFilter('word_to_update'))
async def update_context_example_ask_new_text(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Редактирование WordPhrase, редактирование примера Context - ШАГ 1, запрос ввода нового текста примера.
    Функция добавляет в FSMState дополнительный ключ 'editing_context_obj' с редактируемым объектом Context.

    :param callback: Callback-запрос формата "update_context_{Context.id}"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем id примера из callback и объект примера из БД
    context_id = int(callback.data.replace('update_context_', ''))
    context_obj = await DataBase.get_context_by_id(session, context_id)

    # Помещаем объект примера в FSM под ключом 'editing_context_obj'
    await state.update_data(editing_context_obj=context_obj)

    # Очищаем вспомогательные сообщения
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Отправляем сообщение с запросом ввода нового текста примера и кнопкой отмены. Сохраняем во вспомогательные
    current_data = getattr(context_obj, 'example')  # Текущий текст примера
    btns = {
        'Отмена ❌': 'cancel_update_context',
        'Текст сейчас 📝': f'switch_inline_query_current_chat_{current_data}'
    }
    msg_text = (f'<b>Редактирование примера</b>:\n "{context_obj.example}"\n\n'
                f'Введите новый текст примера или нажмите <i>"Текст сейчас 📝"</i> для подгрузки в строку ввода '
                f'текущих данных для удобной корректировки.')
    info_msg = await callback.message.answer(text=msg_text, reply_markup=get_inline_btns(btns=btns, sizes=(2,)))
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(info_msg)

    # Устанавливаем состояние ввода текста примера
    await state.set_state(WordPhraseFSM.context)


# Редактирование WordPhrase, редактирование примера Context - ШАГ 2, сохранение нового текста примера в БД
@vocabulary_router.message(WordPhraseFSM.context, IsKeyInStateFilter('editing_context_obj', 'word_to_update'))
async def update_context_example_get_new_text(
        message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Редактирование WordPhrase, редактирование примера Context - ШАГ 2, сохранение нового текста примера в БД.

    :param message: Текст сообщения с новым текстом примера
    :param state: Контекст состояния с объектом Context в ключе 'editing_context_obj' и объектом WordPhrase в
                  ключе 'word_to_update'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Делаем валидацию ввода, при некорректном значении уведомляем и выходим из функции
    if not validate_context_example(message.text):
        msg_text = context_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Сохраняем сообщение во вспомогательные
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Получаем данные из контекста
    state_data = await state.get_data()
    context_obj = state_data['editing_context_obj']
    word_to_update = state_data.get('word_to_update')

    # Обновляем текст примера (Context.example)
    is_updated = False
    try:
        is_updated = await DataBase.update_context_by_id(session, context_obj.id, example=message.text)
    except Exception as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Если текст примера (Context.example) был успешно обновлен:
    if is_updated:
        await try_alert_msg(bot, message.chat.id, '✅ Данные успешно изменены!', if_error_send_msg=True)
        try:
            # Обновляем объект WordPhrase в контексте
            word_to_update = await DataBase.get_word_phrase_by_id(session, word_to_update.id)
            await state.update_data(word_to_update=word_to_update)

            # Редактируем caption в основном сообщении
            caption_formatting = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
            caption = banners_details.update_word_main.format(**caption_formatting)
            await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
                reply_markup=bot.reply_markup_save[message.chat.id], caption=caption
            )
        except (Exception,):
            pass

        # Возвращаемся к основному окну редактирования примеров
        await edit_word_show_examples(bot.auxiliary_msgs['cbq'][message.chat.id], state, bot)

    # Удаляем ключ editing_context_obj с объектом Context из FSM
    await state.update_data(editing_context_obj=None)
