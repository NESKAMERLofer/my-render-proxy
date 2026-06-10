# Как получить ключи для Polymarket

Ниже инструкция только для **своих** ключей. Чужие ключи получать или использовать нельзя.

## Что нужно для этого проекта

В файле `.env` нужны эти поля:

```env
POLY_FUNDER_ADDRESS=
POLY_PRIVATE_KEY=
POLY_API_KEY=
POLY_SECRET=
POLY_PASSPHRASE=
POLY_SIGNATURE_TYPE=
```

## 1. Получить `POLY_PRIVATE_KEY`

Есть два сценария.

### Вариант A. У тебя обычный EOA-кошелёк

Пример: MetaMask, Rabby и похожие кошельки, где сид-фраза/ключ у тебя под контролем.

Что делать:

1. Открой свой кошелёк.
2. Найди `Account details`, `Export private key` или похожий пункт.
3. Экспортируй приватный ключ именно того адреса, с которого будешь работать.
4. Вставь его в `.env` как:

```env
POLY_PRIVATE_KEY=0x...
POLY_SIGNATURE_TYPE=0
```

Для EOA-кошелька `POLY_FUNDER_ADDRESS` обычно равен адресу этого же кошелька.

## 2. Получить `POLY_FUNDER_ADDRESS`

### Если `POLY_SIGNATURE_TYPE=0`

Тогда `POLY_FUNDER_ADDRESS` — это просто адрес твоего обычного кошелька.

Пример:

```env
POLY_FUNDER_ADDRESS=0x1234...
POLY_SIGNATURE_TYPE=0
```

### Если аккаунт создан через Polymarket email/login

В документации Polymarket указано, что у таких пользователей средства лежат в proxy wallet, и funder address нужно брать из `polymarket.com/settings` или из адреса, который показывает профиль. Если proxy wallet ещё не создан, он появляется после первого входа в Polymarket.

Тогда:

1. Зайди в [Polymarket Settings](https://polymarket.com/settings)
2. Посмотри адрес кошелька в профиле
3. Вставь его в:

```env
POLY_FUNDER_ADDRESS=0x...
```

## 3. Получить `POLY_PRIVATE_KEY`, если вход был через email

По официальной справке Polymarket это применимо для пользователей, которые регистрировались через email. Экспорт делается через Magic Link:

1. Войди в свой аккаунт Polymarket
2. Открой ссылку: [https://reveal.magic.link/polymarket](https://reveal.magic.link/polymarket)
3. Пройди вход в Magic Link
4. Нажми экспорт приватного ключа
5. Сохрани ключ в безопасном месте

Для такого сценария Polymarket пишет, что обычно используется proxy wallet. В документации для proxy-кошельков указаны `signatureType` `1` или `2`, а funder address должен быть адресом кошелька, который показывается на Polymarket.

Если после этого бот выдаёт `Invalid Signature`, почти всегда причина в одном из трёх:

- неверный приватный ключ
- неверный `POLY_SIGNATURE_TYPE`
- неверный `POLY_FUNDER_ADDRESS`

## 4. Получить `POLY_API_KEY`, `POLY_SECRET`, `POLY_PASSPHRASE`

Эти три значения Polymarket рекомендует **создавать или выводить программно** через CLOB client, используя твой приватный ключ.

В этом проекте они нужны для торговли через CLOB API.

### Самый простой способ

1. Установи зависимости через `setup_windows.bat`
2. Открой `.env`
3. Сначала заполни:

```env
POLY_PRIVATE_KEY=0x...
POLY_FUNDER_ADDRESS=0x...
POLY_SIGNATURE_TYPE=0
```

или свои значения для proxy-кошелька.

4. Потом запусти такой Python-код в той же папке проекта:

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key="ТВОЙ_PRIVATE_KEY",
)

creds = client.create_or_derive_api_creds()
print(creds)
```

Ты получишь 3 значения:

- `api_key`
- `secret`
- `passphrase`

После этого вставь их в `.env`:

```env
POLY_API_KEY=...
POLY_SECRET=...
POLY_PASSPHRASE=...
```

## 5. Готовый шаблон `.env`

```env
POLY_FUNDER_ADDRESS=0x...
POLY_PRIVATE_KEY=0x...
POLY_API_KEY=...
POLY_SECRET=...
POLY_PASSPHRASE=...
POLY_SIGNATURE_TYPE=0
```

## 6. Если не работает

Проверь по порядку:

1. `POLY_PRIVATE_KEY` начинается с `0x`
2. `POLY_FUNDER_ADDRESS` совпадает с твоим реальным адресом на Polymarket или с EOA-адресом
3. `POLY_SIGNATURE_TYPE` выбран правильно
4. Если API-ключи потеряны, создай новые
5. Если proxy wallet не существует, сначала просто зайди в аккаунт на Polymarket

## Официальные источники

- Polymarket Authentication: [https://docs.polymarket.com/api-reference/authentication](https://docs.polymarket.com/api-reference/authentication)
- Polymarket Quickstart: [https://docs.polymarket.com/trading/quickstart](https://docs.polymarket.com/trading/quickstart)
- Export private key for email login: [https://help.polymarket.com/en/articles/13364258-how-do-i-export-my-key](https://help.polymarket.com/en/articles/13364258-how-do-i-export-my-key)

## Очень важно

- Никому не отправляй `POLY_PRIVATE_KEY`
- Никому не отправляй `POLY_SECRET`
- Не храни рабочие ключи в переписках
- Не клади `.env` в архив, который отправляешь другим людям
