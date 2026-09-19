#!/usr/bin/env bash
#
#  Sova Scanner · установщик
#
#  Ставит систему на чистую машину целиком: системные пакеты, PostgreSQL, Python-окружение
#  backend с браузером Playwright, сборку frontend и — в серверном режиме — службы systemd
#  и nginx. После него программа работает и переживает перезагрузку сервера.
#
#      ./install.sh                  спросит режим и поставит
#      ./install.sh --mode server    сервер: systemd + nginx, автозапуск
#      ./install.sh --mode docker    всё в контейнерах (нужен Docker)
#      ./install.sh --mode local     рабочая машина: зависимости, запуск через run.sh
#      ./install.sh --dry-run        показать план, ничего не менять
#      ./install.sh --help           остальные ключи
#
#  Скрипт идемпотентен: повторный запуск переиспользует уже созданные venv, базу и .env,
#  поэтому его же можно запускать после `git pull` как обновление.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/oracle-t"
BACKEND_DIR="$APP_DIR/backend"
FRONTEND_DIR="$APP_DIR/frontend"
LOG_FILE="$ROOT_DIR/install.log"

MODE=""
ASSUME_YES=0
DRY_RUN=0
FORCE_ASCII=0
SKIP_PLAYWRIGHT=0
BACKEND_PORT="${BACKEND_PORT:-8000}"
HTTP_PORT="${HTTP_PORT:-80}"
WEB_ROOT="/var/www/oracle-t"
SERVICE_NAME="oracle-t-backend"
NGINX_SITE="oracle-t"
PY_MIN_MINOR=11          # минимальная поддерживаемая версия — Python 3.11
NODE_MIN_MAJOR=18        # Vite 5 и tsc 5.6 ниже 18 не собираются

PG_DB="oraclet"
PG_USER="oraclet"
PG_PASS=""               # заполняется при генерации .env
ADMIN_USER="admin"
ADMIN_PASS=""

STEP=0
STEP_TOTAL=0
WARNINGS=0
FRESH_ENV=0

# ──────────────────────────────────────────────────────────────────────────────
#  Оформление
# ──────────────────────────────────────────────────────────────────────────────

setup_style() {
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    TTY=1
    C_OFF=$'\033[0m'; C_B=$'\033[1m'; C_DIM=$'\033[2m'
    C_CYAN=$'\033[38;5;44m'; C_GREEN=$'\033[38;5;42m'; C_YEL=$'\033[38;5;214m'
    C_RED=$'\033[38;5;203m'; C_GREY=$'\033[38;5;245m'; C_VIO=$'\033[38;5;141m'
  else
    TTY=0
    C_OFF=""; C_B=""; C_DIM=""; C_CYAN=""; C_GREEN=""; C_YEL=""; C_RED=""; C_GREY=""; C_VIO=""
  fi

  # Псевдографика уместна только в UTF-8: в POSIX-локали она превращается в кашу из
  # вопросительных знаков, поэтому там честнее ASCII.
  local loc="${LC_ALL:-${LC_CTYPE:-${LANG:-}}}"
  case "$loc" in
    *UTF-8*|*utf-8*|*UTF8*|*utf8*) UNI=1 ;;
    *) UNI=0 ;;
  esac
  [ "$FORCE_ASCII" = 1 ] && UNI=0

  if [ "$UNI" = 1 ]; then
    G_OK="✔"; G_BAD="✖"; G_WARN="▲"; G_SKIP="·"; G_ARROW="›"
    G_V="│"; G_TL="╭"; G_BL="╰"; G_RULE="─"; G_FULL="█"; G_EMPTY="░"
    SPIN=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
  else
    G_OK="+"; G_BAD="x"; G_WARN="!"; G_SKIP="-"; G_ARROW=">"
    G_V="|"; G_TL="+"; G_BL="+"; G_RULE="-"; G_FULL="#"; G_EMPTY="."
    SPIN=("|" "/" "-" "\\")
  fi
}

rule() {
  local i=0 out=""
  while [ "$i" -lt 74 ]; do out="$out$G_RULE"; i=$((i + 1)); done
  printf '%s%s%s\n' "$C_DIM" "$out" "$C_OFF"
}

banner() {
  printf '\n'
  if [ "$UNI" = 1 ]; then
    printf '%s' "$C_CYAN"
    cat <<'ART'
    ██████╗ ██████╗  █████╗  ██████╗██╗     ███████╗    ████████╗
   ██╔═══██╗██╔══██╗██╔══██╗██╔════╝██║     ██╔════╝    ╚══██╔══╝
   ██║   ██║██████╔╝███████║██║     ██║     █████╗  ▄▄▄    ██║
   ██║   ██║██╔══██╗██╔══██║██║     ██║     ██╔══╝  ▀▀▀    ██║
   ╚██████╔╝██║  ██║██║  ██║╚██████╗███████╗███████╗       ██║
    ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚══════╝       ╚═╝
ART
    printf '%s' "$C_OFF"
  else
    printf '%s' "$C_CYAN"
    cat <<'ART'
    ___  ____     _    ____ _     _____    _____
   / _ \|  _ \   / \  / ___| |   | ____|__|_   _|
  | | | | |_) | / _ \| |   | |   |  _| |__| | |
  | |_| |  _ < / ___ \ |___| |___| |___      | |
   \___/|_| \_/_/   \_\____|_____|_____|     |_|
ART
    printf '%s' "$C_OFF"
  fi
  printf '   %sСистема автоматизации работы с тендерами%s  %s·%s  %sустановщик%s\n\n' \
    "$C_B" "$C_OFF" "$C_DIM" "$C_OFF" "$C_GREY" "$C_OFF"
}

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE"; }

bar() {
  local done_pct="$1" width=30 filled i out=""
  filled=$((done_pct * width / 100))
  i=0
  while [ "$i" -lt "$width" ]; do
    if [ "$i" -lt "$filled" ]; then out="$out$G_FULL"; else out="$out$G_EMPTY"; fi
    i=$((i + 1))
  done
  printf '%s' "$out"
}

step() {
  STEP=$((STEP + 1))
  local pct=$((STEP * 100 / STEP_TOTAL))
  printf '\n%s%s%s%s %s[%s/%s]%s %s%s%s\n' \
    "$C_CYAN" "$G_TL" "$G_RULE" "$C_OFF" "$C_GREY" "$STEP" "$STEP_TOTAL" "$C_OFF" "$C_B" "$1" "$C_OFF"
  printf '%s%s%s  %s%s%s %s%3s%%%s\n' \
    "$C_CYAN" "$G_V" "$C_OFF" "$C_VIO" "$(bar "$pct")" "$C_OFF" "$C_DIM" "$pct" "$C_OFF"
  log "=== ШАГ $STEP/$STEP_TOTAL: $1"
}

