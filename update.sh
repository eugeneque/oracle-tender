#!/usr/bin/env bash
#
#  Sova Scanner · обновление с GitHub
#
#  Забирает новую версию из репозитория и разворачивает её поверх текущей установки:
#  резервная копия базы → git pull → «install.sh --update» (зависимости, миграции, сборка
#  интерфейса, перезапуск backend) → проверка. Данные, .env, ключ шифрования паролей
#  площадок, документы тендеров и настройки nginx/HTTPS не трогаются.
#
#      sudo ./update.sh                  обновить, если на GitHub есть новая версия
#      sudo ./update.sh --check          только проверить, есть ли обновление
#      sudo ./update.sh --enable-auto    обновляться автоматически каждую ночь (02:30 МСК)
#      sudo ./update.sh --disable-auto   выключить автообновление
#      sudo ./update.sh --status         версия, наличие обновления, состояние автообновления
#      ./update.sh --help                остальные ключи
#
#  Старую версию удалять и ставить заново НЕ нужно — и нельзя: в каталоге проекта лежат
#  .env (пароль базы, пароль администратора), .credentials_key (без него сохранённые пароли
#  от площадок не расшифровать) и скачанная документация тендеров.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/oracle-t"
ENV_FILE="$APP_DIR/.env"
REPO_URL="${UPDATE_REPO_URL:-https://github.com/eugeneque/oracle-tender.git}"
BRANCH="${UPDATE_BRANCH:-main}"
SERVICE_NAME="oracle-t-backend"
TIMER_NAME="oracle-t-update"
AUTO_TIME="02:30"          # ночью: опрос площадок в 09:00 и 14:00, служебные задачи в 03:15 и 04:00
KEEP_BACKUPS=10

ACTION="update"
ASSUME_YES=0
FORCE=0
AUTO=0

# ──────────────────────────────────────────────────────────────────────────────

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_OFF=$'\033[0m'; C_B=$'\033[1m'; C_DIM=$'\033[2m'
  C_GREEN=$'\033[38;5;42m'; C_YEL=$'\033[38;5;214m'; C_RED=$'\033[38;5;203m'; C_CYAN=$'\033[38;5;44m'
else
  C_OFF=""; C_B=""; C_DIM=""; C_GREEN=""; C_YEL=""; C_RED=""; C_CYAN=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s✔%s %s\n' "$C_GREEN" "$C_OFF" "$*"; }
info() { printf '  %s›%s %s\n' "$C_CYAN" "$C_OFF" "$*"; }
warn() { printf '  %s▲%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()  { printf '\n  %s✖ %s%s\n\n' "$C_RED$C_B" "$*" "$C_OFF" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'USAGE'
Sova Scanner — обновление с GitHub.

  sudo ./update.sh [ключи]

Действия:
  (без ключей)         обновить, если на GitHub есть новая версия
  --check              только проверить, есть ли обновление (ничего не меняет)
  --status             текущая версия, наличие обновления, состояние автообновления
  --enable-auto [ЧЧ:ММ] обновляться автоматически раз в сутки (по умолчанию 02:30 МСК)
  --disable-auto       выключить автообновление

Ключи:
  -y, --yes            не спрашивать подтверждения
  --force              развернуть заново, даже если версия уже последняя
  --auto               режим таймера: без вопросов, молча выйти, если обновлений нет

Переменные окружения: UPDATE_BRANCH (по умолчанию main), UPDATE_REPO_URL, NO_COLOR.
Журнал: update.log в корне проекта.
USAGE
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) ACTION="check"; shift ;;
      --status) ACTION="status"; shift ;;
      --enable-auto)
        ACTION="enable-auto"; shift
        if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]{1,2}:[0-9]{2}$ ]]; then AUTO_TIME="$1"; shift; fi
        ;;
      --disable-auto) ACTION="disable-auto"; shift ;;
      -y|--yes) ASSUME_YES=1; shift ;;
      --force) FORCE=1; shift ;;
      --auto) AUTO=1; ASSUME_YES=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) printf 'Неизвестный ключ: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
  done
}

# ──────────────────────────────────────────────────────────────────────────────
#  Окружение
# ──────────────────────────────────────────────────────────────────────────────

# Владелец каталога проекта — тот, от чьего имени работает служба (install.sh берёт его из
# SUDO_USER). Git и сборка идут от его имени: под root в репозитории появились бы файлы,
# которые служба потом не сможет перезаписать, а git отказался бы работать с «чужим» каталогом.
repo_owner() {
  stat -c %U "$ROOT_DIR" 2>/dev/null || stat -f %Su "$ROOT_DIR"
}

as_owner() {
  if [ "$(id -un)" = "$OWNER" ]; then
    "$@"
  else
    sudo -u "$OWNER" -H "$@"
  fi
}

