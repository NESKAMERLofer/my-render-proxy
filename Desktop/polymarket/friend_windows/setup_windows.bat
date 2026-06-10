@echo off
cd /d "%~dp0"

py -3 -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example
)

echo.
echo Setup complete.
echo 1. Open .env and paste your own Polymarket keys.
echo 2. If you do not use Telegram, BOT_TOKEN can stay empty.
echo 3. Double-click run_windows.bat to start the copier.
pause
