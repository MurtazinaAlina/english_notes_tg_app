"""
Обработка действий с заметками Notes в разделе "Словарь".

INFO:

ПРОСМОТР ЗАМЕТОК:

1. Есть 2 режима просмотра заметок:
    - Режим полного просмотра. Заметки располагаются строго по 1 штуке на странице, с пагинацией. Выводится вся
        информация о заметке. (Не менять код. Контроллеры прописаны с расчетом на 1 штуку)
    - Режим просмотра заголовков. На странице располагаются только заголовки заметок (N штук), с возможностью открыть и
        просмотреть заметку уже со всей информацией.
2. Режимы свободно переключаются в 1 клик, при переключении сохраняется просматриваемая заметка (она открывается в
   другом режиме без сброса страницы и перенаправления на первую). Это удобно для быстрого поиска нужной заметки.
3. При просмотре заметки в state сохраняется ключ 'show_user_notes_cbq' с callback_data этой заметки, он используется в
   callback_data кнопок для отмены действий и возврата к просмотру заметки.
4. Для снижения нагрузки на БД при входе в раздел заметки пользователя сохраняются в state  под ключом "user_notes", все
   заметки при пагинации подгружаются оттуда. Новая загрузка из БД происходит только при поиске/отмене поиска,
   добавлении/редактировании/удалении данных (при необходимости обновления данных заметок).

СОЗДАНИЕ:
4. Создание новой заметки с первым примеров и добавление дополнительных примеров новой заметке происходит в одном
   обработчике, логика контроллера разделяется наличием в state ключа "new_note", который добавляется в state после
   создания новой заметки и сохраняется там до возврата в основную функцию показа заметок.

ПОИСК:
5. При поиске в state добавляется ключ "notes_search_keywords" с ключевым словом поиска. Поиск ведется по заголовку и
   тексту заметки.
6. Отмена поиска не предусматривает отдельного обработчика, обрабатывается в основной функции показа заметок
   show_user_notes() как один из вариантов триггерного callback_data.

РЕДАКТИРОВАНИЕ:
7. При выборе редактирования заметки в state сохраняются ключи:
    - 'edited_note' с объектом редактируемой заметки (удаляется при выходе из редактирования), который используется для
    доступа к данным заметки и ветвления логики работы контроллеров.
    - 'note_msg' с сообщением с редактируемой заметкой для доступа к изменению текста.
8. Информационные сообщения при редактировании дополнительно сохраняем в контекст под ключом 'info_msg' для удобного
   удаления при переключении запроса.
9. Редактирование заголовка и текста заметки происходит в одних обработчиках (полностью общая логика).
10. При добавлении нового примера при редактировании в state добавляется ключ 'add_example' для разделения контроллеров
   с редактированием текста примера. После добавления примера ключ удаляется, дополнительно проверяется его удаление
   при вызове примеров для редактирования.
11. Также при редактировании текста примера в state сохраняется ключ 'example_to_update_id' с id редактируемого примера
   Context.
"""
import html
import math

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details
from app.database.db import DataBase
from app.database.models import Notes
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter, IsKeyNotInStateFilter
from app.keyboards.inlines import get_inline_btns, get_kbds_with_navi_header_btns, get_pagination_btns
from app.utils.custom_bot_class import Bot
from app.utils.paginator import Paginator, pages
from app.common.tools import clear_auxiliary_msgs_in_chat, try_alert_msg, modify_callback_data, \
    validate_context_example, delete_last_message, update_note_msg_data, \
    delete_info_message, join_examples_in_unordered_list
from app.common.msg_templates import context_example_msg_template, note_msg_template, oops_with_error_msg_template, \
    oops_try_again_msg_template, context_validation_not_passed_msg_template, action_cancelled_msg_template
from app.common.fsm_classes import NotesFSM
from app.settings import PLUG_TEMPLATE, MIN_NOTE_TITLE_LENGTH, MIN_NOTE_TEXT_LENGTH, PER_PAGE_NOTE_TITLES


# Создаём роутер для приватного чата бота с пользователем
note_router = Router()

# Настраиваем фильтр, что строго приватный чат
note_router.message.filter(ChatTypeFilter(['private']))


