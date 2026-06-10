#!/bin/bash
# Автоперезапуск listener если упал (кроме бана)

source "$(dirname "$0")/venv/bin/activate"
cd "$(dirname "$0")"

while true; do
    echo "[$(date)] Запускаю listener.py..."
    python listener.py
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[$(date)] Остановлен вручную. Выход."
        break
    fi

    echo "[$(date)] Упал с кодом $EXIT_CODE. Перезапуск через 30 сек..."
    sleep 30
done
