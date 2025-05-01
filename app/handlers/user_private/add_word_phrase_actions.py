"""
Обработка добавления нового слова/фразы WordPhrase в разделе "Словарь".

INFO:
1. В начале добавления нового слова/фразы в FSM добавляется ключ 'add_new_word_key', который используется для
   разделения логики контроллеров. Он удаляется при завершении добавления записи или выходе из раздела.
2. Про поиск темы при выборе темы. Отправка сообщения с запросом ввода ключа и обработка отмены сообщения -
   в vocabulary.topic_actions.py, это универсальные обработчики. Там же и обработка введённого текста для поиска темы:
   find_topic_by_matches_ask_keywords    - запрос ключа для поиска темы
   cancel_find_topic                     - отмена ввода ключа для поиска темы
   find_topic_by_matches_get_keywords    - обработка введённого ключа и переброс на запрос темы с фильтром
3. Для фильтра по темам используется FSM, куда добавляется ключ 'search_keywords' со значением для фильтра. При
   отмене фильтрации его значение удаляется из ключа.
4. Для добавления дополнительных примеров при создании слова используется FSM, куда добавляется ключ
   'word_to_add_context', при его наличии ветвится логика в обработчике add_word_get_context()
"""
import re
from typing import Type

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DataBase
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.keyboards.inlines import add_new_or_edit_word_main_btns, get_kbds_with_navi_header_btns
from app.banners import banners_details
from app.utils.custom_bot_class import Bot
from app.common.fsm_classes import WordPhraseFSM
from app.common.tools import re_send_msg_with_step, clear_auxiliary_msgs_in_chat, clear_all_data, \
    get_word_phrase_caption_formatting, try_alert_msg, check_if_user_has_topics, validate_context_example
from app.common.msg_templates import oops_with_error_msg_template, action_cancelled_msg_template, \
    context_validation_not_passed_msg_template, word_validation_not_passed_msg_template
from app.database.models import WordPhrase
from app.handlers.user_private.menu_processing import add_new_word
from app.settings import PLUG_TEMPLATE, PATTERN_WORD

# Создаём роутер для приватного чата бота с пользователем
word_phrase_router = Router()

# Настраиваем фильтр строго на приватный чат
word_phrase_router.message.filter(ChatTypeFilter(['private']))


# ОТМЕНА и ВОЗВРАТ К ПРЕДЫДУЩЕМУ ШАГУ

