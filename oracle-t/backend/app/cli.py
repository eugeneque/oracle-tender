"""CLI-команды Sova Scanner.

Использование:
    python -m app.cli bootstrap-admin
    python -m app.cli reset-admin [--username X] [--password Y] [--full-name "..."]
    python -m app.cli list-sources
    python -m app.cli poll-source <source_key>
    python -m app.cli ping-sources
    python -m app.cli check-mail

Или короче (из каталога backend/, при активированном venv):
    python reset_admin.py
"""

import argparse
import secrets
import sys
from pathlib import Path

from loguru import logger

from app.core.config import get_settings
from app.core.env_file import update_env_file
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.availability_service import ping_all_sources
from app.services.notification_service import (
    bootstrap_from_env as bootstrap_notifications,
    get_settings_out,
    smtp_check,
)
from app.services.tender_service import get_source_by_key, list_sources, poll_source
from app.services.user_service import bootstrap_admin, reset_admin

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
ENV_FILES = [PROJECT_ROOT / ".env", BACKEND_DIR / ".env"]


def cmd_bootstrap_admin(args: argparse.Namespace) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        admin = bootstrap_admin(
            db,
            username=args.username or settings.bootstrap_admin_username,
            password=args.password or settings.bootstrap_admin_password,
            full_name=args.full_name or settings.bootstrap_admin_full_name,
        )
        if admin is None:
            logger.info("В системе уже есть пользователи — bootstrap пропущен")
        else:
            logger.info(f"Создан первый администратор: {admin.username}")
    finally:
        db.close()


def cmd_reset_admin(args: argparse.Namespace) -> None:
    """Создаёт (или, если уже существует, перезаписывает пароль) учётную запись
    администратора и сохраняет логин/пароль в .env — можно запускать сколько угодно раз."""

    settings = get_settings()
    username = args.username or settings.bootstrap_admin_username
    full_name = args.full_name or settings.bootstrap_admin_full_name
    password = args.password or secrets.token_urlsafe(9)

    if len(password) < 8:
        print("Пароль должен быть не короче 8 символов", file=sys.stderr)
        raise SystemExit(1)

    db = SessionLocal()
    try:
        admin = reset_admin(db, username=username, password=password, full_name=full_name)
    finally:
        db.close()

    updated = [
        str(path)
        for path in ENV_FILES
        if update_env_file(
            path,
            {
                "BOOTSTRAP_ADMIN_USERNAME": username,
                "BOOTSTRAP_ADMIN_PASSWORD": password,
                "BOOTSTRAP_ADMIN_FULL_NAME": full_name,
            },
        )
    ]

    logger.info(f"Администратор '{admin.username}' создан/обновлён (id={admin.id})")

    print("\n=== Учётная запись администратора Sova Scanner ===")
    print(f"  Логин:  {username}")
    print(f"  Пароль: {password}")
    print("================================================\n")
    if updated:
        print("Обновлены файлы:")
        for path in updated:
            print(f"  - {path}")
    else:
        print(
            "Ни один .env не найден рядом с проектом — создайте его из .env.example и "
            "запустите команду ещё раз, либо впишите значения выше вручную."
        )