# Просмотр заметок пользователя (всех или отфильтрованных по поиску) в режиме полного просмотра.
# Открытие заметки для детального просмотра из режима просмотра по заголовкам.
# Здесь же обрабатывается перенаправление при отменах действия (удаления, редактирования, добавления, поиска) и
# принудительный вызов после удаления/добавления заметок и ввода ключа для поиска (при режиме полного просмотра).
@note_router.callback_query(F.data.startswith('my_notes_page_') | F.data.startswith('show_note_'))
async def show_user_notes(callback: types.CallbackQuery, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    """
    Просмотр заметок пользователя (всех или отфильтрованных по поиску) в режиме полного просмотра.
    Открытие заметки для детального просмотра из режима просмотра по заголовкам.
    Здесь же обрабатывается перенаправление при отменах действия (удаления, редактирования, добавления, поиска) и
    принудительный вызов после удаления/добавления заметок и ввода ключа для поиска (при режиме полного просмотра).

    :param callback: Callback-запрос формата: "my_notes_page_{page_number}", "cancel_search_notes",
                    "show_note_6:10" (при просмотре из режима по заголовкам)
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :param state: Контекст состояния с возможными ключами:
                 'show_user_notes_cbq' с callback.data последней открытой заметки - 'my_notes_page_1'/ 'show_note_6:10';
                 'user_notes' со списком объектов заметок пользователя - [<Notes_object_1>, <Notes_object_2>, ...] или
                             None (при первом входе или если данные менялись);
                 'notes_search_keywords' c str ключом для поиска - 'Try to find me';
                 'edited_note' (если возврат из редактирования) c объектом заметки - <Notes_object>;
                 'note_msg' (если возврат из редактирования) c message-объектом сообщения редактируемой заметки;
                 'info_msg' (если возврат из редактирования) c message-объектом информационного сообщения;
                 'add_example' (если возврат из редактирования с добавлением примера)
                 'new_note' (если возврат после добавления заметки) c объектом заметки - <Notes_object>;
                 'title', 'text' (если отмена добавления заметки) c str названием и текстом;
                 'note_title_view_mode' (при просмотре из режима по заголовкам) c True;
                 'title_mode_page' (при просмотре из режима по заголовкам) c callback.data последней просмотренной
                    страницы заголовков

    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Сбрасываем состояние ввода (на случаи возврата из редактирования)
    await state.set_state(None)

    # Забираем из callback порядковый номер заметки (соответствует номеру страницы при просмотре по 1 шт.)
    if 'show_note_' in callback.data:
        note_id = int(callback.data.split(':')[-1])
        note_number = int(callback.data.split(':')[0].split('_')[-1])      # Для 'show_note_{note_number}:{Note.id}'
    else:
        note_number = int(callback.data.split('_')[-1])                    # Для 'my_notes_page_{note_number}'

    # Забираем данные из контекста
    state_data = await state.get_data()

    # Определяем ключевое слово для поиска
    search_filter = state_data.get('notes_search_keywords') if state_data.get('notes_search_keywords') else None

    # Определяем режим просмотра и порядковый номер страницы просмотра по заголовкам (если есть)
    title_view_mode = state_data.get('note_title_view_mode')
    title_mode_page = state_data.get('title_mode_page')

    # При фильтрации по ключевому слову в поиске отправляем в чат информационное сообщение
    if search_filter:
        msg = await callback.message.answer(f'<b>Вы ищете 🔎:</b> {search_filter}')
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Если это принудительный вызов функции после отмены/завершения действий, удаляем лишние ключи из контекста
    if state_data.get('edited_note'):                           # Вызов после редактирования
        await state.clear()
        state_data = {}
        if search_filter:
            await state.update_data(notes_search_keywords=search_filter)        # Возвращаем ключевое слово для поиска
        if title_view_mode:
            await state.update_data(note_title_view_mode=title_view_mode)       # Возвращаем режим просмотра
        if title_mode_page:
            await state.update_data(title_mode_page=title_mode_page)            # Возвращаем адрес страницы просмотра

    if state_data.get('new_note'):                              # Возврат на страницу после создания новой заметки
        await state.update_data(new_note=None)

    if state_data.get('title') or state_data.get('text'):      # Возврат на страницу после отмены создания новой заметки
        await state.update_data(title=None, text=None)

    # Редактируем баннер и клавиатуру
    caption = banners_details.vcb_descrptn_notes
    search_btn = {'Отменить поиск ✖': 'cancel_search_notes'} if search_filter else {'Найти заметки 🔎': 'search_notes'}
    view_mode_btn = {'Режим просмотра заголовков 👓': 'note_change_view_mode'} if not title_view_mode else {
        'Режим полного просмотра 📒': 'note_change_view_mode'}
    btns = {
        'Добавить заметку ➕': 'add_new_note',
        **view_mode_btn,
        **search_btn
    }
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception, ) as e:
        print(e)

    # Сохраняем в контекст callback.data текущей страницы (для возврата при отмене и завершении)
    await state.update_data(show_user_notes_cbq=callback.data)

    # При входе в раздел (или после изменений) сохраняем данные из БД в контекст.
    # При пагинации используем данные из контекста
    user_notes = state_data.get('user_notes')
    if not user_notes:
        user_notes = await DataBase.get_user_notes(session, bot.auth_user_id[callback.message.chat.id], search_filter)
        await state.update_data(user_notes=user_notes)

    # Если у пользователя нет заметок, сообщаем и выходим из функции
    if not user_notes:
        msg = await callback.message.answer(text=f'Найдено заметок: <b>0</b>')
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
        return

    # Определяем объект заметки к отображению
    if not title_view_mode:                                                     # Для режима полного просмотра
        try:
            note = user_notes[note_number - 1]                                  # Забираем заметку по порядковому номеру
        except IndexError:      # Обрабатываем исключение, если была удалена последняя запись и такого номера больше нет
            note_number -= 1
            note = user_notes[note_number - 1]
    else:                                                                       # Для режима просмотра заголовков
        note = list(filter(lambda x: x.id == note_id, user_notes))[0]

    # Формируем сообщение с заметкой
    examples = join_examples_in_unordered_list(note)                            # Соединяем примеры по шаблону
    msg_text = note_msg_template.format(
        page=note_number, len_user_notes=len(user_notes), note_title=html.escape(note.title),
        note_text=html.escape(note.text), examples=examples
    )

    # Формируем клавиатуру с редактированием заметки, озвучкой примеров
    btns = {
        'Редактировать 🖌': f'edit_note_{note.id}',
        'Удалить 🗑': f'delete_note_{note.id}',
        '🎧 Примеры': f'speak_note_example_{note.id}'
    }

    # Добавляем кнопки пагинации/возврата в зависимости от режима
    if not title_view_mode:
        paginator = Paginator(list(user_notes), note_number, per_page=1)
        if paginator.has_previous():
            btns["◀ Предыдущая"] = f"my_notes_page_{note_number - 1}"
        if paginator.has_next():
            btns["Следующая ▶"] = f"my_notes_page_{note_number + 1}"
    else:
        btns["Вернуться к списку ⬅"] = title_mode_page                          # Возвращаемся к просмотру заголовков

    kbds = get_inline_btns(btns=btns, sizes=(2, 1, 2))

    # Отправляем сообщение с заметкой в чат
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Просмотр заметок пользователя (всех или отфильтрованных по поиску) в режиме просмотра по заголовкам.
# Здесь же обрабатывается перенаправление при отменах действия (удаления, добавления, поиска) и
# принудительный вызов после удаления/добавления заметок и ввода ключа для поиска (при режиме просмотра по заголовкам).
@note_router.callback_query(F.data.startswith('note_title_view_mode_page_'))
async def note_title_view_mode(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) \
        -> None:
    """
    Просмотр заметок пользователя (всех или отфильтрованных по поиску) в режиме просмотра по заголовкам.
    Здесь же обрабатывается перенаправление при отменах действия (удаления, добавления, поиска) и принудительный вызов
    после удаления/добавления заметок и ввода ключа для поиска (при режиме просмотра по заголовкам).

    :param callback: CallbackQuery-запрос формата "note_title_view_mode_page_<page_number>"
    :param state: Контекст состояния FSM с возможными ключами:
                 'show_user_notes_cbq' с callback.data последней открытой заметки - 'my_notes_page_1'/ 'show_note_6:10';
                 'user_notes' со списком объектов заметок пользователя - [<Notes_object_1>, <Notes_object_2>, ...] или
                             None (при первом входе или если данные менялись);
                 'notes_search_keywords' c str ключом для поиска - 'Try to find me';
                 'new_note' (если возврат после добавления заметки) c объектом заметки - <Notes_object>;
                 'title', 'text' (если отмена добавления заметки) c str названием и текстом;
                 'note_title_view_mode' (при пагинации, принудительном вызове) c True;
                 'title_mode_page' (при пагинации, принудительном вызове) c callback.data последней просмотренной
                    страницы заголовков
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Добавляем в state ключ режим просмотра по заголовкам и callback.data текущей страницы
    await state.update_data(note_title_view_mode=True)
    await state.update_data(title_mode_page=callback.data)

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Сбрасываем состояние ввода (на случаи возврата из редактирования)
    await state.set_state(None)

    # Забираем данные из контекста
    state_data = await state.get_data()

    # Определяем ключевое слово для поиска (если есть)
    search_filter = state_data.get('notes_search_keywords') if state_data.get('notes_search_keywords') else None

    # При фильтрации по ключевому слову в поиске отправляем информационное сообщение
    if search_filter:
        msg = await callback.message.answer(f'<b>Вы ищете 🔎:</b> {search_filter}')
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Редактируем клавиатуру баннера
    search_btn = {'Отменить поиск ✖': 'cancel_search_notes'} if search_filter else {'Найти заметки 🔎': 'search_notes'}
    title_view_mode = state_data.get('note_title_view_mode')
    view_mode_btn = {'Режим просмотра заголовков 👓': 'note_change_view_mode'} if not title_view_mode else {
        'Режим полного просмотра 📒': 'note_change_view_mode'}
    btns = {
        'Добавить заметку ➕': 'add_new_note',
        **view_mode_btn,
        **search_btn
    }
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_reply_markup(reply_markup=kbds)
    except (Exception, ) as e:
        print(e)

    # При входе в раздел (или после изменений) сохраняем данные из БД в контекст.
    # При пагинации используем данные из контекста
    user_notes = state_data.get('user_notes')
    if not user_notes:
        user_notes = await DataBase.get_user_notes(session, bot.auth_user_id[callback.message.chat.id], search_filter)
        await state.update_data(user_notes=user_notes)

    # Получаем номер текущей страницы из callback.data
    page = int(callback.data.split('_')[-1])

    # Получаем список заметок для текущей страницы
    paginator = Paginator(list(user_notes), page, per_page=PER_PAGE_NOTE_TITLES)
    current_page_notes = paginator.get_page()

    # Отправляем информационные сообщения с заголовками заметок текущей страницы и доступом к их просмотру
    for ind, note in enumerate(current_page_notes):
        note_counter = (ind + 1) + (page - 1) * PER_PAGE_NOTE_TITLES
        btns = {'📖 Открыть информацию о заметке': f'show_note_{note_counter}:{note.id}'}
        kbds = get_inline_btns(btns=btns, sizes=(2, 1, 2))
        msg_text = f"▪ Заметка #<b>{note_counter}</b> из <b>{len(user_notes)}</b>:\n<b>{str(note.title)}</b>"
        msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
        bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Отправляем информационное сообщение с пагинацией
    pagi_kbds = get_pagination_btns(pages(paginator), page=page, custom_cb_data='note_title_view_mode')
    msg_text = f'Страница <b>{page}</b> из <b>{paginator.pages}</b>'
    msg = await callback.message.answer(text=msg_text, reply_markup=pagi_kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Переключение режима просмотра заметок
@note_router.callback_query(F.data.startswith('note_change_view_mode'))
async def change_note_view_mode(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) \
        -> None:
    """
    Переключение режима просмотра заметок.

    :param callback: CallbackQuery-запрос формата "note_change_view_mode"
    :param state: Контекст состояния FSM с ключами:
                    'show_user_notes_cbq': callback.data последней просмотренной заметки;
                    'note_title_view_mode' (опционально): режим просмотра заголовков;
                    'title_mode_page' (опционально): callback.data последней просмотренной страницы заголовков;
                    'notes_search_keywords' (опционально): ключ для поиска
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """

    # Забираем данные из контекста
    state_data = await state.get_data()
    notes_search_keywords = state_data.get('notes_search_keywords')     # Ключ для поиска
    show_user_notes_cbq = state_data.get('show_user_notes_cbq')         # callback.data последней просмотренной заметки
    title_view_mode = state_data.get('note_title_view_mode')       # Режим просмотра заголовков

    # Переключаем режим просмотра заголовков на режим полного просмотра
    if title_view_mode:
        try:
            # Определяем номер последней просмотренной заметки
            current_note = show_user_notes_cbq.split(':')[0].split('_')[-1]        # show_note_{note_number}:{Notes.id}
        except AttributeError:
            await callback.answer('⚠️ Сначала выберите заметку!', show_alert=True)
            return

        # Очищаем контекст
        await state.clear()

        # Сохраняем фильтр поиска при переключении режимов
        if notes_search_keywords:
            await state.update_data(notes_search_keywords=notes_search_keywords)

        # Вызываем функцию просмотра заметки
        modified_callback = await modify_callback_data(
            bot.auxiliary_msgs['cbq'][callback.message.chat.id], f'my_notes_page_{current_note}'
        )
        await show_user_notes(modified_callback, bot, session, state)

    # Переключаем режим полного просмотра на режим просмотра заголовков
    else:
        try:
            # Определяем номер последней просмотренной заметки
            current_note_number = int(show_user_notes_cbq.split('_')[-1])                # my_notes_page_{note_number}

            # Определяем номер необходимой страницы просмотра заголовков
            page = math.ceil(current_note_number / PER_PAGE_NOTE_TITLES)
        except AttributeError:
            await callback.answer('⚠️ Сначала выберите заметку!', show_alert=True)
            return

        # Очищаем контекст
        await state.clear()

        # Сохраняем фильтр поиска при переключении режимов
        if notes_search_keywords:
            await state.update_data(notes_search_keywords=notes_search_keywords)

        # Вызываем функцию просмотра заголовков
        modified_callback = await modify_callback_data(callback, f'note_title_view_mode_page_{page}')
        await note_title_view_mode(modified_callback, state, bot, session)


# УДАЛЕНИЕ ЗАМЕТКИ

# Удаление заметки - ШАГ 1, запрос подтверждения
@note_router.callback_query(F.data.startswith('delete_note_'))
async def delete_note_ask_confirm(callback: types.CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) \
        -> None:
    """
    Удаление заметки - ШАГ 1, запрос подтверждения.

    :param callback: CallbackQuery-запрос формата "delete_note_<Notes.id>"
    :param state: Контекст состояния FSM с ключом "show_user_notes_cbq" для возврата к последней просмотренной странице
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """

    # Получаем идентификатор заметки из callback-запроса и объект из БД
    note_id_to_delete = int(callback.data.replace('delete_note_', ''))
    note_to_delete = await DataBase.get_note_by_id(session, note_id_to_delete)

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные страницы для отмены действия
    state_data = await state.get_data()
    show_user_notes_cbq = state_data.get('show_user_notes_cbq')

    # Отправляем информационное сообщение с запросом подтверждения удаления и кнопкой отмены
    msg_text = f'⚠️ Вы действительно хотите удалить заметку <b>"{note_to_delete.title}"</b>?'
    btns = {'Удалить 🗑': f'confirm_delete_note_{note_id_to_delete}', 'Отмена ❌': show_user_notes_cbq}
    kbds = get_inline_btns(btns=btns)
    msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Удаление заметки - ШАГ 2, подтверждение получено, удаление из базы, возврат к просмотру заметок
@note_router.callback_query(F.data.startswith('confirm_delete_note_'))
async def delete_note_get_confirm(callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) \
        -> None:
    """
    Удаление заметки - ШАГ 2, подтверждение получено, удаление из базы.

    :param callback: CallbackQuery-запрос формата "confirm_delete_note_<Notes.id>"
    :param session: Пользовательская сессия
    :param state: Контекст состояния FSM с ключом "show_user_notes_cbq" для возврата к последней просмотренной странице
    :param bot: Объект бота
    :return: None
    """

    # Получаем идентификатор заметки из callback-запроса
    note_id_to_delete = int(callback.data.replace('confirm_delete_note_', ''))

    # Удаляем заметку из базы данных
    try:
        is_deleted = await DataBase.delete_note_by_id(session, note_id_to_delete)

        # Выводим пользователю сообщение с результатом и удаляем информационное сообщение
        if is_deleted:
            await callback.answer(f'✅ Заметка "{is_deleted}" удалена', show_alert=True)
            await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)

            # Удаляем данные о заметках из контекста, чтобы обновились при возврате на страницу
            await state.update_data(user_notes=None)

            # Забираем из контекста данные для определения страницы перенаправления
            state_data = await state.get_data()
            redirect_page = state_data.get('title_mode_page') if state_data.get(
                'note_title_view_mode') else state_data.get('show_user_notes_cbq')

            # Перенаправляем пользователя на нужную страницу в зависимости от режима просмотра
            modified_callback = await modify_callback_data(callback, redirect_page)
            if state_data.get('note_title_view_mode'):
                await note_title_view_mode(modified_callback, state, bot, session)
            else:
                await show_user_notes(modified_callback, bot, session, state)

        # Обрабатываем ошибку, если заметка не найдена
        else:
            await callback.answer('⚠️ Упс, что-то пошло не так! Попробуйте ещё раз', show_alert=True)

    # Обрабатываем исключения
    except (Exception, ) as e:
        await callback.answer(f'⚠️ Упс, что-то пошло не так!\nОшибка: {str(e)}\n', show_alert=True)


# ДОБАВЛЕНИЕ НОВОЙ ЗАМЕТКИ

# Добавление новой заметки - ШАГ 1, запрос заголовка
@note_router.callback_query(F.data == 'add_new_note')
async def add_new_note_ask_title(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Добавление новой заметки - ШАГ 1, запрос заголовка.

    :param callback: CallbackQuery-запрос формата "add_new_note"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "show_user_notes_cbq" для возврата к последней просмотренной странице
    :return: None
    """

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные последней просмотренной страницы для кнопки отмены
    state_data = await state.get_data()
    cancelling_page = state_data.get('title_mode_page') if state_data.get('note_title_view_mode') else state_data.get(
        'show_user_notes_cbq')

    # Редактируем баннер и клавиатуру
    caption = banners_details.add_new_note_step_1
    btns = {'Отмена ❌': cancelling_page}
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=kbds)
    except Exception as e:
        print(e)

    # Сохраняем сообщение с баннером для редактирования и клавиатуру
    bot.reply_markup_save[callback.message.chat.id] = kbds
    bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message

    # Устанавливаем состояния ввода заголовка заметки
    await state.set_state(NotesFSM.title)


# Добавление новой заметки - ШАГ 2, заголовок получен, запрос текста заметки
@note_router.message(NotesFSM.title, IsKeyNotInStateFilter('edited_note'))
async def add_new_note_get_title_ask_text(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """
    Добавление новой заметки - ШАГ 2, заголовок получен, запрос текста заметки.

    :param message: Текстовое сообщение с заголовком заметки
    :param bot: Объект бота
    :param state: Контекст состояния FSM, строго БЕЗ ключа 'edited_note'
    :return: None
    """

    # Сохраняем сообщение во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Проверяем длину заголовка
    if len(message.text) < MIN_NOTE_TITLE_LENGTH:
        msg_text = f'⚠️ Упс! Заголовок заметки должен содержать не менее {MIN_NOTE_TITLE_LENGTH} символов!'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем заголовок в контекст
    await state.update_data(title=message.text)

    # Редактируем баннер
    caption = banners_details.add_new_note_step_2
    try:
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=caption, reply_markup=bot.reply_markup_save[message.chat.id]
        )
    except (Exception, ) as e:
        print(e)

    # Устанавливаем состояния ввода текста заметки
    await state.set_state(NotesFSM.text)


# Добавление новой заметки - ШАГ 3, текст заметки получен, запрос первого примера
@note_router.message(NotesFSM.text, IsKeyNotInStateFilter('edited_note'))
async def add_new_note_get_text_ask_example(message: types.Message, bot: Bot, state: FSMContext) -> None:
    """
    Добавление новой заметки - ШАГ 3, текст заметки получен, запрос первого примера.

    :param message: Текстовое сообщение с текстом заметки
    :param bot: Объект бота
    :param state: Контекст состояния FSM, строго БЕЗ ключа 'edited_note'
    :return: None
    """

    # Сохраняем сообщение во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Проверяем длину текста
    if len(message.text) < MIN_NOTE_TEXT_LENGTH:
        msg_text = f'⚠️ Упс! Текст заметки должен содержать не менее {MIN_NOTE_TEXT_LENGTH} символов!'
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем текст заметки в контекст
    await state.update_data(text=message.text)

    # Редактируем баннер
    caption = banners_details.add_new_note_step_3
    try:
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=caption, reply_markup=bot.reply_markup_save[message.chat.id]
        )
    except Exception as e:
        print(e)

    # Устанавливаем состояния ввода примера
    await state.set_state(NotesFSM.example)


# Добавление новой заметки - ШАГ 4, запись в базу + добавление примеров в новую заметку
# (ветвление логики по наличию ключа "new_note" в контексте - если он есть, это добавление дополнительных примеров)
@note_router.message(NotesFSM.example, IsKeyNotInStateFilter('edited_note'))
async def add_new_note_get_example(message: types.Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    """
    Добавление новой заметки - ШАГ 4, запись в базу + добавление примеров в новую заметку.
    Ветвление логики по наличию ключа "new_note" в контексте - если он есть, это добавление дополнительных примеров

    :param message: Текстовое сообщение с примером или 1 символом для заглушки (тогда пример не будет сохраняться)
    :param bot: Объект бота
    :param state: Контекст состояния FSM с данными для создания заметки, ключом "show_user_notes_cbq" для возврата к
           заметкам и (опционально) ключом "new_note" для добавления дополнительных примеров к уже созданной заметке.
           Строго БЕЗ ключа 'edited_note' для разделения логики контроллеров с добавлением заметки при редактировании.
           Опционально - с ключом 'note_title_view_mode' при режиме просмотра по заголовкам.
    :param session: Пользовательская сессия
    :return: None
    """

    # Сохраняем сообщение во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Если передан 1 символ - преобразуем к заглушке для не добавления примера в БД
    examples = message.text if len(message.text) > 1 else PLUG_TEMPLATE

    # Делаем валидацию ввода (если не заглушка), при некорректном значении уведомляем и выходим из функции
    if examples != PLUG_TEMPLATE:
        if not validate_context_example(examples):
            msg_text = context_validation_not_passed_msg_template
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
            await bot.delete_message(message.chat.id, message.message_id)
            return

    # Записываем пример в контекст и забираем все данные из контекста
    await state.update_data(examples=examples)
    state_data = await state.get_data()

    # Если это добавление второго+ примера:
    if state_data.get('new_note'):

        # Создаём новый пример в БД
        try:
            await DataBase.create_context_example(session, {'context': examples}, note_id=state_data['new_note'].id)
        except Exception as e:
            msg_text = oops_with_error_msg_template.format(error=str(e))
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
            await state.clear()
            return

        # Получаем обновленную заметку
        new_note = await DataBase.get_note_by_id(session, state_data['new_note'].id)

    # Если это новая заметка с первым примером:
    else:
        # Создаем новую заметку с примером в БД
        try:
            new_note = await DataBase.create_new_note(
                session=session, user_id=bot.auth_user_id[message.chat.id], note_data=state_data
            )
        # При ошибке отправляем уведомление + выходим из обработчика
        except Exception as e:
            msg_text = oops_with_error_msg_template.format(error=str(e))
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
            await state.clear()
            return

    if not new_note:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)
        return

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # Забираем из контекста данные режима просмотра для определения страницы перенаправления
    title_view_mode = state_data.get('note_title_view_mode')
    redirect_page = 'note_title_view_mode_page_1' if title_view_mode else 'my_notes_page_1'

    # Редактируем баннер и клавиатуру
    caption = banners_details.add_new_note_step_4.format(title=new_note.title)
    btns = {
        'Добавить ещё пример ➕': f'add_example_to_new_note_{new_note.id}',
        'Вернуться к записям': redirect_page
    }
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
            caption=caption, reply_markup=kbds
        )
    except Exception as e:
        print(e)

    # Отправляем уведомление об успехе
    msg_text = f'✅ Заметка "{new_note.title}" сохранена!'
    await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Отправляем в чат сообщение с данными заметки
    examples = '- ' + '\n- '.join([i.example for i in new_note.examples])
    msg_text = f'📒 <b>{new_note.title}</b>\n{new_note.text}\n\n<b>Примеры:</b>\n<i>{examples}</i>'
    msg = await message.answer(msg_text)
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(msg)

    # Сбрасываем контекст и добавляем туда созданную заметку на случай добавления дополнительных примеров и режим
    await state.clear()
    await state.update_data(new_note=new_note, note_title_view_mode=title_view_mode)


# Добавление новой заметки - ШАГ 5, добавление дополнительных (2+) примеров; запрос текста примера.
@note_router.callback_query(F.data.startswith('add_example_to_new_note_'), IsKeyInStateFilter('new_note'))
async def add_example_to_note(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Добавление новой заметки - ШАГ 5, добавление дополнительных (2+) примеров; запрос текста примера.

    :param callback: CallbackQuery-запрос формата "add_example_to_new_note_<Note.id>"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключами:
                    - "new_note" (обязательно): Объект заметки;
                    - "note_title_view_mode" (опционально): Режим просмотра названий заметок
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из контекста данные для кнопки отмены действия
    state_data = await state.get_data()
    cancelling_page = state_data.get('title_mode_page') if state_data.get('note_title_view_mode') else 'my_notes_page_1'

    # Редактируем баннер и клавиатуру
    caption = banners_details.add_new_note_add_example
    btns = {'Отмена ✖': cancelling_page}
    kbds = get_kbds_with_navi_header_btns(level=2, btns=btns, sizes=(2, 1))
    try:
        await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(caption=caption, reply_markup=kbds)
    except (Exception, ) as e:
        print(e)

    # Устанавливаем состояние ввода примера к заметке
    await state.set_state(NotesFSM.example)


# ПОИСК ЗАМЕТОК

# Поиск заметок - ШАГ 1, запрос ключевого слова
@note_router.callback_query(F.data == 'search_notes')
async def search_notes_ask_keywords(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Поиск заметок - ШАГ 1, запрос ключевого слова.

    :param callback: CallbackQuery-запрос формата "search_notes"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с возможными ключами:
                    - "note_title_view_mode" (опционально): Режим просмотра заметок по заголовкам
                    - "title_mode_page" (опционально): Последняя страница просмотра заметок по заголовкам
                    - "show_user_notes_cbq" (опционально): Последняя просмотренная заметка в режиме полного просмотра
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Забираем из state данные для определения страницы отмены действия
    state_data = await state.get_data()
    cancelling_page = state_data.get('title_mode_page') if state_data.get('note_title_view_mode') else state_data.get(
        'show_user_notes_cbq')

    # Отправляем информационное сообщение с кнопкой отмены
    msg_text = 'Введите текст для поиска в названии или тексте заметки'
    btns = {'Отмена ✖': cancelling_page}
    kbds = get_inline_btns(btns=btns)
    msg = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)

    # Устанавливаем состояние ввода ключевого слова для поиска
    await state.set_state(NotesFSM.search_keywords)


# Поиск заметок - ШАГ 2, ключ получен, сохраняем его в контекст и вызываем основную функцию показа заметок
@note_router.message(StateFilter(NotesFSM.search_keywords))
async def search_notes_get_keywords(message: types.Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    """
    Поиск заметок - ШАГ 2, ключ получен, сохраняем его в контекст и вызываем основную функцию показа заметок.

    :param message: Текстовое сообщение с ключевым словом/фразой для поиска
    :param bot: Объект бота
    :param state: Контекст состояния FSM с возможными ключами:
                    - "note_title_view_mode" (опционально): Режим просмотра заметок по заголовкам
    :param session: Пользовательская сессия
    :return: None
    """

    # Сохраняем сообщение во вспомогательном хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Записываем ключ для поиска notes_search_keywords= в контекст
    notes_search_keywords = message.text
    await state.update_data(notes_search_keywords=notes_search_keywords)

    # Удаляем из контекста заметки пользователя для их обновления
    await state.update_data(user_notes=None)

    # Забираем из контекста данные для определения страницы перенаправления
    state_data = await state.get_data()
    new_callback = 'note_title_view_mode_page_1' if state_data.get('note_title_view_mode') else 'my_notes_page_1'

    # Вызываем необходимую функцию показа заметок в зависимости от режима
    modified_callback = await modify_callback_data(bot.auxiliary_msgs['cbq'][message.chat.id], new_callback)
    if state_data.get('note_title_view_mode'):
        await note_title_view_mode(modified_callback, state, bot, session)
    else:
        await show_user_notes(modified_callback, bot, session, state)


# Отмена поиска заметок
@note_router.callback_query(F.data == 'cancel_search_notes', IsKeyInStateFilter('notes_search_keywords'))
async def cancel_search_notes(callback: types.CallbackQuery, bot: Bot, state: FSMContext, session: AsyncSession) \
        -> None:
    """
    Отмена поиска заметок.

    :param callback: CallbackQuery-запрос формата "cancel_search_notes"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с возможными ключами:
                    - "note_title_view_mode" (опционально): Режим просмотра заметок по заголовкам
    :param session: Пользовательская сессия
    :return: None
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Удаляем из контекста ключ для поиска и отфильтрованные заметки пользователя
    await state.update_data(notes_search_keywords=None, user_notes=None)

    # Забираем из контекста данные для определения страницы перенаправления в зависимости от режима
    state_data = await state.get_data()
    new_callback = 'note_title_view_mode_page_1' if state_data.get('note_title_view_mode') else 'my_notes_page_1'

    # Вызываем необходимую функцию показа заметок в зависимости от режима
    modified_callback = await modify_callback_data(bot.auxiliary_msgs['cbq'][callback.message.chat.id], new_callback)
    if state_data.get('note_title_view_mode'):
        await note_title_view_mode(modified_callback, state, bot, session)
    else:
        await show_user_notes(modified_callback, bot, session, state)


# РЕДАКТИРОВАНИЕ ЗАМЕТКИ

# Редактирование заметки - ШАГ 1, основное окно редактирования с выбором действия.
# Обработчик также принудительно вызывается после редактирования заголовка/текста заметки, добавления нового примера
@note_router.callback_query(F.data.startswith('edit_note_'))
async def edit_note_main(callback: types.CallbackQuery, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    """
    Редактирование заметки - ШАГ 1, основное окно редактирования с выбором действия.
    Обработчик также принудительно вызывается после редактирования заголовка/текста заметки, добавления нового примера.

    :param callback: CallbackQuery-запрос формата "edit_note_<Notes.id>" (или иное, если был принудительный вызов)
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключами:
                    - "show_user_notes_cbq": С callback.data страницы возврата
                    - "edited_note" (опционально): При принудительном вызове после редактирования
                    - "audio_examples" (опционально): При вызове редактирования при наличии аудио примеров в чате
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Забираем данные из контекста
    state_data = await state.get_data()
    show_user_notes_cbq = state_data.get('show_user_notes_cbq')

    # Устанавливаем флаг is_forced, если был принудительный вызов после обновления данных
    is_forced = state_data.get('edited_note')

    # Очищаем чат от аудиозаписей примеров, если они есть
    if state_data.get('audio_examples'):
        audio_msgs = list(state_data.get('audio_examples').values())[0]
        for msg in audio_msgs:
            try:
                await bot.delete_message(callback.message.chat.id, msg.message_id)
            except (Exception, ):
                pass
        await state.update_data(audio_examples=None)

    # Получаем id заметки из callback
    note_id = int(callback.data.split('_')[-1])

    # Получаем объект заметки по id и сохраняем его в контекст под ключом "edited_note"
    edited_note = await DataBase.get_note_by_id(session, note_id)
    await state.update_data(edited_note=edited_note)

    # Формируем и сохраняем клавиатуру
    btns = {
        'Заголовок 🖌': 'edit_note:title',
        'Текст 🖌': 'edit_note:text',
        'Примеры 🖌': 'edit_note:examples',
        'Новый пример ➕': 'add_another_example_to_edited_note',
        'Вернуться к просмотру ⬅': show_user_notes_cbq,
    }
    kbds = get_inline_btns(btns=btns, sizes=(2, 2, 1))
    bot.reply_markup_save[callback.message.chat.id] = kbds

    # Если это принудительный вызов после неких изменений, то редактируем описание сообщения с заметкой
    if is_forced:
        await update_note_msg_data(bot, callback.message.chat.id, state_data, edited_note)

    # Если это первичный вызов редактирования, то сохраняем сообщение с заметкой и редактируем клавиатуру
    else:
        await callback.message.edit_reply_markup(reply_markup=kbds)
        await state.update_data(note_msg=callback.message)


# Редактирование заметки - отмена ввода данных, возврат к основному меню редактирования заметки
@note_router.callback_query(F.data == 'return_to_edit_note_main')
async def return_to_edit_note_main(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование заметки - отмена ввода данных, возврат к основному меню редактирования заметки.
    Сброс состояния ввода, удаление информационного сообщения.

    :param callback: CallbackQuery-запрос формата 'return_to_edit_note_main'
    :param bot: Объект бота
    :param state: Контекст состояния FSM
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback
    await callback.answer('⚠️ Действие отменено!', show_alert=True)            # Отправляем уведомление
    await state.set_state(None)                                                     # Сбрасываем состояние ввода
    await delete_last_message(bot, callback.message.chat.id)                        # Удаляем информационное сообщение


# Редактирование заметки - изменение заголовка или текста заметки, ШАГ 1: запрос новых данных
@note_router.callback_query(F.data.startswith('edit_note:title') | F.data.startswith('edit_note:text'))
async def edit_note_title_or_text_ask_for_new_data(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование заметки - изменение заголовка или текста заметки, ШАГ 1: запрос новых данных.
    Функция обрабатывает оба кейса: 'edit_note:title' и 'edit_note:text'.

    :param callback: CallbackQuery-запрос формата "edit_note:<title | text>"
    :param bot: Объект бота
    :param state: Контекст состояния FSM
    :return:
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Забираем из контекста информацию о редактируемой заметке
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Удаляем из чата сообщения с примерами и информационные (если есть)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id, only_examples=True)
    await delete_info_message(bot, callback.message.chat.id, state_data)

    # Из callback забираем название редактируемого атрибута
    edited_attr = callback.data.split(':')[-1]

    # Получаем текущее значение атрибута для вывода в клавиатуре
    current_data = getattr(edited_note, edited_attr)

    # Отправляем информационное сообщение с кнопкой отмены и вывода текущих данных
    btns = {
        'Отмена ❌': 'return_to_edit_note_main',
        'Текст сейчас 📝': f'switch_inline_query_current_chat_{current_data}'
    }
    kbds = get_inline_btns(btns=btns)
    msg_attr = 'заголовок' if edited_attr == 'title' else 'текст'
    msg_text = (f'Введите <b>новый {msg_attr} заметки</b>  или нажмите <i>"Текст сейчас 📝"</i> для подгрузки в строку '
                'ввода текущих данных для удобной корректировки.')
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
    await state.update_data(info_msg=msg)

    # Определяем требуемое состояние ввода и устанавливаем его
    required_state = NotesFSM.title if edited_attr == 'title' else NotesFSM.text
    await state.set_state(required_state)


# Редактирование заметки - изменение заголовка или текста заметки, ШАГ 2: новые данные получены, обновление в БД
@note_router.message(StateFilter(NotesFSM.title, NotesFSM.text), IsKeyInStateFilter('edited_note'))
async def edit_note_get_title_or_text(message: types.Message, bot: Bot, state: FSMContext, session: AsyncSession) \
        -> None:
    """
    Редактирование заметки - изменение заголовка или текста заметки, ШАГ 2: новые данные получены, обновление в БД,
    возврат к основному сообщению редактирования.

    :param message: Текстовое сообщение с новыми данными
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом 'edited_note'
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Из контекста забираем название редактируемого атрибута
    current_state = await state.get_state()                             # NotesFSM:title | NotesFSM:text
    attr_name = current_state.split(':')[-1]

    # Удаляем сообщение с данными от пользователя и информационное сообщение с кнопками
    await delete_last_message(bot, message.chat.id)
    await delete_last_message(bot, message.chat.id)

    # Забираем из контекста информацию о редактируемой заметке
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Обновляем данные заметки в БД и выводим уведомление
    is_updated = await DataBase.update_note_by_id(session, edited_note.id, **{attr_name: message.text})
    if is_updated:
        await try_alert_msg(bot, message.chat.id, '✅ Данные успешно обновлены!', if_error_send_msg=True)
    else:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)

    # Возвращаемся к основному окну редактирования заметки
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][message.chat.id], f'edit_note_{edited_note.id}'
    )
    await edit_note_main(modified_callback, bot, state, session)


