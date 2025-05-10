"""
Действия в профиле пользователя.

Доступные действия:
- Доступ к данным пользователя:
    - просмотр статистики тестирований + + отправка сводного .xls-файла с попытками Attempt и отчётами Report;
    - выгрузка всей базы данных в .xls-файл (словарь + заметки + отчёты);
    - прослушивание сохраненных аудиозаписей практики произношения + их выгрузка в .zip-архив;
- настройки аудио - выбор голоса для озвучивания и скорости речи;
- выход из профиля.

INFO:
1. Обработка входа в профиль осуществляется через menu_processing.py.
2. При входе в профиль (или завершении регистрации Sign in) в state сохраняется ключ user=<User object> для доступа
   к данным.
3. Настройки воспроизведения аудио (голос и скорость речи). В режиме 'random' при создании каждого аудио файла
   выбирается случайный голос (из списка all_voices_en_US_ShortName_list).
4. При работе с сохраненными аудио в state добавляются ключи:
    - 'last_date_page' - callback.data последней просмотренной страницы с датами аудио (для кнопки "Назад")
    - 'audios_by_date_page' - callback.data последней просмотренной страницы с аудио (для callback.data кнопок)
"""
import os
import re
import zipfile
from tempfile import NamedTemporaryFile

from aiogram import Router, F, types
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details as bnr
from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.keyboards.inlines import get_inline_btns, get_kbds_with_navi_header_btns, get_pagination_btns
from app.common.fsm_classes import UserSettingsFSM
from app.common.tools import try_alert_msg, clear_auxiliary_msgs_in_chat, modify_callback_data
from app.common.msg_templates import report_msg_template, oops_with_error_msg_template, oops_try_again_msg_template
from app.utils.custom_bot_class import Bot
from app.utils.paginator import Paginator, pages
from app.utils.tts import speak_text
from app.utils.tts_voices import all_voices_en_US_ShortName_list
from app.utils.xsl_tools import export_statistic_data_to_xls, export_all_user_data_to_xls
from app.settings import PER_PAGE_STAT_REPORTS, PATTERN_SPEECH_RATE, PER_PAGE_VOICE_SAMPLES, VOICE_SAMPLES_TEXT, \
    PER_PAGE_AUDIO_DATES, SAVED_AUDIO_ROOT_DIR, PER_PAGE_AUDIOS, FILENAME_AUDIOS_ZIP, FILENAME_AUDIOS_CAPTION

# Создаём роутер для приватного чата бота с пользователем
profile_router = Router()

# Настраиваем фильтр, что строго приватный чат
profile_router.message.filter(ChatTypeFilter(['private']))


# USER SETTINGS

# Страница профиля с отображением настроек пользователя и доступом к их редактированию
@profile_router.callback_query(F.data == 'user_settings', IsKeyInStateFilter('user'))
async def show_user_settings(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Страница профиля с отображением настроек пользователя и доступом к их редактированию.

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
        '🔹 Изменить голос': 'change_voice_page_1',
    }
    kbds = get_kbds_with_navi_header_btns(btns=btns, level=2, menu_name='auth', sizes=(2, 1))
    bot.reply_markup_save[callback.message.chat.id] = kbds

    # Редактируем баннер и клавиатуру
    caption = bnr.user_profile_settings.format(
        email=user.email, speech_rate=settings_data.speech_rate, voice=settings_data.voice
    )
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)


