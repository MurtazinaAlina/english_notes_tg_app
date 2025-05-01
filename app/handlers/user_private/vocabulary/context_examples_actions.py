"""
Обработка действий в разделе "Словарь".
CONTEXT - действия с примерами (просмотр, добавление, редактирование, удаление).

INFO:
1. Действия с примерами доступны на завершающем шаге редактирования WordPhrase - оно вынесено в модуль
   vocabulary_actions.py. По сути это ШАГ 7 в редактировании слова/фразы.
2. Отмена добавления нового примера обрабатывается в vocabulary_actions.py -> update_word_wait_context
3. При редактировании примера в контекст добавляется ключ 'editing_context_obj' с редактируемым объектом Context
4. Информационные сообщения с кнопками отмены/подтверждения для удаления/редактирования примера Context сохраняются
   в атрибуте WordPhraseFSM.updating_info_message_with_cancel; объект самого редактируемого/удаляемого сообщения
   сохраняется в атрибуте WordPhraseFSM.editing_message.
"""
from typing import Type

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.banners import banners_details
from app.database.db import DataBase
from app.database.models import Context
from app.keyboards.inlines import get_inline_btns, add_new_or_edit_word_main_btns
from app.utils.custom_bot_class import Bot
from app.filters.custom_filters import ChatTypeFilter, IsKeyNotInStateFilter, IsKeyInStateFilter
from app.common.tools import get_upd_word_and_cancel_page_from_context, get_word_phrase_caption_formatting, \
    clear_auxiliary_msgs_in_chat, try_alert_msg, validate_context_example
from app.common.msg_templates import context_example_msg_template, oops_with_error_msg_template, \
    context_validation_not_passed_msg_template
from app.common.fsm_classes import WordPhraseFSM


# Создаём роутер для приватного чата бота с пользователем
context_examples_router = Router()

# Настраиваем фильтры роутера. Строго приват + наличие ключа 'word_to_update' в FSM
context_examples_router.message.filter(ChatTypeFilter(['private']), IsKeyInStateFilter('word_to_update'))


# Просмотр примеров Context в рамках редактирования записи WordPhrase
@context_examples_router.callback_query(WordPhraseFSM.context, F.data == 'edit_context')
async def show_word_context(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    Просмотр примеров Context в рамках редактирования записи WordPhrase.

    Функция выводит в чат сообщения с информацией о примерах с доступом к редактированию/удалению.

    :param callback: Callback-запрос формата "edit_context"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Получаем данные из контекста
    word_to_update, cancel_page = await get_upd_word_and_cancel_page_from_context(state)

    # Редактируем баннер и клавиатуру
    btns = {
        'Вернуться к записям 📖': cancel_page,
        'Добавить ещё пример ➕': f'add_more_examples_to_word_{word_to_update.id}'
    }
    kbds = add_new_or_edit_word_main_btns(
        btns=btns, level=2, sizes=(2, 1, 1, 2), cancel_possible=False
    )
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_to_update)
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_show_context.format(**caption), reply_markup=kbds
    )

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

    # INFO: на выходе остаётся WordPhraseFSM.context, IsKeyInStateFilter('word_to_update')


# ДОБАВЛЕНИЕ ПРИМЕРА