# Редактирование заметки - добавление нового примера, ШАГ 1: запрос текста примера
@note_router.callback_query(F.data == 'add_another_example_to_edited_note', IsKeyInStateFilter('edited_note'))
async def add_another_example_to_edited_note_ask_text(callback: types.CallbackQuery, bot: Bot, state: FSMContext) \
        -> None:
    """
    Редактирование заметки - добавление нового примера, ШАГ 1: запрос текста примера.

    :param callback: CallbackQuery-запрос формата "add_another_example_to_edited_note"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "edited_note"
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Удаляем из чата сообщения с примерами и информационные сообщения (если есть)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id, only_examples=True)
    state_data = await state.get_data()
    await delete_info_message(bot, callback.message.chat.id, state_data)

    # Отправляем информационное сообщение с запросом ввода и кнопкой отмены действия
    kbds = get_inline_btns(btns={'Отмена ❌': 'return_to_edit_note_main'})
    msg_text = 'Введите текст <b>нового примера</b> заметки'
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
    await state.update_data(info_msg=msg)               # Дублируем в контекст, чтобы удалять при переключении запросов

    # Добавляем в state ключ 'add_example' для разделения контроллеров добавления и редактирования текста примера
    await state.update_data(add_example=True)

    # Устанавливаем состояние ввода примера к заметке
    await state.set_state(NotesFSM.example)


# Редактирование заметки - добавление нового примера, ШАГ 2: текст примера получен, записываем в БД
@note_router.message(NotesFSM.example, IsKeyInStateFilter('edited_note', 'add_example'))
async def add_another_example_to_edited_note_get_text(message: types.Message, bot: Bot, state: FSMContext,
                                                      session: AsyncSession) -> None:
    """
    Редактирование заметки - добавление нового примера, ШАГ 2: текст примера получен, записываем в БД, возвращаемся к
    основному окну редактирования.

    :param message: Текстовое сообщение с новым примером
    :param bot: Объект бота
    :param state: Контекст состояния с ключом 'edited_note' и 'add_example' - для разделения с редактированием
    :param session: Пользовательская сессия
    :return: None
    """

    # Делаем валидацию ввода, при некорректном значении уведомляем и выходим из функции
    if not validate_context_example(message.text):
        msg_text = context_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Сохраняем сообщение с примером во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Забираем из контекста данные
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Создаём новый пример Context в БД и отправляем уведомление с результатом
    try:
        data = {'context': message.text}
        created_example = await DataBase.create_context_example(session, data, note_id=edited_note.id)
        if created_example:
            await try_alert_msg(bot, message.chat.id, '✅ Пример успешно добавлен!', if_error_send_msg=True)
    except (Exception, ) as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Удаляем сообщение с текстом примера и информационное сообщение
    await delete_info_message(bot, message.chat.id, state_data)
    await delete_last_message(bot, message.chat.id)

    # Удаляем из state ключ 'add_example'
    await state.update_data(add_example=None)

    # Возвращаемся к основному окну редактирования заметки
    modified_callback = await modify_callback_data(
        bot.auxiliary_msgs['cbq'][message.chat.id], f'edit_note_{edited_note.id}'
    )
    await edit_note_main(modified_callback, bot, state, session)


# Редактирование заметки - изменение примеров, ШАГ 1: просмотр примеров с кнопками редактирования и удаления.
# Обработчик также принудительно вызывается при отмене удаления примера, после завершения удаления
@note_router.callback_query(F.data.contains('edit_note:example'), IsKeyInStateFilter('edited_note'))
async def edit_note_show_examples(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование заметки - изменение примеров, ШАГ 1: просмотр примеров с кнопками редактирования и удаления.
    Обработчик также принудительно вызывается при отмене удаления примера, после завершения удаления, завершении
    редактирования примера.

    :param callback: CallbackQuery-запрос формата 'edit_note:example' (или иное при принудительном вызове)
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом 'edited_note'
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Забираем из контекста информацию о редактируемой заметке
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Проверяем отсутствие ключа 'add_example' в контексте, если есть - удаляем
    if state_data.get('add_example'):
        await state.update_data(add_example=None)

    # Удаляем из чата сообщения с примерами и информационные сообщения (если есть)
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id, only_examples=True)
    await delete_info_message(bot, callback.message.chat.id, state_data)

    # Отправляем сообщение с примерами и кнопками для их редактирования и удаления
    for example in edited_note.examples:
        msg_text = context_example_msg_template.format(
            example=example.example, created=example.created, updated=example.updated
        )
        kbds = get_inline_btns(
                btns={'Изменить 🖌': f'update_context_{example.id}', 'Удалить 🗑': f'delete_context_{example.id}'}
        )
        msg = await callback.message.answer(text=msg_text, reply_markup=kbds)

        # Сохраняем сообщение с примером в хранилище для примеров
        bot.auxiliary_msgs['example_msgs'][callback.message.chat.id].append(msg)


# Редактирование заметки - возврат к просмотру примеров при отмене удаления/редактирования
@note_router.callback_query(F.data == 'return_to_edit_note_show_examples')
async def return_to_edit_note_show_examples(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование заметки - отмена действия и возврат к просмотру примеров (отмена удаления или редактирования
    примера).
    Функция удаляет информационное сообщение, сбрасывает состояние ввода и вызывает показ примеров.

    :param callback: CallbackQuery-запрос формата 'return_to_edit_note_show_examples'
    :param bot: Объект бота
    :param state: Контекст состояния FSM
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Удаляем информационное сообщение
    await delete_last_message(bot, callback.message.chat.id)

    # Сбрасываем состояние ввода
    await state.set_state(None)

    # Удаляем из state ключ 'example_to_update_id' при отмене редактирования
    state_data = await state.get_data()
    if state_data.get('example_to_update_id'):
        await state.update_data(example_to_update_id=None)

    # Возвращаемся к просмотру примеров
    await edit_note_show_examples(callback, bot, state)


# Редактирование заметки - удаление примера, ШАГ 1: запрос подтверждения
@note_router.callback_query(F.data.startswith('delete_context_'), IsKeyInStateFilter('edited_note'))
async def delete_note_example_ask_confirm(callback: types.CallbackQuery, bot: Bot, state: FSMContext) -> None:
    """
    Редактирование заметки - удаление примера, ШАГ 1: запрос подтверждения.

    :param callback: CallbackQuery-запрос формата "delete_context_<Context.id>"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "edited_note"
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Очищаем чат от сообщений с примерами
    for msg in bot.auxiliary_msgs['example_msgs'][callback.message.chat.id]:
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=msg.message_id)
        except (Exception, ) as e:
            print(e)

    # Получаем данные о заметке из контекста
    state_data = await state.get_data()
    edited_note = state_data.get('edited_note')

    # Определяем ID и объект удаляемого примера
    example_to_delete_id = int(callback.data.split('_')[-1])
    example_to_del_obj = list(filter(lambda x: x.id == example_to_delete_id, edited_note.examples))[0]

    # Отправляем информационное сообщение с подтверждением удаления примера и кнопкой отмены действия
    btns = {
        'Удалить 🗑': f'confirm_delete_context_{example_to_delete_id}',
        'Отмена ❌': 'return_to_edit_note_show_examples'
    }
    kbds = get_inline_btns(btns=btns)
    msg_text = f'Вы уверены, что хотите удалить пример <b>"{example_to_del_obj.example}"</b>?'
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)


