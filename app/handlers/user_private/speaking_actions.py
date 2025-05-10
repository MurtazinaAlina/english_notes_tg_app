"""
Раздел в меню "Произношение".

INFO:

ПРАКТИКА ПРОИЗНОШЕНИЯ
1. Аудио пользователя с попытками произношения изначально сохраняются во временной папке (путь к ней определен в
   настройках). При запросе нового примера все файлы из временной папки удаляются.
2. При определении примера для практики произношения, объект примера сохраняется в state под ключом
   'random_example_obj'.
3. Для хранения данных о временных файлах используется ключ 'saving_structure' в state (Т.к. имя файла не передать в
   callback из-за ограничения по символам). В этом ключе записывается словарь, где ключ - номер попытки
   'attempt_number' (т.е. порядковый номер присланного аудио), а значение - название записанного аудиофайла с попыткой
   произношения.
4. Структура 'saving_structure' с 'attempt_number'=1 инициализируется и записывается в контекст в момент выбора примера.
   Далее, при отправке пользователем аудио с практикой, она перезаписывается с дополненными данными.
5. При сохранении аудио запись переносится из временного хранилища в постоянное.
   Путь к постоянному хранилищу определен в настройках, логика: app/data/audio/user_{user_id}/{date}/{filename}.ogg.
   В папке /audio/ расположены подпапки для каждого пользователя, внутри подпапки по датам, внутри которых аудио
   за каждую дату.
"""
import os
import shutil
import datetime

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.fsm_classes import SpeakingFSM
from app.common.tools import clear_auxiliary_msgs_in_chat, check_if_authorized
from app.common.msg_templates import oops_with_error_msg_template, oops_try_again_msg_template
from app.filters.custom_filters import ChatTypeFilter, IsKeyInStateFilter
from app.utils.custom_bot_class import Bot
from app.utils.tts import speak_text
from app.banners import banners_details
from app.keyboards.inlines import get_kbds_with_navi_header_btns, get_inline_btns
from app.database.db import DataBase
from app.settings import AUDIO_TEMP_PATH, AUDIO_FINAL_PATH


# Создаём роутер для приватного чата бота с пользователем
speaking_router = Router()

# Настраиваем фильтр, что строго приватный чат
speaking_router.message.filter(ChatTypeFilter(['private']))


# Очистка чата - универсальный обработчик
@speaking_router.callback_query(F.data == 'clear_chat')
async def clear_chat(callback: types.CallbackQuery, bot: Bot) -> None:
    """
    Очистка чата от аудио файлов.

    :param callback: CallbackQuery-запрос формата 'clear_chat'
    :param bot: Объект бота
    :return: None
    """
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)


# ТЕКСТ -> В АУДИО

# Выбор раздела "Преобразовать текст в аудио", ШАГ 1 - Запрос ввода текста
@speaking_router.callback_query(F.data.startswith('convert_text_to_audio'))
async def convert_text_to_audio(callback: types.CallbackQuery, state: FSMContext) -> None:
    """
    Выбор раздела "Преобразовать текст в аудио", ШАГ 1 - Запрос ввода текста.

    :param callback: CallbackQuery-запрос формата 'convert_text_to_audio'
    :param state: Контекст состояния FSM
    :return: None
    """

    # Формируем клавиатуру
    btns = {'Очистить чат 🗑': 'clear_chat'}
    kbds = get_kbds_with_navi_header_btns(btns=btns, level=2, menu_name='speaking', sizes=(2, 1))

    # Редактируем баннер и клавиатуру
    try:
        await callback.message.edit_caption(caption=banners_details.speaking_tts, reply_markup=kbds)
    except (Exception, ):
        pass

    # Устанавливаем состояние для ввода текста
    await state.set_state(SpeakingFSM.text_input)


