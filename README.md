# ORACLE-T (Тендеры)

Рабочий репозиторий проекта по автоматизации работы с тендерами.

## Структура

| Каталог | Что это |
|---|---|
| `oracle-t/` | Основное приложение: FastAPI-бэкенд (`backend/`) и React+Vite фронтенд (`frontend/`). Подробности — в [oracle-t/README.md](oracle-t/README.md) и [oracle-t/ARCHITECTURE.md](oracle-t/ARCHITECTURE.md) |
| `ТЗ/` | Техническое задание, описание процесса, документация заказчика |
| `transcriber/` | Утилита расшифровки записей встреч (Whisper) |
| `ORACLE-T.html` | Статический прототип интерфейса |

## Запуск

Конфигурация окружения — `oracle-t/.env` (шаблон: [oracle-t/.env.example](oracle-t/.env.example)).
Скрипт запуска локального стенда — `oracle-t/run.sh`.

## Что не хранится в репозитории

Секреты (`.env`, `.credentials_key`), виртуальные окружения, `node_modules`,
логи, каталог загруженных документов `backend/storage/` и выводы транскрибера.
