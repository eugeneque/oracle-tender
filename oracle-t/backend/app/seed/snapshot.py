"""Снимок рабочих данных для новой установки (просьба заказчика 28.09.2026).

Новая машина должна открываться уже наполненной: каталог продукции с характеристиками и
типами СИ, производители и «Моя компания», закупки с разбором документации, матрицы
соответствия, AI-оценки, списки ПО верхнего уровня. Без снимка всё это набирается днями
опроса площадок и каталога, а часть (разбор документации, матрица) — ещё и расходом ИИ.

Снимок — это данные, а не схема: zip с CSV на таблицу и `manifest.json` с ревизией alembic,
на которой он снят. Загрузка:

1. база доводится миграциями до ревизии снимка (схема и справочники из миграций);
2. таблицы снимка очищаются и заполняются из CSV в порядке внешних ключей — одной
   транзакцией, так что сорванная загрузка не оставляет полбазы;
3. дальше обычный `alembic upgrade head` — миграции новее снимка проходят по его данным
   так же, как по данным живой установки.

Грузится снимок только в **пустую** базу: без таблиц вовсе или без закупок, продукции и
компаний (установка, которая ни разу не работала). Живые данные он не трогает никогда —
поэтому `prepare` можно звать при каждом развёртывании и обновлении.

В снимок не попадает то, что принадлежит конкретной установке или секретно: пользователи,
ключи и пароли интеграций, учётные данные площадок, SMTP, журналы, очереди задач,
закладки. Ссылки на пользователей обнуляются. Файлы документации (`storage/`) в снимок не
входят — извлечённый текст и результаты разбора лежат в базе, этого для работы достаточно.

    python -m app.seed.snapshot export   снять снимок с текущей базы (на рабочей машине)
    python -m app.seed.snapshot prepare  загрузить снимок в пустую базу + upgrade head
    python -m app.seed.snapshot info     что лежит в снимке
    python -m app.seed.snapshot status   сколько данных в базе сейчас (для итога установки)
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = BACKEND_DIR / "data" / "seed_snapshot.zip"
MANIFEST = "manifest.json"

# Таблицы без данных в снимке: секреты, люди, журналы и состояние процессов этой машины.
EXCLUDED_TABLES = frozenset(
    {
        "alembic_version",  # ревизия — в манифесте
        "users",
        "api_clients",
        "source_credentials",
        "notification_settings",
        "yandex_ai_studio_settings",
        "ai_provider_settings",
        "rusprofile_settings",
        "region_responsibles",  # сотрудники заказчика
        "logs",
        "background_jobs",
        "notifications",
        "tender_bookmarks",
        # Совпадения профилей — производные, пересчитываются при первом обращении. Сами
        # профили в снимке: среди них общие профили отбора — настройка системы.
        "relevance_profile_matches",
        # Очередь опроса каталога: история прошлых прогонов этой машины. Без неё новая
        # установка при старте опрашивает каталог заново (`app/services/startup_refresh.py`).
        "catalog_lookup_queue",
    }
)

# Столбцы, которые обнуляются помимо ссылок на пользователей: путь к файлу, которого на
# новой машине нет, и отметка синхронизации с rusprofile — без неё новая установка сразу
# после старта обновит «Мою компанию» (`app/services/startup_refresh.py`).
NULLED_COLUMNS = {
    "company_profile": {"letterhead_file_path", "rusprofile_synced_at"},
}


def _engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


# --- схема -----------------------------------------------------------------------------------


def _tables(conn: Connection) -> list[str]:
    return list(
        conn.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        )
    )


def _columns(conn: Connection, table: str) -> list[tuple[str, bool]]:
    """Столбцы таблицы по порядку: (имя, допускает ли NULL)."""

    rows = conn.execute(
        text(
            "SELECT column_name, is_nullable = 'YES' FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
        ),
        {"t": table},
    )
    return [(name, nullable) for name, nullable in rows]


def _foreign_keys(conn: Connection) -> list[tuple[str, str, str]]:
    """(таблица, столбец, таблица, на которую он ссылается) для всех внешних ключей."""

    rows = conn.execute(
        text(
            """
            SELECT c.conrelid::regclass::text, a.attname, c.confrelid::regclass::text
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
            """
        )
    )
    return [(t, col, ref) for t, col, ref in rows]


def _load_order(tables: list[str], fks: list[tuple[str, str, str]]) -> list[str]:
    """Родители раньше детей. Ссылки таблицы на саму себя порядок не определяют."""

    wanted = set(tables)
    parents: dict[str, set[str]] = {t: set() for t in tables}
    for table, _col, ref in fks:
        if table in wanted and ref in wanted and ref != table:
            parents[table].add(ref)
    ordered: list[str] = []
    done: set[str] = set()
    while len(ordered) < len(tables):
        ready = sorted(t for t in tables if t not in done and parents[t] <= done)
        if not ready:
            raise RuntimeError(f"Цикл внешних ключей между таблицами: {sorted(wanted - done)}")
        ordered.extend(ready)
        done.update(ready)
    return ordered


def _is_empty(conn: Connection) -> bool:
    """Пустая установка: ни закупок, ни продукции, ни компаний. Такую базу снимок может
    заменить, ничего не потеряв; во всех остальных случаях он её не трогает."""

    tables = set(_tables(conn))
    for table in ("tenders", "products", "company_profile"):
        if table in tables and conn.scalar(text(f'SELECT EXISTS (SELECT 1 FROM "{table}")')):
            return False
    return True


# --- выгрузка ---------------------------------------------------------------------------------


def export(path: Path = SNAPSHOT_PATH) -> dict:
    engine = _engine()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with engine.connect() as conn:
        revision = conn.scalar(text("SELECT version_num FROM alembic_version"))
        fks = _foreign_keys(conn)
        user_refs = {(t, col) for t, col, ref in fks if ref in EXCLUDED_TABLES}
        tables = [t for t in _tables(conn) if t not in EXCLUDED_TABLES]
        counts: dict[str, int] = {}
        raw = conn.connection.dbapi_connection
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for table in tables:
                select_list = []
                for name, nullable in _columns(conn, table):
                    nulled = (table, name) in user_refs or name in NULLED_COLUMNS.get(table, set())
                    if nulled and not nullable:
                        raise RuntimeError(
                            f"{table}.{name} ссылается на исключённые данные, но NOT NULL — "
                            "снимок с такой таблицей не загрузится. Исключите таблицу целиком."
                        )
                    select_list.append(f'NULL AS "{name}"' if nulled else f'"{name}"')
                sql = (
                    f'COPY (SELECT {", ".join(select_list)} FROM "{table}") '
                    "TO STDOUT WITH (FORMAT csv, HEADER true)"
                )
                with zf.open(f"{table}.csv", "w", force_zip64=True) as out:
                    with raw.cursor() as cur:
                        cur.copy_expert(sql, out)
                counts[table] = conn.scalar(text(f'SELECT count(*) FROM "{table}"'))
            manifest = {
                "revision": revision,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tables": counts,
            }
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return manifest


# --- загрузка ---------------------------------------------------------------------------------


def read_manifest(path: Path = SNAPSHOT_PATH) -> dict | None:
    if not path.is_file():
        return None
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read(MANIFEST))


def _current_revision(conn: Connection) -> str | None:
    if "alembic_version" not in _tables(conn):
        return None
    return conn.scalar(text("SELECT version_num FROM alembic_version"))


def _needs_upgrade_to(current: str | None, target: str) -> bool:
    """Нужно ли довести базу до ревизии снимка: да, если она пустая или ревизия снимка
    новее текущей. Ревизия новее снимка — схема уже шире, грузим как есть."""

    if current is None:
        return True
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    ancestors = {rev.revision for rev in script.iterate_revisions(target, "base")}
    return current in ancestors and current != target


def _copy_table(cur, zf: zipfile.ZipFile, table: str, columns: set[str]) -> None:
    with zf.open(f"{table}.csv") as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        header = next(csv.reader(io.StringIO(stream.readline())))
        keep = [name for name in header if name in columns]
        column_list = ", ".join(f'"{name}"' for name in keep)
        sql = f'COPY "{table}" ({column_list}) FROM STDIN WITH (FORMAT csv)'
        if len(keep) == len(header):
            cur.copy_expert(sql, stream)
            return
        # Столбец снимка удалён более новой миграцией — пересобираем CSV без него.
        indexes = [header.index(name) for name in keep]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row in csv.reader(stream):
            writer.writerow([row[i] for i in indexes])
        buffer.seek(0)
        cur.copy_expert(sql, buffer)


def _reset_sequences(conn: Connection, tables: list[str]) -> None:
    rows = conn.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND (column_default LIKE 'nextval(%' OR is_identity = 'YES')"
        )
    )
    for table, column in rows:
        if table not in tables:
            continue
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence(:t, :c), "
                f'COALESCE((SELECT max("{column}") FROM "{table}"), 1), '
                f'(SELECT max("{column}") FROM "{table}") IS NOT NULL)'
            ),
            {"t": f'public."{table}"', "c": column},
        )


def restore(path: Path = SNAPSHOT_PATH) -> dict:
    """Загружает снимок в текущую базу. Вызывающий отвечает за то, что база пустая."""

    manifest = read_manifest(path)
    if manifest is None:
        raise FileNotFoundError(path)
    engine = _engine()
    with engine.connect() as conn:
        current = _current_revision(conn)
    if _needs_upgrade_to(current, manifest["revision"]):
        from alembic import command

        logger.info(f"Схема до ревизии снимка {manifest['revision']}")
        command.upgrade(_alembic_config(), manifest["revision"])

    with engine.begin() as conn, zipfile.ZipFile(path) as zf:
        existing = set(_tables(conn))
        tables = [t for t in manifest["tables"] if t in existing]
        order = _load_order(tables, _foreign_keys(conn))
        # Справочники, которые миграции уже завели (регионы, площадки, производители),
        # заменяются версией из снимка: у них те же ключи, но в снимке они дополнены.
        conn.execute(text(f'TRUNCATE {", ".join(f"{chr(34)}{t}{chr(34)}" for t in order)} CASCADE'))
        cur = conn.connection.dbapi_connection.cursor()
        try:
            for table in order:
                columns = {name for name, _ in _columns(conn, table)}
                _copy_table(cur, zf, table, columns)
        finally:
            cur.close()
        _reset_sequences(conn, order)
    return manifest


def prepare() -> None:
    """Развёртывание базы: снимок в пустую базу, затем миграции до последней версии.

    Вызывается установщиком, `run.sh` и контейнером backend вместо голого
    `alembic upgrade head`.
    """

    from alembic import command

    manifest = read_manifest()
    engine = _engine()
    with engine.connect() as conn:
        empty = _is_empty(conn)
    if manifest is not None and empty:
        logger.info(
            f"База пустая — загружаю снимок от {manifest['created_at']} "
            f"(ревизия {manifest['revision']})"
        )
        try:
            restore()
            counts = manifest["tables"]
            logger.info(
                "Снимок загружен: закупок {}, моделей {}, типов СИ {}, производителей {}, "
                "ячеек матрицы {}".format(
                    counts.get("tenders", 0),
                    counts.get("products", 0),
                    counts.get("si_types", 0),
                    counts.get("manufacturers", 0),
                    counts.get("compliance_matrix_entries", 0),
                )
            )
        except Exception as exc:  # noqa: BLE001 - без снимка система работает, просто пустая
            logger.exception(f"Снимок не загрузился, продолжаю с пустой базой: {exc}")
    elif manifest is None:
        logger.info("Снимка данных нет — база остаётся как есть")
    else:
        logger.info("В базе уже есть данные — снимок не загружается")
    command.upgrade(_alembic_config(), "head")


def _info() -> None:
    manifest = read_manifest()
    if manifest is None:
        print(f"Снимка нет: {SNAPSHOT_PATH}")
        return
    print(f"{SNAPSHOT_PATH} ({SNAPSHOT_PATH.stat().st_size / 1024 / 1024:.1f} МБ)")
    print(f"снят {manifest['created_at']}, ревизия {manifest['revision']}")
    for table, count in sorted(manifest["tables"].items()):
        print(f"  {table:32} {count}")


def _status() -> None:
    labels = (
        ("tenders", "закупок"),
        ("products", "моделей в каталоге"),
        ("manufacturers", "производителей"),
        ("company_profile", "компаний"),
        ("compliance_matrix_entries", "ячеек матрицы"),
    )
    with _engine().connect() as conn:
        tables = set(_tables(conn))
        parts = [
            f"{label} {conn.scalar(text(f'SELECT count(*) FROM {table}'))}"
            for table, label in labels
            if table in tables
        ]
    print(", ".join(parts))


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "info"
    if command == "export":
        manifest = export()
        total = sum(manifest["tables"].values())
        print(f"Снимок сохранён: {SNAPSHOT_PATH} — {len(manifest['tables'])} таблиц, {total} строк")
    elif command == "prepare":
        prepare()
    elif command == "info":
        _info()
    elif command == "status":
        _status()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
