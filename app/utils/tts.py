"""
Модуль для генерации и отправки в чат аудиофайлов mp3 с использованием Edge TTS (Microsoft Voices).
"""
import asyncio
import os
import random
import re

import edge_tts
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, CallbackQuery
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.db import DataBase
from app.utils.custom_bot_class import Bot
from app.utils.tts_voices import all_voices_en_US_ShortName_list
from app.settings import PATTERN_AUDIO_CONVERT


# Генерация и сохранение аудиофайла mp3 на основе переданного текста
async def text_to_speech(
        text: str, rate: str, voice: str, is_with_title: bool = True, filename: str = 'output.mp3') -> str:
    """
    Генерация и сохранение аудиофайла mp3 на основе переданного текста с использованием Edge TTS (Microsoft Voices).

    :param text: Текст для озвучки
    :param rate: Скорость речи ('-10%' — медленнее, '+0%' — стандарт, '+10%' — быстрее)
    :param voice: Голос из списка edge_tts.list_voices() (например, 'en-US-JennyNeural')
    :param filename: Имя файла для сохранения.
    :param is_with_title: Добавлять заголовок к аудио файлу (только на desktop)
    :return: Путь к сохранённому аудио файлу
    """

    # Генерируем аудиофайл и сохраняем его по указанному пути
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    try:
        await communicate.save(filename)
    except Exception as e:
        print(e)

    # Добавляем метаданные (ID3 теги)
    try:
        audio = MP3(filename, ID3=ID3)

        # Если файла ID3 нет, создаем новый
        if audio.tags is None:
            audio.tags = ID3()

        # Добавляем метаданные
        if is_with_title:
            audio.tags.add(TIT2(encoding=3, text=f"Произношение: {text}"))            # Заголовок (только на desktop)

        # Сохраняем аудио файл
        audio.save()

    except Exception as e:
        print(e)

    return filename


# Функция отправки голосового сообщения mp3 со сгенерированной речью
async def speak_text(
        text: str, bot: Bot, chat_id: int, is_with_title: bool, autodelete: bool = True,
        state: FSMContext = None, session: AsyncSession = None, test_voice: str = None) -> None:
    """
    Отправка в чат голосового сообщения mp3 со сгенерированной речью.

    :param text: Текст для озвучки
    :param bot: Объект бота
    :param chat_id: ID чата для отправки.
    :param is_with_title: Добавлять заголовок к аудио файлу.
    :param autodelete: Удалять сообщение после отправки через 15 секунд. По умолчанию True
    :param state: Контекст состояния FSM
    :param session: Пользовательская сессия
    :param test_voice: Название голоса при прослушивании в настройках профиля. Для озвучки им, а не сохраненным в БД
    :return: None
    """

    # Делаем валидацию текста. При некорректном тексте выходим из функции
    pattern = PATTERN_AUDIO_CONVERT
    if not re.match(pattern, text):
        print('⚠️ Слишком короткий текст или нет английских букв!')
        return

    # Забираем настройки пользователя из БД
    settings_data = await DataBase.get_user_settings(session, bot.auth_user_id[chat_id])

    # Устанавливаем выбор голоса (Если это тест нового голоса, передаём его в параметр, иначе - из настроек)
    if test_voice:
        voice = test_voice
    else:
        voice = str(settings_data.voice)

        # Если голос "случайный", то выбираем случайный из списка
        if voice == 'random':
            voice = random.choice(all_voices_en_US_ShortName_list)

    # Генерируем аудиофайл
    audio_file_path: str = await text_to_speech(
        text=text, is_with_title=is_with_title, rate=str(settings_data.speech_rate), voice=voice
    )

    # Оборачиваем файл в FSInputFile (необходимо для отправки в Telegram)
    audio_file = FSInputFile(audio_file_path)

    # Отправляем аудиофайл как голосовое сообщение
    try:
        msg = await bot.send_voice(chat_id, audio_file)
    except Exception as e:
        msg = None
        print(e)
    bot.auxiliary_msgs['user_msgs'][chat_id].append(msg)

    # Если аудио отправлено, то добавляем сообщение с ним в state в список аудио примеров
    if msg:
        state_data = await state.get_data()
        audio_examples = state_data.get('audio_examples')
        if audio_examples:
            list(audio_examples.values())[0].append(msg)
            await state.update_data(audio_examples=audio_examples)

    # Удаляем файл из системы после отправки
    os.remove(audio_file_path)

    # Таймер на удаление сообщения через 15 секунд при наличии флага автоматического удаления:
    if autodelete:
        await asyncio.sleep(15)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception as e:
            print(e)


# Функция удаления аудио неактуальных примеров из чата
async def clear_audio_examples_from_chat(
        state: FSMContext, bot: Bot, callback: CallbackQuery, state_data: dict, entity_id: int) -> None:
    """
    Вспомогательная функция для удаления аудио неактуальных примеров из чата. Функция проверяет по списку в state, к
    какому word/note_id относятся примеры, и, если не к текущему, удаляет их и очищает список в state. При первом
    запросе создаёт саму структуру хранения формата audio_examples = {'entity_id': []}.

    :param state: Контекст состояния FSM
    :param bot: Объект бота
    :param callback: CallbackQuery для отправки системных сообщений при ошибке
    :param state_data: словарь с данными из state
    :param entity_id: ID слова/фразы или заметки
    :return: None
    """

    # Проверяем наличие в state ключа audio_examples
    audio_examples_in_chat = state_data.get('audio_examples')                   # {'entity_id': [msg1, msg2, msg3]}

    # Если в чате есть аудио примеры, удаляем их и очищаем audio_examples в state
    if audio_examples_in_chat:
        if list(audio_examples_in_chat.keys())[0] != entity_id:
            for audio_example in list(audio_examples_in_chat.values())[0]:
                try:
                    await bot.delete_message(callback.message.chat.id, audio_example.message_id)
                except Exception as e:
                    await callback.answer(str(e), show_alert=True)
            audio_examples = {entity_id: []}
            await state.update_data(audio_examples=audio_examples)

    # Если в чате нет аудио примеров, создаём структуру и передаём её в state
    if not audio_examples_in_chat:
        audio_examples = {entity_id: []}
        await state.update_data(audio_examples=audio_examples)
