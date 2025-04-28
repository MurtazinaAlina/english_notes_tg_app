"""
Конструкторы inline-клавиатур.
"""
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, KeyboardBuilder

from app.database.models import WordPhrase
from app.settings import REVERSO_URL


# Кастомные классы CallbackData для удобной фильтрации событий

# Основной класс CallbackData, обрабатывается основным обработчиком callback_query, точкой входа
class MenuCallBack(CallbackData, prefix='menu'):
    """
    Класс CallbackData для направления в основной обработчик callback_query-событий.

    Присваивает префикс 'menu', на выходе конструирует callback_data в формате:
    "menu:<level>:<menu_name>:<menu_details>:<page>", например: "menu:1:vocabulary::1"
    """
    level: int                              # Уровень вложенности меню (для обработки кнопок "Назад" и "На главную")
    menu_name: str                          # Имя меню, основной параметр ветвления логики
    menu_details: str | None = None         # Дополнительный параметр для ветвления логики
    page: int = 1                           # Номер текущей страницы для пагинации


# Вспомогательный класс CallbackData, используется для пагинации при необходимости обойти основной обработчик
class VocabularyCallBack(CallbackData, prefix='vcb'):
    """
    Вспомогательный класс CallbackData, используется для пагинации при необходимости обойти основной обработчик событий.

    Придается префикс 'vcb', на выходе конструирует callback_data в формате:
    "vcb:<menu_details>:<page>", например: "vcb:edit_or_delete_topic:2"
    """
    menu_name: str | None = None
    page: int = 1


# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ для создания клавиатур

# Создать клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)
def create_keyboard_with_header(level, menu_name: str | None = None, custom_step_back: str | None = None) \
        -> InlineKeyboardBuilder:
    """
    Создать клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)

    :param level: Текущий уровень меню
    :param menu_name: Имя меню для формирования MenuCallBack-объекта в callback_data
    :param custom_step_back: Данные callback_data= для кнопки "Назад ⬅️" (если требуется не стандартный MenuCallBack)
    :return: Объект клавиатуры InlineKeyboardBuilder
    """
    keyboard = InlineKeyboardBuilder()                              # Создаем клавиатуру
    keyboard.add(InlineKeyboardButton(                              # Добавляем кнопку "На главную 🏠"
        text='На главную 🏠', callback_data=MenuCallBack(level=0, menu_name='start_page').pack()
    ))
    if level > 1:

        # Определяем значение callback_data= для кнопки "Назад ⬅️"
        callback_data = custom_step_back if custom_step_back else MenuCallBack(
            level=level - 1, menu_name=menu_name, menu_details='step_back').pack()

        # Добавляем кнопку "Назад ⬅️"
        keyboard.add(InlineKeyboardButton(text='Назад ⬅️', callback_data=callback_data))

    return keyboard


# Добавить клавиатуре кнопки с пагинацией
def add_pagination_btns(
        keyboard: InlineKeyboardBuilder,
        pagination_btns: dict,
        callback_data_next: str | CallbackData,
        callback_data_previous: str | CallbackData) \
        -> InlineKeyboardBuilder | KeyboardBuilder[InlineKeyboardButton]:
    """
    Добавить клавиатуре InlineKeyboardBuilder кнопки с пагинацией.

    :param keyboard: Объект клавиатуры InlineKeyboardBuilder
    :param pagination_btns: Словарь со значениями кнопок пагинации формата {'текст_кнопки': 'значение(next/previous)'}
                            (уже содержащий ТОЛЬКО нужные кнопки/кнопку, это обрабатывается ДО)
    :param callback_data_next: Данные callback_data= для кнопки next
    :param callback_data_previous: Данные callback_data= для кнопки previous
    :returns: Объект клавиатуры KeyboardBuilder с добавленными кнопками пагинации
    """

    # Инициализируем отдельную строку-массив для кнопок
    row = []

    # Итерируемся по переданному в аргументы словарю с кнопками пагинации и добавляем кнопки в массив
    for text, val in pagination_btns.items():
        if val == 'next':
            row.append(InlineKeyboardButton(text=text, callback_data=callback_data_next))           # Идет ВТОРОЙ
        elif val == 'previous':
            row.append(InlineKeyboardButton(text=text, callback_data=callback_data_previous))       # Идет ПЕРВОЙ

    # Добавляем получившийся массив с кнопками в клавиатуру. Они распакуются в один ряд.
    return keyboard.row(*row)


