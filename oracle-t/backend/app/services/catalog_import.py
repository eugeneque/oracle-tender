"""CSV-импорт каталога «Производитель → код СИ → модель» (раздел 5.3 ТЗ, п.4 алгоритма —
Этап 4). Нужен, чтобы заказчик мог разом загрузить точные коды СИ конкурентов, когда
предоставит их (п.11 истории решений ТЗ), не дожидаясь автопоиска по ФГИС и не вводя
записи по одной.

Импортированные записи помечаются `source=import` и `verified_by_user=True`: данные пришли
от человека, а не от автопоиска, и по разделу 5.3 ТЗ имеют приоритет при конфликте —
повторный автопоиск их не перезапишет.

Формат файла — CSV с заголовком, колонки (регистр и порядок не важны):
`manufacturer` (юр. название или бренд, обязательна), `si_code` (код СИ, обязательна),
`model` (модель прибора, опциональна), `article`, `device_type`.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, SiType, SiTypeSource
from app.models.user import User
from app.services.audit import log_action

REQUIRED_COLUMNS = {"manufacturer", "si_code"}
_ALIASES = {
    "производитель": "manufacturer",
    "manufacturer": "manufacturer",
    "код си": "si_code",
    "si_code": "si_code",
    "модель": "model",
    "model": "model",
    "артикул": "article",
    "article": "article",
    "тип прибора": "device_type",
    "device_type": "device_type",
}


@dataclass
class ImportOutcome:
    manufacturers_matched: int = 0
    si_types_created: int = 0
    si_types_updated: int = 0
    products_created: int = 0
    errors: list[str] = field(default_factory=list)


def _normalise_header(name: str) -> str:
    return _ALIASES.get(name.strip().lower().lstrip("﻿"), name.strip().lower())


def _find_manufacturer(db: Session, name: str) -> Manufacturer | None:
    """Ищем по юр. названию или бренду, без учёта регистра. Точное совпадение, а не
    похожесть: ошибиться производителем при импорте кодов СИ хуже, чем не найти его —
    неправильная привязка кода СИ тихо исказит расчёт процента победителя (раздел 5.5 ТЗ)."""

    cleaned = name.strip()
    return db.scalar(
        select(Manufacturer).where(
            func.lower(Manufacturer.legal_name) == cleaned.lower()
        )
    ) or db.scalar(
        select(Manufacturer).where(func.lower(Manufacturer.brand_name) == cleaned.lower())
    )


def import_catalog_csv(db: Session, *, content: bytes, actor: User) -> ImportOutcome:
    outcome = ImportOutcome()

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Выгрузки из Excel в России часто в cp1251 — не заставляем пользователя
        # перекодировать файл руками.
        try:
            text = content.decode("cp1251")
        except UnicodeDecodeError as exc:
            outcome.errors.append(f"Не удалось определить кодировку файла: {exc}")
            return outcome

    # Excel по-русски сохраняет CSV с разделителем ';' — определяем автоматически.
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        outcome.errors.append("Файл пуст или не содержит заголовок")
        return outcome

    reader.fieldnames = [_normalise_header(name) for name in reader.fieldnames]
    missing = REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        outcome.errors.append(
            f"В файле нет обязательных колонок: {', '.join(sorted(missing))}. "
            f"Найдены: {', '.join(reader.fieldnames)}"
        )
        return outcome

    for line_no, row in enumerate(reader, start=2):  # 1-я строка — заголовок
        manufacturer_name = (row.get("manufacturer") or "").strip()
        si_code = (row.get("si_code") or "").strip()
        if not manufacturer_name and not si_code:
            continue  # пустая строка в конце файла — не ошибка

        if not manufacturer_name or not si_code:
            outcome.errors.append(f"Строка {line_no}: не заполнены «производитель» и/или «код СИ»")
            continue

        manufacturer = _find_manufacturer(db, manufacturer_name)
        if manufacturer is None:
            outcome.errors.append(
                f"Строка {line_no}: производитель «{manufacturer_name}» не найден в справочнике"
            )
            continue
        outcome.manufacturers_matched += 1

        si_type = db.scalar(
            select(SiType).where(
                SiType.manufacturer_id == manufacturer.id, SiType.si_code == si_code
            )
        )
        if si_type is None:
            si_type = SiType(
                manufacturer_id=manufacturer.id,
                si_code=si_code,
                source=SiTypeSource.IMPORT.value,
                verified_by_user=True,  # данные от человека — раздел 5.3 ТЗ, приоритет над автопоиском
            )
            db.add(si_type)
            db.flush()
            outcome.si_types_created += 1
        else:
            si_type.source = SiTypeSource.IMPORT.value
            si_type.verified_by_user = True
            outcome.si_types_updated += 1

        model_name = (row.get("model") or "").strip()
        if not model_name:
            continue

        existing_product = db.scalar(
            select(Product).where(
                Product.manufacturer_id == manufacturer.id,
                func.lower(Product.model_name) == model_name.lower(),
            )
        )
        if existing_product is None:
            db.add(
                Product(
                    manufacturer_id=manufacturer.id,
                    si_type_id=si_type.id,
                    model_name=model_name,
                    article=(row.get("article") or "").strip() or None,
                    device_type=(row.get("device_type") or "").strip() or None,
                )
            )
            db.flush()
            outcome.products_created += 1
        elif existing_product.si_type_id is None:
            existing_product.si_type_id = si_type.id  # доп. привязка кода СИ к уже заведённой модели

    log_action(
        db,
        component="product_catalog",
        action="import_catalog_csv",
        result="success" if not outcome.errors else "partial_error",
        level=LogLevel.INFO if not outcome.errors else LogLevel.WARNING,
        details=(
            f"Кодов СИ создано {outcome.si_types_created}, обновлено {outcome.si_types_updated}; "
            f"моделей создано {outcome.products_created}; ошибок в строках {len(outcome.errors)}"
        ),
        user_id=actor.id,
    )
    db.commit()
    return outcome