line()  { printf '%s%s%s  %s %s\n' "$C_CYAN" "$G_V" "$C_OFF" "$1" "$2"; }
ok()    { line "$C_GREEN$G_OK$C_OFF" "$1"; log "OK   $1"; }
skip()  { line "$C_GREY$G_SKIP$C_OFF" "$C_GREY$1$C_OFF"; log "SKIP $1"; }
info()  { line "$C_CYAN$G_ARROW$C_OFF" "$1"; log "INFO $1"; }
warn()  { WARNINGS=$((WARNINGS + 1)); line "$C_YEL$G_WARN$C_OFF" "$C_YEL$1$C_OFF"; log "WARN $1"; }
note()  { printf '%s%s%s      %s%s%s\n' "$C_CYAN" "$G_V" "$C_OFF" "$C_DIM" "$1" "$C_OFF"; }

step_end() { printf '%s%s%s\n' "$C_CYAN" "$G_BL$G_RULE" "$C_OFF"; }

die() {
  printf '\n%s%s %s%s\n\n' "$C_RED$C_B" "$G_BAD" "$1" "$C_OFF"
  log "FAIL $1"
  if [ -s "$LOG_FILE" ]; then
    printf '%sПоследние строки журнала (%s):%s\n' "$C_GREY" "$LOG_FILE" "$C_OFF"
    printf '%s' "$C_DIM"; tail -n 20 "$LOG_FILE" | sed 's/^/    /'; printf '%s\n' "$C_OFF"
  fi
  exit 1
}

# Выполняет шаг с индикатором. Весь вывод команды уходит в журнал: на экране остаётся
# одна строка на действие, а подробности всегда можно посмотреть в install.log.
task() {
  local label="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then
    line "$C_VIO$G_ARROW$C_OFF" "$C_DIM$label$C_OFF"
    note "$*"
    return 0
  fi
  log "--- $label :: $*"
  local start rc
  start=$SECONDS
  set +e
  if [ "$TTY" = 1 ]; then
    ( "$@" ) >>"$LOG_FILE" 2>&1 &
    local pid=$!
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r%s%s%s  %s%s%s %s' "$C_CYAN" "$G_V" "$C_OFF" "$C_VIO" "${SPIN[$((i % ${#SPIN[@]}))]}" "$C_OFF" "$label"
      i=$((i + 1))
      sleep 0.12
    done
    wait "$pid"; rc=$?
    printf '\r%s\r' "                                                                              "
  else
    printf '   ... %s\n' "$label"
    ( "$@" ) >>"$LOG_FILE" 2>&1
    rc=$?
  fi
  set -e
  local took=$((SECONDS - start))
  if [ "$rc" -ne 0 ]; then
    line "$C_RED$G_BAD$C_OFF" "$label"
    die "Шаг «${label}» завершился с ошибкой (код $rc)."
  fi
  if [ "$took" -ge 3 ]; then
    ok "$label $C_DIM(${took}с)$C_OFF"
  else
    ok "$label"
  fi
}

ask() {
  local prompt="$1" default="$2" answer
  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then
    # Подсказка — на экран (stderr), в stdout только ответ: его читает вызывающий через $(...).
    printf '%s%s%s  %s %s%s%s\n' "$C_CYAN" "$G_V" "$C_OFF" "$prompt" "$C_DIM" "$default" "$C_OFF" >&2
    printf '%s' "$default"
    return 0
  fi
  printf '%s%s%s  %s %s[%s]%s ' "$C_CYAN" "$G_V" "$C_OFF" "$prompt" "$C_DIM" "$default" "$C_OFF" >&2
  read -r answer || answer=""
  [ -z "$answer" ] && answer="$default"
  printf '%s' "$answer"
}

confirm() {
  local answer
  answer="$(ask "$1" "${2:-y}")"
  case "$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')" in
    y|yes|д|да) return 0 ;;
    *) return 1 ;;
  esac
}

usage() {
  cat <<'USAGE'
Sova Scanner — установщик.

  ./install.sh [ключи]

Ключи:
  --mode server        сервер: systemd + nginx, автозапуск при перезагрузке (нужен root/sudo)
  --mode docker        всё в контейнерах через docker compose
  --mode local         рабочая машина: зависимости и сборка, запуск через oracle-t/run.sh
  -y, --yes            не задавать вопросов (для CI и автоустановки)
  --dry-run            показать план действий, ничего не устанавливая
  --http-port N        порт веб-интерфейса (по умолчанию 80; для docker и server)
  --backend-port N     порт API (по умолчанию 8000)
  --skip-playwright    не ставить браузер Playwright (~400 МБ; часть источников станет недоступна)
  --ascii              оформление без псевдографики
  -h, --help           эта справка

Переменные окружения: BACKEND_PORT, HTTP_PORT, NO_COLOR.
Журнал установки: install.log в корне проекта.
USAGE
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --mode) MODE="${2:-}"; shift 2 ;;
      --mode=*) MODE="${1#*=}"; shift ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      --ascii) FORCE_ASCII=1; shift ;;
      --skip-playwright) SKIP_PLAYWRIGHT=1; shift ;;
      --http-port) HTTP_PORT="${2:-}"; shift 2 ;;
      --http-port=*) HTTP_PORT="${1#*=}"; shift ;;
      --backend-port) BACKEND_PORT="${2:-}"; shift 2 ;;
      --backend-port=*) BACKEND_PORT="${1#*=}"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'Неизвестный ключ: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
  done
  case "$MODE" in
    ""|server|docker|local) : ;;
    *) printf 'Режим должен быть server, docker или local (получено: %s)\n' "$MODE" >&2; exit 2 ;;
  esac
}

# ──────────────────────────────────────────────────────────────────────────────
#  Окружение
# ──────────────────────────────────────────────────────────────────────────────

have() { command -v "$1" >/dev/null 2>&1; }

detect_platform() {
  PLATFORM="unknown"; PKG=""; OS_NAME="$(uname -s)"
  case "$OS_NAME" in
    Darwin)
      PLATFORM="macos"; PKG="brew"
      OS_NAME="macOS $(sw_vers -productVersion 2>/dev/null || echo '')"
      ;;
    Linux)
      PLATFORM="linux"
      local id="" like=""
      if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        id="${ID:-}"; like="${ID_LIKE:-}"; OS_NAME="${PRETTY_NAME:-Linux}"
      fi
      case " $id $like " in
        *" debian "*|*" ubuntu "*) PKG="apt" ;;
        *" rhel "*|*" fedora "*|*" centos "*) PKG="dnf" ;;
      esac
      ;;
  esac

  if [ "$(id -u)" = "0" ]; then
    SUDO=""
    ROOTABLE=1
  elif have sudo; then
    SUDO="sudo"
    ROOTABLE=1
  else
    SUDO=""
    ROOTABLE=0
  fi
}