# Добавить клавиатуре отдельную строку с переданными кнопками
def add_separate_line_with_btns(keyboard: InlineKeyboardBuilder, btns: dict[str, str]) \
        -> KeyboardBuilder[InlineKeyboardButton]:
    """
    Добавить клавиатуре отдельную строку с переданными кнопками btns.

    :param keyboard: Объект клавиатуры InlineKeyboardBuilder
    :param btns: Словарь со значениями кнопок формата {'текст_кнопки': 'callback_data'}
    :returns: Объект клавиатуры KeyboardBuilder с добавленными кнопками
    """

    # Инициализируем отдельную строку-массив для кнопок
    row = list()

    # Добавляем переданные кнопки в массив
    for text, callback_data in btns.items():
        row.append(InlineKeyboardButton(text=text, callback_data=callback_data))

    # Добавляем ряд с кнопками в клавиатуру
    return keyboard.row(*row)

###################################################################################################

# СОЗДАНИЕ КЛАВИАТУР


# Универсальная функция для создания простой инлайн-клавиатуры по переданным кнопкам и разметке
def get_inline_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int, ...] = (2, ),
) -> InlineKeyboardMarkup:
    """
    Универсальная функция для создания простой инлайн-клавиатуры по переданным кнопкам и разметке.
    Создаёт кнопки с callback_data, ссылками и switch_inline_query_current_chat:

    - По умолчанию value присваивает callback_data.
    - При встрече http-шаблона '://' - value присвоит url.
    - Если value начинается с 'switch_inline_query_current_chat_' - то заменит эту часть строки на '' и присвоит value
      switch_inline_query_current_chat.

    :param btns: Словарь с нужными кнопками, формата
                {'текст_кнопки': 'value (callback_data / url-ссылка / switch_inline_query_current_chat), ...'}
    :param sizes: Размещение кнопок, кортеж с указанием количества кнопок в каждой строке
    :return: Готовая инлайн-клавиатура с кнопками
    """

    # Создаем объект инлайн-клавиатуры
    keyboard = InlineKeyboardBuilder()

    # Добавляем в клавиатуру переданные кнопки
    for text, value in btns.items():
        if '://' in value:
            keyboard.add(InlineKeyboardButton(text=text, url=value))
        elif value.startswith('switch_inline_query_current_chat_'):
            keyboard.add(InlineKeyboardButton(
                text=text, switch_inline_query_current_chat=value.replace('switch_inline_query_current_chat_', ''))
            )
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Присваиваем клавиатуре размеры
    return keyboard.adjust(*sizes).as_markup()


