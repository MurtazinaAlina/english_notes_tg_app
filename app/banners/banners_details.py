"""
Данные для баннеров.
"""

# Описания для баннера стартовой страницы
start_page_header = ('<b>I ❤️‍🔥 English</b>\n\n'
                     'Ваш персональный помощник в изучении английского языка.\n\n'
                     '<i> ✨ Отличный день, чтобы выучить ещё чуть-чуть!</i>')

# Описания для баннеров страниц входа/регистрации/восстановления пароля, профиля и т.д.
sign_in_step_1 = '👤 <b>Зарегистрироваться в системе.</b>\n\nШаг 1️⃣: введите свой email'
sign_in_step_2 = '👤 <b>Зарегистрироваться в системе</b>\n\nШаг 2️⃣: создайте пароль'
sign_in_step_3 = '👤 <b>Зарегистрироваться в системе</b>\n\nШаг 3️⃣: подтвердите пароль'
sign_in_step_4 = '👤 <b>Зарегистрироваться в системе</b>\n\nПользователь <b>{email}</b> успешно зарегистрирован!'
user_profile = ('👤 <b>Профиль пользователя {email}</b>\n\n'
                'Главная страница вашей учетной записи.\n\n'
                '🔹 Просмотр статистики тестов, архива аудио, выгрузка всех данных пользователя\n'
                '🔹 Настройки воспроизведения аудио\n'
                '🔹 Смена учетной записи\n'
                )
user_profile_data = ('👤 <b>Профиль пользователя {email}</b>\n\n'
                     '<i>Данные пользователя</i>\n\n'
                     '🔹 Отчеты о пройденных тестах\n'
                     '🔹 Доступ к сохраненным аудио практики произношения\n'
                     '🔹 Выгрузка всех данных пользователя в .xls базу (Словарь, заметки, статистика)\n')
user_profile_stat = ('👤 <b>Профиль пользователя {email}</b>\n\n'
                     '<i>Статистика</i>\n\n'
                     '🔹 Отчеты о пройденных тестах\n'
                     '🔹 Выгрузка статистики прохождения тестов в .xls\n'
                     )
user_profile_all_audio_dates = ('👤 <b>Профиль пользователя {email}</b>\n\n'     
                                '<i>Сохраненные аудиозаписи практики произношения</i>\n\n'
                                'Выберите дату для доступа к аудио\n\n'
                                '🔹 Выгрузка архива аудиозаписей\n')
user_profile_audio_by_date = ('👤 <b>Профиль пользователя {email}</b>\n\n'
                              '<i>Сохраненные аудиозаписи практики произношения</i>\n\n'
                              'Данные за: {date}\n\n'
                              '🔹 Выгрузка архива аудиозаписей\n')
user_profile_settings = ('👤 <b>Профиль пользователя {email}</b>\n\n'
                         '<i>Настройки аудио</i>\n\n'
                         '🔹 <b>Скорость речи:</b> {speech_rate}\n'
                         '🔹 <b>Выбор голоса:</b> {voice}')
log_in_step_1 = '👤 <b>Вход в систему.</b>\n\nШаг 1️⃣: введите свой email'
log_in_step_2 = '👤 <b>Вход в систему.</b>\n\nШаг 2️⃣: введите пароль'
new_psw_step_1 = '👤 <b>Сброс пароля.</b>\n\nШаг 1️⃣: введите ключ подтверждения из полученного письма'
new_psw_step_2 = '👤 <b>Сброс пароля.</b>\n\nШаг 2️⃣: введите новый пароль'
new_psw_step_3 = '👤 <b>Сброс пароля.</b>\n\nШаг 3️⃣: подтвердите новый пароль'
new_psw_step_4 = '👤 <b>Сброс пароля.</b>\n\nПользователь <b>{email}</b> успешно изменил пароль!'

# Основные описания для баннеров раздела "Словарь"
vcb_header = '<b>📚 Словарь</b>\n\n'
vcb_descrptn_main = (vcb_header + 'Здесь вы можете просматривать и редактировать свои записи.\n\n'
                     '🔹 <b>Выбрать тему:</b> перейти к записям конкретного раздела.\n'
                     '🔹 <b>Все записи:</b> просмотреть все записи словаря.\n'
                     '🔹 <b>Управление темами:</b> создать новую тему или редактировать текущие.\n'
                     '🔹 <b>Заметки:</b> различные полезные заметки с примерами.\n'
                     '🔹 <b>Импорт/выгрузка данных:</b> загрузить данные/сформировать сводную таблицу Excel c записями.'
                     '\n')