git_() { as_owner git -C "$ROOT_DIR" "$@"; }

get_env() {
  local val
  [ -f "$ENV_FILE" ] || return 1
  val="$(sed -n "s/^$1=//p" "$ENV_FILE" | head -1)"
  [ -n "$val" ] || return 1
  printf '%s' "$val"
}

detect_mode() {
  if [ -f "/etc/systemd/system/$SERVICE_NAME.service" ]; then
    MODE="server"
  elif [ "$(get_env POSTGRES_HOST || true)" = "db" ]; then
    MODE="docker"
  elif [ -f "$ENV_FILE" ]; then
    MODE="local"
  else
    die "Установка не найдена (нет службы $SERVICE_NAME и oracle-t/.env). Сначала: sudo ./install.sh"
  fi
}

need_root() {
  [ "$(id -u)" = "0" ] && return 0
  [ "$MODE" = "local" ] && return 0
  die "Нужны права root: sudo ./update.sh $*"
}

log_line() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" >>"$ROOT_DIR/update.log" 2>/dev/null || true
}

# ──────────────────────────────────────────────────────────────────────────────
#  Версии
# ──────────────────────────────────────────────────────────────────────────────

# Каталог, скачанный архивом или скопированный без .git, превращается в клон на месте:
# отслеживаемые файлы заменяются версией с GitHub, а неотслеживаемые (.env, ключ,
# storage, venv) остаются как есть. Удалять и ставить заново не нужно.
adopt_checkout() {
  [ -d "$ROOT_DIR/.git" ] && return 0
  [ "$AUTO" = 1 ] && die "Каталог $ROOT_DIR не git-клон — первый раз запустите обновление вручную: sudo ./update.sh"
  warn "каталог $ROOT_DIR не связан с GitHub (нет .git)"
  info "подключу его к $REPO_URL: код заменится версией с GitHub, данные (.env, ключ, документы) останутся"
  confirm "Подключить?" || die "Отменено."
  git_ init -q
  git_ remote add origin "$REPO_URL"
  ADOPTED=1
}

fetch_remote() {
  git_ remote get-url origin >/dev/null 2>&1 || git_ remote add origin "$REPO_URL"
  git_ fetch --quiet origin "$BRANCH" || die "Не удалось связаться с GitHub ($REPO_URL). Проверьте доступ сервера в интернет."
  REMOTE_SHA="$(git_ rev-parse "origin/$BRANCH")"
  LOCAL_SHA="$(git_ rev-parse --verify --quiet HEAD || true)"
}

short() { printf '%s' "${1:0:7}"; }

describe() {
  git_ log -1 --format='%h · %cd · %s' --date=format:'%d.%m.%Y %H:%M' "$1" 2>/dev/null || printf '%s' "$(short "$1")"
}

pending_commits() {
  if [ -n "$LOCAL_SHA" ]; then
    git_ log --format='      %h  %s' --no-merges "$LOCAL_SHA..$REMOTE_SHA"
  fi
}

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || return 0
  local reply
  printf '  %s?%s %s [Y/n] ' "$C_YEL" "$C_OFF" "$1"
  read -r reply || reply=""
  case "$reply" in n|N|no|нет|Н|н) return 1 ;; *) return 0 ;; esac
}

# ──────────────────────────────────────────────────────────────────────────────
#  Резервная копия базы
# ──────────────────────────────────────────────────────────────────────────────

backup_dir() {
  if [ "$MODE" = "local" ]; then printf '%s' "$ROOT_DIR/backups"; else printf '%s' "/var/backups/oracle-t/updates"; fi
}