# Меню уровня 0, клавиатура стартовой страницы start_page
def get_kbds_start_page_btns(
        *,
        username: str | None = None,
        sizes: tuple[int, ...] = (1, 1, 2, 2, 1)
) -> InlineKeyboardMarkup:
    """
    Клавиатура стартовой страницы start_page. Начальное меню + кнопки авторизации.

    :param username: Email пользователя - при пройденной аутентификации или None
    :param sizes: Размещение кнопок, кортеж с указанием количества кнопок в каждой строке
    :return: Готовая инлайн-клавиатура с кнопками
    """

    # Создаем клавиатуру и добавляем в нее кнопки стартового меню
    btns = {
        "Добавить запись в словарь ➕": 'add_new_word',
        "Словарь & Заметки 📚": MenuCallBack(level=1, menu_name='vocabulary').pack(),
        "Произнести 🎙": MenuCallBack(level=1, menu_name='speaking').pack(),
        "Тесты 🎓": MenuCallBack(level=1, menu_name='tests').pack(),
        "AI ассистент 💡": MenuCallBack(level=1, menu_name='giga').pack(),
        "Reverso 🌐": REVERSO_URL,
    }
    keyboard = InlineKeyboardBuilder()
    for text, value in btns.items():
        if '://' in value:
            keyboard.add(InlineKeyboardButton(text=text, url=value))
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Форматируем по заданным размерам
    keyboard.adjust(*sizes)

    # Добавляем полосу с кнопками авторизации:
    if not username:                                             # Если пользователь не авторизован
        btns = {
            "Sign in 👤": MenuCallBack(level=1, menu_name='auth', menu_details='sign_in_app').pack(),
            "Log in 👤": MenuCallBack(level=1, menu_name='auth', menu_details='log_in_app').pack()
        }
        keyboard = add_separate_line_with_btns(keyboard, btns)

    else:                                                       # Если пользователь авторизован
        keyboard.add(InlineKeyboardButton(
            text=f'{username} 👤',
            callback_data=MenuCallBack(level=1, menu_name='auth', menu_details='user_profile').pack())
        )
        keyboard.adjust(*sizes)

    # Формируем итоговую клавиатуру
    return keyboard.as_markup()


# Клавиатура для раздела авторизации
def get_auth_btns(
        sizes: tuple[int, ...] = (1, ),
        profile: bool = False,
        login: bool = False
) -> InlineKeyboardMarkup:
    """
    Клавиатура для раздела авторизации.

    :param sizes: Размеры клавиатуры, кортеж с количеством кнопок в строке
    :param profile: Флаг для добавления кнопок профиля (при True). По умолчанию False
    :param login: Флаг для добавления кнопки восстановления пароля (при True). По умолчанию False
    :return: Готовая клавиатура InlineKeyboardMarkup
    """

    # Формируем клавиатуру с кнопками "На главную 🏠"
    keyboard = create_keyboard_with_header(level=1)

    # Добавляем кнопки профиля (при флаге profile=True)
    if profile:
        keyboard.add(InlineKeyboardButton(text='Статистика и данные 📊', callback_data='statistic_page_1'))
        keyboard.add(InlineKeyboardButton(text='Настройки аудио ⚙', callback_data='user_settings'))
        keyboard.add(InlineKeyboardButton(text='Выйти из профиля ➡️', callback_data='log_out_ask_confirm'))
    else:

        # Добавляем кнопку восстановления пароля (при флаге login=True)
        if login:
            keyboard.add(InlineKeyboardButton(text='Забыл пароль', callback_data='reset_password'))

        # Добавляем кнопку отмены действия (для отката в начало регистрации или аутентификации)
        keyboard.add(InlineKeyboardButton(text='Отмена ❌', callback_data='cancel_auth'))

    # Форматируем по заданным размерам и возвращаем готовую клавиатуру
    return keyboard.adjust(*sizes).as_markup()


