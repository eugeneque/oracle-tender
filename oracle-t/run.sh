#!/usr/bin/env bash
# Запуск Sova Scanner для разработки без Docker: Postgres (Homebrew) + backend (Uvicorn) + frontend (Vite).
# Использование: ./run.sh   (остановить — Ctrl+C или просто закрыть окно терминала)
#
# Адреса всегда одни и те же: http://localhost:5173 и http://localhost:8000. Порт можно
# переопределить переменными BACKEND_PORT/FRONTEND_PORT, но по умолчанию скрипт занимает
# именно эти — «плавающий» адрес означает, что открытая вкладка показывает прошлый стенд.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PG_BIN="/opt/homebrew/opt/postgresql@15/bin"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
# Автоперезагрузка backend при правке кода. Отключается: RELOAD=0 ./run.sh
RELOAD="${RELOAD:-1}"

# Управление задачами: с ним каждый фоновый процесс получает собственную группу, и её можно
# погасить целиком. Без этого `kill $!` убивает только оболочку-обёртку, а Uvicorn с его
# перезагрузчиком и Vite с его node остаются жить и держать порт — ровно то, из-за чего
# следующий запуск уезжал на 5174.
set -m

# --- Освобождение портов --------------------------------------------------------------

# Порт после прошлого запуска бывает занят: скрипт убили -9, окно закрыли раньше, чем
# отработала уборка. Свой процесс дожимаем молча, чужой не трогаем — на 5173 может висеть
# другой проект, и убивать его от имени этого скрипта нельзя.
free_port() {
  local port="$1" label="$2" var="$3"
  local pids
  pids="$(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null || true)"
  [ -z "$pids" ] && return 0

  local pid cmd cwd
  for pid in $pids; do
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    if [[ "$cwd" == "$ROOT_DIR"* ]]; then
      echo "    Порт $port занят прошлым запуском ($label, PID $pid) — останавливаю"
      kill "$pid" 2>/dev/null || true
    else
      echo "" >&2
      echo "!!! Порт $port занят посторонним процессом (PID $pid):" >&2
      echo "    $cmd" >&2
      echo "    Освободите порт или задайте другой: $var=... ./run.sh" >&2
      exit 1
    fi
  done

  for _ in $(seq 1 20); do
    lsof -ti "tcp:$port" -sTCP:LISTEN >/dev/null 2>&1 || return 0
    sleep 0.25
  done
  # Не отреагировал на TERM за пять секунд — значит уже не отреагирует.
  kill -9 $(lsof -ti "tcp:$port" -sTCP:LISTEN 2>/dev/null) 2>/dev/null || true
  sleep 0.5
}

# --- Уборка ----------------------------------------------------------------------------

cleanup() {
  # Снимаем ловушку сразу: уборка сама завершает процессы и не должна вызвать себя повторно.
  trap - EXIT INT TERM HUP
  echo ""
  echo "==> Останавливаю backend и frontend"
  local pid
  for pid in "${FRONTEND_PID:-}" "${BACKEND_PID:-}"; do
    [ -n "$pid" ] || continue
    # Минус перед номером — сигнал всей группе: у Uvicorn это ещё и процесс-перезагрузчик,
    # у Vite — порождённый им node.
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done

  for _ in $(seq 1 20); do
    local alive=0
    for pid in "${FRONTEND_PID:-}" "${BACKEND_PID:-}"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" = "0" ] && break
    sleep 0.25
  done

  for pid in "${FRONTEND_PID:-}" "${BACKEND_PID:-}"; do
    [ -n "$pid" ] || continue
    kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  done
}

# HUP здесь — это и есть «закрыли окно терминала»: без него оболочка умирала молча, а
# backend с frontend оставались висеть на портах до перезагрузки машины.
trap cleanup EXIT INT TERM HUP

# --- Подготовка окружения ---------------------------------------------------------------

echo "==> Проверяю PostgreSQL"
if ! "$PG_BIN/pg_isready" >/dev/null 2>&1; then
  echo "    Postgres не запущен, запускаю (brew services)..."
  brew services start postgresql@15
  for i in $(seq 1 20); do
    "$PG_BIN/pg_isready" >/dev/null 2>&1 && break
    sleep 1
  done
fi
"$PG_BIN/pg_isready"

if [ ! -f "$ROOT_DIR/.env" ]; then
  echo "==> .env не найден, создаю из .env.example (замените секреты для боевого использования)"
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
fi
cp "$ROOT_DIR/.env" "$BACKEND_DIR/.env"

if ! "$PG_BIN/psql" -lqt | cut -d '|' -f 1 | grep -qw oraclet; then
  echo "==> Создаю базу данных oraclet"
  "$PG_BIN/createuser" -s oraclet 2>/dev/null || true
  "$PG_BIN/psql" -d postgres -c "ALTER USER oraclet WITH PASSWORD 'oraclet';" >/dev/null
  "$PG_BIN/createdb" -O oraclet oraclet
fi

echo "==> Проверяю backend venv"
if [ ! -d "$BACKEND_DIR/.venv" ]; then
  echo "    Создаю venv и ставлю зависимости (первый запуск, займёт минуту)..."
  /opt/homebrew/bin/python3.11 -m venv "$BACKEND_DIR/.venv"
  "$BACKEND_DIR/.venv/bin/pip" install --quiet --upgrade pip
  "$BACKEND_DIR/.venv/bin/pip" install --quiet -r "$BACKEND_DIR/requirements-dev.txt"
  echo "    Ставлю браузер Playwright (нужен части адаптеров источников, раздел 4.1 ТЗ)..."
  "$BACKEND_DIR/.venv/bin/playwright" install chromium
fi

echo "==> Применяю миграции"
(cd "$BACKEND_DIR" && "./.venv/bin/alembic" upgrade head)

echo "==> Проверяю frontend node_modules"
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "    Ставлю npm-зависимости (первый запуск, займёт минуту)..."
  (cd "$FRONTEND_DIR" && npm install --silent)
fi

echo "==> Освобождаю порты $BACKEND_PORT и $FRONTEND_PORT"
free_port "$BACKEND_PORT" backend BACKEND_PORT
free_port "$FRONTEND_PORT" frontend FRONTEND_PORT

# --- Запуск ------------------------------------------------------------------------------

echo "==> Запускаю backend (http://localhost:$BACKEND_PORT)"
# `exec` обязателен: без него $! указывает на оболочку-обёртку, а не на сам Uvicorn, и
# останавливать оказывается некого.
if [ "$RELOAD" = "1" ]; then
  (cd "$BACKEND_DIR" && exec "./.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload) &
else
  (cd "$BACKEND_DIR" && exec "./.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$BACKEND_PORT") &
fi
BACKEND_PID=$!

# Ждём именно готовности API, а не «две секунды на всякий случай»: миграции и первый импорт
# на холодной машине занимают дольше, и фронт успевал открыться раньше живого backend.
echo -n "    жду ответа API"
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    echo " — готов"
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo "!!! Backend не запустился — причина в строках выше" >&2
    exit 1
  fi
  echo -n "."
  sleep 0.5
done

echo "==> Запускаю frontend (http://localhost:$FRONTEND_PORT)"
(cd "$FRONTEND_DIR" && exec npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
FRONTEND_PID=$!

echo ""
echo "================================================================"
echo " Готово. Открой в браузере:  http://localhost:$FRONTEND_PORT"
echo " API:                        http://localhost:$BACKEND_PORT/health"
echo " Логин/пароль администратора — из .env (BOOTSTRAP_ADMIN_*)"
echo " Остановить: Ctrl+C или закрыть это окно терминала"
echo "================================================================"

wait
