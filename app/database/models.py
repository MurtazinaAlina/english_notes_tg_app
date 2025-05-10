"""
Модели таблиц БД.
"""
from sqlalchemy import String, Text, DateTime, func, ForeignKey, Integer, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy_utils import EmailType
from argon2 import PasswordHasher

from app.utils.tts_voices import all_voices_en_US_ShortName_list
from app.settings import (PATTERN_CONTEXT_EXAMPLE, TEST_TYPES_SQL, MIN_NOTE_TEXT_LENGTH, MIN_NOTE_TITLE_LENGTH,
                          SYSTEM_SHEETS_SQL)


# Кастомизация базового класса для наследования (+ id, created, updated)
class Base(DeclarativeBase):
    """ Кастомизация базового класса для наследования (+ id, created, updated). """

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)                             # id объекта
    created: Mapped[DateTime] = mapped_column(DateTime, default=func.now())                           # Дата создания
    updated: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())      # Дата изменения


# Заглавные изображения сообщения с описанием
class Banner(Base):
    """ Заглавные изображения сообщения с описанием. """
    __tablename__ = 'banner'

    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    image_path: Mapped[str] = mapped_column(String(150), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)


# Пользовательский аккаунт
class User(Base):
    """ Пользовательский аккаунт. """
    __tablename__ = 'user'

    email: Mapped[str] = mapped_column(EmailType, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    # Отношения
    topics = relationship("Topic", back_populates="users", cascade="all, delete-orphan")
    attempts = relationship("Attempt", back_populates="user", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="user", cascade="all, delete-orphan")
    user_chat = relationship("UserChat", back_populates="user", cascade="all, delete-orphan")
    user_settings = relationship("UserSettings", back_populates="user", cascade="all, delete-orphan")
    notes = relationship("Notes", back_populates="user", cascade="all, delete-orphan")
    saved_audio = relationship("SavedAudio", back_populates="user", cascade="all, delete-orphan")

    # Установка пароля (захешированного)
    def set_password(self, password: str) -> None:
        """ Установка пароля (захешированного). """
        ph = PasswordHasher()
        self.password_hash = ph.hash(password)

    # Проверка введённого пароля
    def check_password(self, given_password: str) -> bool:
        """
        Проверка переданного пароля. Хэширует и сверяет с сохраненным в БД.

        :param given_password: Переданный пароль
        :return: True, если пароли совпадают, иначе False
        """
        try:
            ph = PasswordHasher()
            ph.verify(self.password_hash, given_password)
            return True
        except (Exception, ):
            return False


# Токены для подтверждения сброса пароля
class PasswordReset(Base):
    """ Токены для подтверждения сброса пароля. """
    __tablename__ = 'reset_password'

    email: Mapped[str] = mapped_column(EmailType, nullable=False, unique=True)
    reset_token: Mapped[str] = mapped_column(String(128), nullable=False)


# Таблица для автоматической авторизации, связь между пользователем User и чатом Telegram
class UserChat(Base):
    """ Таблица для автоматической авторизации, связь между пользователем User и чатом Telegram. """
    __tablename__ = 'user_chat'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)

    # Отношения
    user = relationship(User, back_populates="user_chat", passive_deletes=True)

    # Ограничения
    __table_args__ = (UniqueConstraint('user_id', 'chat_id', name='uq_user_chat'), )


# Настройки профиля пользователя для воспроизведения аудио
class UserSettings(Base):
    """  Настройки профиля пользователя для воспроизведения аудио. """
    __tablename__ = 'user_settings'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False, unique=True)
    speech_rate: Mapped[str] = mapped_column(Integer, nullable=False, default='-20%')
    voice: Mapped[str] = mapped_column(String(50), nullable=False, default='en-US-AvaNeural')

    # Отношения
    user = relationship(User, back_populates="user_settings", passive_deletes=True)

    # Ограничения
    valid_voices_sql = ", ".join(f"'{v}'" for v in all_voices_en_US_ShortName_list)
    valid_voices_sql += ", 'random'"
    __table_args__ = (
        CheckConstraint(
            """
            speech_rate = '+0%' OR
            speech_rate LIKE '+%' AND
                CAST(SUBSTR(speech_rate, 2, LENGTH(speech_rate)-2) AS INTEGER) BETWEEN 1 AND 100 AND
                speech_rate LIKE '%\%%' ESCAPE '\\' OR
            speech_rate LIKE '-%' AND
                CAST(SUBSTR(speech_rate, 2, LENGTH(speech_rate)-2) AS INTEGER) BETWEEN 1 AND 100 AND
                speech_rate LIKE '%\%%' ESCAPE '\\'
            """,
            name='valid_speech_rate_format'
        ),
        CheckConstraint(
            f"voice IN ({valid_voices_sql})",
            name='valid_voice_format'
        )
    )


# Тема, тематика, раздел словаря
class Topic(Base):
    """ Тема, тематика, раздел словаря. """
    __tablename__ = "topic"

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete='CASCADE'), nullable=False)

    # Отношения
    users = relationship(User, back_populates="topics", passive_deletes=True)
    word_phrases = relationship("WordPhrase", back_populates="topic", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="topic")
    attempts = relationship("Attempt", back_populates="topic")

    # Ограничения
    __table_args__ = (
        UniqueConstraint('name', 'user_id', name='uq_user_topic'),
        CheckConstraint(f"name NOT IN ({SYSTEM_SHEETS_SQL})", name="allowed_topic_name")
    )


