#!/bin/bash
# КОНВЕЙЕР — панель в браузере одним щелчком (macOS: двойной клик; Linux: ./Запустить_панель.command)
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Не найден Python 3.11+ (python.org или brew install python)."; read -r -p "Enter…"; exit 1; fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Первый запуск: создаю окружение .venv и ставлю конвейер…"
  "$PY" -m venv .venv || { read -r -p "Не удалось создать окружение. Enter…"; exit 1; }
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -e ".[llm,dev]" || { read -r -p "Установка не удалась. Enter…"; exit 1; }
fi
.venv/bin/python -c "import konveyer" 2>/dev/null || .venv/bin/python -m pip install -e ".[llm,dev]"
echo "Панель: http://127.0.0.1:8765  (Ctrl+C или закрыть окно — остановить)"
exec .venv/bin/python -m konveyer panel "$@"