# Универсальная клавиатура с хедером навигации (домой, назад). Создаёт кнопки по переданным данным и разметке.
def get_kbds_with_navi_header_btns(
        *,
        level: int,
        btns: dict,
        menu_name: str = 'vocabulary',
        sizes: tuple[int, ...] = (1, ),
        custom_step_back: str | None = None
) -> InlineKeyboardMarkup:
    """
    Универсальная клавиатура с хедером навигации (домой, назад). Создаёт кнопки по переданным данным и разметке.

    :param level: Уровень меню (для обработки кнопки "Назад ⬅️" если level > 1)
    :param btns: Словарь с кнопками, которые необходимо создать, формата {текст: callback_data, ...}
    :param menu_name: Название меню (для формирования callback_data MenuCallBack в кнопке "Назад ⬅️")
    :param sizes: Размеры клавиатуры, кортеж с количеством кнопок в строке
    :param custom_step_back: Данные callback_data= для кнопки "Назад ⬅️" (если требуется не стандартный MenuCallBack)
    :return: Готовая клавиатура InlineKeyboardMarkup
    """

    # Формируем клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)
    keyboard = create_keyboard_with_header(level=level, menu_name=menu_name, custom_step_back=custom_step_back)

    # Добавляем кнопки по переданному в аргументы словарю
    for text, value in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Форматируем по заданным размерам
    keyboard.adjust(*sizes)

    # Формируем итоговую клавиатуру
    return keyboard.as_markup()


