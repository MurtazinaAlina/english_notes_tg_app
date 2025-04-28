"""
Создание чата с GIGACHAT
"""
from langchain_gigachat import GigaChat

from app.settings import GIGA_AUTH, GIGA_SCOPE


# Создание чата с GIGACHAT
def create_gigachat_assistant() -> GigaChat:
    """
    Создание чата с GIGACHAT.
    :return: Объект GigaChat с доступом к GIGACHAT SBER
    """
    giga_chat = GigaChat(
            credentials=GIGA_AUTH,
            scope=GIGA_SCOPE,
            verify_ssl_certs=False
        )
    return giga_chat
