"""
Списки с командами для бота (разделение по типам чата).
"""
from aiogram.types import BotCommand


# Список команд, отображаемых в меню команд приватного чата пользователя с ботом
private = [
    BotCommand(command='start', description='Начало работы'),
]
