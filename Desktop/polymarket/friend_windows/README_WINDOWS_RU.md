# Polymarket copier для Windows

Эта версия уже настроена на копирование кошелька:

- `0x6e1d5040d0ac73709b0621f620d2a60b80d2d0f`

Профиль на Polymarket:

- [https://polymarket.com/@0x6e1d5040d0ac73709b0621f620d2a60b80d2d0f](https://polymarket.com/@0x6e1d5040d0ac73709b0621f620d2a60b80d2d0f)
- [https://polymarket.com/profile/%400x6e1d5040d0ac73709b0621f620d2a60b80d2d0f](https://polymarket.com/profile/%400x6e1d5040d0ac73709b0621f620d2a60b80d2d0f)

## Что нужно сделать

1. Установить Python 3.13 для Windows:
   [https://www.python.org/downloads/windows/](https://www.python.org/downloads/windows/)
2. При установке поставить галочку `Add Python to PATH`.
3. Открыть папку проекта.
4. Дважды кликнуть `setup_windows.bat`.
5. После установки открыть файл `.env`.
6. Вписать туда свои ключи Polymarket.
7. Дважды кликнуть `run_windows.bat`.

## Если совсем по-простому

- `setup_windows.bat` делает установку один раз.
- `run_windows.bat` запускает бота потом каждый раз.
- Список копируемых кошельков лежит в `wallets.txt`.
- Если нужен другой кошелёк, просто замени адрес в `wallets.txt`.

## Важно

- В проекте больше нет рабочих ключей: свои нужно вставить в `.env` вручную.
- Не отправляй никому свой `.env`.
- Не запускай два окна `run_windows.bat` одновременно.
- Если Windows покажет ошибку про Python, переустанови Python и включи `Add Python to PATH`.
