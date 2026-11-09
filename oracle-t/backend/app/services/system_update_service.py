"""Обновление Sova из интерфейса администратора (09.11.2026, раздел 91 ARCHITECTURE.md).

Зачем: зайти на сервер и запустить «sudo ./update.sh» получается не всегда. Теперь то же
самое делается со страницы «Обновление»: Sova сама сверяет установленную версию с GitHub,
показывает, что придёт (записи журнала изменений, коммиты, миграции, зависимости), и по
кнопке ставит обновление.

Как устроено и почему так:
- Проверка — тот же «update.sh --check», что и в консоли (его вывод показывается как есть),
  плюс разбор средствами git: коммиты, затронутые файлы, новые записи `changelog.ts`.
  Проверке root не нужен: git работает от владельца каталога, а служба backend запущена
  именно от него (install.sh берёт его из SUDO_USER).
- Установку backend сам не выполняет и прав root не получает: дамп базы идёт от postgres,
  а install.sh перезапускает саму службу backend — процесс убил бы сам себя на середине.
  Поэтому backend лишь кладёт файл-запрос `.update-request` в корень проекта; юнит
  `oracle-t-update-web.path` (ставит install.sh) замечает его и запускает
  «update.sh --web» от root отдельной службой. Ход пишется в `update-web.log`, итог —
  в `update-web.state`; страница читает их и переживает перезапуск backend.
- Ставится только то, что уже лежит в origin/<ветка> на GitHub, — ровно как вручную.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings

SYSTEMD_DIR = Path("/etc/systemd/system")
BACKEND_UNIT = "oracle-t-backend.service"
WEB_PATH_UNIT = "oracle-t-update-web.path"
WEB_SERVICE_UNIT = "oracle-t-update-web.service"
AUTO_TIMER_UNIT = "oracle-t-update.timer"

CHANGELOG_PATH = "oracle-t/frontend/src/content/changelog.ts"
MIGRATIONS_DIR = "oracle-t/backend/alembic/versions/"

# Журнал хода показываем целиком, но не бесконечно: сборка с ошибкой бывает многословной.
MAX_LOG_BYTES = 256 * 1024
MAX_COMMITS = 200
# Запрос, который служба не забрала за это время, — признак того, что юнит .path не работает.
QUEUED_STALE_SECONDS = 60

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class UpdateError(Exception):
    """Понятная пользователю причина, почему действие сейчас невозможно."""


def root_dir() -> Path:
    configured = get_settings().update_root_dir.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # app/services/system_update_service.py → services → app → backend → oracle-t → корень
    return Path(__file__).resolve().parents[4]


def _branch() -> str:
    return get_settings().update_branch.strip() or "main"


def _paths() -> dict[str, Path]:
    root = root_dir()
    return {
        "root": root,
        "script": root / "update.sh",
        "request": root / ".update-request",
        "log": root / "update-web.log",
        "state": root / "update-web.state",
        "history": root / "update.log",
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Окружение
# ──────────────────────────────────────────────────────────────────────────────


def _run(cmd: list[str], *, timeout: int = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "NO_COLOR": "1", "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8")}
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        cwd=str(root_dir()),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
        stdin=subprocess.DEVNULL,
    )


def _git(*args: str, timeout: int = 30) -> str:
    try:
        result = _run(["git", "-C", str(root_dir()), *args], timeout=timeout)
    except FileNotFoundError as exc:
        raise UpdateError("На сервере не установлен git — обновление невозможно.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(f"git {args[0]} не ответил за {timeout} с.") from exc
    if result.returncode != 0:
        raise UpdateError((result.stderr or result.stdout).strip() or f"git {args[0]}: код {result.returncode}")
    return result.stdout


def _is_git() -> bool:
    return (root_dir() / ".git").exists() and shutil.which("git") is not None


def detect_mode() -> str:
    """server — служба systemd (обновление из интерфейса возможно); local — рабочая машина
    со стендом run.sh; unavailable — нет update.sh рядом (backend в контейнере docker)."""

    if not _paths()["script"].exists():
        return "unavailable"
    if (SYSTEMD_DIR / BACKEND_UNIT).exists():
        return "server"
    return "local"


def web_update_ready() -> bool:
    return (SYSTEMD_DIR / WEB_PATH_UNIT).exists()


def _systemctl(*args: str) -> str | None:
    if shutil.which("systemctl") is None:
        return None
    try:
        result = _run(["systemctl", *args], timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout.strip()


def _commit(ref: str) -> dict[str, str] | None:
    try:
        raw = _git("log", "-1", "--format=%H%x1f%h%x1f%cI%x1f%s", ref).strip()
    except UpdateError:
        return None
    if not raw:
        return None
    sha, short, date, subject = (raw.split("\x1f") + ["", "", "", ""])[:4]
    return {"sha": sha, "short": short, "date": date, "subject": subject}


def _unavailable_reason(mode: str, is_git: bool) -> str | None:
    if mode == "unavailable":
        return (
            "Sova запущена в контейнере docker — рядом с ней нет update.sh и git. Обновление "
            "выполняется на хосте: sudo ./update.sh в каталоге проекта."
        )
    if mode == "local":
        return (
            "Это рабочая машина (стенд run.sh), а не сервер: проверить обновление можно, а ставить — "
            "командой git pull и перезапуском ./run.sh."
        )
    if not is_git:
        return (
            "Каталог установки не связан с GitHub (нет .git). Один раз выполните на сервере "
            "sudo ./update.sh — он подключит каталог к репозиторию, дальше обновлять можно отсюда."
        )
    if not web_update_ready():
        return (
            "Служба обновления из интерфейса ещё не установлена. Один раз выполните на сервере "
            "sudo ./update.sh (или дождитесь ночного автообновления) — после этого кнопка заработает."
        )
    return None


# ──────────────────────────────────────────────────────────────────────────────
#  Ход и итог запуска
# ──────────────────────────────────────────────────────────────────────────────


def clean_console(text: str) -> str:
    """Убирает цвета и «перерисовки» строки (\\r) — остаётся то, что видно в терминале."""

    lines = []
    for line in _ANSI_RE.sub("", text).split("\n"):
        if "\r" in line:
            parts = [part for part in line.split("\r") if part.strip()]
            line = parts[-1] if parts else ""
        lines.append(line.rstrip())
    return "\n".join(lines).strip("\n")


def _read_state() -> dict[str, str]:
    path = _paths()["state"]
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    state: dict[str, str] = {}
    for line in raw.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            state[key.strip()] = value.strip()
    return state


def _read_log() -> str:
    path = _paths()["log"]
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > MAX_LOG_BYTES:
                fh.seek(size - MAX_LOG_BYTES)
            raw = fh.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if size > MAX_LOG_BYTES:
        text = "…\n" + text.split("\n", 1)[-1]
    return clean_console(text)


def run_info() -> dict[str, Any]:
    """Состояние последнего запуска из интерфейса: idle / queued / running / success /
    failed / interrupted, плюс журнал хода."""

    paths = _paths()
    state = _read_state()
    status = state.get("state") or "idle"
    queued_at = None
    stale = False

    if paths["request"].exists():
        status = "queued"
        try:
            queued_at = datetime.fromtimestamp(paths["request"].stat().st_mtime, tz=timezone.utc)
            stale = (datetime.now(timezone.utc) - queued_at).total_seconds() > QUEUED_STALE_SECONDS
        except OSError:
            pass
    elif status == "running":
        # Служба могла погибнуть, не дописав итог (перезагрузка сервера, kill): тогда
        # «идёт» висело бы вечно. systemd знает точно — спрашиваем его.
        active = _systemctl("is-active", WEB_SERVICE_UNIT)
        if active in {"inactive", "failed"}:
            status = "interrupted"

    return {
        "state": status,
        "result": state.get("result"),
        "code": int(state["code"]) if state.get("code", "").lstrip("-").isdigit() else None,
        "started_at": state.get("started") or None,
        "finished_at": state.get("finished") or None,
        "requested_by": state.get("by") or None,
        "from_version": _commit(state["from"]) if state.get("from") else None,
        "to_version": _commit(state["to"]) if state.get("to") else None,
        "queued_at": queued_at.isoformat() if queued_at else None,
        "queued_stale": stale,
        "log": _read_log(),
    }


def _history(limit: int = 10) -> list[str]:
    try:
        lines = _paths()["history"].read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()][-limit:][::-1]


def get_status() -> dict[str, Any]:
    mode = detect_mode()
    is_git = _is_git() if mode != "unavailable" else False
    auto = _systemctl("is-enabled", AUTO_TIMER_UNIT) if mode == "server" else None
    remote_url = None
    if is_git:
        try:
            remote_url = _git("remote", "get-url", "origin").strip() or None
        except UpdateError:
            remote_url = None
    reason = _unavailable_reason(mode, is_git)
    return {
        "mode": mode,
        "is_git": is_git,
        "branch": _branch(),
        "remote_url": remote_url,
        "current": _commit("HEAD") if is_git else None,
        "can_check": mode != "unavailable",
        "can_install": reason is None,
        "unavailable_reason": reason,
        "auto_update": auto == "enabled",
        "run": run_info(),
        "history": _history(),
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Что придёт с обновлением
# ──────────────────────────────────────────────────────────────────────────────

_TS_STRING = r'"((?:[^"\\]|\\.)*)"'


def _ts_unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value.replace('\\"', '"')


def parse_changelog(source: str) -> list[dict[str, Any]]:
    """Записи `CHANGELOG` из changelog.ts. Полноценный разбор TypeScript не нужен: файл
    пишется по одному образцу (date → title → items с kind/text в двойных кавычках)."""

    start = source.find("export const CHANGELOG")
    if start < 0:
        return []
    releases = []
    for chunk in re.split(r"\bdate:\s*", source[start:])[1:]:
        date = re.match(_TS_STRING, chunk)
        title = re.search(r"\btitle:\s*" + _TS_STRING, chunk)
        if not date or not title:
            continue
        items = [
            {"kind": kind, "text": _ts_unescape(text)}
            for kind, text in re.findall(r'\bkind:\s*"(new|improved|fixed)"\s*,\s*text:\s*' + _TS_STRING, chunk)
        ]
        releases.append({"date": _ts_unescape(date.group(1)), "title": _ts_unescape(title.group(1)), "items": items})
    return releases


def new_releases(local_source: str, remote_source: str) -> list[dict[str, Any]]:
    """Записи журнала, которых нет в установленной версии. Запись, дополненная новыми
    пунктами под той же датой и заголовком, попадает только новыми пунктами."""

    known: dict[tuple[str, str], set[str]] = {}
    for release in parse_changelog(local_source):
        known.setdefault((release["date"], release["title"]), set()).update(i["text"] for i in release["items"])
    result = []
    for release in parse_changelog(remote_source):
        seen = known.get((release["date"], release["title"]))
        items = release["items"] if seen is None else [i for i in release["items"] if i["text"] not in seen]
        if items:
            result.append({**release, "items": items})
    return result


def _show(ref: str, path: str) -> str:
    try:
        return _git("show", f"{ref}:{path}")
    except UpdateError:
        return ""


def _run_check_script() -> tuple[int, str]:
    script = _paths()["script"]
    try:
        result = _run(["bash", str(script), "--check"], timeout=120, env={"UPDATE_BRANCH": _branch()})
    except subprocess.TimeoutExpired:
        return 124, "update.sh --check не завершился за 2 минуты — GitHub недоступен или отвечает очень медленно."
    return result.returncode, clean_console((result.stdout or "") + (result.stderr or ""))


def check() -> dict[str, Any]:
    """Сверка с GitHub: вывод «update.sh --check» и разбор того, что придёт."""

    mode = detect_mode()
    if mode == "unavailable":
        raise UpdateError(_unavailable_reason(mode, False) or "Проверка недоступна.")
    if run_info()["state"] in {"queued", "running"}:
        raise UpdateError("Сейчас идёт обновление — проверка будет доступна после его окончания.")

    code, console = _run_check_script()
    checked_at = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {"checked_at": checked_at, "console": console, "branch": _branch()}
    if code != 0:
        last = next((line.strip(" ✖") for line in reversed(console.splitlines()) if line.strip()), "")
        return {**base, "state": "error", "message": last or f"update.sh --check завершился с кодом {code}"}

    branch = _branch()
    remote_ref = f"origin/{branch}"
    remote = _commit(remote_ref)
    current = _commit("HEAD")
    if remote is None:
        return {**base, "state": "error", "message": f"Не удалось прочитать версию {remote_ref} после проверки."}
    base.update({"current": current, "remote": remote})

    if current and current["sha"] == remote["sha"]:
        return {**base, "state": "up_to_date"}

    def is_ancestor(a: str, b: str) -> bool:
        return _run(["git", "-C", str(root_dir()), "merge-base", "--is-ancestor", a, b]).returncode == 0

    if current and is_ancestor(remote["sha"], current["sha"]):
        return {**base, "state": "ahead"}

    local_ref = current["sha"] if current else None
    commits: list[dict[str, str]] = []
    files: list[str] = []
    added: list[str] = []
    shortstat = ""
    if local_ref:
        log = _git("log", "--no-merges", f"--max-count={MAX_COMMITS}", "--format=%H%x1f%h%x1f%cI%x1f%an%x1f%s",
                   f"{local_ref}..{remote['sha']}")
        for line in log.splitlines():
            parts = (line.split("\x1f") + [""] * 5)[:5]
            commits.append({"sha": parts[0], "short": parts[1], "date": parts[2], "author": parts[3], "subject": parts[4]})
        files = [f for f in _git("diff", "--name-only", local_ref, remote["sha"]).splitlines() if f]
        added = [f for f in _git("diff", "--name-only", "--diff-filter=A", local_ref, remote["sha"]).splitlines() if f]
        shortstat = _git("diff", "--shortstat", local_ref, remote["sha"]).strip()

    diverged = bool(local_ref) and not is_ancestor(local_ref, remote["sha"])
    migrations = sorted(Path(f).stem for f in added if f.startswith(MIGRATIONS_DIR) and f.endswith(".py"))
    deps = {
        "python": any(f.endswith("requirements.txt") or f.endswith("pyproject.toml") for f in files),
        "npm": any(f.endswith("package.json") or f.endswith("package-lock.json") for f in files),
        "scripts": any(f in {"install.sh", "update.sh"} for f in files),
    }
    releases = new_releases(_show(local_ref, CHANGELOG_PATH) if local_ref else "", _show(remote["sha"], CHANGELOG_PATH))
    return {
        **base,
        "state": "diverged" if diverged else "available",
        "commits": commits,
        "commit_count": int(_git("rev-list", "--count", f"{local_ref}..{remote['sha']}").strip() or 0) if local_ref else None,
        "files_changed": len(files),
        "shortstat": shortstat,
        "migrations": migrations,
        "dependencies": deps,
        "releases": releases,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  Запуск
# ──────────────────────────────────────────────────────────────────────────────


def request_update(*, username: str, force: bool) -> dict[str, Any]:
    status = get_status()
    if not status["can_install"]:
        raise UpdateError(status["unavailable_reason"] or "Обновление из интерфейса недоступно.")
    if status["run"]["state"] in {"queued", "running"}:
        raise UpdateError("Обновление уже запущено — дождитесь его окончания.")

    request = _paths()["request"]
    tmp = request.with_name(request.name + ".tmp")
    # Сначала пишем во временный файл и только потом переименовываем: юнит .path срабатывает
    # на появление файла и не должен застать его пустым.
    tmp.write_text(
        f"user={username}\nforce={1 if force else 0}\nrequested_at={datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )
    tmp.replace(request)
    return run_info()


def cancel_request() -> dict[str, Any]:
    """Снимает запрос, который служба ещё не забрала. Идущее обновление не прерывается:
    оборванные посреди миграции или сборки, оно оставило бы систему в худшем состоянии."""

    info = run_info()
    if info["state"] == "running":
        raise UpdateError("Обновление уже выполняется — прервать его безопасно нельзя, дождитесь окончания.")
    try:
        _paths()["request"].unlink()
    except FileNotFoundError:
        pass
    return run_info()