# Лучший доступный интерпретатор: сначала явные 3.13/3.12/3.11, потом системный python3.
find_python() {
  local candidate ver minor
  for candidate in python3.13 python3.12 python3.11 python3; do
    have "$candidate" || continue
    ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    [ -n "$ver" ] || continue
    minor="${ver#*.}"
    [ "${ver%%.*}" = "3" ] || continue
    if [ "$minor" -ge "$PY_MIN_MINOR" ] 2>/dev/null; then
      PYTHON="$(command -v "$candidate")"
      PYTHON_VER="$ver"
      return 0
    fi
  done
  return 1
}

node_major() {
  have node || { printf '0'; return 0; }
  local v; v="$(node --version 2>/dev/null || printf 'v0')"
  v="${v#v}"; printf '%s' "${v%%.*}"
}

rand_b64() {
  local bytes="$1"
  if have openssl; then
    openssl rand -base64 "$bytes" | tr -d '\n'
  else
    dd if=/dev/urandom bs="$bytes" count=1 2>/dev/null | base64 | tr -d '\n'
  fi
}

gen_secret()   { rand_b64 48 | tr '+/' '-_' | cut -c1-64; }
# Ключ Fernet: ровно 32 случайных байта в urlsafe-base64 (см. backend/app/core/crypto.py).
gen_fernet()   { rand_b64 32 | tr '+/' '-_'; }
gen_password() { rand_b64 24 | tr -d '=+/' | cut -c1-18; }

# Правка значения в .env: сохраняет комментарии и порядок строк, добавляет ключ,
# если его в файле не было.
set_env() {
  local key="$1" val="$2" file="$3" tmp
  tmp="$(mktemp)"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    awk -v k="$key" -v v="$val" '
      index($0, k "=") == 1 { print k "=" v; next }
      { print }
    ' "$file" >"$tmp"
  else
    cat "$file" >"$tmp"
    printf '%s=%s\n' "$key" "$val" >>"$tmp"
  fi
  cat "$tmp" >"$file"
  rm -f "$tmp"
}

# Пустое значение считаем отсутствующим: иначе `get_env X || echo default` никогда не
# срабатывает — sed завершается успешно и с пустым выводом.
get_env() {
  local key="$1" file="$2" val
  [ -f "$file" ] || return 1
  val="$(sed -n "s/^${key}=//p" "$file" | head -1)"
  [ -n "$val" ] || return 1
  printf '%s' "$val"
}

# Часть работы (venv, npm, браузер Playwright) должна принадлежать пользователю, от имени
# которого потом работает служба, — иначе кэш Playwright уедет в /root, а служба его не найдёт.
run_as() {
  if [ "$(id -un)" = "$SERVICE_USER" ]; then
    "$@"
  else
    sudo -u "$SERVICE_USER" -H "$@"
  fi
}

