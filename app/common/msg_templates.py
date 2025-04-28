"""
ШАБЛОНЫ СООБЩЕНИЙ
"""

# Шаблон сообщения со статистикой прохождения тестов
stat_msg_template = ('📊 <b>Текущая статистика попыток:</b>\n\n'
                     '📚 <b>Тема:</b> {topic_name}\n'
                     '📝 Всего попыток:     {total_attempts}\n'
                     '✅ Верно:                     {correct_attempts}\n'
                     '❌ Неверно:                 {incorrect_attempts}\n\n'
                     '<b>⭐ Результат:  {result_percentage}%</b>')

# Шаблон сообщения со статистикой отчётов
report_msg_template = ('📊 <b>Отчёт №{report_number} от {report_date}</b>\n\n'
                       '🎓 <b>Тип теста:</b> {test_type}\n'
                       '📚 <b>Тема:</b> {topic_name}\n\n'
                       '📝 Всего попыток:     {total_attempts}\n'
                       '✅ Верно:                     {correct_attempts}\n'
                       '📖 Всего слов:             {total_words}\n\n'
                       '<b>⭐ Результат:  {result_percentage}%</b>')

# Шаблон сообщения с примером Context
context_example_msg_template = (
    '▪ {example}\n\n'
    '<b>Создано:</b> {created}\n'
    '<b>Изменено:</b> {updated}'
)

# Шаблон сообщения с заметкой
note_msg_template = (
    'Заметка <b>#{page}</b> из <b>{len_user_notes}</b>\n\n'
    '📒 <b>{note_title}</b>\n'
    '{note_text}\n\n'
    '<b>Примеры:</b>\n'
    '<i>{examples}</i>'
)

# Шаблон сообщения с темой
topic_msg_template = (
    '▪ <b>{topic}</b> \n\n'
    '<b>Записей:</b> {words_total}\n'
    '<b>Создано:</b> {created}\n'
    '<b>Изменено:</b> {updated}'
)

# Шаблон сообщения со словом
word_msg_template = (
    '<b>▪ {word}</b>\n\n'
    '<b>Тема:</b> {topic}\n'
    '<b>Транскрипция:</b> {transcription}\n'
    '<b>Перевод:</b> {translate}\n'
    '<b>Примеры:</b> <i>{context}</i>\n\n'
    '<b>Создано:</b> {created}\n'
    '<b>Изменено:</b> {updated}'

)

# Шаблон сообщения ошибки с выводом ошибки
oops_with_error_msg_template = '⚠️ Упс, что-то пошло не так!\nОшибка: {error}'

# Шаблон сообщения ошибки, повторить попытку
oops_try_again_msg_template = '⚠️ Упс, что-то пошло не так! Попробуйте ещё раз'

# Шаблон сообщения отмены действия
action_cancelled_msg_template = '⚠️ Действие отменено!'

# Шаблон сообщения с валидацией примера Context
context_validation_not_passed_msg_template = (
    '⚠️ Упс! Пример должен содержать минимум 3 латинских буквы. Введите корректное значение'
)

# Шаблон сообщения с валидацией слова WordPhrase
word_validation_not_passed_msg_template = (
    '⚠️ Упс! Слово должно содержать минимум 1 латинскую букву. Введите корректное значение'
)
