#!/bin/bash
set -e  # Завершить скрипт при ошибке

echo "Создание директории /code/data, если не существует..."
mkdir -p /code/data

echo "Запуск приложения..."
exec "$@"