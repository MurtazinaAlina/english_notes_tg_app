"""
Обработка наполнения сообщения бота: баннер + описание + клавиатура.
"""
from aiogram.fsm.context import FSMContext
from aiogram.types import InputMediaPhoto, CallbackQuery, FSInputFile, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DataBase
from app.database.models import Banner
from app.banners import banners_details as bnr
from app.common.tools import clear_all_data, check_if_authorized, get_topic_kbds_helper, \
    get_word_phrase_caption_formatting, clear_auxiliary_msgs_in_chat, check_if_user_has_topics, check_if_words_exist
from app.common.msg_templates import stat_msg_template
from app.common.fsm_classes import GigaAiFSM
from app.keyboards.inlines import (get_kbds_start_page_btns, get_auth_btns, get_kbds_with_navi_header_btns,
                                   MenuCallBack, get_inline_btns, get_kbds_tests_btns)
from app.utils.custom_bot_class import Bot
from app.utils.tts import speak_text
from app.settings import TEST_EN_RU_WORD, TEST_EN_RU_AUDIO, TEST_RU_EN_WORD


# Стартовая страница бота, приветствие + основное меню + кнопки аутентификации.
# Обработка кнопки "НА ГЛАВНУЮ", запросы формата "menu:0:start_page::1"
async def start_page(
        bot: Bot,
        session: AsyncSession,
        state: FSMContext | None,
        callback: CallbackQuery | None,
        user_id: int | None,
        menu_name: str = 'start_page',
        chat_id: int | None = None,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup]:
    """
    Функция формирует и возвращает необходимый баннер с описанием и клавиатуру для обработки стартовой страницы бота.

    При команде /start или при обработке кнопки "НА ГЛАВНУЮ".

    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param callback: Callback-запрос
    :param user_id: ID пользователя - при пройденной аутентификации пользователя
    :param menu_name: Название меню для формирования баннера, по умолчанию 'start_page'
    :param chat_id: ID чата
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике
    """

    if callback:
        chat_id = callback.message.chat.id

    # При обработке кнопки "НА ГЛАВНУЮ" чистим состояние, ключевые атрибуты и удаляем все вспомогательные сообщения.
    await clear_all_data(bot, chat_id, state)

    # Получаем соответствующий странице баннер
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=banner.description)

    # Получаем из БД имя пользователя (email) - при пройденной аутентификации
    username = None
    if user_id:
        user = await DataBase.get_user_by_id(session, user_id)
        username = user.email

    # Формируем кнопки inline стартового меню
    kbds = get_kbds_start_page_btns(username=username)

    return image, kbds


# Сформировать баннер с описанием и клавиатуру для страницы авторизации/профиля пользователя.
# Обработка запросов Sign in, Log in, Log out, профиля пользователя, настроек профиля.
# Вызывается непосредственно из обработчиков, MenuCallBack из основного обработчика user_menu() не обрабатывает
# (за счёт очерёдности роутера auth_router в диспетчере)
async def auth_page(
        session: AsyncSession,
        menu_details: str,
        menu_name: str = 'auth',
        bot: Bot | None = None,
        chat_id: int | None = None,
        state: FSMContext | None = None,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup]:
    """
    Функция формирует и возвращает необходимый баннер с описанием и клавиатуру для обработки страниц авторизации
    ("Sign in", "Log in", "Log out").

    :param session: Пользовательская сессия
    :param menu_details: Детали меню для разделения логики обработки в контроллере
    :param menu_name: Название меню для формирования баннера, по умолчанию 'auth'
    :param bot: Объект бота
    :param chat_id: ID чата
    :param state: Контекст состояния FSM
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике
    """

    # Получаем соответствующий странице баннер
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)
    banner_description = banner.description
    kbds = None

    # Обработка запроса профиля ("menu:1:auth:user_profile:1")
    if menu_details == 'user_profile' or menu_details == 'step_back':

        # При обработке "НАЗАД" удаляем все вспомогательные сообщения.
        if menu_details == 'step_back':
            await clear_auxiliary_msgs_in_chat(bot, chat_id)

        # Получаем из БД пользователя
        user = await DataBase.get_user_by_id(session, bot.auth_user_id.get(chat_id))

        # Формируем описание и клавиатуру
        banner_description = bnr.user_profile.format(email=user.email)
        kbds = get_auth_btns(profile=True)

        # Добавляем пользователя в состояние для дальнейшего использования в настройках профиля
        await state.update_data(user=user)

    # Обработка запроса входа в систему ("menu:1:auth:log_in_app:1")
    elif menu_details == 'log_in_app':
        banner_description = bnr.log_in_step_1
        kbds = get_auth_btns(login=True)

    # Обработка запроса регистрации в системе ("menu:1:auth:sign_in_app:1")
    elif menu_details == 'sign_in_app':
        kbds = get_auth_btns()

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=banner_description)

    return image, kbds