vcb_descrptn_xls = (vcb_header + '<b>Импорт/выгрузка данных.</b>\n\n'
                    'Загрузите данные из файла в базу или сформируйте сводную таблицу Excel c записями.\n\n'
                    '⚠️ <i>Для загрузки используйте файл такой же структуры, как у сводной таблицы с записями.</i>\n\n')
vcb_descrptn_topic_manager = (vcb_header + '<b>Управление темами.</b>\n\n'
                              'Создайте новую тему или редактируйте текущие.\n\n')
vcb_descrptn_records = vcb_header + 'Просмотр записей + поиск.\n\n'
vcb_descrptn_select_topic = (vcb_header + 'Выберите тему для просмотра записей.\n\n'
                             '▪ <i>Показаны темы {first_topic}-{last_topic} из {topics_total}</i>')
vcb_descrptn_notes = (vcb_header + '<b>Заметки.</b>\n\n'
                      'Просматривайте, создавайте и редактируйте различные полезные заметки с примерами.\n\n')

# Описания для баннеров при добавлении новой заметки
add_new_note_header = vcb_header + '<b>Добавление новой заметки</b>\n\n'
add_new_note_step_1 = add_new_note_header + 'Шаг 1️⃣: введите название заметки'
add_new_note_step_2 = add_new_note_header + 'Шаг 2️⃣: введите текст заметки'
add_new_note_step_3 = (add_new_note_header +
                       'Шаг 3️⃣: введите первый пример\n\n'
                       '⚠️ <i>Для пропуска шага введите любой один символ, например "-"</i>')
add_new_note_step_4 = vcb_header + '<i>Заметка <b>"{title}"</b> добавлена!</i>\n\n'
add_new_note_add_example = add_new_note_header + 'Шаг 4️⃣: введите текст следующего примера\n\n'

# Описания для баннеров при добавлении нового слова/фразы
add_new_word_header = '➕ <b>Добавление новой записи в словарь</b>\n\n'
add_new_word_step_1 = (add_new_word_header + 'Шаг 1️⃣: выберите тему'
                       '\n\nТемы: {first_topic} - {last_topic}\nВсего тем: {topics_total}')
add_new_word_step_2 = add_new_word_header + 'Шаг 2️⃣: введите слово/фразу'
add_new_word_step_3 = add_new_word_header + ('Шаг 3️⃣: введите транскрипцию\n\n'
                                             '⚠️ <i>Для пропуска шага введите любой один символ, например "-"</i>')
add_new_word_step_4 = add_new_word_header + ('Шаг 4️⃣: введите перевод\n\n'
                                             '⚠️ <i>Для пропуска шага введите любой один символ, например "-"</i>')
add_new_word_step_5 = add_new_word_header + ('Шаг 5️⃣: введите пример.\n\n'
                                             '⚠️ <i>Для пропуска шага введите любой один символ, например "-"</i>')
add_new_word_step_6 = (add_new_word_header +
                       "<i>Запись добавлена!</i>\n\n"
                       "<b>▪ {word}</b>\n"
                       "<b>Тема:</b> {topic}\n"
                       "<b>Транскрипция:</b> {transcription}\n"
                       "<b>Перевод:</b> {translate}\n"
                       "<b>Примеры:</b><i>{context}</i>")

# Описания для баннеров при редактировании слова/фразы
update_word_header = '<b>✍🏻 Редактирование записи</b>\n\n'
update_word_main = (update_word_header +
                    "<b>▪ {word}</b>\n"
                    "<b>Тема:</b> {topic}\n"
                    "<b>Транскрипция:</b> {transcription}\n"
                    "<b>Перевод:</b> {translate}\n"
                    "<b>Примеры:</b><i>{context}</i>")