# Слово/фраза, запись со всеми данными
class WordPhrase(Base):
    """ Слово/фраза, запись со всеми данными. """
    __tablename__ = "word_phrase"

    topic_id: Mapped[int] = mapped_column(ForeignKey(Topic.id, ondelete='CASCADE'), nullable=False)
    word: Mapped[str] = mapped_column(String(50), nullable=False)
    transcription: Mapped[str]
    translate: Mapped[str] = mapped_column(Text, nullable=True)

    # Отношения
    topic: Mapped[Topic] = relationship(back_populates='word_phrases', passive_deletes=True)
    context = relationship("Context", back_populates='word_phrase', cascade="all, delete-orphan")
    attempts = relationship("Attempt", back_populates="word_phrase")

    # Ограничения
    __table_args__ = (
        CheckConstraint("word GLOB '*[a-zA-Z]*'", name="word_must_have_english_letter"),
        UniqueConstraint('word', 'topic_id', 'translate', name='uq_word_topic_translate')
    )


# Примеры предложений с использованием слова/фразы
class Context(Base):
    """ Примеры использования слова/фразы в контексте. """

    __tablename__ = "context"

    word_id: Mapped[int] = mapped_column(ForeignKey(WordPhrase.id, ondelete='CASCADE'), nullable=True)
    example: Mapped[str] = mapped_column(Text, nullable=False)
    note_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete='CASCADE'), nullable=True, default=None)

    # Отношения
    word_phrase = relationship(WordPhrase, back_populates='context', passive_deletes=True)
    note = relationship("Notes", back_populates='examples', passive_deletes=True)

    # Ограничения
    __table_args__ = (
        UniqueConstraint('word_id', 'example', 'note_id', name='uq_word_note_example'),
        CheckConstraint(f"example REGEXP '{PATTERN_CONTEXT_EXAMPLE}'", name='word_min_3_english_letters'),
    )


# Отчеты по пройденным тестам
class Report(Base):
    """ Отчеты по пройденным тестам. """
    __tablename__ = 'report'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False)
    test_type: Mapped[str] = mapped_column(String(50), nullable=False)
    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    correct_attempts: Mapped[int] = mapped_column(Integer, nullable=True)
    result_percentage: Mapped[int] = mapped_column(Integer, nullable=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey(Topic.id, ondelete='SET NULL'), nullable=True, default=None)
    topic_name: Mapped[str] = mapped_column(String(50), nullable=True)
    total_words: Mapped[int] = mapped_column(Integer, nullable=False)

    # Отношения
    attempts = relationship("Attempt", back_populates="report", cascade="all, delete-orphan")
    user = relationship(User, back_populates="reports", passive_deletes=True)
    topic = relationship(Topic, back_populates='reports')

    # Ограничения
    __table_args__ = (
        CheckConstraint("result_percentage BETWEEN 0 AND 100", name='valid_result_percentage'),
        CheckConstraint("correct_attempts <= total_attempts AND correct_attempts >= 0",
                        name='valid_correct_attempts'),
        CheckConstraint("total_attempts > 0", name='valid_total_attempts'),
        CheckConstraint("total_words > 0", name='valid_total_words'),
        CheckConstraint(f"test_type IN ({TEST_TYPES_SQL})", name='valid_test_type')
    )


# Фиксация ответов при прохождении тестирований
class Attempt(Base):
    """ Фиксация ответов при прохождении тестирований. """
    __tablename__ = 'attempt'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False)
    test_type: Mapped[str] = mapped_column(String(50), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey(Topic.id, ondelete='SET NULL'), nullable=True, default=None)
    word_id: Mapped[int] = mapped_column(ForeignKey(WordPhrase.id, ondelete='SET NULL'), nullable=True, default=None)
    word_text: Mapped[str] = mapped_column(String(50), nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    report_id: Mapped[int] = mapped_column(ForeignKey(Report.id, ondelete='CASCADE'), nullable=True, default=None)

    # Отношения
    user = relationship(User, back_populates="attempts", passive_deletes=True)
    word_phrase = relationship(WordPhrase, back_populates="attempts")
    report = relationship(Report, back_populates="attempts", passive_deletes=True)
    topic = relationship(Topic, back_populates='attempts')

    # Ограничения
    __table_args__ = (
        CheckConstraint(f"test_type IN ({TEST_TYPES_SQL})", name='valid_test_type'),
        CheckConstraint("result IN ('correct', 'wrong')", name='valid_result')
    )


# Заметки пользователя
class Notes(Base):
    """ Заметки пользователя. """
    __tablename__ = 'notes'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Отношения
    user = relationship(User, back_populates='notes', passive_deletes=True)
    examples = relationship(Context, back_populates='note', cascade="all, delete-orphan")

    # Ограничения
    __table_args__ = (
        UniqueConstraint('title', 'text', 'user_id', name='uq_title_text_user'),
        CheckConstraint(f"LENGTH(text) >= {MIN_NOTE_TEXT_LENGTH}", name="check_min_text_length"),
        CheckConstraint(f"LENGTH(title) >= {MIN_NOTE_TITLE_LENGTH}", name="check_min_title_length"),
    )


# Сохраненные аудио. Пути к файлам
class SavedAudio(Base):
    """ Сохраненные аудио. Пути к файлам. """
    __tablename__ = 'saved_audio'

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete='CASCADE'), nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)

    # Отношения
    user = relationship(User, back_populates='saved_audio', passive_deletes=True)