soft() {
  local label="$1"; shift
  if [ "$DRY_RUN" = 1 ]; then line "$C_VIO$G_ARROW$C_OFF" "$C_DIM$label$C_OFF"; return 0; fi
  log "--- (необязательно) $label :: $*"
  if ( "$@" ) >>"$LOG_FILE" 2>&1; then ok "$label"; return 0; fi
  warn "$label — не удалось (подробности в install.log)"
  return 0
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: проверка окружения
# ──────────────────────────────────────────────────────────────────────────────

step_preflight() {
  step "Проверка окружения"
  info "система: $C_B$OS_NAME$C_OFF"
  info "каталог проекта: $C_B$ROOT_DIR$C_OFF"
  info "режим установки: $C_B$MODE$C_OFF"

  [ -d "$APP_DIR" ] || die "Не найден каталог oracle-t — запускать install.sh нужно из корня склонированного репозитория."
  [ -f "$BACKEND_DIR/requirements.txt" ] || die "Не найден oracle-t/backend/requirements.txt — репозиторий склонирован не полностью."

  if [ "$PLATFORM" = "unknown" ]; then
    warn "ОС не распознана — системные пакеты придётся поставить вручную"
  elif [ -z "$PKG" ] && [ "$MODE" != "docker" ]; then
    warn "менеджер пакетов не распознан (ожидались apt/dnf/brew) — пакеты придётся поставить вручную"
  fi

  if [ "$MODE" = "server" ]; then
    [ "$PLATFORM" = "linux" ] || die "Режим server рассчитан на Linux с systemd. На macOS используйте --mode local или --mode docker."
    [ "$ROOTABLE" = 1 ] || die "Режим server требует прав root: запустите «sudo ./install.sh --mode server»."
    have systemctl || die "systemd не найден — режим server недоступен. Используйте --mode docker."
    ok "права root и systemd на месте"
    info "служба будет работать от пользователя $C_B$SERVICE_USER$C_OFF"
    [ "$SERVICE_USER" = "root" ] && warn "backend будет работать от root; безопаснее склонировать репозиторий обычным пользователем и запустить «sudo ./install.sh»"
  fi

  # Место на диске: venv с браузером Playwright и node_modules — это ~2.5 ГБ, а на
  # свежем VPS свободного места нередко меньше, и установка падает на середине pip.
  local free_mb
  free_mb="$(df -Pm "$ROOT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
  if [ -n "${free_mb:-}" ]; then
    if [ "$free_mb" -lt 4096 ]; then
      warn "свободно ${free_mb} МБ — установке нужно около 4 ГБ"
    else
      ok "свободно на диске: $((free_mb / 1024)) ГБ"
    fi
  fi

  if [ "$MODE" != "docker" ] && find_python; then
    ok "Python $PYTHON_VER ($PYTHON)"
  elif [ "$MODE" != "docker" ]; then
    info "подходящий Python (3.$PY_MIN_MINOR+) не найден — поставлю на следующем шаге"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: системные пакеты
# ──────────────────────────────────────────────────────────────────────────────

apt_install() { $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"; }
apt_update()  { $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq; }
dnf_install() { $SUDO dnf install -y "$@"; }

brew_ensure() {
  local formula="$1"
  brew list --formula "$formula" >/dev/null 2>&1 && return 0
  brew install "$formula"
}

install_node_from_nodesource() {
  local script; script="$(mktemp)"
  curl -fsSL https://deb.nodesource.com/setup_20.x -o "$script"
  $SUDO bash "$script"
  rm -f "$script"
  apt_install nodejs
}

ensure_node() {
  local major; major="$(node_major)"
  if [ "$major" -ge "$NODE_MIN_MAJOR" ] 2>/dev/null; then
    ok "Node.js $(node --version) уже подходит"
    return 0
  fi
  if [ "$PKG" = "apt" ]; then
    task "Node.js из репозитория дистрибутива" apt_install nodejs npm
    major="$(node_major)"
    if [ "$major" -lt "$NODE_MIN_MAJOR" ] 2>/dev/null; then
      warn "в дистрибутиве Node.js $(node --version 2>/dev/null || echo 'отсутствует') — для сборки нужен $NODE_MIN_MAJOR+"
      note "официальный репозиторий: https://deb.nodesource.com/setup_20.x"
      if confirm "Подключить репозиторий NodeSource и поставить Node.js 20? (y/n)" "y"; then
        task "Node.js 20 из NodeSource" install_node_from_nodesource
      else
        die "Без Node.js $NODE_MIN_MAJOR+ фронтенд не собрать. Поставьте его вручную и запустите установщик снова."
      fi
    fi
  elif [ "$PKG" = "dnf" ]; then
    task "Node.js" dnf_install nodejs npm
  elif [ "$PKG" = "brew" ]; then
    task "Node.js" brew_ensure node
  fi
  major="$(node_major)"
  [ "$major" -ge "$NODE_MIN_MAJOR" ] 2>/dev/null || die "Node.js $NODE_MIN_MAJOR+ обязателен для сборки фронтенда."
  ok "Node.js $(node --version)"
}

step_packages() {
  step "Системные пакеты"

  if [ -z "$PKG" ]; then
    warn "пропускаю: менеджер пакетов не распознан"
    note "нужны: Python 3.$PY_MIN_MINOR+, Node.js $NODE_MIN_MAJOR+, PostgreSQL 15+, tesseract (rus+eng), bsdtar"
    step_end
    return 0
  fi

  case "$PKG" in
    apt)
      task "обновление списка пакетов apt" apt_update
      task "базовые пакеты сборки" apt_install ca-certificates curl gnupg build-essential libpq-dev
      task "Python и venv" apt_install python3 python3-venv python3-dev
      # Ubuntu 22.04 несёт системный Python 3.10 — приложению его мало. Пакет python3.11
      # там есть в universe, поэтому сначала пробуем его, а не сразу сдаёмся.
      if ! find_python; then
        soft "Python 3.11 (системный слишком старый)" apt_install python3.11 python3.11-venv python3.11-dev
      fi
      task "PostgreSQL" apt_install postgresql postgresql-contrib
      # OCR — необязательная часть: без tesseract сканы PDF просто останутся без текста,
      # система работает (см. oracle-t/README.md, Этап 3).
      soft "OCR: tesseract с русским и английским" apt_install tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
      soft "распаковка RAR: bsdtar" apt_install libarchive-tools
      [ "$MODE" = "server" ] && task "nginx" apt_install nginx
      ensure_node
      ;;
    dnf)
      task "базовые пакеты сборки" dnf_install gcc libpq-devel curl ca-certificates
      if ! find_python; then task "Python 3.11" dnf_install python3.11 python3.11-devel; fi
      task "PostgreSQL" dnf_install postgresql-server postgresql-contrib
      soft "OCR: tesseract с русским и английским" dnf_install tesseract tesseract-langpack-rus tesseract-langpack-eng
      soft "распаковка RAR: bsdtar" dnf_install bsdtar
      [ "$MODE" = "server" ] && task "nginx" dnf_install nginx
      ensure_node
      ;;
    brew)
      have brew || die "Homebrew не найден. Установите его с https://brew.sh и запустите установщик снова."
      if ! find_python; then task "Python 3.11" brew_ensure python@3.11; fi
      task "PostgreSQL 15" brew_ensure postgresql@15
      soft "OCR: tesseract" brew_ensure tesseract
      # tesseract-lang — это ~1,5 ГБ данных для 100+ языков. Нужен он только ради rus,
      # и в свежих версиях формулы tesseract русский уже есть — проверяем, а не ставим вслепую.
      if ! tesseract --list-langs 2>/dev/null | grep -q '^rus$'; then
        soft "OCR: языковые пакеты (rus)" brew_ensure tesseract-lang
      else
        skip "OCR: язык rus уже доступен"
      fi
      ensure_node
      ;;
  esac

  find_python || die "Python 3.$PY_MIN_MINOR+ не найден даже после установки пакетов. Поставьте его вручную."
  ok "Python $PYTHON_VER"

  if have tesseract; then
    if tesseract --list-langs 2>/dev/null | grep -q '^rus$'; then
      ok "OCR готов (tesseract, язык rus на месте)"
    else
      warn "tesseract есть, но языкового пакета rus нет — сканы PDF останутся без текста"
    fi
  else
    warn "tesseract не установлен — сканы PDF без текстового слоя останутся без распознанного текста"
  fi
  have bsdtar || warn "bsdtar не установлен — документация в RAR-архивах не будет распакована"
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: конфигурация и секреты
# ──────────────────────────────────────────────────────────────────────────────

step_config() {
  step "Конфигурация и секреты"
  local env_file="$APP_DIR/.env"
  local pg_host="localhost"
  [ "$MODE" = "docker" ] && pg_host="db"

  if [ -f "$env_file" ]; then
    skip ".env уже существует — значения не трогаю"
    PG_PASS="$(get_env POSTGRES_PASSWORD "$env_file" || true)"
    PG_DB="$(get_env POSTGRES_DB "$env_file" || echo oraclet)"
    PG_USER="$(get_env POSTGRES_USER "$env_file" || echo oraclet)"
    ADMIN_USER="$(get_env BOOTSTRAP_ADMIN_USERNAME "$env_file" || echo admin)"
    ADMIN_PASS="$(get_env BOOTSTRAP_ADMIN_PASSWORD "$env_file" || true)"
    case "$(get_env JWT_SECRET_KEY "$env_file" || true)" in
      ""|change-me*|insecure*) warn "JWT_SECRET_KEY в .env — значение из шаблона, замените его перед боевой эксплуатацией" ;;
    esac
  elif [ "$DRY_RUN" = 1 ]; then
    info "будет создан $env_file со сгенерированными секретами"
  else
    FRESH_ENV=1
    cp "$APP_DIR/.env.example" "$env_file"
    PG_PASS="$(gen_password)"
    ADMIN_PASS="$(gen_password)"
    set_env POSTGRES_HOST "$pg_host" "$env_file"
    set_env POSTGRES_PASSWORD "$PG_PASS" "$env_file"
    set_env JWT_SECRET_KEY "$(gen_secret)" "$env_file"
    set_env BOOTSTRAP_ADMIN_USERNAME "$ADMIN_USER" "$env_file"
    set_env BOOTSTRAP_ADMIN_PASSWORD "$ADMIN_PASS" "$env_file"
    [ "$MODE" = "server" ] && set_env APP_ENV "production" "$env_file"
    # Отправитель писем в шаблоне — заглушка smtp.example.ru. Оставить её включённой
    # значит получить на первом же триггере уведомлений висящие попытки соединения с
    # несуществующим хостом; почта настраивается в интерфейсе после установки.
    set_env NOTIFY_ENABLED "false" "$env_file"
    set_env NOTIFY_SMTP_HOST "" "$env_file"
    set_env NOTIFY_SMTP_USERNAME "" "$env_file"
    set_env NOTIFY_SMTP_PASSWORD "" "$env_file"
    chmod 600 "$env_file"
    ok "создан .env, секреты сгенерированы (JWT, пароль БД, пароль администратора)"
  fi

  # Ключ шифрования паролей от личных кабинетов площадок. В нативной установке его
  # заводит и хранит сам backend в файле .credentials_key рядом с проектом, а в контейнере
  # такой файл исчез бы при пересборке образа — поэтому там ключ живёт в .env.
  if [ "$DRY_RUN" != 1 ]; then
    local key_file="$APP_DIR/.credentials_key"
    local key; key="$(get_env CREDENTIALS_ENCRYPTION_KEY "$env_file" || true)"
    if [ -n "$key" ]; then
      skip "ключ шифрования учётных данных уже задан в .env"
    elif [ "$MODE" = "docker" ] && [ -f "$key_file" ]; then
      set_env CREDENTIALS_ENCRYPTION_KEY "$(tr -d '\n' <"$key_file")" "$env_file"
      ok "ключ шифрования перенесён из .credentials_key в .env — иначе пересборка образа обнулила бы пароли площадок"
    elif [ "$MODE" = "docker" ]; then
      set_env CREDENTIALS_ENCRYPTION_KEY "$(gen_fernet)" "$env_file"
      ok "сгенерирован ключ шифрования учётных данных площадок"
    else
      skip "ключ шифрования: backend создаст .credentials_key при первом запуске"
    fi
  fi

  # docker compose подставляет порт публикации из .env: переменную окружения установщика
  # sudo до compose не доносит, а .env читается им всегда.
  if [ "$MODE" = "docker" ] && [ "$DRY_RUN" != 1 ]; then
    set_env HTTP_PORT "$HTTP_PORT" "$env_file"
    ok "порт веб-интерфейса: $HTTP_PORT"
  fi

  if [ "$MODE" != "docker" ] && [ "$DRY_RUN" != 1 ]; then
    # pydantic-settings читает .env из рабочего каталога процесса, а backend работает из
    # своего каталога — держим там копию, как это делает run.sh.
    cp "$env_file" "$BACKEND_DIR/.env"
    chmod 600 "$BACKEND_DIR/.env"
    ok "конфигурация разложена: oracle-t/.env и backend/.env"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: PostgreSQL
# ──────────────────────────────────────────────────────────────────────────────

as_postgres() {
  if have sudo; then sudo -u postgres "$@"
  elif have runuser; then runuser -u postgres -- "$@"
  else die "Нужен sudo или runuser, чтобы выполнить команды от системного пользователя postgres."
  fi
}

pg_start_linux() {
  if [ "$PKG" = "dnf" ] && [ ! -f /var/lib/pgsql/data/PG_VERSION ]; then
    $SUDO postgresql-setup --initdb
  fi
  $SUDO systemctl enable postgresql
  $SUDO systemctl start postgresql
  local i
  for i in $(seq 1 30); do
    as_postgres pg_isready >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

pg_provision_linux() {
  local exists
  exists="$(as_postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" 2>/dev/null || true)"
  if [ "$exists" = "1" ]; then
    as_postgres psql -q -c "ALTER ROLE \"$PG_USER\" WITH LOGIN PASSWORD '$PG_PASS'"
  else
    as_postgres psql -q -c "CREATE ROLE \"$PG_USER\" WITH LOGIN PASSWORD '$PG_PASS'"
  fi
  exists="$(as_postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" 2>/dev/null || true)"
  [ "$exists" = "1" ] || as_postgres createdb -O "$PG_USER" "$PG_DB"
}

