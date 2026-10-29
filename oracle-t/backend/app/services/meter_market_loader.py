"""Загрузка поверок счётчиков ЭЭ из сервиса выгрузки ФГИС «Аршин» (раздел 79 ARCHITECTURE.md,
разделы 4–5 ТЗ `ARSHIN_ANALYTICS.md`).

Формат проверен на живых файлах 29.10.2026, и он отличается от того, что можно ожидать по
инструкции:

* в CSV **нет строки заголовка** — 16 колонок по позициям (`COLUMNS`), разделитель `;`;
* «слепок» — это не одна таблица, а матрёшка: `snapshot.20261001.zip` хранит без сжатия
  `snapshot.20260901.zip` и 29 файлов изменений за сентябрь, тот — предыдущий месяц, и так
  до базового CSV на 01.09.2022. Слепок = база + все ежедневные изменения, по порядку;
* `global_id` внутри файла уникален: аннулированная запись приходит со своим `global_id`,
  датой `ver_date_end` и причиной, её замена — с новым `global_id` и `ver_id_prev`. Поэтому
  применение — upsert по `global_id`, и порядок строк в файле не важен;
* в ежедневном файле изменений есть поверки 2022–2025 годов (публикуют с опозданием), так что
  агрегат пересчитывается по всем затронутым месяцам, а не за «вчера».

Сохраняются только счётчики электроэнергии (≈11 % строк выгрузки) — **все** их типы, не только
включённые в отчёт: включение нового типа тогда — флаг в справочнике, а не новый проход по
слепку на 11,8 ГБ (раздел 5 ТЗ).

Запуск: `python -m app.services.meter_market_loader catchup` (догнать цепочку до текущего
состояния), `… file <путь>` (применить скачанный пакет), `… rebuild-monthly`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import struct
import sys
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import IO, Any

import httpx
from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.adapters.http_utils import user_agent_for
from app.core.config import get_settings
from app.db.session import engine
from app.models.log import LogLevel
from app.services.audit import log_action
from app.services.meter_market_normalize import ModificationMatcher, modification_key

COMPONENT = "meter_market"
BASE_URL = "https://fgis.gost.ru/fundmetrology/exporter/poverki/"
MANIFEST_URL = BASE_URL + "manifest.json"

COLUMNS = (
    "global_id",
    "org_title",
    "mit_number",
    "mit_title",
    "mit_notation",
    "mi_modification",
    "mi_number",
    "verification_date",
    "valid_date",
    "result_docnum",
    "applicability",
    "ver_date_start",
    "ver_date_end",
    "ver_id_prev",
    "ver_id_next",
    "reason",
)
_IDX = {name: i for i, name in enumerate(COLUMNS)}

# Поверки раньше этой даты не храним: отчёт показывает годы с 2021-го (раздел 5 ТЗ), а база
# слепка начинается с 2020 года и раньше.
DEFAULT_SINCE = date(2021, 1, 1)
BATCH_ROWS = 100_000
# Ключ рекомендательной блокировки Postgres: загрузку из планировщика и из командной строки
# одновременно не запустить — две параллельные цепочки upsert одной таблицы перепутали бы
# чекпойнты.
_LOCK_KEY = 7_919_079

# Счётчик электроэнергии по наименованию типа. «Счетчики воды электронные» и «Счетчики
# тепловой энергии» не подходят — нужна электрическая энергия или ватт-часы; «Установки для
# поверки счетчиков электрической энергии» и модули РЗА «с функциями счетчика» — тоже: это не
# счётчики, поэтому наименование обязано с «Счетчик» начинаться.
_EE_TITLE_RE = re.compile(
    r"^\W*сч[её]тчик.*(электрическ\w*\s+энерги|электроэнерги|ватт-?час)",
    re.IGNORECASE | re.DOTALL,
)


def is_ee_title(title: str | None) -> bool:
    return bool(title) and bool(_EE_TITLE_RE.search(title))


def parse_date(value: str | None) -> date | None:
    """Дата из выгрузки: ISO с временем и без, `dd.mm.yyyy` — у программы Чернова встречались
    все варианты (раздел 4 ТЗ). Неразборчивое — `None`, строка тогда пропускается, а не роняет пакет."""

    if not value:
        return None
    value = value.strip()
    try:
        if len(value) >= 10 and value[4] == "-":
            return date.fromisoformat(value[:10])
        if len(value) >= 10 and value[2] == ".":
            return date(int(value[6:10]), int(value[3:5]), int(value[0:2]))
    except ValueError:
        return None
    return None


# ── Манифест и цепочка пакетов ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Package:
    id: str
    prev_id: str | None
    kind: str
    file_name: str
    size: int | None
    md5: str | None


def fetch_manifest(client: httpx.Client | None = None) -> dict[str, Any]:
    own = client is None
    client = client or httpx.Client(timeout=60, headers={"User-Agent": user_agent_for(MANIFEST_URL)})
    try:
        response = client.get(MANIFEST_URL)
        response.raise_for_status()
        return response.json()
    finally:
        if own:
            client.close()


def _packages(manifest: dict[str, Any]) -> tuple[list[Package], list[Package]]:
    snapshots = [
        Package(s["id"], None, "snapshot", s["relativeUri"], s.get("size"), s.get("md5sum"))
        for s in manifest.get("snapshots") or []
    ]
    deltas = [
        Package(d["id"], d.get("prevId"), "delta", d["relativeUri"], d.get("size"), d.get("md5sum"))
        for d in manifest.get("deltas") or []
    ]
    return sorted(snapshots, key=lambda p: p.id), deltas


def plan_chain(manifest: dict[str, Any], state: str | None) -> list[Package]:
    """Какие пакеты применить, чтобы из состояния `state` прийти к `currentId`.

    Без состояния (первая загрузка) или когда манифест уже не хранит изменений от нашего
    состояния (изменения лежат ~2 месяца) — последний слепок и цепочка от него. Слепок
    повторно применять безопасно: запись — upsert по `global_id`."""

    snapshots, deltas = _packages(manifest)
    current = manifest.get("currentId")
    by_prev = {d.prev_id: d for d in deltas}

    def walk(start: str) -> list[Package] | None:
        chain: list[Package] = []
        cursor = start
        while cursor != current:
            nxt = by_prev.get(cursor)
            if nxt is None:
                return None
            chain.append(nxt)
            cursor = nxt.id
        return chain

    if state is not None:
        if state == current:
            return []
        chain = walk(state)
        if chain is not None:
            return chain
    for snapshot in reversed(snapshots):
        chain = walk(snapshot.id)
        if chain is not None:
            return [snapshot, *chain]
    raise RuntimeError("В манифесте ФГИС нет цепочки изменений от слепка до текущего состояния")


def storage_dir() -> Path:
    path = Path(get_settings().storage_dir) / "fgis_poverki"
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - сверка с md5sum манифеста, не криптография
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


DOWNLOAD_CHUNK = 64 * 1024 * 1024


def download(package: Package, *, progress: Callable[[int, int | None], None] | None = None) -> Path:
    """Скачивает пакет кусками по `Range` с докачкой и сверяет md5 с манифестом.

    Кусками, а не одним запросом: 29.10.2026 на обычный GET слепка и на открытый диапазон
    `bytes=0-` сервер ФГИС минутами не начинал отдавать данные, а на ограниченный диапазон
    отвечал 206 сразу. Слепок — 11,8 ГБ, ~50 минут на 4 МБ/с: обрыв посреди закачки не
    начинает её заново. Уже скачанный файл с верным md5 повторно не качается."""

    target = storage_dir() / package.file_name
    url = BASE_URL + package.file_name
    headers = {"User-Agent": user_agent_for(url)}
    size = package.size
    failures = 0
    with httpx.Client(timeout=httpx.Timeout(60, read=120), headers=headers) as client:
        if size is None:
            head = client.head(url)
            head.raise_for_status()
            size = int(head.headers["content-length"])
        while True:
            have = target.stat().st_size if target.exists() else 0
            if have >= size:
                break
            last = min(have + DOWNLOAD_CHUNK, size) - 1
            try:
                response = client.get(url, headers={"Range": f"bytes={have}-{last}"})
                response.raise_for_status()
                if response.status_code != 206:
                    raise RuntimeError(f"{package.file_name}: сервер не отдаёт файл по частям (HTTP {response.status_code})")
                with target.open("ab") as fh:
                    fh.write(response.content)
                failures = 0
                if progress:
                    progress(have + len(response.content), size)
            except httpx.HTTPError as exc:
                failures += 1
                logger.warning(f"ФГИС: обрыв закачки {package.file_name} на {have} байт (попытка {failures}): {exc}")
                if failures >= 20:
                    raise RuntimeError(f"{package.file_name}: закачка оборвалась 20 раз подряд: {exc}") from exc
    if target.stat().st_size != size:
        raise RuntimeError(f"{package.file_name}: скачано {target.stat().st_size} из {size} байт")
    if package.md5 and file_md5(target) != package.md5:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"{package.file_name}: md5 не совпал с манифестом, файл удалён")
    return target


# ── Чтение вложенных архивов ──────────────────────────────────────────────────────────────


class _Window(io.RawIOBase):
    """Окно `[offset, offset + length)` поверх файла — несжатая запись внешнего zip как
    самостоятельный zip-файл. `zipfile` открывает вложенный архив только через seek, а
    `ZipExtFile` в Python 3.11 при seek назад перечитывает запись с начала — на 11 ГБ это
    часы. Окно отдаёт байты напрямую из файла по смещению."""

    def __init__(self, fh: IO[bytes], offset: int, length: int) -> None:
        self._fh, self._offset, self._length, self._pos = fh, offset, length, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._length}[whence]
        self._pos = max(0, min(self._length, base + pos))
        return self._pos

    def read(self, size: int = -1) -> bytes:
        remaining = self._length - self._pos
        if size is None or size < 0 or size > remaining:
            size = remaining
        if size <= 0:
            return b""
        self._fh.seek(self._offset + self._pos)
        data = self._fh.read(size)
        self._pos += len(data)
        return data

    def readinto(self, buffer: Any) -> int:
        data = self.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


def _member_window(fh: IO[bytes], parent_offset: int, archive: IO[bytes], info: zipfile.ZipInfo) -> _Window:
    archive.seek(info.header_offset)
    header = archive.read(30)
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = info.header_offset + 30 + name_len + extra_len
    return _Window(fh, parent_offset + start, info.compress_size)


def iter_csv_members(path: Path) -> Iterator[tuple[str, Callable[[], IO[bytes]]]]:
    """CSV-файлы пакета в порядке применения: для слепка — база, затем изменения по дням."""

    fh = path.open("rb")
    try:
        yield from _walk(fh, 0, fh)
    finally:
        fh.close()


def _walk(fh: IO[bytes], offset: int, archive: IO[bytes]) -> Iterator[tuple[str, Callable[[], IO[bytes]]]]:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.rsplit("/", 1)[-1]
            if name.endswith(".zip") and info.compress_type == zipfile.ZIP_STORED:
                window = _member_window(fh, offset, archive, info)
                yield from _walk(fh, window._offset, window)
            elif name.endswith(".csv"):
                yield name, (lambda info=info: zf.open(info))


# ── Применение строк ──────────────────────────────────────────────────────────────────────


@dataclass
class _Caches:
    types: dict[str, int] = field(default_factory=dict)
    variants: dict[tuple[int, str], int] = field(default_factory=dict)
    auto_map: dict[tuple[int, str], int] = field(default_factory=dict)
    orgs: dict[str, int] = field(default_factory=dict)
    titles: dict[str, bool] = field(default_factory=dict)
    matcher: ModificationMatcher = field(default_factory=lambda: ModificationMatcher([]))
    # Типы из справочника аналитика без наименования ФГИС — дописываются по первой поверке.
    untitled: set[str] = field(default_factory=set)


def _load_caches(conn: Connection) -> _Caches:
    caches = _Caches()
    for number, type_id, title in conn.execute(text("SELECT mit_number, id, title FROM meter_market_types")):
        caches.types[number] = type_id
        if not title:
            caches.untitled.add(number)
    for vid, type_id, raw, key, mod_id in conn.execute(
        text("SELECT id, type_id, raw, norm_key, modification_id FROM meter_market_variants")
    ):
        caches.variants[(type_id, raw)] = vid
        if mod_id is not None:
            caches.auto_map.setdefault((type_id, key), mod_id)
    modifications = [tuple(r) for r in conn.execute(text("SELECT id, type_id, name FROM meter_market_modifications"))]
    for mod_id, type_id, name in modifications:
        caches.auto_map.setdefault((type_id, modification_key(name)), mod_id)
    notations = dict(conn.execute(text("SELECT id, notation FROM meter_market_types")).all())
    caches.matcher = ModificationMatcher(modifications, notations)
    caches.orgs = dict(conn.execute(text("SELECT title, id FROM meter_market_orgs")).all())
    return caches


def _type_id(conn: Connection, caches: _Caches, row: list[str]) -> int | None:
    number = row[_IDX["mit_number"]].strip()
    if not number:
        return None
    type_id = caches.types.get(number)
    if type_id is not None:
        if number in caches.untitled:
            caches.untitled.discard(number)
            conn.execute(
                text(
                    "UPDATE meter_market_types SET title = :t, notation = coalesce(notation, :no) WHERE id = :i"
                ),
                {"t": row[_IDX["mit_title"]].strip() or None, "no": row[_IDX["mit_notation"]].strip() or None, "i": type_id},
            )
        return type_id
    title = row[_IDX["mit_title"]]
    ee = caches.titles.get(title)
    if ee is None:
        ee = caches.titles[title] = is_ee_title(title)
    if not ee:
        return None
    type_id = conn.execute(
        text(
            "INSERT INTO meter_market_types (mit_number, title, notation, review_status, in_report) "
            "VALUES (:n, :t, :no, 'new', false) RETURNING id"
        ),
        {"n": number, "t": title.strip() or None, "no": row[_IDX["mit_notation"]].strip() or None},
    ).scalar_one()
    caches.types[number] = type_id
    return type_id


def _variant_id(conn: Connection, caches: _Caches, type_id: int, raw: str) -> int:
    variant_id = caches.variants.get((type_id, raw))
    if variant_id is not None:
        return variant_id
    key = modification_key(raw)
    mod_id = caches.auto_map.get((type_id, key)) or caches.matcher.match(type_id, key)
    variant_id = conn.execute(
        text(
            "INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id, mapped_by) "
            "VALUES (:t, :r, :k, :m, :b) RETURNING id"
        ),
        {"t": type_id, "r": raw, "k": key, "m": mod_id, "b": "auto" if mod_id else None},
    ).scalar_one()
    caches.variants[(type_id, raw)] = variant_id
    return variant_id


def _org_id(conn: Connection, caches: _Caches, title: str) -> int | None:
    title = title.strip()
    if not title:
        return None
    org_id = caches.orgs.get(title)
    if org_id is None:
        org_id = conn.execute(
            text(
                "INSERT INTO meter_market_orgs (title) VALUES (:t) "
                "ON CONFLICT (title) DO UPDATE SET title = EXCLUDED.title RETURNING id"
            ),
            {"t": title},
        ).scalar_one()
        caches.orgs[title] = org_id
    return org_id


def _copy_value(value: Any) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    text_value = str(value)
    return text_value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


_TMP_COLUMNS = "global_id, variant_id, org_id, mi_number, verification_date, valid_date, applicable, annulled"


def _flush(conn: Connection, batch: dict[int, tuple], months: set[date] | None) -> None:
    if not batch:
        return
    buffer = io.StringIO()
    for values in batch.values():
        buffer.write("\t".join(_copy_value(v) for v in values))
        buffer.write("\n")
    buffer.seek(0)
    raw = conn.connection.dbapi_connection
    with raw.cursor() as cur:
        cur.execute("TRUNCATE tmp_meter_verifications")
        cur.copy_expert(f"COPY tmp_meter_verifications ({_TMP_COLUMNS}) FROM STDIN", buffer)
    if months is not None:
        # Месяцы и старых значений (запись могла переехать на другую дату или модификацию),
        # и новых — их агрегат пересчитывается после пакета.
        for (month,) in conn.execute(
            text(
                "SELECT DISTINCT date_trunc('month', d)::date FROM ("
                " SELECT v.verification_date AS d FROM meter_verifications v"
                " JOIN tmp_meter_verifications t USING (global_id)"
                " UNION ALL SELECT verification_date FROM tmp_meter_verifications) x"
            )
        ):
            months.add(month)
    conn.execute(
        text(
            f"INSERT INTO meter_verifications ({_TMP_COLUMNS}) SELECT {_TMP_COLUMNS} FROM tmp_meter_verifications "
            "ON CONFLICT (global_id) DO UPDATE SET variant_id = EXCLUDED.variant_id, org_id = EXCLUDED.org_id, "
            "mi_number = EXCLUDED.mi_number, verification_date = EXCLUDED.verification_date, "
            "valid_date = EXCLUDED.valid_date, applicable = EXCLUDED.applicable, annulled = EXCLUDED.annulled"
        )
    )
    batch.clear()


@dataclass
class MemberStats:
    total: int = 0
    kept: int = 0
    annulled: int = 0


def apply_csv(
    conn: Connection,
    stream: IO[bytes],
    caches: _Caches,
    *,
    since: date = DEFAULT_SINCE,
    months: set[date] | None = None,
    on_batch: Callable[[MemberStats], None] | None = None,
) -> MemberStats:
    """Применяет один CSV выгрузки: строки счётчиков ЭЭ — upsert в `meter_verifications`."""

    stats = MemberStats()
    batch: dict[int, tuple] = {}
    reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline=""), delimiter=";")
    for row in reader:
        stats.total += 1
        if len(row) < len(COLUMNS):
            continue
        type_id = _type_id(conn, caches, row)
        if type_id is None:
            continue
        verified = parse_date(row[_IDX["verification_date"]])
        if verified is None or verified < since:
            continue
        try:
            global_id = int(row[_IDX["global_id"]])
        except ValueError:
            continue
        annulled = bool(row[_IDX["ver_date_end"]].strip())
        batch[global_id] = (
            global_id,
            _variant_id(conn, caches, type_id, row[_IDX["mi_modification"]]),
            _org_id(conn, caches, row[_IDX["org_title"]]),
            row[_IDX["mi_number"]].strip()[:64] or None,
            verified,
            parse_date(row[_IDX["valid_date"]]),
            row[_IDX["applicability"]].strip().casefold() in {"1", "true", "t", "да"},
            annulled,
        )
        stats.kept += 1
        stats.annulled += annulled
        if len(batch) >= BATCH_ROWS:
            _flush(conn, batch, months)
            conn.commit()
            if on_batch:
                on_batch(stats)
    _flush(conn, batch, months)
    return stats


def rebuild_monthly(conn: Connection, months: set[date] | None = None) -> None:
    """Пересчёт «вариант × месяц → количество»: все месяцы или только затронутые пакетом.
    Считаются пригодные и неаннулированные поверки (правила 2 и 7 ТЗ)."""

    if months is None:
        conn.execute(text("TRUNCATE meter_verification_monthly"))
        where, params = "", {}
    elif not months:
        return
    else:
        conn.execute(text("DELETE FROM meter_verification_monthly WHERE month = ANY(:m)"), {"m": sorted(months)})
        where = "AND date_trunc('month', verification_date)::date = ANY(:m)"
        params = {"m": sorted(months)}
    conn.execute(
        text(
            "INSERT INTO meter_verification_monthly (variant_id, month, count) "
            "SELECT variant_id, date_trunc('month', verification_date)::date, count(*) "
            f"FROM meter_verifications WHERE applicable AND NOT annulled {where} "
            "GROUP BY 1, 2"
        ),
        params,
    )


# ── Пакет целиком ─────────────────────────────────────────────────────────────────────────


def _journal(conn: Connection, action: str, result: str, details: str, level: LogLevel = LogLevel.INFO) -> None:
    """Запись в журнал операций в той же транзакции, что и статус пакета."""

    with Session(bind=conn) as session:
        log_action(session, component=COMPONENT, action=action, result=result, level=level, details=details)
        session.flush()


def _open_connection() -> Connection:
    conn = engine.connect()
    # Блокировка общая на весь кластер Postgres — второй ключ отделяет базы, иначе тесты на
    # `oraclet_test` ждали бы загрузку, идущую в рабочей базе.
    locked = conn.execute(
        text("SELECT pg_try_advisory_lock(:k, (SELECT oid::int FROM pg_database WHERE datname = current_database()))"),
        {"k": _LOCK_KEY},
    ).scalar_one()
    if not locked:
        conn.close()
        raise RuntimeError("Загрузка поверок ФГИС уже идёт")
    conn.execute(
        text(
            "CREATE TEMP TABLE IF NOT EXISTS tmp_meter_verifications "
            "(LIKE meter_verifications INCLUDING DEFAULTS)"
        )
    )
    conn.commit()
    return conn


def _close_connection(conn: Connection) -> None:
    """Закрыть соединение загрузчика, сняв блокировку: `close()` возвращает соединение в пул,
    и без явного снятия блокировка уехала бы туда вместе с ним."""

    try:
        conn.rollback()
        conn.execute(text("SELECT pg_advisory_unlock_all()"))
        conn.commit()
    finally:
        conn.close()


def apply_package_file(
    conn: Connection,
    package: Package,
    path: Path,
    *,
    since: date = DEFAULT_SINCE,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Применяет скачанный пакет с продолжением после обрыва по чекпойнту. Возвращает id
    записи `meter_market_loads`."""

    load = conn.execute(
        text(
            "SELECT id, checkpoint FROM meter_market_loads WHERE package_id = :p AND kind = :k "
            "AND status <> 'done' ORDER BY id DESC LIMIT 1"
        ),
        {"p": package.id, "k": package.kind},
    ).first()
    if load is None:
        load_id = conn.execute(
            text(
                "INSERT INTO meter_market_loads (package_id, prev_id, kind, file_name, size, md5, status) "
                "VALUES (:p, :prev, :k, :f, :s, :m, 'processing') RETURNING id"
            ),
            {
                "p": package.id,
                "prev": package.prev_id,
                "k": package.kind,
                "f": package.file_name,
                "s": package.size,
                "m": package.md5,
            },
        ).scalar_one()
        checkpoint = None
    else:
        load_id, checkpoint = load
        conn.execute(text("UPDATE meter_market_loads SET status = 'processing' WHERE id = :i"), {"i": load_id})
    conn.commit()

    caches = _load_caches(conn)
    # Слепок перестраивает агрегат целиком в конце — отслеживать месяцы по ходу незачем.
    months: set[date] | None = set() if package.kind == "delta" else None
    skipping = checkpoint is not None
    try:
        for name, opener in iter_csv_members(path):
            if skipping:
                if name == checkpoint:
                    skipping = False
                continue
            if progress:
                progress(f"{package.file_name}: {name}")
            with opener() as stream:
                stats = apply_csv(conn, stream, caches, since=since, months=months)
            conn.execute(
                text(
                    "UPDATE meter_market_loads SET checkpoint = :c, rows_total = rows_total + :t, "
                    "rows_kept = rows_kept + :k, rows_annulled = rows_annulled + :a WHERE id = :i"
                ),
                {"c": name, "t": stats.total, "k": stats.kept, "a": stats.annulled, "i": load_id},
            )
            conn.commit()
        rebuild_monthly(conn, months)
        totals = conn.execute(
            text("SELECT rows_total, rows_kept, rows_annulled FROM meter_market_loads WHERE id = :i"), {"i": load_id}
        ).one()
        message = f"строк {_n(totals[0])}, счётчиков ЭЭ {_n(totals[1])}, аннулированных {_n(totals[2])}"
        conn.execute(
            text(
                "UPDATE meter_market_loads SET status = 'done', finished_at = now(), message = :m WHERE id = :i"
            ),
            {"m": message, "i": load_id},
        )
        _journal(conn, f"load:{package.kind}", "success", f"{package.file_name}: {message}")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute(
            text("UPDATE meter_market_loads SET status = 'failed', message = :m WHERE id = :i"),
            {"m": str(exc)[:2000], "i": load_id},
        )
        _journal(conn, f"load:{package.kind}", "error", f"{package.file_name}: {exc}", LogLevel.ERROR)
        conn.commit()
        raise
    return load_id


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def current_state(conn: Connection) -> str | None:
    """Последний применённый пакет: id слепка или изменений, после которого база актуальна."""

    return conn.execute(
        text("SELECT package_id FROM meter_market_loads WHERE status = 'done' ORDER BY finished_at DESC, id DESC LIMIT 1")
    ).scalar()