# Миграции вниз не откатываются, поэтому откат версии = восстановление этого дампа.
backup_database() {
  local dir file db user
  dir="$(backup_dir)"
  db="$(get_env POSTGRES_DB || echo oraclet)"
  user="$(get_env POSTGRES_USER || echo oraclet)"
  mkdir -p "$dir"
  chmod 700 "$dir" 2>/dev/null || true
  file="$dir/$(date +%Y%m%d-%H%M%S)-$(short "${LOCAL_SHA:-none}").dump"

  case "$MODE" in
    server)
      sudo -u postgres pg_dump -Fc "$db" >"$file"
      ;;
    docker)
      local compose="docker compose"
      docker compose version >/dev/null 2>&1 || compose="docker-compose"
      (cd "$APP_DIR" && $compose exec -T db pg_dump -U "$user" -Fc "$db") >"$file"
      ;;
    local)
      PGPASSWORD="$(get_env POSTGRES_PASSWORD || true)" pg_dump \
        -h "$(get_env POSTGRES_HOST || echo localhost)" -p "$(get_env POSTGRES_PORT || echo 5432)" \
        -U "$user" -Fc "$db" >"$file"
      ;;
  esac
  chmod 600 "$file"
  [ -s "$file" ] || die "Резервная копия базы получилась пустой ($file) — обновление остановлено."
  BACKUP_FILE="$file"
  ok "резервная копия базы: $file ($(du -h "$file" | awk '{print $1}'))"

  # Храним последние KEEP_BACKUPS копий — дамп большой, а нужен только для отката.
  ls -1t "$dir"/*.dump 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r old; do rm -f "$old"; done
}

# ──────────────────────────────────────────────────────────────────────────────
#  Действия
# ──────────────────────────────────────────────────────────────────────────────

do_check() {
  fetch_remote
  if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    ok "установлена последняя версия: $(describe HEAD)"
    return 1
  fi
  # Здесь версия новее GitHub (коммиты сделаны на этой машине и не отправлены) — это не
  # обновление: «установить» его значило бы откатиться назад.
  if [ -n "$LOCAL_SHA" ] && git_ merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA" 2>/dev/null; then
    ok "установленная версия новее GitHub: $(describe HEAD)"
    info "не отправлено на GitHub коммитов: $(git_ rev-list --count "$REMOTE_SHA..$LOCAL_SHA") — сервер их не получит, пока их нет в origin/$BRANCH"
    return 1
  fi
  if [ -n "$LOCAL_SHA" ]; then
    info "установлена: $(describe HEAD)"
  else
    info "установлена: неизвестно (каталог не был git-клоном)"
  fi
  info "на GitHub:   $(describe "$REMOTE_SHA")"
  if [ -n "$LOCAL_SHA" ]; then
    say "    ${C_B}Что нового:${C_OFF}"
    pending_commits
  fi
  return 0
}

do_update() {
  need_root
  adopt_checkout
  ADOPTED="${ADOPTED:-0}"

  printf '\n  %sSova Scanner · обновление%s  %s(режим: %s, ветка: %s)%s\n\n' \
    "$C_B" "$C_OFF" "$C_DIM" "$MODE" "$BRANCH" "$C_OFF"

  if ! do_check && [ "$FORCE" != 1 ]; then
    [ "$AUTO" = 1 ] || say ""
    return 0
  fi
  say ""

  # Правки, сделанные прямо на сервере, git pull молча не перезапишет — остановимся и
  # покажем их, чтобы ничего не потерялось.
  if [ "$ADOPTED" != 1 ]; then
    local dirty
    dirty="$(git_ status --porcelain --untracked-files=no)"
    if [ -n "$dirty" ]; then
      warn "на сервере изменены файлы проекта:"
      printf '%s\n' "$dirty" | sed 's/^/      /' >&2
      die "Обновление остановлено, чтобы не затереть эти правки. Сохраните их (git stash) или отмените (git checkout -- .) и запустите снова."
    fi
    if ! git_ merge-base --is-ancestor HEAD "$REMOTE_SHA"; then
      die "Локальная версия разошлась с GitHub (на сервере есть свои коммиты). Разберитесь вручную: git -C $ROOT_DIR log --oneline origin/$BRANCH..HEAD"
    fi
  fi

  confirm "Установить обновление?" || die "Отменено."

  local previous="${LOCAL_SHA:-}"
  log_line "обновление: ${previous:-?} → $REMOTE_SHA (режим $MODE)"

  backup_database

  if [ "$ADOPTED" = 1 ]; then
    git_ checkout -q -f -B "$BRANCH" "origin/$BRANCH"
  elif [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
    git_ checkout -q "$BRANCH" 2>/dev/null || git_ checkout -q -B "$BRANCH" "origin/$BRANCH"
    git_ merge -q --ff-only "origin/$BRANCH"
  fi
  git_ branch -q --set-upstream-to="origin/$BRANCH" "$BRANCH" 2>/dev/null || true
  ok "код обновлён: $(describe HEAD)"

  # install.sh берёт пользователя службы из SUDO_USER — передаём владельца проекта явно:
  # из таймера systemd скрипт идёт от root, и служба иначе переехала бы на root.
  local install_args=(--update)
  [ "$MODE" = "docker" ] && install_args+=(--mode docker)
  local rc=0
  if [ "$(id -u)" = "0" ]; then
    SUDO_USER="$OWNER" "$ROOT_DIR/install.sh" "${install_args[@]}" || rc=$?
  else
    "$ROOT_DIR/install.sh" "${install_args[@]}" || rc=$?
  fi

  if [ "$rc" != 0 ]; then
    log_line "ОШИБКА: развёртывание $REMOTE_SHA завершилось с кодом $rc"
    printf '\n  %s✖ Обновление не развернулось (код %s). Подробности — install.log.%s\n\n' "$C_RED$C_B" "$rc" "$C_OFF" >&2
    if [ -n "$previous" ]; then
      say "  Вернуть прежнюю версию:"
      say "    ${C_B}sudo systemctl stop $SERVICE_NAME${C_OFF}                     ${C_DIM}# только для режима server${C_OFF}"
      say "    ${C_B}sudo -u postgres pg_restore --clean --if-exists -d $(get_env POSTGRES_DB || echo oraclet) $BACKUP_FILE${C_OFF}"
      say "    ${C_B}git -C $ROOT_DIR checkout -q $previous${C_OFF}"
      say "    ${C_B}sudo ./install.sh --update${C_OFF}"
      say ""
    fi
    exit "$rc"
  fi

  log_line "готово: $REMOTE_SHA"
  printf '\n  %s✔ Установлена версия %s%s\n' "$C_GREEN$C_B" "$(describe HEAD)" "$C_OFF"
  [ -n "$previous" ] && printf '  %sпрежняя: %s · копия базы: %s%s\n' "$C_DIM" "$(short "$previous")" "$BACKUP_FILE" "$C_OFF"
  say ""
}

do_status() {
  printf '\n  %sSova Scanner%s  %s(режим: %s, каталог: %s)%s\n\n' "$C_B" "$C_OFF" "$C_DIM" "$MODE" "$ROOT_DIR" "$C_OFF"
  if [ -d "$ROOT_DIR/.git" ]; then
    do_check || true
  else
    warn "каталог не git-клон — первый запуск «sudo ./update.sh» подключит его к GitHub"
  fi
  say ""
  if have systemctl && systemctl list-unit-files "$TIMER_NAME.timer" >/dev/null 2>&1 \
     && systemctl is-enabled "$TIMER_NAME.timer" >/dev/null 2>&1; then
    ok "автообновление включено"
    systemctl list-timers "$TIMER_NAME.timer" --no-pager 2>/dev/null | sed -n '1,2p' | sed 's/^/      /'
  else
    info "автообновление выключено (включить: sudo ./update.sh --enable-auto)"
  fi
  [ -f "$ROOT_DIR/update.log" ] && { say ""; say "  ${C_B}Последние обновления:${C_OFF}"; tail -n 5 "$ROOT_DIR/update.log" | sed 's/^/      /'; }
  say ""
}

do_enable_auto() {
  [ "$MODE" = "local" ] && die "Автообновление — для сервера (режимы server и docker). На рабочей машине: git pull."
  need_root
  have systemctl || die "systemd не найден — автообновление через таймер недоступно."
  [ -d "$ROOT_DIR/.git" ] || die "Каталог не git-клон. Сначала один раз обновитесь вручную: sudo ./update.sh"

  tee "/etc/systemd/system/$TIMER_NAME.service" >/dev/null <<UNIT
[Unit]
Description=Sova Scanner: обновление с GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$ROOT_DIR/update.sh --auto
Environment=NO_COLOR=1
# Сборка интерфейса и pip на слабом сервере идут минутами.
TimeoutStartSec=1h
UNIT

  tee "/etc/systemd/system/$TIMER_NAME.timer" >/dev/null <<UNIT
[Unit]
Description=Sova Scanner: ежедневная проверка обновлений

[Timer]
OnCalendar=*-*-* $AUTO_TIME:00 Europe/Moscow
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

  systemctl daemon-reload
  systemctl enable --now "$TIMER_NAME.timer" >/dev/null
  ok "автообновление включено: каждый день в $AUTO_TIME МСК"
  info "журнал: journalctl -u $TIMER_NAME -n 100 --no-pager  и  $ROOT_DIR/update.log"
  info "выключить: sudo ./update.sh --disable-auto"
}

do_disable_auto() {
  need_root
  if have systemctl; then
    systemctl disable --now "$TIMER_NAME.timer" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$TIMER_NAME.timer" "/etc/systemd/system/$TIMER_NAME.service"
    systemctl daemon-reload
  fi
  ok "автообновление выключено"
}

main() {
  parse_args "$@"
  have git || die "git не установлен: sudo apt install git"
  OWNER="$(repo_owner)"
  detect_mode

  case "$ACTION" in
    check)
      [ -d "$ROOT_DIR/.git" ] || die "Каталог не git-клон — версию не сравнить. Первый запуск «sudo ./update.sh» подключит его к GitHub."
      do_check || true
      ;;
    status) do_status ;;
    enable-auto) do_enable_auto ;;
    disable-auto) do_disable_auto ;;
    update) do_update ;;
  esac
}

# `exit` на той же строке: git во время обновления заменяет и этот файл, а bash читает
# скрипт по ходу выполнения — после возврата из main он не должен дочитывать новую версию.
main "$@"; exit $?