pg_bin_macos() {
  local prefix
  for prefix in /opt/homebrew/opt/postgresql@15/bin /usr/local/opt/postgresql@15/bin; do
    [ -x "$prefix/pg_isready" ] && { printf '%s' "$prefix"; return 0; }
  done
  have pg_isready && { dirname "$(command -v pg_isready)"; return 0; }
  return 1
}

pg_setup_macos() {
  local bin; bin="$(pg_bin_macos)" || return 1
  if ! "$bin/pg_isready" >/dev/null 2>&1; then
    brew services start postgresql@15
    local i
    for i in $(seq 1 30); do "$bin/pg_isready" >/dev/null 2>&1 && break; sleep 1; done
  fi
  "$bin/pg_isready" >/dev/null 2>&1 || return 1
  "$bin/psql" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" | grep -q 1 \
    || "$bin/createuser" -s "$PG_USER"
  "$bin/psql" -d postgres -q -c "ALTER USER \"$PG_USER\" WITH PASSWORD '$PG_PASS'"
  "$bin/psql" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" | grep -q 1 \
    || "$bin/createdb" -O "$PG_USER" "$PG_DB"
}

step_database() {
  step "PostgreSQL: служба, роль и база"
  if [ "$PLATFORM" = "macos" ]; then
    task "запуск PostgreSQL и создание базы «${PG_DB}»" pg_setup_macos
  else
    task "запуск службы PostgreSQL" pg_start_linux
    task "роль «${PG_USER}» и база «${PG_DB}»" pg_provision_linux
  fi
  ok "база $C_B$PG_DB$C_OFF доступна пользователю $C_B$PG_USER$C_OFF"
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: Python-окружение backend
# ──────────────────────────────────────────────────────────────────────────────

VENV=""

step_python_env() {
  step "Python-окружение backend"
  VENV="$BACKEND_DIR/.venv"

  if [ -x "$VENV/bin/python" ]; then
    skip "venv уже создан ($("$VENV/bin/python" -V 2>&1))"
  else
    task "создание venv (Python $PYTHON_VER)" run_as "$PYTHON" -m venv "$VENV"
  fi

  task "обновление pip" run_as "$VENV/bin/pip" install --quiet --upgrade pip wheel
  task "зависимости backend (FastAPI, SQLAlchemy, разбор документов, Yandex AI SDK)" \
    run_as "$VENV/bin/pip" install --quiet -r "$BACKEND_DIR/requirements.txt"

  if [ "$SKIP_PLAYWRIGHT" = 1 ]; then
    warn "браузер Playwright пропущен (--skip-playwright): источники, отдающие разметку только после JS, собираться не будут"
  else
    if [ "$PLATFORM" = "linux" ] && [ "$ROOTABLE" = 1 ]; then
      soft "системные библиотеки для браузера" $SUDO "$VENV/bin/playwright" install-deps chromium
    fi
    task "браузер Chromium для Playwright (~150 МБ)" run_as "$VENV/bin/playwright" install chromium
  fi

  if [ "$DRY_RUN" != 1 ]; then
    local count; count="$("$VENV/bin/pip" list --format=freeze 2>/dev/null | wc -l | tr -d ' ')"
    ok "окружение готово: $count пакетов в backend/.venv"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: миграции
# ──────────────────────────────────────────────────────────────────────────────

run_migrations() { cd "$BACKEND_DIR" && run_as "$VENV/bin/alembic" upgrade head; }

step_migrations() {
  step "Схема базы данных"
  task "накат миграций (alembic upgrade head)" run_migrations
  if [ "$DRY_RUN" != 1 ]; then
    local rev; rev="$(cd "$BACKEND_DIR" && run_as "$VENV/bin/alembic" current 2>/dev/null | tail -1 || true)"
    [ -n "$rev" ] && note "текущая ревизия: $rev"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: сборка фронтенда
# ──────────────────────────────────────────────────────────────────────────────

npm_install() {
  cd "$FRONTEND_DIR"
  if [ -f package-lock.json ]; then
    run_as npm ci --no-audit --no-fund
  else
    run_as npm install --no-audit --no-fund
  fi
}
npm_build() { cd "$FRONTEND_DIR" && run_as npm run build; }

step_frontend() {
  step "Сборка веб-интерфейса"
  task "npm-зависимости" npm_install
  task "сборка бандла (tsc + vite build)" npm_build
  if [ "$DRY_RUN" != 1 ]; then
    [ -f "$FRONTEND_DIR/dist/index.html" ] || die "Сборка прошла, но frontend/dist/index.html не появился."
    local size; size="$(du -sh "$FRONTEND_DIR/dist" 2>/dev/null | awk '{print $1}')"
    ok "собрано в frontend/dist ($size)"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: службы systemd и nginx
# ──────────────────────────────────────────────────────────────────────────────

write_systemd_unit() {
  $SUDO tee "/etc/systemd/system/$SERVICE_NAME.service" >/dev/null <<UNIT
[Unit]
Description=Sova Scanner backend (FastAPI)
Documentation=file://$APP_DIR/README.md
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT
Restart=always
RestartSec=5
# Планировщик опроса площадок и очередь разбора документов живут внутри процесса,
# поэтому остановка должна быть мягкой: даём доработать текущую задачу.
KillSignal=SIGINT
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
UNIT
  $SUDO systemctl daemon-reload
}

write_nginx_site() {
  $SUDO mkdir -p "$WEB_ROOT"
  $SUDO rm -rf "${WEB_ROOT:?}/"*
  $SUDO cp -R "$FRONTEND_DIR/dist/." "$WEB_ROOT/"
  $SUDO chmod -R a+rX "$WEB_ROOT"

  local conf_dir
  if [ -d /etc/nginx/sites-available ]; then
    conf_dir="/etc/nginx/sites-available"
  else
    conf_dir="/etc/nginx/conf.d"
  fi
  # У сборок nginx без conf.d каталога может не быть вовсе — tee тогда падает на записи.
  $SUDO mkdir -p "$conf_dir"

  $SUDO tee "$conf_dir/$NGINX_SITE.conf" >/dev/null <<SITE
# Sova Scanner. Статика и API на одном origin: фронтенд ходит в /api/ относительным путём.
server {
    listen $HTTP_PORT default_server;
    server_name _;

    root $WEB_ROOT;
    index index.html;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;
    gzip_min_length 1024;

    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
        try_files \$uri =404;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    # Косая черта в конце proxy_pass срезает префикс /api — как rewrite в vite.config.ts.
    location /api/ {
        proxy_pass http://127.0.0.1:$BACKEND_PORT/;
        proxy_http_version 1.1;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Документация тендеров весит десятки мегабайт, а разбор архива с OCR внутри
        # запроса занимает минуты — дефолтных 1m/60s не хватает.
        client_max_body_size 256m;
        proxy_read_timeout   600s;
        proxy_send_timeout   600s;
        proxy_buffering      off;
    }
}
SITE

  if [ -d /etc/nginx/sites-enabled ]; then
    # default-сайт Debian слушает тот же 80 как default_server — два default_server на одном
    # порту nginx не запустит.
    $SUDO rm -f /etc/nginx/sites-enabled/default
    $SUDO ln -sfn "$conf_dir/$NGINX_SITE.conf" "/etc/nginx/sites-enabled/$NGINX_SITE.conf"
  fi
  $SUDO nginx -t
}

selinux_allow_proxy() {
  have getenforce || return 0
  [ "$(getenforce 2>/dev/null || echo Disabled)" = "Enforcing" ] || return 0
  have setsebool || return 0
  $SUDO setsebool -P httpd_can_network_connect 1
}

open_firewall() {
  if have ufw && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    $SUDO ufw allow "$HTTP_PORT/tcp"
  elif have firewall-cmd && $SUDO firewall-cmd --state >/dev/null 2>&1; then
    $SUDO firewall-cmd --permanent --add-port="$HTTP_PORT/tcp"
    $SUDO firewall-cmd --reload
  fi
  # Межсетевого экрана может не быть вовсе — это не повод пугать предупреждением.
  return 0
}

step_services() {
  step "Службы: systemd и nginx"
  task "юнит systemd ($SERVICE_NAME.service)" write_systemd_unit
  task "запуск backend" $SUDO systemctl enable --now "$SERVICE_NAME"
  soft "разрешение SELinux на проксирование" selinux_allow_proxy
  task "сайт nginx ($WEB_ROOT)" write_nginx_site
  task "перезапуск nginx" $SUDO systemctl enable --now nginx
  if [ "$DRY_RUN" != 1 ]; then $SUDO systemctl reload nginx >>"$LOG_FILE" 2>&1 || $SUDO systemctl restart nginx >>"$LOG_FILE" 2>&1; fi
  soft "открытие порта $HTTP_PORT в межсетевом экране" open_firewall
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Режим docker
# ──────────────────────────────────────────────────────────────────────────────

COMPOSE=""
# Docker обычно требует root, но не всегда: у Docker Desktop и у пользователя в группе
# docker сокет доступен напрямую, а лишний sudo увёл бы compose в чужой контекст.
DOCKER_SUDO=""

detect_compose() {
  if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"; return 0; fi
  if have docker-compose; then COMPOSE="docker-compose"; return 0; fi
  return 1
}

install_docker_official() {
  local script; script="$(mktemp)"
  curl -fsSL https://get.docker.com -o "$script"
  $SUDO sh "$script"
  rm -f "$script"
  $SUDO systemctl enable --now docker
}

step_docker_engine() {
  step "Docker"
  # curl нужен и для проверки готовности стенда, и для официального скрипта установки Docker.
  if ! have curl && [ "$PKG" = "apt" ]; then
    task "обновление списка пакетов apt" apt_update
    task "curl" apt_install curl ca-certificates
  elif ! have curl && [ "$PKG" = "dnf" ]; then
    task "curl" dnf_install curl ca-certificates
  fi
  if have docker && detect_compose; then
    ok "Docker на месте ($(docker --version 2>/dev/null | sed 's/,.*//'), $COMPOSE)"
  elif [ "$PLATFORM" = "macos" ]; then
    die "На macOS Docker ставится вручную: скачайте Docker Desktop с https://docs.docker.com/desktop/ и запустите установщик снова (или используйте --mode local)."
  elif [ "$PKG" = "apt" ]; then
    task "обновление списка пакетов apt" apt_update
    soft "Docker из репозитория дистрибутива" apt_install docker.io docker-compose-v2
    if ! (have docker && detect_compose); then
      warn "в дистрибутиве нет Docker с плагином compose"
      note "официальный установочный скрипт: https://get.docker.com"
      if confirm "Поставить Docker официальным скриптом с get.docker.com? (y/n)" "y"; then
        task "Docker Engine" install_docker_official
      else
        die "Без Docker режим docker недоступен. Поставьте его вручную или используйте --mode server."
      fi
    fi
    detect_compose || die "docker compose так и не появился — проверьте установку Docker."
    $SUDO systemctl enable --now docker >>"$LOG_FILE" 2>&1 || true
    ok "Docker готов ($COMPOSE)"
  else
    have docker || die "Docker не найден, а автоматическая установка для этой ОС не поддерживается. Поставьте Docker и запустите установщик снова."
    detect_compose || die "Найден docker, но нет docker compose. Поставьте плагин docker-compose."
    ok "Docker готов ($COMPOSE)"
  fi

  if docker info >/dev/null 2>&1; then
    DOCKER_SUDO=""
  elif [ "$ROOTABLE" = 1 ]; then
    DOCKER_SUDO="$SUDO"
    note "демон Docker доступен только через sudo — команды compose пойдут через него"
  else
    die "Демон Docker недоступен: нет прав на /var/run/docker.sock. Добавьте пользователя в группу docker («sudo usermod -aG docker \$USER», затем перелогиньтесь) или запустите установщик через sudo."
  fi
  step_end
}

dc() { $DOCKER_SUDO $COMPOSE "$@"; }
dc_build() { cd "$APP_DIR" && dc build; }
dc_up()    { cd "$APP_DIR" && dc up -d; }

step_docker_build() {
  step "Сборка образов"
  info "backend: python 3.11 + tesseract (rus/eng) + bsdtar + браузер Playwright"
  info "frontend: сборка Vite и раздача через nginx"
  note "первый раз это занимает 5–15 минут — образ backend тянет браузер"
  task "docker compose build" dc_build
  step_end
}

step_docker_up() {
  step "Запуск контейнеров"
  task "docker compose up -d" dc_up
  if [ "$DRY_RUN" != 1 ]; then
    printf '%s' "$C_DIM"; (cd "$APP_DIR" && dc ps 2>/dev/null | sed 's/^/  /') || true; printf '%s' "$C_OFF"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Шаг: проверка работоспособности
# ──────────────────────────────────────────────────────────────────────────────

# Приложение импортируется только из своего каталога: `app` — это пакет в backend/,
# а не установленная библиотека.
check_import() { cd "$BACKEND_DIR" && run_as "$VENV/bin/python" -c "import app.main"; }

wait_http() {
  local url="$1" tries="${2:-90}" i
  for i in $(seq 1 "$tries"); do
    curl -fsS -o /dev/null --max-time 5 "$url" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}

step_verify() {
  step "Проверка работоспособности"
  if [ "$DRY_RUN" = 1 ]; then
    info "будет опрошен /health и главная страница"
    step_end
    return 0
  fi

  local api_url page_url
  if [ "$MODE" = "docker" ]; then
    api_url="http://127.0.0.1:$HTTP_PORT/api/health"
    page_url="http://127.0.0.1:$HTTP_PORT/"
  elif [ "$MODE" = "server" ]; then
    api_url="http://127.0.0.1:$BACKEND_PORT/health"
    page_url="http://127.0.0.1:$HTTP_PORT/"
  else
    api_url=""
    page_url=""
  fi

  if [ -z "$api_url" ]; then
    # Локальный режим ничего не запускает как службу — проверяем то, что можно проверить
    # без работающего сервера: импорт приложения и собранный фронтенд.
    task "импорт приложения (проверка зависимостей)" check_import
    ok "фронтенд собран: $FRONTEND_DIR/dist/index.html"
    info "запуск стенда: $C_B./oracle-t/run.sh$C_OFF"
    step_end
    return 0
  fi

  if wait_http "$api_url" 90; then
    ok "API отвечает: $api_url"
  else
    warn "API не ответил за 3 минуты"
    if [ "$MODE" = "server" ]; then
      note "журнал: journalctl -u $SERVICE_NAME -n 50 --no-pager"
      printf '%s' "$C_DIM"; $SUDO journalctl -u "$SERVICE_NAME" -n 20 --no-pager 2>/dev/null | sed 's/^/    /' || true; printf '%s' "$C_OFF"
    else
      note "журнал: cd oracle-t && $DOCKER_SUDO $COMPOSE logs backend"
    fi
    die "Установка завершена, но backend не отвечает — посмотрите журнал выше."
  fi

  if wait_http "$page_url" 15; then
    ok "веб-интерфейс отвечает: $page_url"
  else
    warn "страница на порту $HTTP_PORT не открылась — проверьте nginx"
  fi
  step_end
}

# ──────────────────────────────────────────────────────────────────────────────
#  Итог
# ──────────────────────────────────────────────────────────────────────────────

host_hint() {
  local ip=""
  if have hostname; then ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; fi
  [ -z "$ip" ] && ip="localhost"
  printf '%s' "$ip"
}

summary() {
  local port_suffix=""
  [ "$HTTP_PORT" != "80" ] && port_suffix=":$HTTP_PORT"

  printf '\n'
  rule
  if [ "$DRY_RUN" = 1 ]; then
    printf '  %s%s ПЛАН ГОТОВ%s — ничего не изменено (--dry-run)\n' "$C_VIO$C_B" "$G_OK" "$C_OFF"
    rule
    printf '\n'
    return 0
  fi
  printf '  %s%s Sova Scanner установлен%s\n' "$C_GREEN$C_B" "$G_OK" "$C_OFF"
  rule
  printf '\n'

  if [ "$MODE" = "local" ]; then
    printf '  %sЗапуск стенда%s\n' "$C_B" "$C_OFF"
    printf '    ./oracle-t/run.sh   %s→ http://localhost:5173%s\n\n' "$C_DIM" "$C_OFF"
  else
    printf '  %sВеб-интерфейс%s\n' "$C_B" "$C_OFF"
    printf '    http://%s%s\n\n' "$(host_hint)" "$port_suffix"
  fi

  printf '  %sВход администратора%s\n' "$C_B" "$C_OFF"
  if [ "$FRESH_ENV" = 1 ]; then
    printf '    логин:  %s%s%s\n' "$C_B" "$ADMIN_USER" "$C_OFF"
    printf '    пароль: %s%s%s\n' "$C_B" "$ADMIN_PASS" "$C_OFF"
    printf '    %sсохранены в oracle-t/.env; сменить — backend/reset_admin.py%s\n\n' "$C_DIM" "$C_OFF"
  else
    printf '    из существующего oracle-t/.env (BOOTSTRAP_ADMIN_USERNAME / BOOTSTRAP_ADMIN_PASSWORD)\n\n'
  fi

  printf '  %sЧто настроить в интерфейсе%s\n' "$C_B" "$C_OFF"
  printf '    Настройки → Интеграции  %s— ключ и Folder ID Yandex AI Studio%s\n' "$C_DIM" "$C_OFF"
  printf '    Настройки → Уведомления %s— SMTP-ящик и получатели писем%s\n' "$C_DIM" "$C_OFF"
  printf '    Настройки → Площадки    %s— учётные данные личных кабинетов ЭТП%s\n\n' "$C_DIM" "$C_OFF"

  printf '  %sУправление%s\n' "$C_B" "$C_OFF"
  case "$MODE" in
    server)
      printf '    systemctl status %s\n' "$SERVICE_NAME"
      printf '    journalctl -u %s -f\n' "$SERVICE_NAME"
      printf '    %sобновление: git pull && ./install.sh --mode server -y%s\n' "$C_DIM" "$C_OFF"
      ;;
    docker)
      printf '    cd oracle-t && %s ps\n' "$COMPOSE"
      printf '    cd oracle-t && %s logs -f backend\n' "$COMPOSE"
      printf '    %sобновление: git pull && ./install.sh --mode docker -y%s\n' "$C_DIM" "$C_OFF"
      ;;
    *)
      printf '    ./oracle-t/run.sh          %s— поднять backend и frontend%s\n' "$C_DIM" "$C_OFF"
      printf '    %sобновление: git pull && ./install.sh --mode local -y%s\n' "$C_DIM" "$C_OFF"
      ;;
  esac
  printf '\n'

  if [ "$WARNINGS" -gt 0 ]; then
    printf '  %s%s предупреждений: %s%s — установка прошла, но часть возможностей ограничена (см. выше)\n\n' \
      "$C_YEL" "$G_WARN" "$WARNINGS" "$C_OFF"
  fi
  printf '  %sЖурнал установки: %s%s\n\n' "$C_DIM" "$LOG_FILE" "$C_OFF"
}

# ──────────────────────────────────────────────────────────────────────────────
#  Выбор режима и запуск
# ──────────────────────────────────────────────────────────────────────────────

choose_mode() {
  [ -n "$MODE" ] && return 0

  local default="local"
  if [ "$PLATFORM" = "linux" ] && [ "$ROOTABLE" = 1 ]; then default="server"; fi

  if [ "$ASSUME_YES" = 1 ] || [ ! -t 0 ]; then
    MODE="$default"
    return 0
  fi

  printf '  %sКак ставим?%s\n\n' "$C_B" "$C_OFF"
  printf '    %s1%s  %sserver%s  служба systemd + nginx, автозапуск при перезагрузке %s(нужен sudo)%s\n' \
    "$C_VIO$C_B" "$C_OFF" "$C_B" "$C_OFF" "$C_DIM" "$C_OFF"
  printf '    %s2%s  %sdocker%s  всё в контейнерах, изолированно от системы %s(нужен Docker)%s\n' \
    "$C_VIO$C_B" "$C_OFF" "$C_B" "$C_OFF" "$C_DIM" "$C_OFF"
  printf '    %s3%s  %slocal%s   рабочая машина: зависимости и сборка, запуск через run.sh\n\n' \
    "$C_VIO$C_B" "$C_OFF" "$C_B" "$C_OFF"

  local pick default_pick=3
  case "$default" in server) default_pick=1 ;; docker) default_pick=2 ;; esac
  pick="$(ask "Номер режима:" "$default_pick")"
  case "$pick" in
    1|server) MODE="server" ;;
    2|docker) MODE="docker" ;;
    3|local)  MODE="local" ;;
    *) die "Не понял выбор «${pick}»." ;;
  esac
  printf '\n'
}

