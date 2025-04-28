"""
Планировщик задач.
"""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


async def delete_old_pass_reset_tokens_task(db):
    """ Удаление старых токенов сброса пароля из БД. """
    await db.delete_expired_tokens_pass_reset()


async def delete_old_user_chats_task(db):
    """ Удаление старых записей UserChat из БД. """
    await db.delete_old_user_chats()


async def schedule_tasks(db):
    """ Планировщик задач с использованием APScheduler. """

    # Создаем планировщик для асинхронных задач
    scheduler = AsyncIOScheduler()

    # Добавляем задачу, определяем интервал и передаём аргументы
    scheduler.add_job(delete_old_pass_reset_tokens_task, IntervalTrigger(minutes=10), args=[db])
    scheduler.add_job(delete_old_user_chats_task, IntervalTrigger(days=1), args=[db])

    # Запускаем планировщик
    scheduler.start()
    while True:
        await asyncio.sleep(60)                                     # Задержка 1 минута