# Описания для баннеров при тестированиях
tests_header = '<b>🎓 Тестирование</b>'
tests_descrptn_main = tests_header + '\n\nВыберите тип тестирования для закрепления изученных слов'
tests_dscr_select_topic = tests_header + '\n\nВыберите тему.'
tests_dscr_en_ru_word = (tests_header + '<b>: (EN -> RU текст)</b>\n\n'
                                        '<b>📚 Тема:</b> {topic}\n\n'
                                        '<b>Переведите: </b>{word}\n\n'
                                        '<b>Примеры:</b><i>{context}</i>')
tests_dscr_en_ru_audio = (tests_header + '<b>: (EN -> RU аудио)</b>\n\n'
                                         '<b>📚 Тема:</b> {topic}\n\n'
                                         '<b>Прослушайте аудио (+ примеры)</b>\n\n')
tests_dscr_ru_en_word = (tests_header + '<b>: (RU -> EN текст)</b>\n\n'
                                        '<b>📚 Тема:</b> {topic}\n\n'
                                        '<b>Переведите: </b>{translate}\n\n')
tests_dscr_en_ru_word_hint = (tests_header + '<b>: (EN -> RU текст)</b>\n\n'
                                             '<b>📚 Тема:</b> {topic}\n\n'
                                             '<b>▪ {word}</b>\n\n'
                                             '<b>Транскрипция:</b> {transcription}\n'
                                             '<b>Перевод:</b> {translate}\n'
                                             '<b>Примеры:</b><i>{context}</i>\n\n')
tests_dscr_en_ru_audio_hint = (tests_header + '<b>: (EN -> RU аудио) </b>\n\n'
                                              '<b>📚 Тема:</b> {topic}\n\n'
                                              '<b>▪ {word}</b>\n\n'
                                              '<b>Транскрипция:</b> {transcription}\n'
                                              '<b>Перевод:</b> {translate}\n'
                                              '<b>Примеры:</b><i>{context}</i>\n\n')
tests_dscr_ru_en_word_hint = (tests_header + '<b>: (RU -> EN текст)</b>\n\n'
                                             '<b>📚 Тема:</b> {topic}\n\n'
                                             '<b>▪ {word}</b>\n\n'
                                             '<b>Транскрипция:</b> {transcription}\n'
                                             '<b>Перевод:</b> {translate}\n'
                                             '<b>Примеры:</b><i>{context}</i>\n\n')

# Описания для баннеров раздела с произношением
speaking_header = '<b>🎙 Произношение</b>\n\n'
speaking_main = speaking_header + 'Выберите действие.\n'
speaking_tts = (speaking_header +
                'Преобразовать в аудио.\n\n<i>Отправляйте текст, который хотите прослушать, в чат.</i>\n')
speaking_practice = (speaking_header +
                     'Практика. Запишите аудиозапись примера:\n\n<b>▪ {example}</b>\n\n'
                     '<i>Результат можно сохранить для оценки динамики.</i>\n')

# Описания для баннера AI ассистента
ai_header = ('<b>💡 AI-ассистент</b>\n\n'
             'Отправляйте ваши вопросы в чат и получайте ответы от GIGA.\n\n'
             '<i>Например: В чем разница между call upon и invoke?</i>')

# Данные для создания баннеров
banner_details = [
    # Стартовая страница с главным меню
    {
        'name': 'start_page',
        'description': start_page_header,
        'image_path': 'app/banners/start.jpg'
    },
    # Страница авторизации
    {
        'name': 'auth',
        'description': sign_in_step_1,
        'image_path': 'app/banners/auth.jpg'
    },
    # Страница добавления нового слова/фразы
    {
        'name': 'add_new_word',
        'description': add_new_word_header,
        'image_path': 'app/banners/add_new.jpg'
    },
    # Страница "Словарь"
    {
        'name': 'vocabulary',
        'description': vcb_descrptn_main,
        'image_path': 'app/banners/vocabulary.jpg'
    },
    # Страница "Тесты"
    {
        'name': 'tests',
        'description': tests_descrptn_main,
        'image_path': 'app/banners/tests.jpg'
    },
    # Страница "Произношение"
    {
        'name': 'speaking',
        'description': speaking_main,
        'image_path': 'app/banners/speaking.jpg'
    },
    # Страница "AI ассистент"
    {
        'name': 'giga',
        'description': ai_header,
        'image_path': 'app/banners/giga.jpg'
    }
]
