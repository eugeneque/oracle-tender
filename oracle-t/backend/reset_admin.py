#!/usr/bin/env python
"""Сброс/создание администратора ORACLE-T одной командой.

Запуск (из каталога backend/, при активированном venv):
    python reset_admin.py
    python reset_admin.py --username admin --password MyPass123

Создаёт пользователя-администратора с указанным логином, если его нет, либо перезаписывает
пароль/ФИО существующего — команду можно запускать сколько угодно раз. Итоговые логин/пароль
сохраняются в .env (в корне проекта и в backend/), чтобы их не пришлось запоминать.
"""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.argv = [sys.argv[0], "reset-admin", *sys.argv[1:]]
    sys.exit(main())