# Изменить скорость речи - ШАГ 1, запрос ввода нового значения скорости
@profile_router.callback_query(F.data == 'change_speech_rate', IsKeyInStateFilter('user'))
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
@profile_router.callback_query(F.data == 'cancel_user_settings', IsKeyInStateFilter('user'))
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
@profile_router.message(UserSettingsFSM.speech_rate, IsKeyInStateFilter('user'))
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
@profile_router.callback_query(F.data.contains('change_voice_page_'), IsKeyInStateFilter('user'))
async def change_voice_ask_value(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Изменение голоса озвучки аудио - ШАГ 1, отправка образцов голосов озвучки для выбора.

    :param callback: Callback-запрос формата "change_voice", "change_voice_page_<page_number>"
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Определяем страницу пагинации из callback
    page = int(callback.data.split('_')[-1])

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
@profile_router.callback_query(F.data.contains('play_voice:'), IsKeyInStateFilter('user'))
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
@profile_router.callback_query(F.data.contains('apply_voice:'), IsKeyInStateFilter('user'))
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


# STATISTIC & DATA

# Вход в раздел "Статистика и данные"
@profile_router.callback_query(F.data.startswith('user_stat_and_data'), IsKeyInStateFilter('user'))
async def show_user_stat_and_data(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Вход в раздел "Статистика и данные".
    Доступ к просмотру статистики тестирований, сохраненных аудиозаписей и выгрузке базы данных.

    :param callback: CallbackQuery-запрос формата "user_stat_and_data"
    :param state: Контекст состояния FSM с ключом "user" с данными о пользователе
    :param bot: Объект бота
    :return: None
    """

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о пользователе
    state_data = await state.get_data()
    user = state_data.get('user')

    # Обновляем описание баннера и клавиатуру
    caption = bnr.user_profile_data.format(email=user.email)
    btns = {
        'Статистика тестирований 📊': 'statistic_page_1',
        'Сохраненные аудиозаписи 🎙': 'user_audios_page_1',
        'Выгрузить базу пользователя .xls 💾': 'export_all_user_data'
    }
    kbds = get_kbds_with_navi_header_btns(level=2, menu_name='auth', btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception,) as e:
        print(e)


# Просмотр отчётов прохождения тестов + выгрузка отчетов статистики
@profile_router.callback_query(F.data.startswith('statistic_page_'), IsKeyInStateFilter('user'))
async def show_statistic_reports(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Просмотр отчётов прохождения тестов + выгрузка отчетов статистики.

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
    }
    custom_step_back = 'user_stat_and_data'
    kbds = get_kbds_with_navi_header_btns(
        level=3, menu_name='auth', btns=btns, sizes=(2, 1), custom_step_back=custom_step_back
    )
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
@profile_router.callback_query(F.data.startswith('create_statistic_report') | F.data.startswith('export_all_user_data'),
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
    last_page = state_data.get('show_statistic_reports_cbq')

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
        last_page = 'user_stat_and_data'

    # Отправляем отчёт в чат
    with open(path_to_file, 'rb') as f:
        data_file = types.BufferedInputFile(f.read(), filename=os.path.basename(path_to_file))
    kbds = get_inline_btns(btns={'Вернуться к просмотру данных?': last_page})
    msg = await bot.send_document(chat_id=callback.message.chat.id, document=data_file, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Удаляем xlsx-файл из системы после отправки
    os.remove(path_to_file)


# AUDIOS

# Вход в раздел "Сохраненные аудиозаписи". Просмотр списка дат с сохранёнными аудио с количеством записей по датам
@profile_router.callback_query(F.data.startswith('user_audios_page_'), IsKeyInStateFilter('user'))
async def user_audios(callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Вход в раздел "Сохраненные аудиозаписи". Просмотр списка дат с сохранёнными аудио с количеством записей по датам.

    :param callback: CallbackQuery-запрос формата "user_audios_page_<page_number>"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом 'user'
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о пользователе
    state_data = await state.get_data()
    user = state_data.get('user')

    # Добавляем в state адрес последней просмотренной страницы с датами аудио (для custom_step_back кнопки "Назад")
    await state.update_data(last_date_page=callback.data)

    # Получаем список дат с сохранёнными аудио с количеством записей по датам
    all_audio_dates_and_count = await DataBase.get_audio_dates_and_count(session, user.id)
    all_dates = [i[0] for i in all_audio_dates_and_count]                            # Список дат
    date_count_dict = {date: count for date, count in all_audio_dates_and_count}     # Словарь дата: количество записей

    # Обновляем описание баннера и клавиатуру
    caption = bnr.user_profile_all_audio_dates.format(email=user.email)
    btns = {
        'Выгрузить архив аудиозаписей 💾': 'export_all_user_audios'
    }
    custom_step_back = 'user_stat_and_data'
    kbds = get_kbds_with_navi_header_btns(
        level=3, menu_name='auth', btns=btns, sizes=(2, 1), custom_step_back=custom_step_back
    )
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception,) as e:
        print(e)

    # Проверяем, есть ли сохранённые аудиозаписи. Если нет, выходим из функции
    if len(all_audio_dates_and_count) == 0:
        await callback.answer('⚠️ У вас нет сохранённых аудиозаписей!', show_alert=True)
        return

    # Получаем номер текущей страницы из callback
    page = int(callback.data.split('_')[-1])

    # Получаем срез дат архива для текущей страницы
    paginator = Paginator(list(all_dates), page, per_page=PER_PAGE_AUDIO_DATES)
    current_page_dates = paginator.get_page()

    # Для всех записей страницы отправляем сообщение с информацией о дате, количестве записей и кнопкой прослушивания
    for date in current_page_dates:
        kbds = get_inline_btns(btns={'🎧 Перейти к аудиозаписям': f'audio_records_{date}_page_1'})
        msg_text = f"📅 <b>Дата:</b> {date}\nКоличество записей: {date_count_dict[date]}"
        msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Отправляем информационное сообщение с пагинацией страниц просмотра
    pagi_kbds = get_pagination_btns(page=page, pagination_btns=pages(paginator), custom_cb_data='user_audios')
    msg_text = 'Всего дат с практикой: <b>{}</b>.\nСтраница <b>{}</b> из <b>{}</b>'
    msg_text = msg_text.format(len(all_dates), page, paginator.pages)
    msg = await callback.message.answer(text=msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Просмотр аудио записей за выбранную дату.
# Обработка принудительного вызова после удаления аудиозаписи
@profile_router.callback_query(F.data.startswith('audio_records_'), IsKeyInStateFilter('user', 'last_date_page'))
async def audio_by_date(callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Просмотр аудио записей за выбранную дату. Функция отправляет в чат все аудио записи выбранной даты.
    Обработка принудительного вызова после удаления аудиозаписи.

    :param callback: CallbackQuery-запрос формата "audio_records_<date>_page_<page_number>"
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключами:
                  'user'(<User_object>) и 'last_date_page'(callback.data для кнопки назад)
    :return: None
    """

    # Чистим чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные
    state_data = await state.get_data()
    user = state_data.get('user')
    last_date_page = state_data.get('last_date_page')

    # Добавляем в контекст данные последней просмотренной страницы с аудиофайлами (для использования в callback-данных)
    await state.update_data(audios_by_date_page=callback.data)

    # Получаем страницу и дату из callback
    page = int(callback.data.split('_')[-1])
    date = callback.data.split('_')[2]                                              # 2025-05-08

    # Обновляем баннер и клавиатуру
    caption = bnr.user_profile_audio_by_date.format(email=user.email, date=date)
    btns = {
        'Выгрузить архив аудиозаписей 💾': 'export_all_user_audios'
    }
    kbds = get_kbds_with_navi_header_btns(
        level=3, menu_name='auth', btns=btns, sizes=(2, 1), custom_step_back=last_date_page
    )
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception, ) as e:
        print(e)

    # Получаем все аудиозаписи за выбранную дату (пути из БД)
    all_saved_audios = await DataBase.get_all_saved_audios(session, user.id, filter_date=date)

    # Если это вызов после удаления и записей за дату больше нет, оповещаем и выходим из функции
    if len(all_saved_audios) == 0:
        msg = await callback.message.answer('⚠️ У вас нет сохранённых аудиозаписей за эту дату!')
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
        return

    # Получаем срез аудиозаписей для текущей страницы
    paginator = Paginator(list(all_saved_audios), page, per_page=PER_PAGE_AUDIOS)
    current_page_data = paginator.get_page()

    # Если это вызов после удаления и удалена последняя запись текущей страницы - вызываем предыдущую страницу
    if len(current_page_data) == 0:
        page = page - 1
        paginator = Paginator(list(all_saved_audios), page, per_page=PER_PAGE_AUDIOS)
        current_page_data = paginator.get_page()

    # Отправляем аудиозаписи в чат
    for audio_obj in current_page_data:
        try:
            audio = FSInputFile(path=audio_obj.file_path)
            kbds = get_inline_btns(btns={'Удалить 🗑': f'delete_audio:{audio_obj.id}'})
            msg = await callback.message.answer_audio(
                audio=audio,
                caption=f"Аудио {audio_obj.created.date().isoformat()}",
                reply_markup=kbds
            )
            bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
        except Exception as e:
            print(e)

    # Отправляем информационное сообщение с пагинацией страниц просмотра
    pagi_kbds = get_pagination_btns(page=page, pagination_btns=pages(paginator), custom_cb_data=f'audio_records_{date}')
    msg_text = 'Всего аудиозаписей: <b>{}</b>.\nСтраница <b>{}</b> из <b>{}</b>'
    msg_text = msg_text.format(len(all_saved_audios), page, paginator.pages)
    msg = await callback.message.answer(text=msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Удаление аудио, ШАГ 1 - запрос подтверждения
@profile_router.callback_query(F.data.startswith('delete_audio:'), IsKeyInStateFilter('user', 'audios_by_date_page'))
async def delete_audio_ask_to_confirm(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Удаление аудиозаписи, ШАГ 1 - запрос подтверждения.

    :param callback: CallbackQuery-запрос формата "delete_audio:<SavedAudio.id>"
    :param state: Контекст состояния FSM с ключами:
                  'user' (с объектом User) и 'audios_by_date_page' (с callback.data при отмене удаления)
    :param bot: Объект бота
    :return: None
    """

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные
    state_data = await state.get_data()
    audios_by_date_page = state_data.get('audios_by_date_page')

    # Отправляем сообщение с запросом подтверждения удаления и кнопкой отмены
    btns = {
        'Удалить 🗑': f'confirm_delete_audio:{callback.data.split(":")[-1]}',
        'Отмена ❌': audios_by_date_page
    }
    msg = await callback.message.answer(
        text='⚠️ Вы уверены, что хотите удалить эту аудиозапись?',
        reply_markup=get_inline_btns(btns=btns)
    )
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Удаление аудио, ШАГ 2 - подтверждение получено, удаление из файловой системы и БД
@profile_router.callback_query(F.data.startswith('confirm_delete_audio:'),
                               IsKeyInStateFilter('user', 'audios_by_date_page'))
async def confirm_delete_audio(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Удаление аудио, ШАГ 2 - подтверждение получено, удаление из файловой системы и БД

    :param callback: CallbackQuery-запрос формата "confirm_delete_audio:<audio_id>"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключами:
                  'user' (с объектом User) и 'audios_by_date_page' (с callback.data страницы с аудиозаписями)
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Забираем из контекста данные
    state_data = await state.get_data()
    audios_by_date_page = state_data.get('audios_by_date_page')                 # 'audio_records_2025-05-07_page_2'

    # Забираем SavedAudio.id из callback
    audio_id = int(callback.data.split(':')[-1])

    # Удаляем аудио из файловой системы и запись о нем из БД
    is_del = False                                                              # Флаг удаления
    try:
        # Удаляем запись об аудио из БД
        path_for_delete = await DataBase.delete_audio_by_id(session, audio_id)
        if path_for_delete:
            try:
                # Удаляем аудио из файловой системы
                os.remove(path_for_delete)
                is_del = True
            except Exception as e:
                await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)
    except Exception as e:
        await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)

    # При успешном удалении аудиозаписи выводим информационное сообщение
    if is_del:
        await callback.answer(text='✅ Аудиозапись удалена!', show_alert=True)

    # Возвращаемся к последней просмотренной странице с аудиозаписями
    modified_callback = await modify_callback_data(callback, audios_by_date_page)
    await audio_by_date(modified_callback, bot, session, state)


# Выгрузка всех аудиозаписей пользователя в zip-архив (с сохранением структуры папок)
@profile_router.callback_query(F.data.startswith('export_all_user_audios'), IsKeyInStateFilter('user'))
async def export_all_user_audios(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Выгрузка всех аудиозаписей пользователя в zip-архив (с сохранением структуры папок).

    :param callback: CallbackQuery-запрос формата 'export_all_user_audios'
    :param state: Контекст состояния FSM с ключом 'user' с данными о пользователе User
    :param bot: Объект бота
    :return: None
    """

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные о пользователе
    state_data = await state.get_data()
    user = state_data.get('user')

    # Формируем список папок с сохраненными аудиозаписями
    try:

        # Формируем путь до корневой папки с аудиозаписями пользователя
        user_audio_root = SAVED_AUDIO_ROOT_DIR.format(user_id=user.id)

        # Проверяем существование папки в файловой системе
        if not os.path.exists(user_audio_root):
            await callback.answer(text='⚠️ У вас нет сохранённых аудиозаписей!', show_alert=True)
            return

        # Получаем список всех поддиректорий с датами. Исключаем папку с временными файлами
        all_audio_dirs = os.listdir(user_audio_root)  # ['2025-05-05', '2025-05-06', ... , 'tmp']
        if 'tmp' in all_audio_dirs:
            all_audio_dirs.remove('tmp')

    except Exception as e:
        await callback.answer(text=oops_with_error_msg_template.format(error=str(e)), show_alert=True)
        return

    # Формируем архив
    files = None                                                      # Флаг наличия аудиофайлов для архивации в архиве
    with NamedTemporaryFile(delete=False, suffix=".zip") as tmp_zip:  # Создаем временный zip-файл
        with zipfile.ZipFile(tmp_zip.name, "w") as archive:

            # Формируем пути до каждого аудиофайла в каждой датированной папке хранилища
            for date_dir in all_audio_dirs:
                date_dir_path = os.path.join(user_audio_root, date_dir)
                for file_name in os.listdir(date_dir_path):
                    audio_path = os.path.join(date_dir_path, file_name)
                    files = True

                    # Добавляем файл в архив с сохранением вложенных директорий
                    arcname = os.path.join(date_dir, file_name)
                    archive.write(audio_path, arcname=arcname)

            # Если у пользователя нет сохранённых аудиозаписей, оповещаем и выходим из функции
            if not files:
                await callback.answer("⚠️ У вас нет аудиофайлов для архивации!", show_alert=True)
                return

        # Отправляем архив в чат бота
        file_to_send = FSInputFile(tmp_zip.name, filename=FILENAME_AUDIOS_ZIP)
        msg = await callback.message.answer_document(file_to_send, caption=FILENAME_AUDIOS_CAPTION)
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Удаляем архив после отправки
    os.remove(tmp_zip.name)