# Сформировать баннер с описанием и клавиатуру для страницы словаря.
# Словарь - основное меню раздела, обработка выбора раздела "Выбрать тему" в "Словаре" + пагинация в нём, обработка
# выбора раздела "Управление темами", обработка принудительного вызова vocabulary() из show_vocabulary_words()
async def vocabulary(
        bot: Bot,
        session: AsyncSession,
        state: FSMContext | None,
        level: int,
        menu_details: str,
        menu_name: str = 'vocabulary',
        callback: CallbackQuery | None = None,
        page: int = 1,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Функция формирует и возвращает необходимый баннер с описанием и клавиатуру для обработки страницы словаря.

    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM (проверяется наличие ключа 'search_keywords' для фильтрации тем)
    :param level: Уровень меню
    :param menu_details: Детали меню для разделения логики обработки в контроллере
    :param menu_name: Название меню (для формирования баннера и callback_data MenuCallBack при пагинации),
                      по умолчанию 'vocabulary'
    :param callback: Callback-запрос формата: "menu:1:vocabulary::1", "menu:2:vocabulary:select_topic:{page_number}",
                "menu:2:vocabulary:topic_manager:{page_number}"
    :param page: Текущий номер страницы, по умолчанию 1
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике или None при ошибке
    """

    # Получаем баннер страницы
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)
    caption = banner.description
    kbds = None

    # INFO: Далее редактируем описание баннера и формируем клавиатуру в зависимости от уровня и menu_details:

    # Словарь - основное меню раздела.
    # Вход из стартового меню бота или обработка кнопки "НАЗАД" из разделов словаря с уровнем 2.
    # Обработка запросов формата "menu:1:vocabulary::1"
    if level == 1:

        # Проверяем, что пользователь аутентифицирован
        if not await check_if_authorized(callback, bot, callback.message.chat.id):
            return None

        # При обработке "НАЗАД" чистим состояние, ключевые атрибуты и удаляем все вспомогательные сообщения.
        if menu_details == 'step_back':
            await clear_all_data(bot, callback.message.chat.id, state)

        # Формируем клавиатуру
        btns = {
            'Выбрать тему 🔎': MenuCallBack(level=2, menu_name='vocabulary', menu_details='select_topic').pack(),
            'Все записи 📖': 'select_all_words',
            'Управление темами ⚙️': MenuCallBack(level=2, menu_name='vocabulary', menu_details='topic_manager').pack(),
            'Заметки 📒': 'my_notes_page_1',
            'Импорт/экспорт данных в .xlsx 📑': 'xls_actions'
        }
        kbds = get_kbds_with_navi_header_btns(level=level, btns=btns, sizes=(1, 2, 1))

    # Обработка выбора раздела "Выбрать тему" в "Словаре" + пагинация в нём
    # Запросы формата "menu:2:vocabulary:select_topic:{page_number}"
    elif menu_details == 'select_topic':

        # Сохраняем callback
        bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

        # Проверяем, есть ли у пользователя темы
        if not await check_if_user_has_topics(bot, callback.message.chat.id, session):
            return None

        # Забираем из контекста информацию о поиске
        data = await state.get_data()
        search_keyword = data.get('search_keywords')

        # Формируем клавиатуру и описание
        kbds, topic_info_for_caption = await get_topic_kbds_helper(
            bot, chat_id=callback.message.chat.id, session=session, level=level, menu_name=menu_name,
            menu_details=menu_details, topic_name_prefix='select_topic_id_', search_key=search_keyword, page=page,
            sizes=(2, 2)
        )
        caption = bnr.vcb_descrptn_select_topic.format(**topic_info_for_caption)

    # Обработка принудительного вызова vocabulary() из vocabulary_actions.show_vocabulary_words()
    # MenuCallBack из основного обработчика user_menu() не обрабатывает
    elif menu_details == 'show_word_phrases':

        # Формируем кнопку поиска/отмены поиска
        if bot.word_search_keywords.get(callback.message.chat.id):
            btn = {'Отменить поиск ✖️': 'cancel_search_word_phrase'}
        else:
            btn = {'Найти слово/фразу 🔎': 'search_word_phrase_by_keyword'}

        # Формируем клавиатуру и описание
        kbds = get_kbds_with_navi_header_btns(level=level, btns=btn, sizes=(2, 1))
        caption = bnr.vcb_descrptn_records

    # Обработка выбора раздела "Управление темами"
    # Запросы формата "menu:2:vocabulary:topic_manager:{page_number}"
    elif menu_details == 'topic_manager':
        btns = {
            'Добавить новую тему ➕': 'add_new_topic',
            'Редактировать/удалить темы 📝': 'edit_or_delete_topic'
        }
        kbds = get_kbds_with_navi_header_btns(level=level, btns=btns, sizes=(2, 1))
        caption = bnr.vcb_descrptn_topic_manager

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=caption)

    return image, kbds


# Сформировать баннер с описанием и клавиатуру для начальной страницы меню "Добавить новое слово" (Выбор темы).
# Обработка пагинации с выбором темы при создании нового слова, запросы "menu:1:add_new_word:add_new_word:{page}"
async def add_new_word(
        bot: Bot,
        session: AsyncSession,
        state: FSMContext | None,
        callback: CallbackQuery,
        menu_name: str = 'add_new_word',
        level: int = 1,
        page: int = 1,
        per_page: int = 4,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Функция формирует и возвращает необходимый баннер с описанием и клавиатуру для обработки страницы с выбором темы
    при создании нового слова (Выбор раздела из главного меню + пагинация тем).

    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM.
                  Проверяется наличие ключа 'search_keywords' для фильтрации тем
    :param callback: Callback-запрос формата:
                     "add_new_word" или "menu:1:add_new_word:add_new_word:{page_number}" (при пагинации тем)
    :param menu_name: Название меню (для формирования баннера и callback_data MenuCallBack при пагинации),
                      по умолчанию 'add_new_word'
    :param level: Уровень вложенности меню, по умолчанию 1
    :param page: Текущий номер страницы, по умолчанию 1
    :param per_page: Количество элементов на странице, по умолчанию 4
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике или None при ошибке
    """

    # Проверяем, что пользователь аутентифицирован
    if not await check_if_authorized(callback, bot, callback.message.chat.id):
        return None

    # Запрашиваем данные из контекста состояния
    data = await state.get_data()

    # Проверяем наличие ключа для фильтра по темам
    search_key = data.get('search_keywords', None)

    # Формируем клавиатуру и информацию для баннера
    kbds, topic_info_for_caption = await get_topic_kbds_helper(
        bot, chat_id=callback.message.chat.id, session=session, level=level, menu_name=menu_name,
        menu_details=menu_name, topic_name_prefix='add_word_topic_', search_key=search_key,
        page=page, per_page=per_page
    )

    # Получаем соответствующий меню баннер и редактируем его описание по конкретным данным
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)
    banner_description = bnr.add_new_word_step_1.format(**topic_info_for_caption)

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=banner_description)

    # Возвращаем объект изображения и клавиатуру
    return image, kbds