def catch_up(*, keep_files: bool = False, progress: Callable[[str], None] | None = None) -> list[str]:
    """Догоняет цепочку пакетов до `currentId` манифеста. Возвращает применённые id."""

    conn = _open_connection()
    applied: list[str] = []
    try:
        manifest = fetch_manifest()
        chain = plan_chain(manifest, current_state(conn))
        for package in chain:
            if progress:
                progress(f"скачивание {package.file_name}")
            reported = [0]

            def on_bytes(done: int, size: int | None, name: str = package.file_name) -> None:
                if progress and done - reported[0] >= 500 * 1024 * 1024:
                    reported[0] = done
                    progress(f"{name}: скачано {done // (1024 * 1024)} из {(size or 0) // (1024 * 1024)} МБ")

            path = download(package, progress=on_bytes)
            apply_package_file(conn, package, path, progress=progress)
            applied.append(package.id)
            if not keep_files:
                path.unlink(missing_ok=True)
        return applied
    finally:
        _close_connection(conn)


def _package_from_name(path: Path) -> Package:
    name = path.name
    match = re.match(r"poverki\.delta\.(\d+)--(\d+)\.csv\.zip$", name)
    if match:
        return Package(match.group(2), match.group(1), "delta", name, path.stat().st_size, None)
    match = re.match(r"poverki\.snapshot\.(\d+)\.csv\.zip$", name)
    if match:
        return Package(match.group(1), None, "snapshot", name, path.stat().st_size, None)
    raise ValueError(f"Не похоже на пакет выгрузки поверок ФГИС: {name}")


def main(argv: list[str]) -> int:
    def say(message: str) -> None:
        print(f"{datetime.now(timezone.utc):%H:%M:%S} {message}", flush=True)

    command = argv[0] if argv else "catchup"
    if command == "catchup":
        say(f"применено: {catch_up(keep_files='--keep' in argv, progress=say)}")
    elif command == "file":
        path = Path(argv[1])
        package = _package_from_name(path)
        conn = _open_connection()
        try:
            apply_package_file(conn, package, path, progress=say)
        finally:
            _close_connection(conn)
    elif command == "rebuild-monthly":
        conn = _open_connection()
        try:
            rebuild_monthly(conn)
            conn.commit()
        finally:
            _close_connection(conn)
    else:
        print("Команды: catchup [--keep] | file <путь> | rebuild-monthly", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    os.environ.setdefault("SCHEDULER_ENABLED", "false")
    raise SystemExit(main(sys.argv[1:]))
