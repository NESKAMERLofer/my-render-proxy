# Проект Polymarket

> Автоматизированный трейдинг на Polymarket через копирование сделок инсайдеров

## Обзор

Этот проект представляет собой систему для автоматического копирования сделок с кошельков инсайдеров на платформе Polymarket. Система состоит из двух основных компонентов:

- `copy_trader.py` — бэкенд, мониторящий кошельки и копирующий сделки через CLOB API
- `bot.py` — Telegram-бот, принимающий и сохраняющий сигналы от PMTrackBot

Используется SQLite для хранения истории сделок и аналитики.

## Архитектура

```mermaid
graph TD
    A[PMTrackBot (Telegram)] --> B(bot.py)
    B --> C[SQLite: trades, wallet_stats]
    C --> D[analyzer.py]
    D --> E[Telegram: /stats, /top, /wallet]
    F[Кошельки инсайдеров] --> G[copy_trader.py]
    G --> H[Polymarket CLOB API]
    H --> I[Позиции и ордера на Polymarket]
    G --> C
```

## Компоненты

### 1. `copy_trader.py`

Автоматически отслеживает кошельки из `wallets.txt` и копирует их сделки:

- Поддерживает BUY/SELL
- Использует CLOB API Polymarket
- Имеет систему take-profit и profit-take
- Ограничивает копирование по ключевым словам и ценовым диапазонам
- Работает в режиме "один экземпляр" через lock-файл
- Сохраняет историю сделок в `seen_trade_ids.json`

### 2. `bot.py`

Telegram-бот, принимающий сигналы от @PMTrackBot:

- Принимает текстовые сообщения с сигналами
- Парсит их через `parser.py`
- Сохраняет в SQLite базу данных
- Предоставляет команды:
  - `/start` — приветствие
  - `/stats` — общая статистика
  - `/top [N]` — топ трейдеров по PnL
  - `/wallet <addr>` — анализ кошелька
  - `/recent [N]` — последние сделки
  - `/report` — полный отчёт

### 3. `database.py`

SQLite-база данных с двумя таблицами:

- `trades`: история всех сделок с полями `ts`, `wallet`, `action`, `asset`, `price_cents`, `amount_usd`, `market`
- `wallet_stats`: агрегированная статистика по кошелькам — `total_trades`, `buy_volume`, `sell_volume`, `realized_pnl`

### 4. `analyzer.py`

Формирует текстовые отчёты на основе данных из базы:

- `format_global_report()` — общая статистика проекта
- `format_top_report(limit=10)` — топ N трейдеров
- `format_wallet_report(wallet)` — детализированный анализ кошелька

### 5. `config.py`

Основные настройки:

- `BOT_TOKEN` — токен Telegram-бота
- `ADMIN_CHAT_ID` — идентификатор разрешённого чата
- `DATABASE_PATH` — путь к SQLite-файлу (по умолчанию `polymarket.db`)

### 6. `requirements.txt`

Зависимости:

```txt
python-telegram-bot==21.9
telethon
aiosqlite==0.19.0
pandas==2.2.0
tabulate==0.9.0
python-dotenv==1.0.0
```

### 7. `wallets.txt`

Список кошельков для мониторинга (по одному на строку, начинаются с `0x`):

> Пример:
> 0x123...abc
> 0x456...def

### 8. `.env`

Переменные окружения для `copy_trader.py`:

```env
POLY_PRIVATE_KEY=...
POLY_API_KEY=...
POLY_SECRET=...
POLY_PASSPHRASE=...
POLY_FUNDER_ADDRESS=...
COPY_NOTIONAL_USD=1.05
MIN_ORDER_SIZE=5.5
POLL_INTERVAL_SEC=30
TAKE_PROFIT_ENABLE=1
TAKE_PROFIT_TRIGGER_PCT=0.20
TAKE_PROFIT_LOCK_PCT=0.10
...
```

## Запуск

### 1. Установка зависимостей

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Настройка `.env`

Создайте файл `.env` в корне проекта с ключами Polymarket и настройками.

### 3. Запуск бота

```bash
python bot.py
```

### 4. Запуск copy_trader

```bash
python copy_trader.py
```

## Безопасность

- Ключи Polymarket хранятся в `.env`, не коммитятся в Git
- Логи сохраняются в `copy_trader.log` и `copy_trader.supervisor.log`
- Используется `lock`-файл для предотвращения дублирования процессов

## Мониторинг

- `copy_trader.health.json` — статус работы
- `take_profit_state.json` — состояние take-profit ордеров
- Telegram-бот предоставляет интерфейс для анализа

## Дополнительные файлы

- `run.sh` — скрипт запуска
- `launchd.copy_trader.plist` — автозапуск на macOS
- `run_copy_trader_forever.sh` — цикл перезапуска
- `seen_trade_ids.json` — история обработанных сделок
- `copy_trader.supervisor.nohup.out` — логи работы в фоне

## Технические детали

- Язык: Python 3.13
- Библиотеки: `py_clob_client`, `requests`, `telegram`, `sqlite3`
- Протокол: Polymarket CLOB API (Polygon)
- Целевая платформа: macOS

## Рекомендации по развитию

- Добавить уведомления через Discord/Email
- Реализовать веб-панель управления
- Добавить историю цен активов
- Интеграция с TradingView для анализа графиков
- Поддержка нескольких сетей (например, Base)

> **Важно:** Этот проект требует внимательного управления рисками и достаточного капитала для покрытия комиссий и проскальзывания.