# Сформировать баннер с описанием и клавиатуру для раздела "Тестирование".
# Страница с выбором типа тестов и основное окно тестирования выбранного типа.
async def tests(
        bot: Bot,
        session: AsyncSession,
        state: FSMContext | None,
        level: int,
        callback: CallbackQuery,
        menu_name: str = 'tests',
        menu_details: str | None = None,
        page: int = 1,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Функция формирует и возвращает необходимый баннер с описанием и клавиатуру для обработки страницы с тестами.
    Обрабатывается страница с выбором типа теста + основное окно тестирования выбранного типа.

    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с возможными ключами: 'test_type': str, 'search_keywords': str,
                 'selected_topic_id': int, 'word_obj': <WordPhrase.object>, 'stat_data' : tuple
    :param level: Уровень вложенности меню
    :param callback: CallbackQuery-запросы формата: "menu:1:tests::1", "menu:2:tests:en_ru_audio:1",
                    "menu:2:tests:ru_en_word:1", "menu:2:tests:en_ru_word_previous:1",
                    "tests_cancel_select_topic", "tests_topic_{TOPIC.id}", "cancel_find_topic_by_matches"
    :param menu_name: Название меню (для формирования баннера и callback_data MenuCallBack при пагинации),
                      по умолчанию 'tests'
    :param menu_details: Детали меню для разделения логики обработки в контроллере
    :param page: Номер текущей страницы, по умолчанию 1
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике или None при ошибке
    """

    # Получаем баннер страницы
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)

    # Тестирование - основное меню раздела с выбором типа теста.
    # Вход из стартового меню бота или обработка кнопки "НАЗАД" из разделов тестирования с уровнем 2.
    # Обработка запросов формата "menu:1:tests::1".
    if level == 1:

        # Проверяем, что пользователь аутентифицирован
        if not await check_if_authorized(callback, bot, callback.message.chat.id):
            return None

        # При обработке "НАЗАД" чистим состояние, ключевые атрибуты и удаляем все вспомогательные сообщения.
        if menu_details == 'step_back':
            await clear_all_data(bot, callback.message.chat.id, state)

        # Формируем описание и клавиатуру
        caption = banner.description
        btns = {
            '🎓 en -> ru: аудио 🎧': MenuCallBack(menu_name=menu_name, menu_details=TEST_EN_RU_AUDIO, level=2).pack(),
            '🎓 en -> ru: текст 📘': MenuCallBack(menu_name=menu_name, menu_details=TEST_EN_RU_WORD, level=2).pack(),
            '🎓 ru -> en: текст 📙': MenuCallBack(menu_name=menu_name, menu_details=TEST_RU_EN_WORD, level=2).pack(),
        }
        kbds = get_kbds_with_navi_header_btns(level=level, btns=btns, menu_name=menu_name)

        # Сохраняем сообщение с баннером
        bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

    # level == 2 - универсальная обработка любого выбранного типа теста
    # Здесь же: обработка "Предыдущее слово" и "Следующее слово"
    # Запросы формата "menu:2:tests:en_ru_audio:1", "menu:2:tests:ru_en_word:1", "menu:2:tests:en_ru_word_previous:1"
    # + "tests_cancel_select_topic", "tests_topic_{TOPIC.id}", "cancel_find_topic_by_matches" - принудительный вызов
    else:

        # Сохраняем callback
        bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

        # Проверяем, что у пользователя есть записи
        if not await check_if_words_exist(bot, callback.message.chat.id, session):
            return None

        # Удаляем возможные вспомогательные сообщения из чата
        await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

        # Забираем из контекста тип теста и выбранную тему (если есть)
        state_data = await state.get_data()
        test_type = state_data.get('test_type')
        topic_filter = state_data.get('selected_topic_id')

        # При входе в раздел с типом теста в state пробрасывается ключ test_type= с выбранным типом тестирования
        if not test_type:
            test_type = menu_details
            await state.update_data(test_type=test_type)

        # Определяем слово: генерируем новое или достаём из истории попыток

        # Обработка кнопки "предыдущее слово"
        if '_previous' in menu_details:
            bot.tests_word_navi[callback.message.chat.id][test_type]['navi_index'] -= 2
            menu_details = menu_details.replace('_previous', '')

        # Забираем текущий индекс попытки по типу теста и историю слов
        navi_index_now = bot.tests_word_navi[callback.message.chat.id][test_type]['navi_index']
        history = bot.tests_word_navi[callback.message.chat.id][test_type]['history']

        # Если в истории есть записанное слово за таким индексом, будем выводить его
        if history.get(navi_index_now):
            random_word = history.get(navi_index_now)

        # Если в истории нет слова за таким индексом, будем генерировать новое
        else:
            # Генерируем случайное слово (с учётом фильтра по теме)
            random_word = await DataBase.get_random_word_phrase(
                session, bot.auth_user_id.get(callback.message.chat.id), topic_filter=topic_filter
            )
            #  Записываем полученное слово в историю попыток за текущим индексом
            bot.tests_word_navi[callback.message.chat.id][test_type]['history'][navi_index_now] = random_word

        # Записываем полученное слово в state для последующей обработки
        await state.update_data(word_obj=random_word)

        # Формируем описание баннера
        word_info: dict = await get_word_phrase_caption_formatting(random_word)
        caption: str = getattr(bnr, f'tests_dscr_{test_type}')
        caption = caption.format(**word_info)

        # Формируем и сохраняем клавиатуру
        kbds = get_kbds_tests_btns(level, menu_name, menu_details, topic_filter, random_word, sizes=(2, 2, 2, 2, 1, 1))
        bot.reply_markup_save[callback.message.chat.id] = kbds

        # При аудио-тесте сразу отправляем в чат файлы для прослушивания
        if test_type == TEST_EN_RU_AUDIO:
            await speak_text(
                str(random_word.word), bot, callback.message.chat.id, is_with_title=False, autodelete=False,
                state=state, session=session
            )
            if random_word.context:
                for example in random_word.context:
                    await speak_text(
                        example.example, bot, callback.message.chat.id, is_with_title=False, autodelete=False,
                        state=state, session=session
                    )

        # Определяем данные статистики прохождения тестирований
        stat_data = await DataBase.get_stat_attempts(session, bot.auth_user_id.get(callback.message.chat.id), test_type)
        total_attempts, correct_attempts, incorrect_attempts, result_percentage, topic_count, topic_obj = stat_data
        topic_name = topic_obj.name if topic_obj else '-'

        # Записываем данные статистики в state
        await state.update_data(stat_data=stat_data)

        # Отправляем информационное сообщение со статистикой и кнопкой "Записать отчёт"
        stat_msg_text = stat_msg_template.format(**locals())
        msg = await callback.message.answer(
            stat_msg_text, reply_markup=get_inline_btns(btns={'Записать отчёт': 'tests_create_report'})
        )
        bot.auxiliary_msgs['statistic_msg'][callback.message.chat.id] = msg

        # Увеличиваем индекс попытки в хранилище
        bot.tests_word_navi[callback.message.chat.id][test_type]['navi_index'] += 1

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=caption)
    return image, kbds


# Сформировать баннер с описанием и клавиатуру для раздела "Озвучить текст".
# Обработка callback-запросов формата "menu:1:speaking::1", "menu:1:speaking:step_back:1"
async def speaking(
        session: AsyncSession,
        level: int,
        menu_name: str,
        bot: Bot,
        callback: CallbackQuery,
        state: FSMContext,
        **kwargs
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Сформировать баннер с описанием и клавиатуру для раздела "Произношение".
    Обработка callback-запросов формата "menu:1:speaking::1", "menu:1:speaking:step_back:1"

    :param session: Пользовательская сессия
    :param level: Уровень меню
    :param menu_name: Название меню  (для формирования баннера)
    :param bot: Объект бота
    :param callback: Callback-запрос
    :param state: Контекст состояния FSM
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике или None при ошибке
    """

    # Сохраняем callback-запрос и баннер
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

    # Очищаем чат и контекст FSM
    await clear_all_data(bot, callback.message.chat.id, state)

    # Получаем баннер страницы
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)

    # Формируем описание баннера и клавиатуру
    btns = {'Преобразовать текст в аудио 🔊': 'convert_text_to_audio', 'Практика произношения 🎙': 'speaking_practice'}
    kbds = get_kbds_with_navi_header_btns(btns=btns, level=level, menu_name=menu_name)

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=banner.description)
    return image, kbds