main() {
  parse_args "$@"
  setup_style
  : >"$LOG_FILE" 2>/dev/null || LOG_FILE="$(mktemp)"
  log "install.sh запущен: $* (pwd=$ROOT_DIR)"

  banner
  detect_platform
  choose_mode

  # От чьего имени будет работать программа. Под sudo это тот, кто вызвал sudo, а не root:
  # иначе venv, node_modules и кэш браузера окажутся в /root и служба их не увидит.
  SERVICE_USER="${SUDO_USER:-$(id -un)}"
  [ "$MODE" = "local" ] && SERVICE_USER="$(id -un)"

  if [ "$DRY_RUN" = 1 ]; then
    printf '  %s%s режим предпросмотра: ничего не будет установлено%s\n' "$C_VIO" "$G_ARROW" "$C_OFF"
  fi

  case "$MODE" in
    docker) STEP_TOTAL=6 ;;
    server) STEP_TOTAL=9 ;;
    local)  STEP_TOTAL=8 ;;
  esac

  if [ "$MODE" = "docker" ]; then
    step_preflight
    step_docker_engine
    step_config
    step_docker_build
    step_docker_up
    step_verify
  else
    step_preflight
    step_packages
    step_config
    step_database
    step_python_env
    step_migrations
    step_frontend
    [ "$MODE" = "server" ] && step_services
    step_verify
  fi

  summary
}

# Запускаемся только при прямом вызове: так установщик можно подключить через `source`
# и проверить отдельные функции (генерацию юнита systemd, конфига nginx) в песочнице.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