# Редактирование WordPhrase - добавить НОВЫЙ пример Context
@context_examples_router.callback_query(WordPhraseFSM.context, F.data.startswith('add_more_examples_to_word_'))
async def add_new_context_ask_text(callback: types.CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """
    ДОБАВЛЕНИЕ ПРИМЕРА. Добавление нового примера Context к слову/фразе WordPhrase.

    :param callback: Callback-запрос формата "add_more_examples_to_word_{word_id}"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Чистим сообщения в чате
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Формируем клавиатуру
    # Отдельный обработчик отмены не требуется, тк state == WordPhraseFSM:context и есть ключ word_to_update, при клике
    # запрос автоматически уйдет в обработчик update_word_wait_context
    btns = {'Отмена ❌': 'edit_word_cancel_add_example'}
    kbds = add_new_or_edit_word_main_btns(level=2, btns=btns, cancel_possible=False, sizes=(2, 1))

    # Забираем слово/фразу из контекста
    data = await state.get_data()
    word = data.get('word_to_update')

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.update_word_add_context.format(word=word.word), reply_markup=kbds
    )
    # INFO: StateContext и так актуален (context), установка не нужна.
    #       Обработка введённых данных уходит напрямую в update_word_get_context


# Добавление нового введённого примера из add_new_context_ask_text, запрос завершения / дальнейших действий с примерами
@context_examples_router.message(WordPhraseFSM.context, IsKeyNotInStateFilter('editing_context_obj'))
async def update_word_get_context(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Добавление нового введённого примера Context из add_new_context, запрос завершения/дальнейших действий с примерами.

    :param message: Сообщение пользователя с новым примером Context
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update' и БЕЗ ключа 'editing_context_obj'
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

    # Записываем контекст в контекст состояния
    await state.update_data(context=message.text)

    # Забираем все введённые данные
    data: dict = await state.get_data()
    word_id = data.get('word_to_update').id
    cancel_page_address = data.get('page_address')

    # Добавляем пример Context в БД и отправляем сообщение пользователю
    try:
        await DataBase.create_context_example(session, word_id=word_id, data=data)
        await try_alert_msg(bot, message.chat.id, '✅ Пример успешно добавлен!', if_error_send_msg=True)

    except Exception as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

    # Обновляем данные в ключе word_to_update в контексте (для дальнейшей работы с примерами)
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
    kbds = add_new_or_edit_word_main_btns(
        level=2, btns=btns, cancel_possible=False, sizes=(2, 1, 1), cancel_page_address=cancel_page_address
    )
    bot.reply_markup_save[message.chat.id] = kbds
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word_with_new_date)
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.update_word_step_6.format(**caption), reply_markup=kbds
    )

    # Очищаем вспомогательные сообщения. Состояние пока не сбрасываем, тк возможна дальнейшая работа с примерами
    await clear_auxiliary_msgs_in_chat(bot, message.chat.id)

    # INFO: на выходе остаётся WordPhraseFSM.context, IsKeyInStateFilter('word_to_update') на случай, если
    #       нужно будет дальше работать с примерами Context


# УДАЛЕНИЕ ПРИМЕРОВ

# Отмена редактирования/удаления примера Context
@context_examples_router.callback_query(F.data == 'cancel_update_context')
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

    # Удаляем информационное сообщение с кнопкой отмены
    await bot.delete_message(
        chat_id=WordPhraseFSM.updating_info_message_with_cancel.chat.id,
        message_id=WordPhraseFSM.updating_info_message_with_cancel.message_id
    )

    # Удаляем редактируемое сообщение из атрибута WordPhraseFSM
    WordPhraseFSM.editing_message = None

    # INFO: на выходе остаётся state == state, ключи в state: word_to_update


# Удаление примера Context в рамках редактирования слова/фразы WordPhrase - ШАГ 1, запрос подтверждения
@context_examples_router.callback_query(WordPhraseFSM.context, F.data.startswith('delete_context_'))
async def delete_context_example_ask_confirm(callback: types.CallbackQuery, session: AsyncSession, bot: Bot,) -> None:
    """
    Удаление примера Context в рамках редактирования слова/фразы WordPhrase - ШАГ 1, запрос подтверждения.

    :param callback: Callback-запрос формата "delete_context_{context_id}"
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Получаем id примера из callback и его объект из БД
    context_id = int(callback.data.replace('delete_context_', ''))
    context_obj = await DataBase.get_context_by_id(session, context_id)

    # Отправляем пользователю информационное сообщение с запросом подтверждения действия или отмены
    msg_text = f'⚠️ Вы действительно хотите удалить пример <b>"{context_obj.example}"</b>?'
    btns = {'Удалить 🗑': f'confirm_delete_context_{context_id}', 'Отмена ❌': 'cancel_update_context'}
    kbds = get_inline_btns(btns=btns)
    WordPhraseFSM.updating_info_message_with_cancel = await callback.message.answer(msg_text, reply_markup=kbds)
    bot.auxiliary_msgs['user_msgs'][callback.message.chat.id].append(WordPhraseFSM.updating_info_message_with_cancel)

    # Сохраняем объект удаляемого сообщения
    WordPhraseFSM.editing_message = callback.message


# Удаление примера Context в рамках редактирования слова/фразы WordPhrase - ШАГ 2, удаление из БД
@context_examples_router.callback_query(WordPhraseFSM.context, F.data.startswith('confirm_delete_context_'))
async def delete_context_example_get_confirm(callback: types.CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """
    Удаление примера Context в рамках редактирования слова/фразы WordPhrase.

    :param callback: Callback-запрос формата "delete_context_{context_id}"
    :param session: Пользовательская сессия
    :param bot: Объект бота
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

    # Оповещаем пользователя при успехе, удаляем сообщения
    if is_del:
        await callback.answer('✅ Пример удалён', show_alert=True)

        # Удаляем информационное сообщение
        await bot.delete_message(
            chat_id=callback.message.chat.id, message_id=WordPhraseFSM.updating_info_message_with_cancel.message_id
        )
        WordPhraseFSM.updating_info_message_with_cancel = None

        # Удаляем сообщение с уже удалённым примером
        await bot.delete_message(
            chat_id=callback.message.chat.id, message_id=WordPhraseFSM.editing_message.message_id
        )
        WordPhraseFSM.editing_message = None

    # INFO: на выходе остаётся WordPhraseFSM.context, IsKeyInStateFilter('word_to_update') на случай, если
    #       нужно будет дальше работать с примерами Context


# РЕДАКТИРОВАНИЕ ПРИМЕРОВ

# Редактирование примера Context, запрос ввода нового текста примера, проброс ключа editing_context_obj в FSM
@context_examples_router.callback_query(WordPhraseFSM.context, F.data.startswith('update_context_'))
async def update_context_example_ask_new_text(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Редактирование примера Context, запрос ввода нового текста примера.

    Функция добавляет в FSMState дополнительный ключ 'editing_context_obj' с редактируемым объектом Context.

    :param callback: Callback-запрос формата "update_context_{Context.id}"
    :param state: Контекст состояния с объектом WordPhrase в ключе 'word_to_update'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Получаем id примера из callback и объект примера из БД
    context_id = int(callback.data.replace('update_context_', ''))
    context_obj: Type[Context] = await DataBase.get_context_by_id(session, context_id)

    # Помещаем объект примера в FSM под ключом 'editing_context_obj'
    await state.update_data(editing_context_obj=context_obj)

    # Сохраняем редактируемое сообщение с примером для дальнейшего отображения изменений после редактирования
    WordPhraseFSM.editing_message = callback.message

    # Сохраняем callback
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Отправляем сообщение с запросом ввода нового текста примера и кнопкой отмены. Сохраняем во вспомогательные
    current_data = getattr(context_obj, 'example')                                          # Текущий текст примера
    btns = {
        'Отмена ❌': 'cancel_update_context',
        'Текст сейчас 📝': f'switch_inline_query_current_chat_{current_data}'
    }
    msg_text = (f'<b>Редактирование примера</b>:\n "{context_obj.example}"\n\n'
                f'Введите новый текст примера или нажмите <i>"Текст сейчас 📝"</i> для подгрузки в строку ввода '
                f'текущих данных для удобной корректировки.')
    WordPhraseFSM.updating_info_message_with_cancel = await callback.message.answer(
        text=msg_text, reply_markup=get_inline_btns(btns=btns, sizes=(2, ))
    )

    # INFO: на выходе остаётся state == WordPhraseFSM.context, ключи в state: editing_context_obj, word_to_update


# Завершение редактирования примера Context, сохранение нового текста примера
@context_examples_router.message(WordPhraseFSM.context, IsKeyInStateFilter('editing_context_obj'))
async def update_context_example_get_new_text(
        message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Завершение редактирования примера Context, сохранение нового текста примера.

    :param message: Текст сообщения с новым текстом примера
    :param state: Контекст состояния с объектом Context в ключе 'editing_context_obj'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Получаем редактируемый объект Context из FSM по ключу 'editing_context_obj'
    data = await state.get_data()
    context_obj = data['editing_context_obj']

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

        # Получаем обновленный объект Context
        updated_context_obj: Type[Context] = await DataBase.get_context_by_id(session, context_obj.id)

        # Обновляем данные в сообщении с примером в чате
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=WordPhraseFSM.editing_message.message_id,
            text=context_example_msg_template.format(
                example=updated_context_obj.example, created=updated_context_obj.created,
                updated=updated_context_obj.updated
            ),
            reply_markup=get_inline_btns(
                btns={
                    'Изменить 🖌': f'update_context_{updated_context_obj.id}',
                    'Удалить 🗑': f'delete_context_{updated_context_obj.id}'
                })
        )

    # Удаляем сообщение с запросом ввода нового текста примера и пользовательское сообщение с текстом
    await bot.delete_message(
        chat_id=WordPhraseFSM.updating_info_message_with_cancel.chat.id,
        message_id=WordPhraseFSM.updating_info_message_with_cancel.message_id
    )
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Удаляем ключ editing_context_obj с объектом Context из FSM
    await state.update_data(editing_context_obj=None)
