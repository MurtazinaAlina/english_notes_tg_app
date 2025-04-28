"""
Кастомные фильтры для обработки событий.
"""
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from aiogram import types


class ChatTypeFilter(Filter):
    """
    Кастомный фильтр для разделения событий в зависимости от типа чата, в котором они произошли (личный, группа ...).

    При обращении к фильтру будем задавать список типов чатов и проверять, относится ли тип чата сообщения
    к этому списку
    """
    def __init__(self, chat_types: list[str]) -> None:
        self.chat_types = chat_types

    async def __call__(self, message: types.Message) -> bool:
        """ Проверяем, что тип чата у сообщения входит в заданный при инициализации список"""
        return message.chat.type in self.chat_types


# Фильтр для проверки, что в контексте состояния ЕСТЬ переданные атрибуты.
class IsKeyInStateFilter(Filter):

    def __init__(self, *args: str):
        """
        Фильтр для проверки, что в контексте состояния ЕСТЬ переданные атрибуты.
        :param args: Названия ключей для проверки их наличия в контексте состояния
        """
        self.keys = [*args]

    async def __call__(self, message: types.Message, state: FSMContext) -> bool:
        """
        Проверяем, есть ли в контексте состояния атрибуты с указанным названием.
        Если да, возвращаем True, если нет - False.
        """

        # Забираем данные из контекста
        state_data = await state.get_data()

        # Проверяем для каждого переданного названия ключа, есть ли он в FSMContext
        for key in self.keys:
            if state_data.get(key):
                continue

            # Если хоть один ключ отсутствует, возвращаем False
            else:
                return False

        # Если все ключи есть, возвращаем True
        return True


# Фильтр для проверки, что в контексте состояния НЕТ переданных атрибутов.
class IsKeyNotInStateFilter(Filter):

    def __init__(self, *args: str):
        """
        Фильтр для проверки, что в контексте состояния НЕТ переданных атрибутов.
        :param args: Названия ключей для проверки их отсутствия в контексте состояния
        """
        self.keys = [*args]

    async def __call__(self, message: types.Message, state: FSMContext) -> bool:
        """
        Проверяем, есть ли в контексте состояния атрибут с указанным названием.
        Если НЕТ, возвращаем True, если есть - False.
        """

        # Забираем данные из контекста
        state_data = await state.get_data()

        # Проверяем для каждого переданного названия ключа, есть ли он в FSMContext
        for key in self.keys:
            if state_data.get(key):

                # Если хоть один ключ есть, возвращаем False
                return False

        # Если ни одного ключа в FSMContext нет, возвращаем True
        return True
