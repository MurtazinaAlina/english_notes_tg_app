"""
Обработчики роутера для поведения в групповом чате.
"""
from aiogram import F, types, Router

from app.filters.custom_filters import ChatTypeFilter


user_group_router = Router()

# Настраиваем, что роутер работает строго в чатах групп
user_group_router.message.filter(ChatTypeFilter(['group', 'supergroup']))
user_group_router.edited_message.filter(ChatTypeFilter(['group', 'supergroup']))

# Здесь можно реализовать желаемую логику поведения бота при нахождении в групповом чате


# Тестовый handler
@user_group_router.message(F.text)
async def moderate_msg(message: types.Message) -> None:
    await message.answer('Keep calm and HERRACH (с)')