# Сброс добавления нового слова, возврат к первому шагу с выбором темы
@word_phrase_router.callback_query(StateFilter('*'), F.data == 'add_word_cancel')
async def cancel_add_word(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Сброс добавления нового слова, возврат к первому шагу с выбором темы.

    :param callback: Callback-запрос формата "add_word_cancel"
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """
    await callback.answer(action_cancelled_msg_template, show_alert=True)

    # Чистим вспомогательные сообщения в чате и удаляем значение ключа 'word_to_add_context'
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)
    await state.update_data(word_to_add_context=None)

    # Редактируем баннер и клавиатуру, чтобы отобразить первый шаг с выбором темы
    media, kbds = await add_new_word(bot, session, state, callback)
    await callback.message.edit_media(media=media, reply_markup=kbds)

    # Устанавливаем состояние ввода темы
    await state.set_state(WordPhraseFSM.topic)


# Возврат к предыдущему шагу
@word_phrase_router.callback_query(
    StateFilter('*'), F.data == 'add_or_edit_word_step_back', IsKeyInStateFilter('add_new_word_key'))
async def add_word_step_back(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Возврат к предыдущему шагу добавления нового слова/фразы.

    :param callback: Callback-запрос формата "add_or_edit_word_step_back"
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Определяем текущий шаг состояния
    current_state = await state.get_state()

    # Проверяем каждое из состояний класса на соответствие текущему из FSM, при совпадении устанавливаем предыдущее
    # (которое записали ранее, на предыдущей итерации)
    previous = None
    for step in WordPhraseFSM.__all_states__:
        if step.state == current_state:
            await state.set_state(previous)
            await callback.answer('⚠️ Вы вернулись к предыдущему шагу!', show_alert=True)

            # Редактируем баннер и клавиатуру под предыдущий шаг
            if step.state == 'WordPhraseFSM:word':                                                  # ВЫБОР ТЕМЫ
                media, kbds = await add_new_word(bot, session, state, callback)
                await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_media(media=media, reply_markup=kbds)
            else:                                                                                   # Любой другой шаг
                await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
                    caption=WordPhraseFSM.add_word_caption[previous.state],
                    reply_markup=bot.reply_markup_save[callback.message.chat.id]
                )

            # Удаляем сообщение со значением из отменённого шага
            try:
                await bot.delete_message(
                    chat_id=callback.from_user.id,
                    message_id=bot.auxiliary_msgs['add_or_edit_word'][
                        callback.message.chat.id][previous.state].message_id
                )
            except (Exception, ):
                pass
            return

        # Сохраняем шаг как предыдущий при несовпадении состояния
        previous = step


# ДОБАВЛЕНИЕ НОВОГО СЛОВА WordPhrase

# Добавление новой записи - ШАГ 1, запрос выбора темы
@word_phrase_router.callback_query(StateFilter(None), F.data.contains('add_new_word'))
async def add_word_ask_topic(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 1, запрос темы.

    На этом шаге в FSM добавляется ключ 'add_new_word_key'=True для фильтрации событий в контроллерах.

    :param callback: Callback-запрос формата "add_new_word" или "find_topic_by_matches", "cancel_find_topic_by_matches"
                    (после фильтра/сброса фильтра по теме идёт принудительный вызов функции)
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Проверяем, есть ли у пользователя темы
    if not await check_if_user_has_topics(bot, callback.message.chat.id, session):
        return

    # Редактируем баннер и клавиатуру
    media, reply_markup = await add_new_word(bot, session, state, callback)
    try:
        await callback.message.edit_media(media=media, reply_markup=reply_markup)
    except (Exception, ):
        pass

    # Добавляем в FSM ключ 'add_new_word_key' для фильтрации событий в контроллерах
    await state.update_data(add_new_word_key=True)

    # Сохраняем сообщение с баннером и клавиатуру с темами для обработки шага назад
    if callback.data == 'add_new_word':
        bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id] = callback.message
    bot.markup_user_topics[callback.message.chat.id] = reply_markup

    # Устанавливаем состояние ввода темы
    await state.set_state(WordPhraseFSM.topic)


# Добавление новой записи - ШАГ 1 VAR: Отмена фильтра по темам
@word_phrase_router.callback_query(F.data == 'cancel_find_topic_by_matches', IsKeyInStateFilter('add_new_word_key'))
async def cancel_find_topic_by_matches(
        callback: types.CallbackQuery, session: AsyncSession, state: FSMContext, bot: Bot) -> None:
    """
    Отмена фильтра по темам при добавлении новой записи слова/фразы WordPhrase.

    :param callback: Callback-запрос формата "cancel_find_topic_by_matches"
    :param session: Пользовательская сессия
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param bot: Объект бота
    :return: None
    """
    await callback.answer('⚠️ Фильтр по теме отменен!', show_alert=True)

    # Удаляем из FSM значение ключа search_keywords
    await state.update_data(search_keywords=None)

    # Вызываем обработчик запроса темы
    await add_word_ask_topic(callback, state, session, bot)


# Добавление новой записи - ШАГ 2: тема получена, запрос ввода слова/фразы
@word_phrase_router.callback_query(WordPhraseFSM.topic, IsKeyInStateFilter('add_new_word_key'))
async def add_word_get_topic_ask_word(
        callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 2, запрос слова/фразы.

    :param callback: Callback-запрос формата "add_word_topic_{Topic.id}"
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Получаем id выбранной темы из callback и записываем её в контекст состояния
    topic_id = int(callback.data.replace('add_word_topic_', ''))
    await state.update_data(topic=topic_id)

    # Отправляем системное сообщение в чат с названием выбранной темы и сохраняем его
    edit_word_state = await state.get_state()                                  # Получаем состояние для названия ключа
    topic = await DataBase.get_topic_by_id(session, topic_id=topic_id)
    msg = await callback.message.answer(text=f'<b>Выбрана тема:</b> {topic.name}')
    bot.auxiliary_msgs['add_or_edit_word'][callback.message.chat.id][edit_word_state] = msg

    # Редактируем баннер и клавиатуру
    kbds = add_new_or_edit_word_main_btns(btns={}, cancel_page_address='add_word_cancel')
    await bot.auxiliary_msgs['cbq_msg'][callback.message.chat.id].edit_caption(
        caption=banners_details.add_new_word_step_2, reply_markup=kbds
    )

    # Сохраняем клавиатуру и callback
    bot.reply_markup_save[callback.message.chat.id] = kbds
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Переходим в состояние ввода слова
    await state.set_state(WordPhraseFSM.word)


# Добавление новой записи - ШАГ 3: слово/фраза получены, запрос ввода транскрипции
@word_phrase_router.message(WordPhraseFSM.word, F.text, IsKeyInStateFilter('add_new_word_key'))
async def add_word_get_word_ask_transcription(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 3: слово/фраза получены, запрос транскрипции.

    :param message: Текстовое сообщение с введённым словом/фразой
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param bot: Объект бота
    :return: None
    """

    # Делаем валидацию введённого значения
    if not re.match(PATTERN_WORD, message.text):
        msg_text = word_validation_not_passed_msg_template
        await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)
        await bot.delete_message(message.chat.id, message.message_id)
        return

    # Записываем полученное слово/фразу в контекст
    await state.update_data(word=message.text)

    # Переотправка в чат введённого сообщения с новыми данными (с пометкой шага)
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Слово/фраза:</b>')

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.add_new_word_step_3, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Переходим в состояние ввода транскрипции
    await state.set_state(WordPhraseFSM.transcription)