# Редактирование заметки - удаление примера, ШАГ 2: подтверждение получено, удаление из БД, возврат к просмотру примеров
@note_router.callback_query(F.data.startswith('confirm_delete_context_'), IsKeyInStateFilter('edited_note'))
async def confirm_delete_note_example(callback: types.CallbackQuery, bot: Bot, state: FSMContext,
                                      session: AsyncSession) -> None:
    """
    Редактирование заметки - удаление примера, ШАГ 2: подтверждение получено, удаление из БД, возврат к просмотру
    примеров.

    :param callback: CallbackQuery-запрос формата "confirm_delete_context_<Context.id>"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "edited_note"
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем данные о редактируемой заметке из контекста
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Забираем из callback ID удаляемого примера
    example_to_delete_id = int(callback.data.split('_')[-1])

    # Удаляем пример из БД
    deleted = await DataBase.delete_context_by_id(session, example_to_delete_id)

    # Если удалить не удалось, отправляем оповещение и выходим из функции
    if not deleted:
        await try_alert_msg(bot, callback.message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)
        return

    # При успехе отправляем оповещение
    await try_alert_msg(bot, callback.message.chat.id, f'✅ Пример удален!', if_error_send_msg=True)

    # Удаляем информационное сообщение с подтверждением
    await delete_last_message(bot, callback.message.chat.id)

    # Обновляем в контексте данные о заметке с учетом изменений
    edited_note = await DataBase.get_note_by_id(session, edited_note.id)
    await state.update_data(edited_note=edited_note)

    # Обновляем данные сообщения с описанием заметки
    await update_note_msg_data(bot, callback.message.chat.id, state_data, edited_note)

    # Возвращаемся к просмотру примеров заметки
    await edit_note_show_examples(callback, bot, state)


# Редактирование заметки - изменение примера, ШАГ 1: запрос нового текста
@note_router.callback_query(F.data.startswith('update_context_'), IsKeyInStateFilter('edited_note'))
async def update_context_example_ask_new_text(callback: types.CallbackQuery, bot: Bot, state: FSMContext,
                                              session: AsyncSession) -> None:
    """
    Редактирование заметки - изменение примера, ШАГ 1: запрос нового текста.

    :param callback: CallbackQuery-запрос формата "update_context_<Context.id>"
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "edited_note"
    :param session: Пользовательская сессия
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Удаляем примеры из чата
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id, only_examples=True)

    # Забираем из callback ID редактируемого примера и сохраняет его в state
    example_to_update_id = int(callback.data.split('_')[-1])
    await state.update_data(example_to_update_id=example_to_update_id)

    # Получаем текущее значение атрибута для вывода в клавиатуре
    context_obj = await DataBase.get_context_by_id(session, example_to_update_id)
    current_data = getattr(context_obj, 'example')

    # Отправляем информационное сообщение с кнопкой отмены и вывода в чат текущего текста примера
    msg_text = (f'Введите <b>новый текст примера</b> или нажмите <i>"Текст сейчас 📝"</i> для подгрузки в строку '
                'ввода текущих данных для удобной корректировки.')
    btns = {
        'Отмена ❌': 'return_to_edit_note_show_examples',
        'Текст сейчас 📝': f'switch_inline_query_current_chat_{current_data}'
    }
    kbds = get_inline_btns(btns=btns)
    msg = await callback.message.answer(text=msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(msg)
    await state.update_data(info_msg=msg)

    # Устанавливаем состояние ввода нового текста примера
    await state.set_state(NotesFSM.example)


# Редактирование заметки - изменение примера, ШАГ 2: новый текст получен, обновление в БД, возврат к просмотру примеров
@note_router.message(NotesFSM.example, IsKeyInStateFilter('edited_note', 'example_to_update_id'))
async def update_context_example_get_text(message: types.Message, bot: Bot, state: FSMContext, session: AsyncSession) \
        -> None:
    """
    Редактирование заметки - изменение примера, ШАГ 2: новый текст получен, обновление в БД, возврат к просмотру
    примеров.

    :param message: Текстовое сообщение с новым текстом примера
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом "edited_note" и ключом "example_to_update_id" с id редактируемого
                  примера Context
    :param session: Пользовательская сессия
    :return: None
    """

    # Делаем валидацию ввода, при некорректном значении уведомляем и выходим из функции
    if not validate_context_example(message.text):
        msg_text = context_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Сохраняем сообщение во вспомогательное хранилище
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Получаем данные о редактируемой заметке из контекста
    state_data = await state.get_data()
    edited_note: Notes = state_data.get('edited_note')

    # Получаем ID редактируемого примера
    example_to_update_id = state_data.get('example_to_update_id')

    # Обновляем данные примера в БД и отправляем оповещение
    try:
        updated = await DataBase.update_context_by_id(session, example_to_update_id, message.text)
        await try_alert_msg(bot, message.chat.id, f'✅ Пример обновлен!', if_error_send_msg=True)
    except (Exception, ) as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        return

    # Если обновить не удалось, отправляем оповещение и выходим из функции
    if not updated:
        await try_alert_msg(bot, message.chat.id, oops_try_again_msg_template, if_error_send_msg=True)
        await delete_last_message(bot, message.chat.id)
        return

    # Удаляем информационное сообщение с подтверждением и текущим текстом примера
    await delete_last_message(bot, message.chat.id)
    await delete_last_message(bot, message.chat.id)

    # Удаляем из контекста ID редактируемого примера
    await state.update_data(example_to_update_id=None)

    # Обновляем в контексте данные о заметке с учетом изменений
    edited_note = await DataBase.get_note_by_id(session, edited_note.id)
    await state.update_data(edited_note=edited_note)

    # Обновляем данные сообщения с описанием заметки
    await update_note_msg_data(bot, message.chat.id, state_data, edited_note)

    # Возвращаемся к просмотру примеров заметки
    await edit_note_show_examples(bot.auxiliary_msgs['cbq'][message.chat.id], bot, state)
