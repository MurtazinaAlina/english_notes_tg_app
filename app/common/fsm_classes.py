"""
Классы состояний FSM
"""
from aiogram.fsm.state import State, StatesGroup

from banners import banners_details


# Класс состояний FSM для взаимодействия с AI-ассистентом
class GigaAiFSM(StatesGroup):
    """ Класс состояний FSM для взаимодействия с AI-ассистентом. """
    text_input = State()


# Класс состояний FSM для озвучивания введённого текста
class SpeakingFSM(StatesGroup):
    """ Класс состояний FSM для озвучивания введённого текста. """
    text_input = State()


class ImportXlsFSM(StatesGroup):
    """ Класс состояний FSM для импорта данных. """
    xls_file = State()


# Класс для машины состояний при работе с настройками профиля пользователя
class UserSettingsFSM(StatesGroup):
    """ Класс для машины состояний при работе с настройками профиля пользователя """
    speech_rate = State()


# Класс для машины состояний при auth пользователя
class AuthFSM(StatesGroup):
    """ Класс для машины состояний при auth пользователя. """

    # Все состояния
    email = State()
    password = State()
    confirm_password = State()

    reset_pass_token = State()
    new_password = State()
    confirm_new_password = State()

    # Переменная для сохранения пароля при первом вводе - для сверки с подтверждением
    psw_first_input = None


# Класс для машины состояний при добавлении/редактировании слова/фразы WordPhrase
class WordPhraseFSM(StatesGroup):
    """ Класс для машины состояний при добавлении/редактировании слова/фразы WordPhrase. """

    # Все состояния ввода значения при добавлении нового слова/фразы или редактировании
    topic = State()
    word = State()
    transcription = State()
    translate = State()
    context = State()

    # Состояние для поиска слова/фразы по ключевому слову
    search_keywords = State()

    # Переменная для записи редактируемого сообщения с Context-примером, для онлайн-отображения изменения примера
    editing_message = None

    # Переменная для записи информационного сообщения с запросом текста и отменой при редактировании примера Context
    updating_info_message_with_cancel = None

    # Переменные для отображения в описании баннера при возврате на шаг назад:

    # При создании новой записи
    add_word_caption = {
        'WordPhraseFSM:topic': banners_details.add_new_word_step_1,
        'WordPhraseFSM:word': banners_details.add_new_word_step_2,
        'WordPhraseFSM:transcription': banners_details.add_new_word_step_3,
        'WordPhraseFSM:translate': banners_details.add_new_word_step_4,
        'WordPhraseFSM:context': banners_details.add_new_word_step_5
    }

    # При редактировании существующей записи
    edit_word_caption = {
        'WordPhraseFSM:topic': banners_details.update_word_step_1,
        'WordPhraseFSM:word': banners_details.update_word_step_2,
        'WordPhraseFSM:transcription': banners_details.update_word_step_3,
        'WordPhraseFSM:translate': banners_details.update_word_step_4,
        'WordPhraseFSM:context': banners_details.update_word_step_5
    }


# Класс для работы с таблицей тем Topic в контексте машины состояний
class TopicFSM(StatesGroup):
    """ Класс для работы с таблицей тем Topic в контексте машины состояний. """

    # Состояния
    name = State()
    search_keywords = State()                                           # Ввод ключа для поиска по темам

    # Переменная для записи редактируемого сообщения с темой, для онлайн-отображения изменения названия
    editing_message = None

    # Переменная для записи информационного сообщения с запросом темы и кнопкой отмены при редактировании
    updating_info_message_with_cancel = None


# Класс для работы с таблицей заметок Notes в контексте машины состояний
class NotesFSM(StatesGroup):
    """ Класс для работы с таблицей заметок Notes в контексте машины состояний. """

    # Состояния
    title = State()
    text = State()
    example = State()

    # Состояние для поиска заметки по ключевому слову
    search_keywords = State()