# Добавление новой записи - ШАГ 4: транскрипция получена, запрос ввода перевода
@word_phrase_router.message(WordPhraseFSM.transcription, F.text, IsKeyInStateFilter('add_new_word_key'))
async def add_word_get_transcription_ask_translation(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 4: транскрипция получена, запрос перевода.

    :param message: Текстовое сообщение с введённой транскрипцией
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param bot: Объект бота
    :return: None
    """

    # Записываем полученную транскрипцию в контекст
    transcription = message.text if len(message.text) > 1 else PLUG_TEMPLATE
    await state.update_data(transcription=transcription)

    # Переотправка в чат введённого сообщения с новыми данными с пометкой шага
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Транскрипция:</b>')

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.add_new_word_step_4, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Переходим в состояние ввода перевода
    await state.set_state(WordPhraseFSM.translate)


# Добавление новой записи - ШАГ 5: перевод получен, запрос ввода примера Context
@word_phrase_router.message(WordPhraseFSM.translate, F.text, IsKeyInStateFilter('add_new_word_key'))
async def add_word_get_translate_ask_context(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 5: перевод получен, запрос примера Context.

    :param message: Текстовое сообщение с введённым переводом
    :param state: Контекст состояния с ключом 'add_new_word_key'
    :param bot: Объект бота
    :return: None
    """

    # Записываем полученный перевод в контекст
    translate = message.text if len(message.text) > 1 else PLUG_TEMPLATE
    await state.update_data(translate=translate)

    # Переотправляем в чат введённое пользователем сообщение с переводом с пометкой шага
    await re_send_msg_with_step(message=message, bot=bot, state=state, msg_text='<b>Перевод:</b>')

    # Редактируем баннер и клавиатуру
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.add_new_word_step_5, reply_markup=bot.reply_markup_save[message.chat.id]
    )

    # Переходим в состояние ввода контекста
    await state.set_state(WordPhraseFSM.context)