# Выбор раздела "Преобразовать текст в аудио", ШАГ 2 - Преобразование введённого текста в аудио файл
@speaking_router.message(SpeakingFSM.text_input, F.text)
async def speaking_text_input(message: types.Message, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    """
    Выбор раздела "Преобразовать текст в аудио", ШАГ 2 - Преобразование введённого текста в аудио файл.

    :param message: Входящий текст
    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :param session: Пользовательская сессия
    :return: None
    """
    text = message.text
    await speak_text(text, bot, message.chat.id, is_with_title=True, autodelete=False, state=state, session=session)
    await bot.delete_message(message.chat.id, message.message_id)

    # INFO: на выходе остаётся SpeakingFSM.text_input, можно продолжать вводить фрагменты текста для получения аудио


# ПРАКТИКА ПРОИЗНОШЕНИЯ. ЗАПИСЬ ПРОИЗНОШЕНИЯ

# Вход в раздел "Практика произношения"
# Обработчик вызывается при входе в раздел и при вызове следующего примера
@speaking_router.callback_query(F.data == 'speaking_practice')
async def speaking_practice_main(callback: types.CallbackQuery, bot: Bot, state: FSMContext, session: AsyncSession) \
        -> None:
    """
    Вход в раздел "Практика произношения".
    Обработчик вызывается при входе в раздел и при вызове следующего примера.

    :param callback: CallbackQuery-запрос формата 'speaking_practice'
    :param bot: Объект бота
    :param state: Контекст состояния FSM
    :param session: Пользовательская сессия
    :return: None
    """

    # Проверяем, что пользователь аутентифицирован
    if not await check_if_authorized(callback, bot, callback.message.chat.id):
        return None

    # Получаем идентификатор пользователя
    user_id = bot.auth_user_id[callback.message.chat.id]

    # Проверяем, что у пользователя есть записи примеров Context
    if not await DataBase.check_if_user_has_examples(session, user_id):
        await callback.answer('⚠️ У вас нет записей примеров!', show_alert=True)

    # Очищаем чат
    await clear_auxiliary_msgs_in_chat(bot, callback.message.chat.id)

    # Формируем путь до временного хранилища аудио
    tmp_audio_dir = AUDIO_TEMP_PATH.format(user_id=user_id)

    # Создаем папку для временного хранилища аудио, если она не существует
    os.makedirs(tmp_audio_dir, exist_ok=True)

    # Удаляем все файлы в папке временного хранения аудио
    try:
        for filename in os.listdir(tmp_audio_dir):
            file_path = os.path.join(tmp_audio_dir, filename)
            if os.path.isfile(file_path):
                os.unlink(file_path)
    except (Exception, ) as e:
        print(f"Ошибка при удалении файлов: {e}")

    # Достаем случайный пример пользователя
    random_example_obj = await DataBase.get_random_context(session, user_id)

    # Сохраняем в state данные объекта, инициируем структуру временного хранения присланных аудио с произношением
    await state.update_data(random_example_obj=random_example_obj)
    await state.update_data(saving_structure={})                            # Словарь с данными временного хранения
    await state.update_data(attempt_number=1)                               # Номер попытки произношения

    # Формируем клавиатуру и описание баннера с примером
    btns = {
        '🎧 Прослушать': 'listen_example',
        'Новый пример ⏩': 'speaking_practice',
        'Очистить чат 🗑': 'clear_chat',
    }
    kbds = get_kbds_with_navi_header_btns(btns=btns, level=2, menu_name='speaking', sizes=(2, ))
    caption = banners_details.speaking_practice.format(example=random_example_obj.example)

    # Редактируем баннер и клавиатуру
    try:
        await callback.message.edit_caption(caption=caption, reply_markup=kbds)
    except (Exception, ) as e:
        print(e)


# Прослушать аудио с произношением примера
@speaking_router.callback_query(F.data == 'listen_example', IsKeyInStateFilter('random_example_obj'))
async def speaking_practice_listen_example(callback: types.CallbackQuery, bot: Bot, state: FSMContext,
                                           session: AsyncSession) -> None:
    """
    Прослушать аудио с произношением примера.

    :param callback: CallbackQuery-запрос формата 'listen_example'
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключом 'random_example_obj' с объектом примера Context;
    :param session: Пользовательская сессия
    :return: None
    """

    # Получаем объект примера из контекста
    state_data = await state.get_data()
    random_example_obj = state_data.get('random_example_obj')

    # Формируем аудиофайл с произношением примера
    text = random_example_obj.example
    chat_id = callback.message.chat.id
    await speak_text(text, bot, chat_id, is_with_title=True, autodelete=False, state=state, session=session)


# Обработка голосового сообщения с практикой произношения от пользователя
# Запись аудио с произношением примера во временное хранилище файлов
@speaking_router.message(F.voice, IsKeyInStateFilter('random_example_obj'))
async def speaking_practice_recording(message: types.Message, state: FSMContext, bot: Bot) -> None:
    """
    Обработка голосового сообщения с практикой произношения от пользователя.
    Запись аудио с произношением примера во временное хранилище файлов.

    :param message: Голосовое сообщение
    :param state: Контекст состояния FSM с ключами:
                  'random_example_obj' - объект примера Context;
                  'attempt_number' - номер записываемого аудио с попыткой произношения;
                  'saving_structure' - словарь, где ключ - номер попытки, а значение - название записанного аудиофайла
                  с попыткой произношения
    :param bot: Объект бота
    :return: None
    """

    # Получаем информацию о присланном голосовом сообщении
    file = await bot.get_file(message.voice.file_id)
    file_name = f"{message.voice.file_id}.ogg"

    # Записываем аудио во временное хранилище
    user_id = bot.auth_user_id[message.chat.id]
    tmp_path = os.path.join(AUDIO_TEMP_PATH.format(user_id=user_id), file_name)
    await bot.download_file(file.file_path, destination=tmp_path)

    # Достаем из state информацию о структуре временного хранилища
    state_data = await state.get_data()
    saving_structure = state_data.get('saving_structure')
    attempt_number = state_data.get('attempt_number')

    # Удаляем присланное аудио и переотправляем его в чат с подписью и кнопкой сохранения
    await bot.delete_message(message.chat.id, message.message_id)
    keyboard = get_inline_btns(btns={'💾 Сохранить': f"save_audio:attempt_{attempt_number}"})
    audio = FSInputFile(path=tmp_path)
    msg = await message.answer_audio(audio=audio, caption=f"Попытка {attempt_number}", reply_markup=keyboard)
    bot.auxiliary_msgs['user_msgs'][message.chat.id].append(msg)

    # Обновляем информацию в структуре временного хранилища и загружаем ее обратно в state
    saving_structure[attempt_number] = message.voice.file_id
    attempt_number += 1
    await state.update_data(saving_structure=saving_structure)
    await state.update_data(attempt_number=attempt_number)


# Обработка сохранения аудио. Переносит аудио из временного хранилища в постоянное
@speaking_router.callback_query(F.data.startswith('save_audio'), IsKeyInStateFilter('saving_structure'))
async def speaking_practice_save_audio(callback: types.CallbackQuery, bot: Bot, state: FSMContext,
                                       session: AsyncSession) -> None:
    """
    Обработка сохранения аудио. Переносит аудио из временного хранилища в постоянное.

    :param callback: CallbackQuery-запрос формата 'save_audio'
    :param bot: Объект бота
    :param state: Контекст состояния FSM с ключами:
                  'attempt_number' - номер записываемого аудио с попыткой произношения;
                  'saving_structure' - словарь, где ключ - номер попытки, а значение - название записанного аудиофайла
                  с попыткой произношения
    :param session:
    :return:
    """

    # Достаем из state информацию
    state_data = await state.get_data()
    saving_structure = state_data.get('saving_structure')

    # Получаем номер аудиозаписи с попыткой из callback
    attempt_number = int(callback.data.split('_')[-1])

    # Определяем название сохраняемого файла
    file_name = saving_structure[attempt_number]

    # Получаем идентификатор пользователя
    user_id = bot.auth_user_id[callback.message.chat.id]

    # Формируем финальную директорию сохранения за дату
    audio_root_dir = AUDIO_FINAL_PATH.format(user_id=user_id, date=datetime.date.today())

    # Создаем директорию хранилища, если она не существует
    os.makedirs(audio_root_dir, exist_ok=True)

    try:
        # Переносим файл из временного хранилища в сохраняемые файлы
        temp_path = os.path.join(AUDIO_TEMP_PATH.format(user_id=user_id), f'{file_name}.ogg')
        final_path = os.path.join(audio_root_dir, f'{file_name}.ogg')
        shutil.move(temp_path, final_path)

        # Записываем путь к сохранённому аудио в БД
        write_to_db = await DataBase.save_file_path_to_audio(session, final_path, user_id)
        if write_to_db:
            await callback.answer('✅ Аудио сохранено!', show_alert=True)
        else:
            await callback.answer(oops_try_again_msg_template, show_alert=True)

    except Exception as e:
        msg_text = oops_with_error_msg_template.format(error=str(e))
        await callback.answer(msg_text, show_alert=True)
