"""
Обработка действий в разделе "Тесты".
TESTS - обработчики действий с тестами в БД.

INFO:
1. Вход в раздел и выбор типа теста - обрабатываются в menu_processing.py.
2. При выборе типа теста пробрасывается в state FSM ключ test_type=<тип_теста>, напр. {'test_type': 'en_ru_word'}
3. ТЕМЫ. Ветвление callback_data для пагинации тем определяется внутри get_kbds_with_topic_btns().
   Пагинация выбора темы для тестирования будет по логике 'tests_select_topic_page_{page + 1}'.
   id выбранной темы сохраняется в state за ключом selected_topic_id=<Topic.id>.
   Найти тему - в универсальном обработчике topic_actions.py -> find_topic_by_matches_ask_keywords().
   Отбой ввода ключа для поиска темы - тоже в универсале: topic_actions.py -> cancel_find_topic().
   Ключ для фильтра по теме сохраняется в state: search_keywords=<value>.
   При отмене фильтра по теме просто удаляется значение ключа 'selected_topic_id' из контекста.
4. ПРОСЛУШАТЬ слово/примеры - универсальные обработчики в vocabulary_actions.py: speak_word_aloud,
   speak_example_aloud. Сработают автоматически, callback единый.
5. НАВИГАЦИЯ по словам: пропустить - к предыдущему. Пропуск просто перевызывает обработчик, переход к предыдущему
   происходит за счет добавления к menu_details в callback-запросе вставки '_previous', которая обрабатывается в коде
   обработчика, меняя индекс попытки.
6. ИСТОРИЯ слов реализуется в структуре словаря внутри бота, куда под ключом типа теста сохраняется слово за каждым
   индексом попытки + текущий индекс попытки. Если слова с текущим индексом попытки нет в словаре, рандомно
   выбирается слово из базы и записывается в словарь, если есть - достаётся из словаря.
   Формат структуры:
   bot.tests_word_navi[chat_id] =
   {'en_ru_word': {
        'history': {1:  <word_obj>, 2: <word_obj>, ... },
        'navi_index': 3},
    'en_ru_audio': {'history': {},  'navi_index': 1}}
7. Фиксация ОТВЕТОВ. Универсальный обработчик get_tests_answer() для ответов и 'да', и 'нет'. При подборе слова
   (из истории или сгенерированного рандомайзером) в state пробрасывается ключ word_obj=<word_obj> с объектом слова.
   Он используется для доступа к данным слова при обработке ответов.
8. Отображение текущей СТАТИСТИКИ прохождения. В чат отправляется сообщение с текущей статистикой + в state
   сохраняются данные статистики под ключом 'stat_data'. При записи отчёта данные берутся из state:
   (total_attempts, correct_attempts, incorrect_attempts, result_percentage, topic_count, <Topic object>)
"""
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details as bnr
from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.utils.custom_bot_class import Bot
from app.handlers.user_private.menu_processing import tests
from app.common.tools import get_topic_kbds_helper, get_word_phrase_caption_formatting, try_alert_msg
from app.common.msg_templates import stat_msg_template, oops_with_error_msg_template
from app.settings import PER_PAGE_INLINE_TOPICS

# Создаём роутер для приватного чата бота с пользователем
tests_router = Router()

# Настраиваем фильтр, что строго приватный чат
tests_router.message.filter(ChatTypeFilter(['private']))