# Добавление новой записи - ШАГ 6: контекст получен, запись в базу
# Здесь же добавление последующих примеров Context в только что созданную запись WordPhrase
@word_phrase_router.message(WordPhraseFSM.context, F.text, IsKeyInStateFilter('add_new_word_key'))
async def add_word_get_context(message: types.Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    """
    Добавление новой записи слова/фразы WordPhrase - шаг 6: контекст получен, запись в базу.

    Здесь же добавление последующих примеров Context в только что созданную запись WordPhrase.

    :param message: Текстовое сообщение с введённым примером
    :param state: Контекст состояния с ключом 'add_new_word_key' и (опционально) 'word_to_add_context'
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Сохраняем сообщение в список вспомогательных сообщений
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(message)

    # Записываем пример в контекст и забираем все данные из контекста
    await state.update_data(context=message.text)
    data: dict = await state.get_data()
    word_in_context = data.get('word_to_add_context')         # Проверка на добавление примеров в уже созданную запись
    word, word_id = None, None

    # Если это создание новой записи WordPhrase в БД:
    if not word_in_context:

        # Создаем новую запись WordPhrase
        try:
            word = await DataBase.create_word_phrase(session, data)
        except Exception as e:
            msg_text = oops_with_error_msg_template.format(error=str(e))
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

        # При успехе отправляем пользователю уведомление
        if word:
            word_id = word.id
            await try_alert_msg(bot, message.chat.id, '✅ Запись добавлена!', if_error_send_msg=True)

    # Если это добавление примеров в уже созданную запись WordPhrase:
    else:
        word_id = word_in_context.id

        # Делаем валидацию ввода, при некорректном значении уведомляем и выходим из функции
        if not validate_context_example(message.text):
            await try_alert_msg(
                bot, message.chat.id, context_validation_not_passed_msg_template, if_error_send_msg=True
            )
            await bot.delete_message(message.chat.id, message.message_id)
            return

        # Добавляем пример Context
        try:
            example = await DataBase.create_context_example(session, word_id=word_id, data=data)
        except Exception as e:
            example = False
            msg_text = oops_with_error_msg_template.format(error=str(e))
            await try_alert_msg(bot, message.chat.id, msg_text, if_error_send_msg=True)

        # Если пример был успешно добавлен, обновляем информацию о записи WordPhrase
        if example:
            word = await DataBase.get_word_phrase_by_id(session, word_id)
            await try_alert_msg(bot, message.chat.id, '✅ Пример добавлен!', if_error_send_msg=True)

    # Редактируем баннер и клавиатуру
    btns = {
        'Добавить ещё пример ✏️': f'add_more_examples_to_word_{word_id}',
        'Добавить следующую запись ➕': 'add_new_word'
    }
    kbds = get_kbds_with_navi_header_btns(level=1, btns=btns)
    caption = await get_word_phrase_caption_formatting(word_phrase_obj=word)
    await bot.auxiliary_msgs['cbq_msg'][message.chat.id].edit_caption(
        caption=banners_details.add_new_word_step_6.format(**caption), reply_markup=kbds
    )

    # Очищаем чат и контекст
    await clear_all_data(bot, message.chat.id, state)


# ДОБАВИТЬ ПРИМЕРЫ созданной записи WordPhrase

# ДОБАВИТЬ ПРИМЕРЫ только что созданной записи WordPhrase
# * Разделение с контроллером добавления примеров при редактировании: FSM-ключ "word_to_update" + разный StateFilter
@word_phrase_router.callback_query(StateFilter(None), F.data.startswith('add_more_examples_to_word_'))
async def add_new_context_ask_text(callback: types.CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot) \
        -> None:
    """
    Добавить примеры только что созданной записи WordPhrase.

    * Разделение с контроллером добавления примеров при редактировании: FSM-ключ "word_to_update" + разный StateFilter

    :param callback: Callback-запрос формата "add_more_examples_to_word_{WordPhrase.id}"
    :param state: Контекст состояния
    :param session: Пользовательская сессия
    :param bot: Объект бота
    :return: None
    """

    # Получаем объект слова из БД по id из callback
    word_id = int(callback.data.replace('add_more_examples_to_word_', ''))
    word_phrase_obj: Type[WordPhrase] = await DataBase.get_word_phrase_by_id(session, word_id)

    # Записываем в контекст ключ word_to_add_context с полученным объектом и ключ add_new_word_key
    await state.update_data(word_to_add_context=word_phrase_obj, add_new_word_key=True)

    # Редактируем баннер и клавиатуру
    kbds = get_kbds_with_navi_header_btns(level=1, btns={'Отмена ❌': 'add_word_cancel'})
    await callback.message.edit_caption(caption=banners_details.add_new_word_step_5, reply_markup=kbds)

    # Сохраняем callback-запрос
    bot.auxiliary_msgs['cbq'][callback.message.chat.id] = callback

    # Переходим в состояние ввода контекста
    await state.set_state(WordPhraseFSM.context)
