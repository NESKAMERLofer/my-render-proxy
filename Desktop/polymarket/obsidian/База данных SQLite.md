# База данных SQLite

Основная база данных проекта — `polymarket.db` (или `DATABASE_PATH` из `config.py`).

## Структура таблиц

### 1. `trades` — история сделок

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER | Уникальный ID сделки (PK) |
| `ts` | TEXT | Время записи (ISO 8601, UTC) |
| `wallet` | TEXT | Адрес кошелька (например, `0x123...abc`) |
| `action` | TEXT | BUY / SELL |
| `asset` | TEXT | Актив (например, `ETH`, `BTC`) |
| `price_cents` | REAL | Цена в центах (0–100) |
| `amount_usd` | REAL | Объём в USD |
| `market` | TEXT | Название рынка |
| `insider_type` | TEXT | Тип инсайдера (опционально) |
| `note` | TEXT | Дополнительные заметки |
| `raw` | TEXT | Исходный текст сообщения |

**Индексы:**
- `idx_trades_wallet` — по `wallet`
- `idx_trades_ts` — по `ts`

### 2. `wallet_stats` — агрегированная статистика кошельков

| Поле | Тип | Описание |
|------|-----|----------|
| `wallet` | TEXT | Адрес кошелька (PK) |
| `first_seen` | TEXT | Время первой сделки (ISO 8601) |
| `last_seen` | TEXT | Время последней сделки (ISO 8601) |
| `total_trades` | INTEGER | Общее количество сделок |
| `buy_count` | INTEGER | Количество BUY |
| `sell_count` | INTEGER | Количество SELL |
| `buy_volume` | REAL | Общий объём BUY в USD |
| `sell_volume` | REAL | Общий объём SELL в USD |
| `realized_pnl` | REAL | Прибыль/убыток: `sell_volume - buy_volume` (приблизительно) |

**Особенности:**
- `realized_pnl` — это приблизительная оценка, основанная на разнице объёмов (не учитывает рыночную стоимость оставшихся позиций)
- Обновляется при каждой новой сделке через `_update_wallet_stats()`

## Пример данных

### `trades`

| id | ts | wallet | action | asset | price_cents | amount_usd | market |
|----|----|--------|--------|-------|-------------|------------|--------|
| 1 | 2026-04-18T10:00:00Z | 0x123...abc | BUY | ETH | 78 | 1.82 | ETH vs USD |
| 2 | 2026-04-18T10:05:00Z | 0x456...def | SELL | BTC | 62 | 2.10 | BTC 24hr |

### `wallet_stats`

| wallet | first_seen | last_seen | total_trades | buy_count | sell_count | buy_volume | sell_volume | realized_pnl |
|--------|------------|-----------|--------------|-----------|------------|------------|-------------|--------------|
| 0x123...abc | 2026-04-18T10:00:00Z | 2026-04-18T10:00:00Z | 1 | 1 | 0 | 1.82 | 0.00 | -1.82 |
| 0x456...def | 2026-04-18T10:05:00Z | 2026-04-18T10:05:00Z | 1 | 0 | 1 | 0.00 | 2.10 | 2.10 |

## Запросы

### Получить все сделки кошелька

```sql
SELECT * FROM trades WHERE wallet = "0x123...abc" ORDER BY ts DESC;
```

### Получить топ-5 кошельков по прибыли

```sql
SELECT * FROM wallet_stats ORDER BY realized_pnl DESC LIMIT 5;
```

### Получить общую статистику

```sql
SELECT 
  COUNT(*) AS total_trades,
  COUNT(DISTINCT wallet) AS total_wallets,
  SUM(amount_usd) AS total_volume,
  SUM(CASE WHEN action = 'BUY' THEN amount_usd ELSE 0 END) AS buy_volume,
  SUM(CASE WHEN action = 'SELL' THEN amount_usd ELSE 0 END) AS sell_volume
FROM trades;
```

## Резервное копирование

Регулярно копируйте `polymarket.db` в безопасное место:

```bash
cp polymarket.db polymarket.db.backup.$(date +%Y%m%d)
```

## Рекомендации

- Не редактируйте базу вручную — всё управление через код
- Делайте бэкапы перед запуском новых версий
- Для анализа используйте `analyzer.py`, а не SQL вручную
- Проверяйте целостность базы через `sqlite3 polymarket.db ".schema"`

> **Важно:** База данных — основной источник истины для аналитики. Её повреждение приведёт к потере всей истории трейдинга.