"""
Основные настройки приложения + константы
"""
import os

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())


UTC_ADJUSTMENT = 3                                                  # Корректировка UTC
RESET_PASS_TOKEN_EXPIRE_MINUTES = 10                                # Время жизни ключа сброса пароля в минутах
CHAT_AUTOLOGIN_EXPIRE_DAYS = 90                                     # Срок автоматической аутентификации по чату в днях

# Настройки GIGACHAT
GIGA_AUTH = os.getenv('SBER_AUTH')
GIGA_SCOPE = os.getenv('SBER_SCOPE')
GIGA_SYSTEM_PROMPT = ("Ты репетитор английского языка. Помогаешь русскоязычным ученикам понять разницу в значениях "
                      "слов, контекст употребления, коннотации. Отвечай кратко, дружелюбно, с примерами. Избегай "
                      "дословных переводов.")

# Настройки почты для отправки писем (восстановление пароля и т.д.)
SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = int(os.getenv('SMTP_PORT'))
SENDER_EMAIL = os.getenv('SENDER_EMAIL')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD')

# Заглушка для БД - при встрече символа будет установлено значение None или не создан объект
PLUG_TEMPLATE = '-'

# Валидация
PATTERN_WORD = r'^(?=.*[a-zA-Z]).+$'                                              # Не менее 1 латинской буквы
PATTERN_CONTEXT_EXAMPLE = '[a-zA-Z].*[a-zA-Z].*[a-zA-Z]'                          # Не менее 3х латинских букв
PATTERN_EMAIL = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
PATTERN_SPEECH_RATE = r"^(\+0|\+([1-9][0-9]?|100)|-([1-9][0-9]?|100))$"           # +0, +1-100, -1-100
PATTERN_AUDIO_CONVERT = r'^(?=.*[a-zA-Z]).{3,}$'                                # Мин 3 символа + мин 1 латинская буква
MIN_USER_PSW_LENGTH = 4                                                           # Минимальная длина пароля
MIN_NOTE_TITLE_LENGTH = 3                                                         # Минимальная длина заголовка заметки
MIN_NOTE_TEXT_LENGTH = 5                                                          # Минимальная длина текста заметки

# Список слов-триггеров для проверки на установку заглушки при переотправке сообщения ввода
KEYWORDS_FOR_RE_SEND_MSG = ['транскрипция', 'перевод']

# Настройки пагинации
PER_PAGE_STAT_REPORTS = 5                                            # Количество отчётов статистики на странице
PER_PAGE_VOCABULARY = 4                                              # Количество слов/фраз на странице
PER_PAGE_TOPICS = 5                                                  # Количество тем на странице
PER_PAGE_VOICE_SAMPLES = 5                                           # Количество образцов голоса на странице
PER_PAGE_INLINE_TOPICS = 4                                           # Количество тем на странице инлайн-клавиатуры
PER_PAGE_AUDIO_DATES = 5                                             # Количество дат с сохраненными аудио на странице
PER_PAGE_AUDIOS = 5                                                  # Количество сохранённых аудио на странице
PER_PAGE_NOTE_TITLES = 5                                             # Количество заголовков заметок на странице


# Зачитываемый текст для образца голоса при выборе
VOICE_SAMPLES_TEXT = ('I will be your reliable assistant and help you improve your English listening skills '
                      'and vocabulary.')

# Ссылка на Reverso Context
REVERSO_URL = 'https://context.reverso.net/translation/english-russian/'

# Типы тестов
TEST_EN_RU_WORD = 'en_ru_word'
TEST_EN_RU_AUDIO = 'en_ru_audio'
TEST_RU_EN_WORD = 'ru_en_word'
TEST_TYPES = (TEST_EN_RU_WORD, TEST_EN_RU_AUDIO, TEST_RU_EN_WORD)       # Обозначения типов тестов в коде
TEST_TYPES_SQL = ", ".join(f"'{tt}'" for tt in TEST_TYPES)              # Типы тестов для SQL (для ограничения моделей)

# Excel
FILENAME_STATISTICS = 'Statistics.xlsx'                     # Название xsl-файла со статистикой
FILENAME_VOCABULARY = 'Vocabulary_all.xlsx'                 # Название xsl-файла со словарем
FILENAME_ALL_DATA = 'English_notes.xlsx'                    # Название xsl-файла со всеми данными