# Обработка кнопки "Выбрать тему". Вывод клавиатуры с темами на выбор.
# Здесь же - обработка пагинации списка тем, обработка страницы после получения ключа для поиска тем.
@tests_router.callback_query(F.data.startswith('tests_select_topic'))
async def tests_ask_select_topic(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Обработчик выбора темы. Вывод клавиатуры с темами на выбор.
    + Обработка пагинации списка тем, обработка страницы после получения ключа для поиска тем.

    :param callback: CallbackQuery-запрос формата:
                    "tests_select_topic", "tests_select_topic_page_{number}",  "find_topic_by_matches" (после поиска)
    :param state: Контекст состояния FSM с ключами 'test_type' и (опционально) 'search_keywords'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Забираем из контекста ключ для фильтра по темам (если он есть)
    state_data = await state.get_data()
    search_key = state_data.get('search_keywords')

    # Сбрасываем состояние ввода (если это принудительный вызов из topic_actions.find_topic_by_matches_get_keywords())
    await state.set_state(None)

    # Получаем номер текущей страницы
    if '_page_' in callback.data:
        page = int(callback.data.split('_')[-1])
    else:
        page = 1

    # Редактируем баннер и клавиатуру с темами (с учётом фильтров)
    topic_name_prefix = 'tests_topic_'                          # Для формирования callback-запроса при выборе темы
    kbds, _ = await get_topic_kbds_helper(
        bot, chat_id=callback.message.chat.id, session=session, level=2,
        menu_name='tests', menu_details='tests_select_topic', topic_name_prefix=topic_name_prefix,
        search_key=search_key, page=page, per_page=PER_PAGE_INLINE_TOPICS, sizes=(2, 2, 2)
    )
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=bnr.tests_dscr_select_topic, reply_markup=kbds
    )


