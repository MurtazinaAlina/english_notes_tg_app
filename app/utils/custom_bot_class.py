"""
Кастомизация телеграм бота, добавление дополнительных атрибутов.
"""
from aiogram import Bot as AiogramBot


class Bot(AiogramBot):
    """
    Кастомизация телеграм бота, добавление дополнительных атрибутов.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Добавляем боту дополнительные атрибуты:

        # Для отметки авторизации в системе. Структура словаря: {'chat_id': 'User.id', 'chat_id2': 'User.id', ...}
        self.auth_user_id: dict = {}

        # Для хранения временных сообщений. Словарь с различными ключами по типу сообщений.
        # Внутри словаря вложенные словари с ключами по id чата (подробнее ниже)
        self.auxiliary_msgs = dict()

        # Сохранение сообщения с баннером, запись редактируемых callback-messages
        # Структура словаря: {'chat_id': None | <msg_obj>, 'chat_id2': None | <msg_obj>, ...}
        self.auxiliary_msgs['cbq_msg'] = {}

        # Сохранение callback-запросов и отправка через них системных сообщений
        # Структура словаря: {'chat_id': None | <cbq_obj>, 'chat_id2': None | <cbq_obj>, ...}
        self.auxiliary_msgs['cbq'] = {}

        # Сохранение сообщений со статистикой
        # Структура словаря: {'chat_id': None | <msg_obj>, 'chat_id2': None | <msg_obj>, ...}
        self.auxiliary_msgs['statistic_msg'] = {}  # Для хранения сообщения со статистикой

        # Сохранение списка пользовательских сообщений к удалению (ввод, аудио, страницы и т.д.)
        # Структура словаря: {'chat_id': [< msg_obj >, < msg_obj2 >, ...], 'chat_id2': [< msg_obj >, ...], ...}
        self.auxiliary_msgs['user_msgs'] = {}

        # Сохранение сообщений с примерами контекста для удаления комплектом
        # Структура словаря: {'chat_id': [< msg_obj >, < msg_obj2 >, ...], 'chat_id2': [< msg_obj >, ...], ...}
        self.auxiliary_msgs['example_msgs'] = {}

        # Сохранение сообщений редактирования, где ключ - имя шага State(), значение - сообщение с новыми данными
        # Для привязки сообщений к шагу FSM-класса и обработке действия "Шаг назад"
        # Структура словаря: {'chat_id': {'step_name': <msg_obj>, ...}, 'chat_id2': {'step_name': <msg_obj>, ...}, ...}
        self.auxiliary_msgs['add_or_edit_word'] = {}

        # Для хранения клавиатур:
        # Структура словаря: {'chat_id': < markup_obj >, 'chat_id2': < markup_obj >, ...}
        self.reply_markup_save = {}                                         # Любая клавиатура
        self.markup_user_topics = {}                                        # Для хранения клавиатуры с темами

        # Для хранения ключевых слов для поиска
        # Структура словаря: {'chat_id': 'keyword', 'chat_id2': 'keyword', ...}
        self.word_search_keywords = {}                                      # Поиск WordPhrase
        self.topic_search_keywords = {}                                     # Поиск Topic

        # Для хранения истории навигации слов в тестированиях.
        # Словарь с ключом по id чата и вложенным словарем с ключом по типу теста, с внутренним словарём
        #  истории слов и текущим индексом слова
        # Структура словаря:
        #     {'chat_id': {
        #                   'en_ru_audio': {'history': {1: <word_obj>, 2: <word_obj>, ...}, 'navi_index': 3},
        #                   'en_ru_word': {'history': {}, 'navi_index': 1}
        #                 }
        #     }
        self.tests_word_navi = {}
