"""
Управление БД.
"""
import os
import re
import secrets
from typing import Sequence, Type
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine, AsyncEngine
from sqlalchemy import select, update, func, desc, exists, event, or_, Row
from sqlalchemy.orm import joinedload, selectinload
from argon2 import PasswordHasher

from app.database.models import Base, WordPhrase, Topic, Context, Banner, User, PasswordReset, Attempt, Report, \
    UserChat, UserSettings, Notes, SavedAudio
from app.banners.banners_details import banner_details
from app.settings import PLUG_TEMPLATE, PATTERN_CONTEXT_EXAMPLE, UTC_ADJUSTMENT, RESET_PASS_TOKEN_EXPIRE_MINUTES, \
    CHAT_AUTOLOGIN_EXPIRE_DAYS


# Функция для регистрации функции REGEXP в БД. Возвращает True, если строка row содержит pattern
def regexp(pattern: str, row: str) -> bool:
    """
    Проверяет, содержит ли строка row регулярное выражение pattern.

    :param pattern: Шаблон регулярного выражения для проверки
    :param row: Строка для проверки
    :return: True, если строка row содержит pattern, иначе False
    """
    if row is None:
        return False
    return re.search(pattern, row, re.IGNORECASE) is not None


# Регистрируем функцию поддержки регулярных выражений на движке (позволяет использовать REGEXP в SQL-запросах)
def create_engine_with_regexp() -> AsyncEngine:
    """
    Создает движок с поддержкой регулярных выражений.

    :return: Асинхронный движок с поддержкой регулярных выражений
    """

    # Создаем асинхронный движок
    engine = create_async_engine(os.getenv('DB_LITE'), echo=True)

    @event.listens_for(engine.sync_engine, "connect")
    def register_regexp(dbapi_connection, connection_record):
        """
        Функция-обработчик события подключения к базе данных ("connect").

        :param dbapi_connection: Соединение SQLite (объект sqlite3.Connection)
        :param connection_record: Метаинформация о соединении

        """
        dbapi_connection.create_function("REGEXP", 2, regexp)                       # Регистрируем функцию

    # Возвращаем настроенный AsyncEngine с поддержкой REGEXP
    return engine