# Сформировать баннер с описанием и клавиатуру для раздела "AI-ассистент".
# Обработка callback-запросов формата "menu:1:giga::1"
async def giga_ai(session: AsyncSession, state: FSMContext | None, level, menu_name: str, **kwargs) \
        -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Сформировать баннер с описанием и клавиатуру для раздела "AI-ассистент".
    Обработка callback-запросов формата "menu:1:giga::1"

    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param level: Уровень меню
    :param menu_name: Название меню  (для формирования баннера)
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике или None при ошибке
    """

    # Получаем баннер страницы
    banner: Banner = await DataBase.get_banner_by_name(session, menu_name)

    # Формируем описание баннера и клавиатуру
    caption = bnr.ai_header
    kbds = get_kbds_with_navi_header_btns(btns={'Очистить чат 🗑': 'clear_chat'}, level=level, menu_name=menu_name)

    # Устанавливаем состояние для ввода текста запроса к нейросети
    await state.set_state(GigaAiFSM.text_input)

    # Формируем объект изображения с описанием
    image = InputMediaPhoto(media=FSInputFile(banner.image_path), caption=caption)
    return image, kbds


# Общая функция для наполнения "страницы" меню бота - возвращает баннер и клавиатуру
async def get_menu_content(
        bot: Bot,
        session: AsyncSession,
        state: FSMContext | None,
        level: int,
        menu_name: str,
        menu_details: str | None = None,
        callback: CallbackQuery | None = None,
        page: int | None = None,
        user_id: int | None = None,
) -> tuple[InputMediaPhoto, InlineKeyboardMarkup] | None:
    """
    Общий обработчик для формирования баннера и клавиатуры страницы в зависимости от переданного названия меню.

    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM
    :param level: Уровень меню
    :param menu_name: Название меню (для формирования баннера)
    :param menu_details: Детали меню для разделения логики обработки в контроллере
    :param callback: Callback-запрос
    :param page: Текущая страница (номер)
    :param user_id: ID пользователя
    :return: Баннер с описанием и клавиатуру для дальнейшего редактирования в вызывающем обработчике
    """

    func_params = {
        'bot': bot, 'session': session, 'state': state, 'level': level, 'menu_name': menu_name,
        'menu_details': menu_details, 'callback': callback, 'page': page, 'user_id': user_id,
        'chat_id': callback.message.chat.id
    }

    # Соответствие названия меню и функции обработки
    menu_name_func = {
        'start_page': start_page,
        'auth': auth_page,
        'vocabulary': vocabulary,
        'add_new_word': add_new_word,
        'tests': tests,
        'speaking': speaking,
        'giga': giga_ai
    }

    # Вызываем функцию обработки в зависимости от переданного названия меню
    return await menu_name_func[menu_name](**func_params)