EXCEL_ATTEMPTS = 'Attempts'                                 # Название листа с попытками прохождения тестов
EXCEL_STATISTICS = 'Stat'                                   # Название листа со статистикой отчётов пройденных тестов
EXCEL_TABLE_OF_CONTENTS = 'Table of contents'               # Название листа с оглавлением
EXCEL_NOTES = 'Notes'                                       # Название листа с заметками

SYSTEM_SHEETS = (                                  # Системные листы в xsl-файле (данные из них не будут импортированы)
        EXCEL_STATISTICS, EXCEL_ATTEMPTS, EXCEL_TABLE_OF_CONTENTS
)
SYSTEM_SHEETS_SQL = ", ".join(f"'{sheet}'" for sheet in SYSTEM_SHEETS)              # Системные листы для SQL

TABLE_OF_CONTENTS_TITLE = 'Оглавление. Ссылки на страницы тем'                      # Заголовок оглавления
INDEX_MIN_ROW = 2                                       # Индекс первой строки с данными
EXAMPLES_SEPARATOR = '\n'                               # Разделитель примеров в столбце Е с контекстом
EXCEL_COLUMNS_STAT_SHEET = {                            # Данные для столбцов листа со статистикой - заголовки, ширина
        'A': {'header': 'ID отчёта', 'width': 10},
        'B': {'header': 'Дата', 'width': 20},
        'C': {'header': 'Верно', 'width': 10},
        'D': {'header': 'Попыток', 'width': 10},
        'E': {'header': '%', 'width': 10},
        'F': {'header': 'Тема', 'width': 20},
        'G': {'header': 'Всего слов', 'width': 15},
        'H': {'header': 'Тип теста', 'width': 15},
    }
EXCEL_PERCENT_COLUMN_FORMATTING = '0.0%'                    # Форматирование столбца процентов
EXCEL_COLUMNS_ATTEMPTS_SHEET = {                            # Данные для столбцов листа с попытками - заголовки, ширина
        'A': {'header': 'ID', 'width': 10},
        'B': {'header': 'datetime', 'width': 20},
        'C': {'header': 'word', 'width': 30},
        'D': {'header': 'result', 'width': 15},
        'E': {'header': 'report_id', 'width': 15},
        'F': {'header': 'test_type', 'width': 15}
    }
EXCEL_COLUMNS_VCB_SHEET = {                                  # Данные для столбцов листа со словарём - заголовки, ширина
        'A': {'header': 'ID', 'width': 10},
        'B': {'header': 'Word', 'width': 25},
        'C': {'header': 'Transcription', 'width': 20},
        'D': {'header': 'Translate', 'width': 60},
        'E': {'header': 'Context', 'width': 80},
    }
EXCEL_COLUMNS_NOTES_SHEET = {
        'A': {'header': 'ID', 'width': 10},
        'B': {'header': 'Заголовок', 'width': 40},
        'C': {'header': 'Текст', 'width': 60},
        'D': {'header': 'Примеры', 'width': 80},
    }
EXCEL_CONTEXT_COLOR = '064681'                                # Цвет контекста в xsl-файле

XLS_DB_CAPTION = """
ℹ Экспортированный файл является <b>шаблоном</b>, предусматривающим добавление новой информации и последующий импорт обратно в
приложение бота.

🔹 При добавлении в xls-файл дополнительных примеров в записи словаря или заметки, при импорте они будут добавлены к
текущим записям в базе.

🔹 Таким же образом, если перевод слова/текст заметки был дополнен в xls-файле (сохранен весь старый текст + добавлен
новый), при импорте будет обновлена текущая запись в базе. 
"""

# Хранение аудио файлов
FILENAME_AUDIOS_ZIP = 'my_audios.zip'                         # Название zip-архива с сохранёнными аудио
FILENAME_AUDIOS_CAPTION = 'Ваш архив с аудио'                 # Заголовок zip-архива с сохранёнными аудио

# Путь к временному хранилищу несохраненных аудио пользователя
AUDIO_TEMP_PATH = os.path.join(os.getcwd(), 'app', 'data', 'audio', 'user_{user_id}', 'tmp')

# Путь к общей папке пользователя с сохранёнными аудио
SAVED_AUDIO_ROOT_DIR = os.path.join(os.getcwd(), 'app', 'data', 'audio', 'user_{user_id}')

# Путь к конкретной папке при сохранении аудио (с датой)
AUDIO_FINAL_PATH = os.path.join(SAVED_AUDIO_ROOT_DIR, '{date}')