class DataBase:
    """ Класс для взаимодействия с БД. """

    def __init__(self):

        # Создаем движок с регистрацией функции REGEXP для поддержки регулярных выражений в ограничениях таблиц
        self.engine = create_engine_with_regexp()

        # Создаем сессию; expire_on_commit - чтобы сессия НЕ закрывалась после commit()
        self.session_maker = async_sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

    async def create_db(self):
        """ Создать все таблицы. """

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Заполнить таблицу баннеров
        await self.create_banners()

    async def drop_db(self):
        """ Снести все таблицы. """

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    # BANNERS

    async def create_banners(self) -> None:
        """  Создать баннеры Banner. """

        for banner in banner_details:
            try:
                banner = Banner(
                    name=banner['name'],
                    image_path=banner['image_path'],
                    description=banner['description']
                )

                async with self.session_maker() as session:
                    session.add(banner)
                    await session.commit()

            except (Exception, ):
                pass

    @staticmethod
    async def get_banner_by_name(session: AsyncSession, page_name: str) -> Banner | None:
        """
        Получить баннер Banner по его имени.

        :param session: Пользовательская сессия
        :param page_name: Название баннера Banner.name
        :return: Объект Banner или None, если баннер с переданным названием не найден
        """
        result = await session.execute(select(Banner).where(Banner.name == page_name))
        return result.scalar()

    # USERS & AUTH

    @staticmethod
    async def create_user(session: AsyncSession, data: dict) -> User:
        """
        Создать нового пользователя User по переданным данным с email и паролем.

        :param session: Пользовательская сессия
        :param data: Словарь с данными для создания (email, password)
        :return: Созданный объект User
        """

        # Создаём объект для хеширования пароля
        ph = PasswordHasher()

        # Создаём нового пользователя
        new_user = User(
            email=data['email'],
            password_hash=ph.hash(data['password'])
        )
        session.add(new_user)
        await session.commit()

        # Создаём запись пользователя в таблицу с настройками
        await session.flush()
        await DataBase.create_user_settings(session, new_user.id)

        return new_user

    @staticmethod
    async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
        """
        Получить пользователя User по его id.

        :param session: Пользовательская сессия
        :param user_id: id пользователя
        :return: User или None если пользователь с переданным id не найден
        """
        result = await session.execute(select(User).options(joinedload(User.topics)).where(User.id == user_id))
        return result.scalar()

    @staticmethod
    async def get_user_id_by_data(session: AsyncSession, data: dict) -> int | None:
        """
        Получить id пользователя User по его email и паролю.
        Функция используется для аутентификации, находит пользователя по логину и сверяет хеш пароля.

        :param session: Пользовательская сессия
        :param data: Словарь с данными для получения (email, password)
        :return: User.id или None в случае ошибки
        """

        # Определяем пользователя по email
        result = await session.execute(select(User).where(User.email == data['email']))
        user = result.scalar()

        # Проверяем хеш пароля, если пользователь найден. Возвращаем его id
        if user:
            if user.check_password(given_password=data['password']):
                return user.id
        return None

    @staticmethod
    async def user_change_password(session: AsyncSession, data: dict) -> User | None:
        """
        Изменить пароль пользователя User по переданным данным(email, новый password).

        :param session: Пользовательская сессия
        :param data: Словарь с данными для изменения (email, password)
        :return: Объект User или None в случае ошибки
        """

        # Определяем пользователя по email
        user = await session.execute(select(User).where(User.email == data['email']))
        user = user.scalar()

        if not user:
            return None

        # Пробуем установить новый пароль
        try:
            user.set_password(data['password'])
            session.add(user)
            await session.commit()
            return user

        except (Exception, ):
            return None

    @staticmethod
    async def create_token_reset_psw(session: AsyncSession, data: dict) -> PasswordReset | None:
        """
        Создать токен для сброса пароля PasswordReset. Срок жизни токена 10 минут.

        :param session: Пользовательская сессия
        :param data: Словарь с данными для создания токена (email)
        :return: созданный токен - объект PasswordReset или None в случае ошибки
        """

        # Генерация случайного токена с secrets
        try:
            new_token = PasswordReset(email=data['email'], reset_token=secrets.token_hex(16))
            session.add(new_token)
            await session.commit()
            return new_token

        except (Exception, ):
            return None

    @staticmethod
    async def get_token_pass_reset_by_email(session: AsyncSession, email: str) -> str | None:
        """
        Получить токен для сброса пароля PasswordReset по переданному email пользователя.

        :param session: Пользовательская сессия
        :param email: email пользователя
        :return: PasswordReset.reset_token для сброса пароля или None в случае ошибки
        """
        result = await session.execute(select(PasswordReset.reset_token).where(PasswordReset.email == email))
        token = result.scalar()
        return token

    async def delete_expired_tokens_pass_reset(self) -> None:
        """  Удалить просроченные токены PasswordReset. """

        async with self.session_maker() as session:

            # Определяем крайний срок создания токена. Удаляем токены старше установленного в settings.py времени
            expiration_time = datetime.now() - timedelta(
                minutes=RESET_PASS_TOKEN_EXPIRE_MINUTES) - timedelta(hours=UTC_ADJUSTMENT)

            # Находим все токены старше установленного времени
            result = await session.execute(
                select(PasswordReset).filter(PasswordReset.created < expiration_time)
            )
            expired_tokens = result.scalars().all()

            # Удаляем старые токены
            try:
                if expired_tokens:
                    for token in expired_tokens:
                        await session.delete(token)
                    await session.commit()

            except Exception as e:
                print(str(e))

    # TOPICS

    @staticmethod
    async def create_topic(session: AsyncSession, data: dict, user_id: int) -> Topic | None:
        """
        Создать новую тему Topic по переданным данным (name, user_id).

        :param session: Пользовательская сессия
        :param data: Словарь с данными для создания (name)
        :param user_id: id пользователя User
        :return: Созданный объект Topic или None в случае ошибки
        """

        # Создаём новую тему
        try:
            new_topic = Topic(name=data['name'], user_id=user_id)
            session.add(new_topic)
            await session.commit()
            return new_topic

        except (Exception, ):
            return None

    @staticmethod
    async def get_all_topics(session: AsyncSession, user_id: int, search_key: str | None = None) -> Sequence[Topic]:
        """
        Получить темы Topic пользователя по его id (все или отфильтрованные по названию).

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param search_key: Ключ для фильтра по названию или None
        :return: Список объектов тем Topic
        """

        # Забираем все темы Topic аутентифицированного пользователя User
        query = select(Topic).where(Topic.user_id == user_id).options(selectinload(Topic.word_phrases))

        # Если передан фильтр по названию, применяем
        if search_key:
            query = query.filter(Topic.name.icontains(search_key))

        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_topic_by_id(session: AsyncSession, topic_id: int) -> Type[Topic] | None:
        """
        Получить тему Topic по её id.

        :param session: Пользовательская сессия
        :param topic_id: id темы Topic
        :return: объект Topic с переданным id или None в случае ошибки
        """
        query = select(Topic).where(Topic.id == topic_id).options(selectinload(Topic.word_phrases))
        result = await session.execute(query)
        return result.scalar()

    @staticmethod
    async def count_topics(session: AsyncSession, user_id: int) -> int:
        """
        Подсчитать количество тем Topic у пользователя.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :return: Количество тем Topic
        """
        result = await session.execute(select(func.count(Topic.id)).where(Topic.user_id == user_id))
        return result.scalar()

    @staticmethod
    async def delete_topic_by_id(session: AsyncSession, topic_id: int) -> str | None:
        """
        Удалить тему Topic по её id.

        :param session: Пользовательская сессия
        :param topic_id: id темы Topic
        :return: Название удалённой темы Topic.name или None в случае ошибки
        """
        try:
            # Находим тему по id
            topic = await session.get(Topic, topic_id)

            # Удаляем тему и возвращаем ее название
            if topic:
                topic_name = topic.name
                await session.delete(topic)
                await session.commit()
                return str(topic_name)

        except (Exception, ):
            return None

    @staticmethod
    async def update_topic_by_id(session: AsyncSession, topic_id: int, data: dict) -> bool:
        """
        Изменить данные записи темы в таблице Topic по id.

        :param session: Пользовательская сессия
        :param topic_id: int id записи в таблице
        :param data: Словарь с новыми данными слова/фразы
        :return: True в случае успеха (для проверки в контроллере)
        """
        await session.execute(update(Topic).where(Topic.id == topic_id).values(name=data['name']))
        await session.commit()
        return True

    # WORD_PHRASES

    @staticmethod
    async def create_word_phrase(session: AsyncSession, data: dict) -> WordPhrase:
        """
        Функция создаёт новую запись в таблице WordPhrase + пример в таблице Context + возвращает созданный
        объект WordPhrase с подгруженными связями.

        :param session: Пользовательская сессия
        :param data: Словарь с данными слова/фразы для создания
        """

        # Создаем запись в таблице WordPhrase
        obj = WordPhrase(
            topic_id=int(data['topic']),
            word=data['word'],
            transcription=data['transcription'],
            translate=data['translate']
        )
        session.add(obj)

        # Фиксируем изменения в БД
        await session.flush()

        # Если пример Context прошел валидацию (не заглушка), создаём запись с примером
        if re.match(PATTERN_CONTEXT_EXAMPLE, data.get('context', PLUG_TEMPLATE)):
            obj1 = Context(
                word_id=obj.id,
                example=data['context']
            )
            session.add(obj1)

        await session.commit()

        # Перезагружаем объект с подгруженными связями и возвращаем его
        result = await session.execute(
            select(WordPhrase)
            .options(selectinload(WordPhrase.topic), selectinload(WordPhrase.context))
            .where(WordPhrase.id == obj.id)
        )
        return result.scalar_one()

    @staticmethod
    async def get_user_word_phrases(
            session: AsyncSession, user_id: int, topic_id: int | None = None, search_keywords: str | None = None,
            ordering_asc: bool = False) -> Sequence[WordPhrase]:
        """
        Получить список всех слов/фраз пользователя

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param topic_id: id выбранной пользователем темы Topic или None, если тема не была выбрана
        :param search_keywords: Строка поиска (по названию слова/фразы)
        :param ordering_asc: True - сортировка по возрастанию, False - сортировка по убыванию. По умолчанию False

        :return: Последовательность - список объектов класса WordPhrase, отфильтрованных по User и Topic (если передана)
                [<app.database.models.WordPhrase object at 0x000002261E1F0D70>, <...>, ...]
        """

        # Забираем все записи WordPhrase аутентифицированного пользователя User, подгружаем отношения через
        # selectinload для обращения к таблицам Topic и Context через соответствующие атрибуты
        query = (
            select(WordPhrase).
            join(Topic, Topic.id == WordPhrase.topic_id).
            where(Topic.user_id == user_id).
            options(selectinload(WordPhrase.topic), selectinload(WordPhrase.context))
                 )

        # Если передан фильтр темы, применяем
        if topic_id:
            query = query.filter(Topic.id == topic_id)

        # Если передан фильтр по тексту, применяем
        if search_keywords:
            query = query.filter(WordPhrase.word.icontains(search_keywords))

        # Если сортировка по убыванию, применяем
        if not ordering_asc:
            query = query.order_by(desc(WordPhrase.id))

        # Возвращаем список записей
        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_random_word_phrase(session: AsyncSession, user_id: int, topic_filter: int | None) \
            -> WordPhrase | None:
        """
        Получить случайную запись слова/фразы из таблицы WordPhrase.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param topic_filter: id выбранной пользователем темы Topic или None если тема не была выбрана
        :return: Случайная запись WordPhrase или None если записей нет
        """

        # Забираем случайную запись WordPhrase аутентифицированного пользователя User
        query = (select(WordPhrase).
                 join(Topic, Topic.id == WordPhrase.topic_id).
                 where(Topic.user_id == user_id).
                 options(selectinload(WordPhrase.topic), selectinload(WordPhrase.context)).
                 order_by(func.random()).limit(1)
                 )

        # Если передан фильтр темы, применяем
        if topic_filter:
            query = query.filter(Topic.id == topic_filter)

        result = await session.execute(query)
        random_word = result.scalars().first()
        return random_word

    @staticmethod
    async def update_word_phrase(session: AsyncSession, word_id: int, data: dict) -> bool:
        """
        Изменить данные записи слова/фразы в таблице WordPhrase.

        :param session: Пользовательская сессия
        :param word_id: int id записи в таблице
        :param data: Словарь с новыми данными слова/фразы
        :return: True в случае успеха (для проверки в контроллере)
        """
        query = update(WordPhrase).where(WordPhrase.id == word_id).values(**data)
        await session.execute(query)
        await session.commit()
        return True

    @staticmethod
    async def delete_word_phrase(session: AsyncSession, word_id: int) -> bool:
        """
        Удалить запись слова/фразы из таблицы WordPhrase по id.

        :param session: Пользовательская сессия
        :param word_id: int id записи в таблице
        :return: True в случае успеха, False в случае ошибки
        """
        word_to_delete = await session.get(WordPhrase, word_id)
        if word_to_delete:
            await session.delete(word_to_delete)
            await session.commit()
            return True
        return False

    @staticmethod
    async def get_word_phrase_by_id(session: AsyncSession, word_id: int) -> Type[WordPhrase] | None:
        """
        Получить запись слова/фразы из таблицы WordPhrase по id.

        :param session: Пользовательская сессия
        :param word_id: int id записи в таблице
        :return: объект WordPhrase
        """
        word_phrase = await session.get(
            WordPhrase, word_id, options=[joinedload(WordPhrase.context), joinedload(WordPhrase.topic)]
        )
        return word_phrase

    @staticmethod
    async def get_word_phrase_by_data(session: AsyncSession, word: str, translate: str, topic_id: int) \
            -> WordPhrase | None:
        """
        Получить запись слова/фразы из таблицы WordPhrase по набору данных.
        Ищет по совпадению id темы, точному совпадению слова и частичному (!) совпадению перевода (Находит, если перевод
        в БД полностью является частью перевода в файле или идентичен).

        :param session: Пользовательская сессия
        :param word: Значение атрибута word
        :param translate: Значение атрибута translate
        :param topic_id: Значение атрибута topic_id
        :return: Найденный объект WordPhrase или None
        """
        query = select(WordPhrase).where(
            WordPhrase.word == word,
            func.instr(translate, WordPhrase.translate) > 0,
            WordPhrase.topic_id == topic_id
        )
        result = await session.execute(query)
        return result.scalars().first()

    # CONTEXT

    @staticmethod
    async def create_context_example(session: AsyncSession, data: dict, word_id: int | None = None,
                                     note_id: int | None = None) -> Context | None:
        """
        Создание новой записи примера в таблице Context.

        :param session: Пользовательская сессия
        :param data: Словарь с данными примера
        :param word_id: id связанной записи WordPhrase
        :param note_id: id связанной записи Note
        :return: созданный объект Context или None при ошибке
        """
        obj = Context(
            word_id=word_id,
            example=data['context'],
            note_id=note_id
        )
        session.add(obj)
        await session.commit()
        return obj

    @staticmethod
    async def delete_context_by_id(session: AsyncSession, context_id: int) -> bool | None:
        """
        Удалить пример Context из таблицы Context по id.

        :param session: Пользовательская сессия
        :param context_id: id записи Context в БД
        :return: True при успешном удалении (для проверки в контроллере) или None при ошибке
        """
        context_to_delete = await session.get(Context, context_id)
        if context_to_delete:
            await session.delete(context_to_delete)
            await session.commit()
            return True

    @staticmethod
    async def get_context_by_id(session: AsyncSession, context_id: int) -> Type[Context] | None:
        """
        Получить объект примера Context по id.

        :param session: Пользовательская сессия
        :param context_id: id записи Context в БД
        :return: Объект Context
        """
        context = await session.get(Context, context_id)
        return context

    @staticmethod
    async def get_context_by_data(session: AsyncSession, word_id: int | None, note_id: int | None, example: str) \
            -> Context | None:
        """
        Получить пример Context по переданным данным.

        :param session: Пользовательская сессия
        :param word_id: id связанной записи WordPhrase или None
        :param note_id: id связанной записи Note или None
        :param example: Текст примера
        :return: Найденный объект Context или None
        """
        query = (select(Context).where(
            Context.note_id == note_id,
            Context.word_id == word_id,
            Context.example == example)
        )
        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    async def update_context_by_id(session: AsyncSession, context_id: int, example: str) -> bool:
        """
        Обновление текста примера Context.example по переданным данным.

        :param session: Пользовательская сессия
        :param context_id: id редактируемого примера Context
        :param example: Новый текст примера
        :return: True при успешном обновлении для проверки в контроллере
        """
        query = update(Context).where(Context.id == context_id).values(example=example)
        await session.execute(query)
        await session.commit()
        return True

    @staticmethod
    async def get_random_context(session: AsyncSession, user_id: int) -> Type[Context] | None:
        """
        Получить случайный пример Context пользователя.

        :param session: Пользовательская сессия
        :param user_id: id пользователя
        :return: Объект Context
        """
        query = (select(Context)
                 .join(WordPhrase, Context.word_id == WordPhrase.id)
                 .join(Topic, Topic.id == WordPhrase.topic_id)
                 .where(Topic.user_id == user_id).order_by(func.random())
                 .limit(1)
                 )
        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    async def check_if_user_has_examples(session: AsyncSession, user_id: int) -> bool:
        """
        Проверка, есть ли примеры Context у пользователя.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :return: True, если у пользователя есть примеры, False в противном случае
        """
        query = select(exists().where(
            Context.word_id == WordPhrase.id,
            WordPhrase.topic_id == Topic.id,
            Topic.user_id == user_id)
        )
        result = await session.execute(query)
        return result.scalar()

    # ATTEMPTS

    @staticmethod
    async def create_attempt(
            session: AsyncSession, user_id: int, test_type: str, word: WordPhrase, result: str) -> None:
        """
        Создание записи о попытке прохождения теста в таблице Attempts.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param test_type: тип теста
        :param word: объект WordPhrase
        :param result: correct/wrong результат попытки
        :return: None
        """
        obj = Attempt(
            user_id=user_id, test_type=test_type, topic_id=word.topic_id, word_id=word.id,
            word_text=word.word, result=result
        )
        session.add(obj)
        await session.commit()

    @staticmethod
    async def get_stat_attempts(session: AsyncSession, user_id: int, test_type: str) \
            -> tuple[int, int, int, float, int, Type[Topic] | None]:
        """
        Получить статистику по попыткам прохождения типа теста пользователем.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param test_type: Тип теста
        :return: Кортеж с данными:
                total_attempts, correct_attempts, incorrect_attempts, result_percentage, topic_count, topic_obj/None
        """
        query = (select(
            func.count(Attempt.id).label('total_attempts'),
            func.count(Attempt.id).filter(Attempt.result == 'correct').label('correct_attempts'),
            func.count(Attempt.id).filter(Attempt.result == 'wrong').label('incorrect_attempts'),
            func.count(func.distinct(Attempt.topic_id)).label('total_topics'),
            Attempt.topic_id
        ).
                 where(Attempt.user_id == user_id, Attempt.test_type == test_type, Attempt.report_id == None))
        result = await session.execute(query)
        total_attempts, correct_attempts, incorrect_attempts, topic_count, topic_id = result.first()

        # Возвращаем объект темы Topic, если все попытки относятся к одной теме, иначе None
        if topic_count > 1:
            topic = None
        else:
            topic = await DataBase.get_topic_by_id(session, topic_id)

        # Рассчитываем % результата
        result_percentage = 0 if total_attempts == 0 else round((correct_attempts / total_attempts * 100), 2)
        return total_attempts, correct_attempts, incorrect_attempts, result_percentage, topic_count, topic

    @staticmethod
    async def get_user_attempts(session: AsyncSession, user_id: int) -> Sequence[Attempt]:
        """
        Получить все попытки Attempt прохождения тестов пользователем.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :return: Список попыток Attempt
        """
        query = select(Attempt).where(Attempt.user_id == user_id).options(selectinload(Attempt.word_phrase))
        attempts = await session.execute(query)
        return attempts.scalars().all()

    # REPORTS

    @staticmethod
    async def create_stat_report(
            session: AsyncSession, user_id: int, test_type: str, total_attempts: int, correct_attempts: int,
            result_percentage: float, topic_obj: Type[Topic] | None) -> Report | None:
        """
        Создать новый отчёт Report с результатами статистики на основе текущих попыток Attempt пользователя.

        :param session: Пользовательская сессия
        :param user_id: id пользователя User
        :param test_type: Тип теста
        :param total_attempts: Всего попыток (по типу теста)
        :param correct_attempts: Верных попыток
        :param result_percentage: % результата
        :param topic_obj: Тема Topic (если выбрана) или None
        :return: Объект созданного отчёта Report или None при ошибке
        """

        # Определяем id и название темы
        topic_id = topic_obj.id if topic_obj else None
        topic_name = topic_obj.name if topic_obj else None

        # Подсчитываем общее количество слов в теме или всех темах (если не выбрана тема)
        if topic_id:
            query = select(func.count(WordPhrase.id)).where(WordPhrase.topic_id == topic_id)
            total_words = await session.execute(query)
        else:
            query = (select(func.count(WordPhrase.id)).join(Topic, Topic.id == WordPhrase.topic_id).
                     where(Topic.user_id == user_id))
            total_words = await session.execute(query)
        total_words = total_words.scalar()

        # Создаем новый отчёт
        new_report = Report(
            user_id=user_id, test_type=test_type, total_attempts=total_attempts, correct_attempts=correct_attempts,
            result_percentage=result_percentage, topic_id=topic_id, topic_name=topic_name, total_words=total_words
        )
        session.add(new_report)

        # Фиксируем изменения в БД
        await session.flush()

        # Присваиваем id созданного отчёта учтенным попыткам Attempt
        query = (update(Attempt).where(
            Attempt.user_id == user_id, Attempt.test_type == test_type, Attempt.report_id == None).
                 values(report_id=new_report.id))
        await session.execute(query)
        await session.commit()
        return new_report

    @staticmethod
    async def check_if_user_has_words(session: AsyncSession, user_id: int) -> bool:
        """
        Проверка, есть ли у пользователя хотя бы одно слово/фраза WordPhrase в БД.

        :param session: Асинхронная сессия SQLAlchemy
        :param user_id: ID пользователя
        :return: True, если есть хотя бы одна запись, иначе False
        """
        query = select(exists().where(WordPhrase.topic_id == Topic.id, Topic.user_id == user_id))
        result = await session.execute(query)
        return result.scalar()

    @staticmethod
    async def get_user_reports(session: AsyncSession, user_id: int, is_desc: bool = False) -> Sequence[Report]:
        """
        Получение отчётов Report пользователя.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param is_desc: Признак сортировки отчётов в обратном порядке
        :return: Последовательность отчётов Report пользователя
                [<app.database.models.Report object at 0x0000021BCE5AE510>, ...]
        """
        query = select(Report).where(Report.user_id == user_id)
        if is_desc:
            query = query.order_by(Report.id.desc())
        result = await session.execute(query)
        return result.scalars().all()

    # USER_CHATS

    @staticmethod
    async def create_user_chat(session: AsyncSession, user_id: int, chat_id: int) -> UserChat:
        """
        Создание записи о привязке ID чата Telegram к пользователю в таблице UserChat.
        Используется для автоматической аутентификации в чате Telegram.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param chat_id: ID чата Telegram
        :return: Созданный объект UserChat
        """
        user_chat = UserChat(user_id=user_id, chat_id=chat_id)
        session.add(user_chat)
        await session.commit()
        return user_chat

    @staticmethod
    async def get_user_chat(session: AsyncSession, chat_id: int) -> Type[UserChat] | None:
        """
        Получение запись с привязкой ID чата Telegram к пользователю в таблице UserChat.

        :param session: Пользовательская сессия
        :param chat_id: ID чата Telegram
        :return: Объект UserChat или None если запись для переданного чата не найдена
        """
        query = select(UserChat).where(UserChat.chat_id == chat_id)
        result = await session.execute(query)
        return result.scalar()

    @staticmethod
    async def check_if_chat_attached_to_another_user(session: AsyncSession, chat_id: int, user_id: int) -> bool:
        """
        Проверка, привязан ли чат Telegram к другому пользователю в таблице UserChat.

        :param session: Пользовательская сессия
        :param chat_id: ID чата Telegram
        :param user_id: ID пользователя
        :return: True, если чат привязан к другому пользователю, иначе False
        """
        query = select(exists().where(UserChat.chat_id == chat_id, UserChat.user_id != user_id))
        result = await session.execute(query)
        return result.scalar()

    @staticmethod
    async def delete_outdated_user_chats(session: AsyncSession, chat_id: int, user_id: int) -> None:
        """
        Удаление неактуальных записей с привязкой ID чата Telegram к пользователю в таблице UserChat.
        Неактуальные - значит произошла новая авторизация другого пользователя в том же чате Telegram.

        :param session: Пользовательская сессия
        :param chat_id: ID чата Telegram
        :param user_id: ID пользователя
        :return: None
        """

        # Находим все неактуальные записи, которые нужно удалить
        query = select(UserChat).where(UserChat.chat_id == chat_id, UserChat.user_id != user_id)
        result = await session.execute(query)
        outdated_user_chats = result.scalars().all()

        # Удаляем найденные записи
        for user_chat in outdated_user_chats:
            await session.delete(user_chat)
        await session.commit()

    async def delete_old_user_chats(self) -> None:
        """
        Удаление устаревших записей с привязкой ID чата Telegram к пользователю в таблице UserChat.
        Срок жизни записи с момента создания привязки уставливается в settings.py.

        :return: None
        """
        async with self.session_maker() as session:

            # Удаляем записи старше установленного в settings.py времени
            expiration_time = datetime.now() - timedelta(
                days=CHAT_AUTOLOGIN_EXPIRE_DAYS) - timedelta(hours=UTC_ADJUSTMENT)

            # Находим все неактуальные записи, которые нужно удалить
            query = select(UserChat).filter(UserChat.created < expiration_time)
            result = await session.execute(query)
            outdated_user_chats = result.scalars().all()

            # Удаляем старые записи
            if outdated_user_chats:
                try:
                    for user_chat in outdated_user_chats:
                        await session.delete(user_chat)
                    await session.commit()
                except Exception as e:
                    print(f'Ошибка при удалении неактуальных записей UserChat: {e}')

    # USER SETTINGS

    @staticmethod
    async def create_user_settings(session: AsyncSession, user_id: int) -> None:
        """
        Создание записи о настройках пользователя в таблице UserSettings.
        Настройки проставляются по умолчанию.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя
        :return: None
        """
        obj = UserSettings(user_id=user_id)
        session.add(obj)
        await session.commit()

    @staticmethod
    async def get_user_settings(session: AsyncSession, user_id: int) -> Type[UserSettings] | None:
        """
        Получить запись с персональными настройками пользователя из таблицы UserSettings.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя
        :return: Объект UserSettings с настройками или None если запись не найдена
        """
        query = select(UserSettings).where(UserSettings.user_id == user_id)
        result = await session.execute(query)
        return result.scalar()

    @staticmethod
    async def update_user_settings(session: AsyncSession, user_id: int, **kwargs) -> bool:
        """
        Обновление записи с персональными настройками пользователя в таблице UserSettings.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param kwargs: Параметры для обновления
        :return: True если обновление прошло успешно
        """
        query = update(UserSettings).where(UserSettings.user_id == user_id).values(**kwargs)
        await session.execute(query)
        await session.commit()
        return True

    # NOTES

    @staticmethod
    async def get_user_notes(session: AsyncSession, user_id: int, search_filter: str | None = None,
                             ordering_asc: bool = False) -> Sequence[Notes] | None:
        """
        Получить все заметки Notes пользователя User.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param search_filter: Фильтр поиска
        :param ordering_asc: Порядок сортировки, по умолчанию asc False
        :return: None
        """

        # Получаем все заметки пользователя с подгруженными примерами
        query = (select(Notes).where(Notes.user_id == user_id).options(selectinload(Notes.examples)))

        # Обрабатываем фильтры поиска. Используем REGEXP для корректного поиска для кириллицы без учёта регистра
        if search_filter:
            safe_filter = re.escape(search_filter)                          # Экранируем спецсимволы, если нужно
            query = query.filter(
                or_(
                    Notes.title.op("REGEXP")(safe_filter),
                    Notes.text.op("REGEXP")(safe_filter),
                )
            )

        # Устанавливаем порядок сортировки
        if not ordering_asc:
            query = query.order_by(desc(Notes.id))

        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_note_by_id(session: AsyncSession, note_id: int) -> Notes | None:
        """
        Получить заметку Notes по ID (с подгруженными примерами Context).

        :param session: Пользовательская сессия
        :param note_id: ID заметки Notes
        :return: Объект заметки Notes или None
        """
        query = select(Notes).where(Notes.id == note_id).options(selectinload(Notes.examples))
        note = await session.execute(query)
        return note.scalar_one()

    @staticmethod
    async def get_note_by_data(session: AsyncSession, title: str, text: str, user_id: int) -> Notes | None:
        """
        Получить заметку Notes по переданным данным.

        :param session: Пользовательская сессия
        :param title: Значение заголовка
        :param text: Значение текста заметки
        :param user_id: ID пользователя User
        :return:
        """
        query = (select(Notes).where(
            Notes.title == title,
            func.instr(text, Notes.text) > 0,           # Если был дополнен текст заметки, не дублируем, а обновляем
            Notes.user_id == user_id)
        )
        result = await session.execute(query)
        return result.scalars().first()

    @staticmethod
    async def delete_note_by_id(session: AsyncSession, note_id: int) -> str | None:
        """
        Удалить заметку Notes по ID.

        :param session: Пользовательская сессия
        :param note_id: ID заметки Notes
        :return: Заголовок удаленной заметки, если удаление прошло успешно, иначе None
        """
        note = await session.get(Notes, note_id)
        if note:
            note_title = str(note.title)
            await session.delete(note)
            await session.commit()
            return note_title
        return None

    @staticmethod
    async def create_new_note(session: AsyncSession, user_id: int, note_data: dict, **kwargs) -> Notes:
        """
        Создание новой заметки Notes.
        Создаёт новый объект заметки + пример Context (если передан текст, а не заглушка) и возвращает созданный объект
        с подгруженными примерами.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param note_data: Данные для создания заметки
        :return: Созданный объект заметки Notes
        """
        new_note = Notes(user_id=user_id, title=note_data.get('title'), text=note_data.get('text'))
        session.add(new_note)
        await session.flush()

        # Создаём запись примера Context или сохраняем запись заметки без него
        if note_data.get('examples') != PLUG_TEMPLATE:
            await DataBase.create_context_example(
                session, note_id=new_note.id, data={'context': note_data.get('examples')}
            )
        else:
            await session.commit()

        # Перезагружаем объект с подгруженными связями и возвращаем его
        query = select(Notes).where(Notes.id == new_note.id).options(selectinload(Notes.examples))
        result = await session.execute(query)
        new_note = result.scalar_one()

        return new_note

    @staticmethod
    async def update_note_by_id(session: AsyncSession, note_id: int, **kwargs) -> bool:
        """
        Обновление записи заметки Notes в таблице Notes.

        :param session: Пользовательская сессия
        :param note_id: ID заметки Notes
        :param kwargs: Параметры для обновления
        :return: True если обновление прошло успешно
        """
        query = update(Notes).where(Notes.id == note_id).values(**kwargs)
        await session.execute(query)
        await session.commit()
        return True

    # AUDIOS

    @staticmethod
    async def save_file_path_to_audio(session: AsyncSession, file_path: str, user_id: int) -> SavedAudio:
        """
        Сохранение пути к аудиофайлу в таблице SavedAudio.
        Сам аудиофайл сохраняется в другом модуле, здесь фиксируется в БД путь к нему.

        :param session: Пользовательская сессия
        :param file_path: Путь к сохранённому аудиофайлу
        :param user_id: ID пользователя User
        :return: Объект SavedAudio
        """
        new_audio = SavedAudio(file_path=file_path, user_id=user_id)
        session.add(new_audio)
        await session.commit()
        return new_audio

    @staticmethod
    async def get_all_saved_audios(session: AsyncSession, user_id: int, filter_date: str = None) -> Sequence[SavedAudio]:
        """
        Получить все сохранённые аудиофайлы пользователя.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :param filter_date: Дата в формате 'YYYY-MM-DD'
        :return: Список объектов SavedAudio
        """
        query = select(SavedAudio).where(SavedAudio.user_id == user_id)

        # Если передан фильтр по дате, применяем
        if filter_date:
            filter_date = datetime.strptime(filter_date, "%Y-%m-%d").date()
            query = query.where(func.date(SavedAudio.created) == filter_date)

        result = await session.execute(query)
        return result.scalars().all()

    @staticmethod
    async def get_audio_dates_and_count(session: AsyncSession, user_id: int) -> Sequence[Row[tuple[datetime, int]]]:
        """
        Получить список всех дат сохранённых аудиофайлов пользователя + количество аудио за каждую дату.

        :param session: Пользовательская сессия
        :param user_id: ID пользователя User
        :return: Список кортежей (дата в формате 'YYYY-MM-DD', количество аудио за дату)
        """
        query = (select(
            func.date(SavedAudio.created),
            func.count(SavedAudio.id).label('count')
        )
                 .where(SavedAudio.user_id == user_id)
                 .group_by(func.date(SavedAudio.created))
                 .order_by(func.date(SavedAudio.created).desc())
                 )
        result = await session.execute(query)
        return result.all()

    @staticmethod
    async def delete_audio_by_id(session: AsyncSession, audio_id: int) -> str | None:
        """
        Удаление аудиофайла из таблицы SavedAudio по его ID.

        :param session: Пользовательская сессия
        :param audio_id: ID аудиофайла SavedAudio
        :return: Путь к аудиофайлу если удаление из БД прошло успешно (для передачи на удаление из файловой системы)
        """
        query = await session.get(SavedAudio, audio_id)

        # Получаем путь к аудиофайлу
        file_name = None
        if query is not None:
            file_name = str(query.file_path)

        await session.delete(query)
        await session.commit()
        return file_name
