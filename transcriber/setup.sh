#!/usr/bin/env bash
# Установка окружения транскрибатора (macOS / Apple Silicon).
set -euo pipefail
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "Нужен ffmpeg: brew install ffmpeg"; exit 1; }
PY=$(command -v python3.11 || command -v python3.12 || command -v python3)

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt

echo "Готово. Запуск:  ./transcriber/.venv/bin/python transcribe.py видео.mp4"