# Клавиатура выбора темы (Универсальная).
# При добавлении нового слова, редактировании существующей записи, выборе раздела в словаре
def get_kbds_with_topic_btns(
        *,
        level: int,
        btns: dict,
        page: int,
        pagination_btns: dict,
        menu_name: str,
        menu_details: str,
        cancel_possible: bool = False,              # Добавление кнопок отмены действия
        cancel_page_address: str = None,            # Данные callback_data при отмене действия
        search_possible: bool = False,              # Добавление кнопки поиска
        search_cancel: bool = False,                # Добавление кнопки отмены поиска
        sizes: tuple[int, ...] = (1, 2)
) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора темы (Универсальная).

    При добавлении нового слова, редактировании существующей записи, выборе раздела в словаре.
    Проставьте флаги в соответствующих аргументах.

    :param level: Уровень глубины меню (для обработки кнопки "Назад ⬅️")
    :param btns: Словарь с кнопками тем (срез текущей страницы + опционально пропуск)
    :param page: Номер текущей страницы
    :param pagination_btns: Словарь со значениями кнопок пагинации формата {'текст_кнопки': 'значение(next/previous)'}
                            (уже содержащий ТОЛЬКО нужные кнопки/кнопку, это обрабатывается ДО)
    :param menu_name: Название меню (для формирования callback_data MenuCallBack)
    :param menu_details: Детали меню (для формирования callback_data MenuCallBack)
    :param cancel_possible: Добавление кнопок отмены действия. По умолчанию - False
    :param cancel_page_address: Данные callback_data при отмене действия
    :param search_possible: Добавление кнопки поиска. По умолчанию - False
    :param search_cancel: Добавление кнопки отмены поиска. По умолчанию - False
    :param sizes: Размеры клавиатуры, кортеж с перечислением, сколько кнопок разместить в каждой строке
    :return: Готовая клавиатура InlineKeyboardMarkup
    """

    # Формируем клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)
    keyboard = create_keyboard_with_header(level=level, menu_name='vocabulary')

    # Добавляем кнопки с темами по переданному в аргументы словарю (срез текущей страницы + опционально пропуск)
    for text, value in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Форматируем по заданным размерам
    keyboard.adjust(*sizes)

    # Определяем значения callback_data для кнопок пагинации
    callback_data_next = MenuCallBack(       # 'menu:1:add_new_word:add_new_word:2', 'menu:2:vocabulary:select_topic:2'
        level=level, menu_name=menu_name, menu_details=menu_details, page=page + 1).pack()
    callback_data_previous = MenuCallBack(
        level=level, menu_name=menu_name, menu_details=menu_details, page=page - 1).pack()

    # Для редактирования слова или тестирования корректируем значения callback_data
    if (menu_details == 'tests_select_topic') or ('update_word_' in menu_details):
        callback_data_next = f'{menu_details}_page_{page + 1}'                  # Сформирует update_word_{id}_page_2
        callback_data_previous = f'{menu_details}_page_{page - 1}'              # Или tests_select_topic_page_2

    # Добавляем кнопки пагинации тем
    keyboard = add_pagination_btns(keyboard, pagination_btns, callback_data_next, callback_data_previous)

    # Добавляем кнопку поиска темы при передаче аргумента search_possible=True. По умолчанию False
    if search_possible:
        keyboard = add_separate_line_with_btns(keyboard, {'Найти тему 🔎': 'find_topic_by_matches'})

    # Добавляем кнопку отмены поиска темы при передаче аргумента search_cancel=True. По умолчанию False
    if search_cancel:
        keyboard = add_separate_line_with_btns(keyboard, {'Отменить поиск темы ✖️': 'cancel_find_topic_by_matches'})

    # Добавляем кнопку "Отмена" при передаче аргумента cancel_possible=True. По умолчанию False
    if cancel_possible:
        keyboard = add_separate_line_with_btns(keyboard, {'Отмена ❌': cancel_page_address})

    # Возвращаем сформированную клавиатуру
    return keyboard.as_markup()


# Основная клавиатура добавления/редактирования слова/фразы WordPhrase
def add_new_or_edit_word_main_btns(
        *,
        btns: dict,                             # Кнопки с callback_data для создания в клавиатуре
        level: int = 1,                         # Уровень вложенности меню для кнопки "Назад
        cancel_possible: bool = True,           # Добавление кнопок отмены действия и шага назад
        cancel_page_address: str = None,        # Данные callback_data при отмене действия
        pass_step: bool = False,                # Добавление кнопки пропуска шага. При редактировании слова
        sizes: tuple[int, ...] = (1, 2)
) -> InlineKeyboardMarkup:                      # Возвращает клавиатуру
    """
    Основная клавиатура добавления/редактирования слова/фразы WordPhrase.

    Навигационный хедер + кнопка пропуска шага (при флаге) + создание пользовательских кнопок (с опцией загрузки
    value в switch_inline_query_current_chat при text == 'Редактировать значение ✏️') + блок с отменой/шагом назад.

    :param btns: Словарь с кнопками с для создания в клавиатуре формата {text: callback_data, ...}
            При text == 'Редактировать значение ✏️' вместо callback_data= будет switch_inline_query_current_chat= value
    :param level: Уровень вложенности меню (для обработки кнопки "Назад ⬅️" если level > 1)
    :param cancel_possible: Добавление кнопок отмены действия и шага назад
    :param cancel_page_address: Данные callback_data при отмене действия
    :param pass_step: Добавление кнопки пропуска шага. При редактировании слова
    :param sizes: Размеры клавиатуры, кортеж с количеством кнопок в строке
    :return: Готовая клавиатура формата InlineKeyboardMarkup
    """

    # Формируем клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)
    keyboard = create_keyboard_with_header(level=level, menu_name='vocabulary')

    # Добавляем кнопку "Оставить текущее значение" для пропуска шага. По умолчанию False
    if pass_step:
        keyboard.add(InlineKeyboardButton(text='Оставить текущее значение ▶', callback_data='pass'))

    # Добавляем кнопки по переданному в аргументы словарю
    for text, value in btns.items():

        # Для кнопки "Редактировать значение ✏️" подставляем значение value в ПОЛЬЗОВАТЕЛЬСКИЙ ВВОД
        if text == 'Редактировать значение ✏️':
            keyboard.add(InlineKeyboardButton(text=text, switch_inline_query_current_chat=value))
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Добавляем кнопки "Отмена" и "Шаг назад" при передаче аргумента cancel_possible=True. По умолчанию False
    if cancel_possible:
        keyboard.add(InlineKeyboardButton(text='Отмена ❌', callback_data=cancel_page_address))
        keyboard.add(InlineKeyboardButton(text='Шаг назад', callback_data='add_or_edit_word_step_back'))

    # Форматируем по заданным размерам и возвращаем готовую клавиатуру
    return keyboard.adjust(*sizes).as_markup()


# Клавиатура для прохождения тестов
def get_kbds_tests_btns(level: int, menu_name: str, menu_details: str, topic_filter: int | None,
                        random_word: WordPhrase, sizes: tuple[int, ...]) -> InlineKeyboardMarkup:
    """
    Основная клавиатура для прохождения тестов.

    :param level: Уровень вложенности меню (для обработки кнопки "Назад ⬅️" если level > 1)
    :param menu_name: Название меню (для формирования callback_data MenuCallBack)
    :param menu_details: Дополнительные данные меню (для формирования callback_data MenuCallBack)
    :param topic_filter: id выбранной темы или None (если тема не выбрана)
    :param random_word: отображаемая запись WordPhrase
    :param sizes: Размеры клавиатуры, кортеж с количеством кнопок в строке
    :return: Готовая клавиатура InlineKeyboardMarkup
    """

    # Формируем клавиатуру с кнопками "На главную 🏠" и "Назад ⬅️" (если level > 1)
    keyboard = create_keyboard_with_header(level=level, menu_name=menu_name)

    # Кнопка перехода к выбору темы/отмены выбранной темы
    if not topic_filter:
        topic_btn: dict = {'Выбрать тему': 'tests_select_topic'}
    else:
        topic_btn: dict = {f'Тема: {random_word.topic.name} ✖': 'tests_cancel_select_topic'}

    # Словарь с кнопками
    btns = {
        '⏪ Предыдущее': MenuCallBack(menu_name=menu_name, menu_details=f'{menu_details}_previous', level=2).pack(),
        'Пропустить ⏩': MenuCallBack(menu_name=menu_name, menu_details=menu_details, level=2).pack(),
        'Верно ✅': f'tests_answer_correct',
        'Неверно ❌': f'tests_answer_wrong',
        '🎧 Слово/фраза': f'speak_word_{random_word.id}',
        '🎧 Примеры': f'speak_example_{random_word.id}',
        'Ответ ℹ': f'tests_ask_hint_{random_word.id}',
        **topic_btn,
    }

    # Добавляем кнопки
    for text, value in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    # Форматируем по заданным размерам
    keyboard.adjust(*sizes)

    # Формируем итоговую клавиатуру
    return keyboard.as_markup()


# Гибкое генерирование кнопок пагинации с различными callback_data
def get_pagination_btns(
        pagination_btns: dict,   # кнопки для пагинации передаем словарем
        menu_details: str | None = None,
        page: int = 1,
        sizes: tuple[int, ...] = (2, ),
        custom_cb_data: str | None = None
) -> InlineKeyboardMarkup:
    """
    Функция для гибкого генерирования кнопок пагинации с различными callback_data

    :param pagination_btns: Словарь с кнопками пагинации
    :param menu_details: Дополнительные данные меню (для формирования callback_data VocabularyCallBack).
                        Формируется, если не передан custom_cb_data
    :param page: Текущая страница
    :param sizes: Размеры клавиатуры (количество кнопок в строке)
    :param custom_cb_data: Пользовательская основа callback_data для подстановки f'{custom_cb_data}_page_{page + 1}'
    :return: Готовая клавиатура InlineKeyboardMarkup
    """

    # Формируем callback_data
    if custom_cb_data:
        callback_data_next = f'{custom_cb_data}_page_{page + 1}'
        callback_data_previous = f'{custom_cb_data}_page_{page - 1}'
    else:
        callback_data_next = VocabularyCallBack(page=page + 1, menu_name=menu_details).pack()
        callback_data_previous = VocabularyCallBack(page=page - 1, menu_name=menu_details).pack()

    # Добавляем кнопки с пагинацией
    keyboard = InlineKeyboardBuilder()
    keyboard = add_pagination_btns(keyboard, pagination_btns, callback_data_next, callback_data_previous)

    # Форматируем по заданным размерам и возвращаем готовую клавиатуру
    return keyboard.adjust(*sizes).as_markup()
