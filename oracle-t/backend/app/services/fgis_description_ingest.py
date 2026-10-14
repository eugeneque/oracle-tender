"""Обучение справочника по «Описанию типа» из Аршина (замечание заказчика 15.09.2026).

**Что было.** Текст «Описания типа» загружался по кнопке у одного кода СИ, а характеристики
из него извлекались по кнопке у одной модели. На тринадцати производителях и шести сотнях
типов это означало, что из Аршина система не знала почти ничего: на момент правки текст был
загружен у четырёх типов из ~630. Заказчик прямо требует обучить систему характеристикам
приборов всех конкурентов и МИРТЕК по Аршину.

**Что делает этот модуль.** Проходит по типам-электросчётчикам производителя и для каждого:

1. скачивает «Описание типа» (ФГИС, при недоступности — зеркало), если текста ещё нет или
   вышла новая редакция;
2. один раз вычитывает документ моделью и разносит характеристики по всем привязанным
   моделям, у которых их ещё нет либо чьи данные описывают прежнюю редакцию;
3. для исполнений, заведённых из реестра (`registry_modification`), расшифровывает полное
   условное обозначение по легенде структуры из той же карточки — там закодированы ток,
   корпус, интерфейсы конкретного исполнения, которых в общем тексте документа нет;
4. без обращения к модели проставляет то, что известно из самой карточки типа: номер в
   Госреестре, межповерочный интервал, срок действия, ссылку на документ.

**Учёт изменений.** `description_type_extracted_version` хранит редакцию, из которой
характеристики уже разнесены. Ревалидация ФГИС, заметив новую редакцию, сбрасывает текст;
следующий проход обучения видит расхождение версий и перечитывает документ — характеристики
прежней редакции перезаписываются (кроме подтверждённых человеком и введённых вручную).

Один документ — одно обращение к модели на всё семейство, по тому же принципу, что и разбор
руководств (`product_manual_ingest`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
    SiType,
)
from app.services import characteristic_extraction, characteristic_values, si_type_linking
from app.services.ai_client import AiQuotaExceededError
from app.services.audit import log_action

COMPONENT = "product_catalog"

# Какой набор полей искать в «Описании типа». `any` — все автозаполняемые группы: документ
# описывает не только метрологию, но и корпуса, интерфейсы, функции.
DOCUMENT_SOURCE = "any"

# Сколько знаков документа отдавать модели. «Описание типа» — 10-25 листов; технические
# характеристики стоят в первой половине, дальше — знаки утверждения, комплектность, поверка.
MAX_DESCRIPTION_CHARS = 45_000



def _heading(*names: str) -> re.Pattern[str]:
    return re.compile(r"^\s*(" + "|".join(names) + r")\s*$", re.IGNORECASE | re.MULTILINE)


# Разделы «Описания типа», в которых характеристик прибора нет никогда: идентификация ПО
# (версии и контрольные суммы), знак утверждения, методики, правообладатель, испытательный
# центр. Шаблон документа задан Росстандартом, заголовки стоят отдельными строками.
# Вырезаются скриптом до вызова модели (14.10.2026): на 194 описаниях в базе это 16% текста,
# за которые модель ничего не возвращала. Пара «начало — конец»: вырезается от начала до
# следующего полезного раздела; если конец не найден, раздел остаётся — лучше лишнее, чем
# потерянная таблица характеристик.
_BOILERPLATE_SECTIONS: tuple[tuple[re.Pattern[str], re.Pattern[str] | None], ...] = (
    (_heading("Программное обеспечение"), _heading("Метрологические и технические характеристики")),
    (_heading("Знак утверждения типа"), _heading("Нормативные (и технические )?документы.*", "Нормативные правовые.*")),
    (_heading("Правообладатель"), _heading("Изготовитель")),
    (_heading("Испытательный центр"), None),
)
_PAGE_LINE_RE = re.compile(r"^\s*(Лист № ?\d+|Всего листов \d+)\s*$", re.MULTILINE)


def strip_boilerplate(text: str) -> str:
    """«Описание типа» без разделов, где не бывает характеристик (см. выше)."""

    for start, end in _BOILERPLATE_SECTIONS:
        head = start.search(text)
        if head is None:
            continue
        tail = end.search(text, head.end()) if end is not None else None
        if end is not None and tail is None:
            continue
        text = text[: head.start()] + (text[tail.start():] if tail is not None else "")
    return _PAGE_LINE_RE.sub("", text)


# Значение версии для типов, у которых реестр версию не сообщает: сравнивать нужно с чем-то
# стабильным, иначе NULL != NULL заставлял бы перечитывать документ каждый проход.
_NO_VERSION = "n/a"


@dataclass
class DescriptionIngestOutcome:
    si_types_scanned: int = 0
    documents_fetched: int = 0
    documents_read: int = 0
    products_updated: int = 0
    modifications_decoded: int = 0
    characteristics_saved: int = 0
    skipped_up_to_date: int = 0
    skipped_no_url: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)


def ingest_description_types(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor_id: uuid.UUID | None = None,
    limit: int = 10,
    refresh: bool = False,
    use_ai: bool = True,
    adapter: FgisAdapter | None = None,
) -> DescriptionIngestOutcome:
    """Загружает «Описания типов» электросчётчиков производителя и разносит характеристики
    по привязанным моделям. `limit` — сколько документов вычитывать моделью за проход;
    `refresh=True` перечитывает и уже разобранные (после расширения справочника полей)."""

    outcome = DescriptionIngestOutcome()
    adapter = adapter or FgisAdapter()

    for si_type in si_type_linking.electricity_meter_types(db, manufacturer.id):
        outcome.si_types_scanned += 1
        products = list(db.scalars(select(Product).where(Product.si_type_id == si_type.id)))
        if not products:
            continue

        _fill_registry_facts(db, si_type, products, outcome)

        version = si_type.description_type_version or _NO_VERSION
        stale = refresh or si_type.description_type_extracted_version != version
        # Модель без характеристик читается заново, только если эту редакцию она ещё не
        # получала: пустой результат по той же редакции не изменится, а стоит токенов
        # (14.10.2026, `Product.fgis_description_read_version`).
        targets = [
            p
            for p in products
            if stale or (not _has_fgis_characteristics(db, p) and p.fgis_description_read_version != version)
        ]
        if not targets:
            outcome.skipped_up_to_date += 1
            db.commit()
            continue

        if not si_type.description_type_text or refresh:
            if not si_type.description_type_url and not si_type.description_type_mirror_url:
                outcome.skipped_no_url += 1
                db.commit()
                continue
            if outcome.documents_read >= limit:
                continue
            text = adapter.fetch_description_type_text(
                si_type.description_type_url or "", mirror_url=si_type.description_type_mirror_url
            )
            if not text:
                outcome.failed += 1
                outcome.messages.append(f"{si_type.si_code}: «Описание типа» не загрузилось")
                db.commit()
                continue
            si_type.description_type_text = text
            outcome.documents_fetched += 1
            db.commit()

        if not use_ai:
            continue
        if outcome.documents_read >= limit:
            continue

        try:
            reading = characteristic_extraction.read_characteristics(
                db,
                text=strip_boilerplate(si_type.description_type_text)[:MAX_DESCRIPTION_CHARS],
                document_source=DOCUMENT_SOURCE,
            )
        except AiQuotaExceededError as exc:
            # Лимит, отклонённый ключ, нет связи — следующие описания получат тот же отказ.
            outcome.failed += 1
            outcome.messages.append(f"Разбор моделью остановлен: {exc}")
            logger.warning(f"Аршин: разбор «Описаний типа» остановлен: {exc}")
            break
        except Exception as exc:  # noqa: BLE001 - недоступность модели не должна ронять проход
            outcome.failed += 1
            outcome.messages.append(f"{si_type.si_code}: разбор моделью не удался: {exc}")
            logger.warning(f"Аршин: разбор «Описания типа» {si_type.si_code} не удался: {exc}")
            continue
        outcome.documents_read += 1

        for product in targets:
            saved = _apply(db, product, reading.characteristics)
            saved += _decode_modification(db, si_type, product, outcome)
            outcome.characteristics_saved += saved
            outcome.products_updated += 1
            product.fgis_description_read_version = version

        si_type.description_type_extracted_version = version
        db.commit()

    log_action(
        db,
        component=COMPONENT,
        action=f"ingest_description_types:{manufacturer.id}",
        result="success" if outcome.characteristics_saved else "empty",
        level=LogLevel.INFO,
        details=(
            f"Аршин: типов просмотрено {outcome.si_types_scanned}, документов загружено "
            f"{outcome.documents_fetched}, вычитано моделью {outcome.documents_read}, моделей "
            f"обновлено {outcome.products_updated} (исполнений расшифровано "
            f"{outcome.modifications_decoded}), характеристик сохранено "
            f"{outcome.characteristics_saved}; актуальны {outcome.skipped_up_to_date}, без "
            f"ссылки {outcome.skipped_no_url}, не удалось {outcome.failed}"
        ),
        user_id=actor_id,
    )
    db.commit()
    return outcome


def _has_fgis_characteristics(db: Session, product: Product) -> bool:
    return (
        db.scalar(
            select(ProductCharacteristic.id)
            .where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.source == CharacteristicSource.FGIS_DESCRIPTION_TYPE.value,
                # Прямые факты карточки (номер в Госреестре, МПИ) не считаются: они есть у
                # каждой привязанной модели ещё до разбора документа.
                ProductCharacteristic.group_name != "Метрологические характеристики",
                ProductCharacteristic.group_name != "Документация",
            )
            .limit(1)
        )
        is not None
    )


def _fill_registry_facts(
    db: Session, si_type: SiType, products: list[Product], outcome: DescriptionIngestOutcome
) -> None:
    """Факты карточки типа, которые не надо извлекать моделью."""

    facts: list[tuple[str, str, str]] = [
        ("Метрологические характеристики", "Номер в Госреестре", si_type.si_code),
        ("Метрологические характеристики", "Утверждение типа СИ", "да" if si_type.is_actual is not False else "тип неактуален"),
    ]
    if si_type.mpi_months:
        facts.append(("Метрологические характеристики", "Межповерочный интервал", f"{si_type.mpi_months} мес."))
    if si_type.valid_to:
        facts.append(("Метрологические характеристики", "Дата утверждения типа", f"действует до {si_type.valid_to.isoformat()}"))
    url = si_type.description_type_mirror_url or si_type.description_type_url
    if url:
        facts.append(("Документация", "Ссылка на описание типа", url))

    items = [
        characteristic_extraction.ExtractedCharacteristic(
            group_name=group, field_name=name, value=value, confidence=1.0
        )
        for group, name, value in facts
    ]
    # В счётчик характеристик не входят: факты проставляются каждым проходом заново, и
    # отчёт «сохранено N» иначе врал бы о работе модели. Модель их не читает — пометка
    # «требует проверки» им не нужна.
    for product in products:
        _apply(db, product, items, ai_extracted=False)


def _apply(db: Session, product: Product, characteristics: list, *, ai_extracted: bool = True) -> int:
    result = characteristic_extraction.ExtractionOutcome()
    characteristic_extraction.apply_characteristics(
        db,
        product,
        characteristics,
        characteristic_source=CharacteristicSource.FGIS_DESCRIPTION_TYPE,
        outcome=result,
        ai_extracted=ai_extracted,
    )
    return result.saved


def _decode_modification(
    db: Session, si_type: SiType, product: Product, outcome: DescriptionIngestOutcome
) -> int:
    """Расшифровка полного условного обозначения исполнения по легенде из карточки типа.

    Легенда («(ХХХХ)2 - тип корпуса; (Х)6 - базовый ток; …») и само обозначение
    («НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D») вместе — небольшой текст,
    из которого модель достаёт характеристики именно этого исполнения: максимальный ток
    80 А против 100 А у соседнего, интерфейс RS-485, тип корпуса W115. Идёт после общего
    разбора документа, чтобы значения исполнения перекрыли значения семейства."""

    if not product.registry_modification or not si_type.allowed_modifications:
        return 0
    # Только исполнения, заведённые из реестра: у них полное обозначение и есть сама модель.
    # У модели с сайта обозначение из списка «представлены на испытания» — один из образцов
    # семейства, а не она: «CE210 C10.746.B SPDS» получала расшифровку образца «…L1F»
    # (замечание 13.10.2026), МИРТЕК-12-РУ-SP17 — «80 А» вместо своих 100 А.
    if product.data_source != ProductDataSource.FGIS.value:
        return 0
    text = (
        "Структура условного обозначения типа (легенда из реестра средств измерений):\n"
        f"{si_type.allowed_modifications}\n\n"
        "Условное обозначение конкретного исполнения прибора, которое нужно расшифровать по "
        f"легенде выше: {product.registry_modification}\n"
        "Расшифруй сегменты обозначения в характеристики прибора. Записывай значение, а не код: "
        "если легенда называет только смысл позиции («код класса точности», «количество "
        "фаз»), но не говорит, что означает стоящий в ней код, — эту позицию пропусти. "
        "Нельзя записывать буквы и цифры обозначения как значение поля."
    )
    try:
        reading = characteristic_extraction.read_characteristics(
            db, text=text, document_source=DOCUMENT_SOURCE
        )
    except Exception as exc:  # noqa: BLE001 - см. ingest_description_types
        logger.warning(f"Аршин: расшифровка обозначения «{product.registry_modification}» не удалась: {exc}")
        return 0
    outcome.modifications_decoded += 1
    # Страховка на случай, если модель всё же переписала код: «F», «L1», «2» — это куски
    # обозначения, а не характеристики.
    decoded = [
        item
        for item in reading.characteristics
        if not characteristic_values.looks_like_designation_code(
            item.value, product.registry_modification, item.field_name
        )
    ]
    return _apply(db, product, decoded)


def mark_description_changed(si_type: SiType, *, new_version: str | None) -> None:
    """Вызывается ревалидацией ФГИС при новой редакции документа: текст прежней редакции
    больше не описывает актуальный тип, а дата нужна интерфейсу."""

    si_type.description_type_version = new_version
    si_type.description_type_text = None
    si_type.description_type_changed_at = datetime.now(timezone.utc)