def cmd_list_sources(_args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        sources = list_sources(db)
    finally:
        db.close()

    if not sources:
        print("Источники не найдены (миграции применены?)")
        return

    for source in sources:
        polled = source.last_polled_at.isoformat() if source.last_polled_at else "никогда"
        print(
            f"{source.key:16} {source.name:25} status={source.status:14} "
            f"adapter={source.adapter_status:16} last_polled={polled}"
        )
        if source.note:
            print(f"{'':16} примечание: {source.note}")


def cmd_ping_sources(_args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        ping_all_sources(db)
        sources = list_sources(db)
    finally:
        db.close()

    for source in sources:
        checked = source.availability_checked_at.isoformat() if source.availability_checked_at else "—"
        print(
            f"{source.key:16} {source.availability_status or 'не проверено':12} "
            f"проверено={checked} {source.availability_error or ''}"
        )


def cmd_check_mail(_args: argparse.Namespace) -> None:
    """Проверяет почтовый ящик, не отправляя письма.

    На сервере это единственный способ отличить «письма не приходят, потому что ящик не
    настроен» от «не приходят, потому что триггер не сработал»: проверочное письмо из
    интерфейса требует и браузера, и заполненного списка получателей."""

    db = SessionLocal()
    try:
        # Тот же первичный перенос из .env, что и при старте сервера: команду запускают
        # сразу после развёртывания, когда приложение ещё ни разу не поднималось.
        bootstrap_notifications(db)
        settings = get_settings_out(db)
        ok, message = smtp_check(db)
    finally:
        db.close()

    print(f"Сервер:      {settings.smtp_host or '—'}:{settings.smtp_port} ({settings.smtp_security})")
    print(f"Отправитель: {settings.from_address or '—'}")
    print(f"Логин:       {settings.smtp_username or '—'} (пароль {'задан' if settings.has_password else 'НЕ задан'})")
    print(f"Получатели:  {settings.recipients or '— не заданы, письма никуда не уйдут'}")
    print(f"  об ошибках: {settings.admin_recipients or '— не заданы, уйдут общему списку'}")
    print(f"Рассылка:    {'включена' if settings.is_enabled else 'ВЫКЛЮЧЕНА в настройках'}")
    print(f"\n{'OK: ' if ok else 'ОШИБКА: '}{message}")
    if not ok:
        raise SystemExit(1)


def cmd_poll_source(args: argparse.Namespace) -> None:
    db = SessionLocal()
    try:
        source = get_source_by_key(db, args.source_key)
        if source is None:
            print(f"Источник с ключом '{args.source_key}' не найден", file=sys.stderr)
            raise SystemExit(1)

        result = poll_source(db, source)
    finally:
        db.close()

    print(
        f"Источник '{result.source_key}': найдено {result.found}, создано {result.created}, "
        f"обновлено {result.updated}, ошибок {result.errors}"
    )


def cmd_reparse_documents(args: argparse.Namespace) -> None:
    """Переразбор уже скачанных документов новыми экстракторами.

    Нужна после расширения списка поддержанных форматов: файлы `.doc`, `.xls` и архивы
    лежат в хранилище с момента скачивания, но текста у них нет — раньше система такие
    форматы не читала. Перекачивать их незачем, достаточно перечитать с диска.
    """

    from pathlib import Path

    from sqlalchemy import select

    from app.models.tender_document import ParseStatus, TenderDocument
    from app.services.document_extraction import extract_text, sniff_extension
    from app.services.document_service import get_storage_root

    db = SessionLocal()
    updated = 0
    failed = 0
    skipped = 0
    try:
        query = select(TenderDocument).where(TenderDocument.storage_path.is_not(None))
        if not args.all:
            # По умолчанию трогаем только те, у которых текста нет: переразбирать уже
            # разобранные документы — лишняя работа и риск затереть готовый текст ошибкой.
            query = query.where(
                (TenderDocument.extracted_text.is_(None)) | (TenderDocument.extracted_text == "")
            )

        documents = list(db.scalars(query))
        root = get_storage_root()
        for document in documents:
            path = Path(root) / document.storage_path
            if not path.exists():
                skipped += 1
                continue

            content = path.read_bytes()
            file_type = document.file_type or sniff_extension(content)
            try:
                text = extract_text(file_type, content)
            except Exception as exc:  # noqa: BLE001 - один документ не должен прерывать проход
                document.parse_status = ParseStatus.ERROR.value
                document.parse_error = f"Файл скачан, но текст не извлечён: {exc}"
                failed += 1
                continue

            if text:
                document.extracted_text = text
                document.file_type = file_type
                document.parse_status = ParseStatus.SUCCESS.value
                document.parse_error = None
                updated += 1
            else:
                skipped += 1

        db.commit()
    finally:
        db.close()

    print(
        f"Переразобрано документов: {updated}; без текста осталось: {skipped}; ошибок: {failed}"
    )


def cmd_purge_placeholder_documents(_args: argparse.Namespace) -> None:
    """Удаляет документы-заглушки, оставшиеся от прежней схемы сбора.

    Пока у восьми адаптеров не было разбора документации, вместо файлов сохранялась ссылка
    на саму карточку тендера — скачивалась HTML-страница, из которой текста, разумеется, не
    получалось. Такие записи мешают дважды: занимают место настоящих документов (повторный
    заход в карточку видит, что документы «уже есть», и ничего не качает) и портят
    статистику разбора. После удаления система при следующем открытии карточки скачает
    настоящие файлы.
    """

    from sqlalchemy import select

    from app.models.tender import Tender
    from app.models.tender_document import TenderDocument
    from app.services.document_service import get_storage_root

    db = SessionLocal()
    removed = 0
    files_removed = 0
    try:
        rows = db.execute(
            select(TenderDocument, Tender)
            .join(Tender, Tender.id == TenderDocument.tender_id)
            .where(
                (TenderDocument.extracted_text.is_(None))
                | (TenderDocument.extracted_text == "")
            )
        ).all()

        for document, tender in rows:
            # Признак заглушки: ссылка документа совпадает с адресом карточки тендера.
            # Настоящий файл всегда лежит по собственному адресу.
            if tender.source_url and document.source_url == tender.source_url:
                # Файл удаляем вместе с записью: иначе он остаётся в storage навсегда —
                # ссылки на него больше нет ни у кого, а место он занимает. Именно так в
                # хранилище накопились папки скачанных файлов без строк в `tender_documents`.
                if document.storage_path:
                    file_path = get_storage_root() / document.storage_path
                    try:
                        file_path.unlink(missing_ok=True)
                        files_removed += 1
                    except OSError as exc:  # noqa: PERF203 - падение на одном файле не должно рушить чистку
                        print(f"Не удалён файл {file_path}: {exc}")
                db.delete(document)
                removed += 1
        db.commit()
    finally:
        db.close()

    print(f"Удалено документов-заглушек: {removed}; файлов удалено: {files_removed}")


def cmd_backfill_cards(args: argparse.Namespace) -> None:
    """Дозаполняет карточки и регионы у уже собранных тендеров.

    Регион до сих пор проставлялся только ИИ-анализом документации, которого на большинстве
    закупок никто не запускал, — в базе тысячи тендеров с «регион не определён», хотя на
    странице ЕИС он есть всегда. Команда проходит по ним и берёт данные из карточки.

    Работает порциями (`--limit`) и с паузой между закупками: это тысячи запросов к сайту
    ЕИС, и вываливать их разом невежливо и рискованно — площадка ограничит доступ.
    """

    import time

    from sqlalchemy import select

    from app.models.tender import Tender
    from app.models.tender_card import TenderCard
    from app.services.tender_card_service import sync_card

    db = SessionLocal()
    processed = 0
    filled = 0
    try:
        query = select(Tender).where(Tender.region_organizer_code.is_(None))
        if not args.all:
            query = query.where(Tender.external_id.op("~")(r"^\d{11}$|^\d{19}$"))
        tenders = list(db.scalars(query.limit(args.limit)))
        print(f"К обработке тендеров: {len(tenders)}")

        for tender in tenders:
            before = tender.region_organizer_code
            # Пустую карточку (её мог сохранить прежний разбор, не знавший вёрстки 44-ФЗ)
            # перечитываем заново: иначе команда будет раз за разом пропускать именно те
            # тендеры, ради которых её и запускают.
            saved = db.get(TenderCard, tender.id)
            stale = saved is not None and not (saved.payload or {}).get("sections")
            sync_card(db, tender, force=stale)
            db.refresh(tender)
            processed += 1
            if tender.region_organizer_code and tender.region_organizer_code != before:
                filled += 1
            if processed % 10 == 0:
                print(f"  обработано {processed}, регион определён у {filled}")
            time.sleep(args.delay)
    finally:
        db.close()

    print(f"Обработано: {processed}; регион определён у: {filled}")


def cmd_fill_gaps(args: argparse.Namespace) -> None:
    """Дозаполнение полей для фильтров (замечание тестировщика 16.09.2026) — то же, что
    делает планировщик после каждого опроса, но руками и с выбранной порцией."""

    from app.services import tender_gaps_service

    db = SessionLocal()
    try:
        outcome = tender_gaps_service.run(db, fetch_limit=args.fetch_limit, delay=args.delay)
    finally:
        db.close()
    print(f"Дозаполнение: {outcome.summary()}")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="oracle-t-cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap-admin", help="Создать первого администратора, если пользователей ещё нет"
    )
    bootstrap_parser.add_argument("--username", default=None)
    bootstrap_parser.add_argument("--password", default=None)
    bootstrap_parser.add_argument("--full-name", dest="full_name", default=None)
    bootstrap_parser.set_defaults(func=cmd_bootstrap_admin)

    reset_parser = subparsers.add_parser(
        "reset-admin",
        help="Создать администратора или сбросить его пароль; можно запускать многократно",
    )
    reset_parser.add_argument("--username", default=None)
    reset_parser.add_argument(
        "--password", default=None, help="Если не указан — генерируется случайный"
    )
    reset_parser.add_argument("--full-name", dest="full_name", default=None)
    reset_parser.set_defaults(func=cmd_reset_admin)

    list_sources_parser = subparsers.add_parser(
        "list-sources", help="Показать все источники тендеров и статус их адаптеров"
    )
    list_sources_parser.set_defaults(func=cmd_list_sources)

    poll_source_parser = subparsers.add_parser(
        "poll-source",
        help="Запустить опрос одного источника вручную, не дожидаясь расписания",
    )
    poll_source_parser.add_argument("source_key", help="Ключ источника (см. list-sources)")
    poll_source_parser.set_defaults(func=cmd_poll_source)

    ping_sources_parser = subparsers.add_parser(
        "ping-sources", help="Проверить доступность всех источников прямо сейчас"
    )
    ping_sources_parser.set_defaults(func=cmd_ping_sources)

    check_mail_parser = subparsers.add_parser(
        "check-mail",
        help="Проверить почтовый ящик уведомлений (соединение и логин, без отправки письма)",
    )
    check_mail_parser.set_defaults(func=cmd_check_mail)

    reparse_parser = subparsers.add_parser(
        "reparse-documents",
        help="Перечитать уже скачанные документы (после расширения поддержки форматов)",
    )
    reparse_parser.add_argument(
        "--all",
        action="store_true",
        help="Переразобрать все документы, а не только те, у которых нет текста",
    )
    reparse_parser.set_defaults(func=cmd_reparse_documents)

    purge_parser = subparsers.add_parser(
        "purge-placeholder-documents",
        help="Удалить документы-заглушки прежней схемы (ссылка на карточку вместо файла)",
    )
    purge_parser.set_defaults(func=cmd_purge_placeholder_documents)

    backfill_parser = subparsers.add_parser(
        "backfill-cards",
        help="Дозаполнить карточки и регионы у тендеров, где регион не определён",
    )
    backfill_parser.add_argument("--limit", type=int, default=100)
    backfill_parser.add_argument(
        "--delay", type=float, default=1.0, help="Пауза между закупками, секунд"
    )
    backfill_parser.add_argument(
        "--all",
        action="store_true",
        help="Включая закупки без реестрового номера ЕИС (у них карточки может не быть)",
    )
    backfill_parser.set_defaults(func=cmd_backfill_cards)

    gaps_parser = subparsers.add_parser(
        "fill-gaps",
        help=(
            "Дозаполнить ОКПД2, регион и тип конкурса для фильтров списка: из сохранённых "
            "карточек (без сети), затем карточки открытых релевантных закупок с сайта"
        ),
    )
    gaps_parser.add_argument("--fetch-limit", type=int, default=100)
    gaps_parser.add_argument("--delay", type=float, default=1.5)
    gaps_parser.set_defaults(func=cmd_fill_gaps)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
