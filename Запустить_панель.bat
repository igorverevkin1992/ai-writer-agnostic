@echo off
chcp 65001 >nul
cd /d "%~dp0"
title КОНВЕЙЕР — панель
where py >nul 2>nul && (set "PY=py -3") || (set "PY=python")
if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: создаю окружение .venv и ставлю конвейер...
  %PY% -m venv .venv || (echo Не найден Python 3.11+. Установите с python.org, отметив "Add python.exe to PATH". & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -e ".[llm,dev]" || (echo Установка не удалась. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -c "import konveyer" 2>nul || ".venv\Scripts\python.exe" -m pip install -e ".[llm,dev]"
echo Панель: http://127.0.0.1:8765  (закройте это окно, чтобы остановить)
".venv\Scripts\python.exe" -m konveyer panel %*
pause