# Выбор темы для тестирования, обработка нажатия на выбранную тему.
# Фиксирует в state id выбранной темы и редактирует страницу на основную для тестирования
@tests_router.callback_query(F.data.startswith('tests_topic_'))
async def tests_get_selected_topic(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Выбор темы для тестирования. Функция фиксирует в state id выбранной темы под ключом 'selected_topic_id' и
    перенаправляет на основную страницу тестирования.

    :param callback: CallbackQuery-запрос формата "tests_topic_{Topic.id}"
    :param state: Контекст состояния FSM с ключом 'test_type'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем id выбранной темы в state за ключом selected_topic_id=
    await state.update_data(selected_topic_id=callback.data.split('_')[-1])

    # Определяем выбранный пользователем тип тестирования для передачи в menu_details=
    state_data = await state.get_data()
    menu_details = state_data.get('test_type')

    # Редактируем баннер и клавиатуру (отображение основного окна теста)
    image, kbds = await tests(
        bot, session, state, level=2, callback=callback, menu_name='tests', menu_details=menu_details
    )
    await callback.message.edit_media(media=image, reply_markup=kbds)


# Отмена выбора темы и отображения выбранной темы в основном окне тестирования
@tests_router.callback_query(F.data == 'tests_cancel_select_topic')
async def tests_cancel_select_topic(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot)\
        -> None:
    """
    Отмена выбора темы и отображения выбранной темы в основном окне тестирования.

    :param callback: CallbackQuery-запрос формата "tests_cancel_select_topic"
    :param state: Контекст состояния FSM
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Убираем из контекста значения ключей с фильтром по теме
    await state.update_data(selected_topic_id=None)
    await state.update_data(search_keywords=None)

    # Определяем выбранный пользователем тип тестирования для передачи в menu_details=
    state_data = await state.get_data()
    menu_details = state_data.get('test_type')

    # Редактируем баннер и клавиатуру (отображение основного окна теста)
    image, kbds = await tests(
        bot, session, state, level=2, callback=callback, menu_name='tests', menu_details=menu_details
    )
    await callback.message.edit_media(media=image, reply_markup=kbds)


# Отмена фильтра по темам на клавиатуре с выбором тем (кнопка "Отменить поиск")
@tests_router.callback_query(F.data.contains('cancel_find_topic_by_matches'), IsKeyInStateFilter('test_type'))
async def cancel_find_topic_by_matches_tests(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Отмена фильтра по темам на клавиатуре с выбором тем (кнопка "Отменить поиск").

    :param callback: CallbackQuery-запрос формата "cancel_find_topic_by_matches"
    :param state: Контекст состояния FSM с ключом 'test_type'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """
    await callback.answer('⚠️ Фильтр по теме отменен!', show_alert=True)

    # Убираем из контекста значение ключа с фильтром по теме
    await state.update_data(search_keywords=None)

    # Убираем состояние ввода
    await state.set_state(None)

    # Редактируем баннер и клавиатуру, отображение основного окна теста
    state_data = await state.get_data()
    menu_details = state_data.get('test_type')
    image, kbds = await tests(
        bot, session, state, level=2, callback=callback, menu_name='tests', menu_details=menu_details
    )
    await callback.message.edit_media(media=image, reply_markup=kbds)


# Показать всю информацию по слову/фразе
@tests_router.callback_query(F.data.startswith('tests_ask_hint_'))
async def tests_ask_hint(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Показать всю информацию по слову/фразе.

    :param callback: CallbackQuery-запрос формата 'tests_ask_hint_<WordPhrase.id>'
    :param state: Контекст состояния FSM с ключом 'test_type' с типом тестирования и 'word_obj' с объектом WordPhrase
    :param bot: Объект бота
    :return: None
    """

    # Забираем информацию из контекста
    state_data = await state.get_data()
    test_type = state_data.get('test_type')
    word = state_data.get('word_obj')

    # Формируем новое описание баннера
    hint_caption = getattr(bnr, f'tests_dscr_{test_type}_hint')
    word_info = await get_word_phrase_caption_formatting(word)
    caption = hint_caption.format(**word_info)

    # Редактируем баннер, отображая информацию по слову
    await callback.message.edit_caption(caption=caption, reply_markup=bot.reply_markup_save[callback.message.chat.id])


# Обработка ответов. Запись попыток Attempt с результатом в БД
@tests_router.callback_query(F.data.startswith('tests_answer_'))
async def get_tests_answer(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Обработка ответов на тест. Запись попыток Attempt с полученным результатом в БД.

    :param callback: CallbackQuery-запрос формата 'tests_answer_<correct/wrong>'
    :param state: Контекст состояния FSM с ключом 'test_type' с типом тестирования и 'word_obj' с объектом WordPhrase
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Забираем информацию о типе теста, слове и результате из контекста и callback
    state_data = await state.get_data()
    test_type = state_data.get('test_type')
    word = state_data.get('word_obj')
    result = callback.data.split('_')[-1]

    # фиксация попытки в БД.
    try:
        await DataBase.create_attempt(
            session, user_id=bot.auth_user_id[callback.message.chat.id], test_type=test_type, word=word, result=result
        )
    except (Exception, ) as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, callback.message.chat.id, msg_text, if_error_send_msg=True)

    # переброс на главную страницу тестирования со следующим словом
    image, kbds = await tests(
        bot, session, state, level=2, callback=callback, menu_name='tests', menu_details=test_type
    )
    await callback.message.edit_media(media=image, reply_markup=kbds)


# Создать новый отчёт Report с результатами статистики на основе текущих попыток Attempt
@tests_router.callback_query(F.data == 'tests_create_report')
async def tests_create_report(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Создать новый отчёт Report с результатами статистики на основе текущих попыток Attempt пользователя.

    :param callback: CallbackQuery-запрос формата 'tests_create_report'
    :param state: Контекст состояния FSM с ключом 'test_type' с типом тестирования и 'stat_data' с данными статистики
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Забираем из контекста данные о типе теста и статистике
    state_data = await state.get_data()
    test_type = state_data.get('test_type')
    stat_data = state_data.get('stat_data')
    total_attempts, correct_attempts, incorrect_attempts, result_percentage, topic_count, topic_obj = stat_data

    if total_attempts == 0:
        await callback.answer('⚠️ Нет результатов к записи!', show_alert=True)
        return

    # Создаем отчёт статистики в БД
    try:
        new_report = await DataBase.create_stat_report(
            session, bot.auth_user_id[callback.message.chat.id], test_type, total_attempts, correct_attempts,
            result_percentage, topic_obj
        )
    except Exception as e:
        new_report = None
        await callback.answer(oops_with_error_msg_template.format(error=str(e)), show_alert=True)

    # При успешном создании отправляем системку пользователю и редактируем сообщение со статистикой
    if new_report:
        await callback.answer(f'✅ Создан новый отчёт статистики от {new_report.created}!', show_alert=True)
        topic_name, total_attempts, correct_attempts, incorrect_attempts, result_percentage = '-', 0, 0, 0, 0
        await bot.auxiliary_msgs['statistic_msg'][callback.message.chat.id].edit_text(
            text=stat_msg_template.format(**locals())
        )
