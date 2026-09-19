"""AI-экстракция характеристик продукции из текста документов (раздел 5.3 ТЗ, п.2-3
алгоритма заполнения каталога — Этап 4).

Два источника текста, разная природа:
- «Описание типа» из ФГИС (`si_types.description_type_text`) → метрологические/электрические
  группы Приложения C;
- «Руководство пользователя» с сайта производителя → интерфейсы, протоколы, функциональные
  возможности и т.д.

Общее у них — способ извлечения: модели передаётся текст документа и **явный список полей**
из Приложения C (`app/seed/characteristics_data.py`). Без явного списка модель возвращает
формулировки исходного документа («Номинальный (максимальный) ток: 5(100) А» одним полем)
вместо полей каталога — проверено вживую на реальном вызове YandexGPT.

Раздел 5.3 ТЗ требует **обязательную проверку человеком перед сохранением в каталог**:
поэтому всё извлечённое сохраняется с `verified_by_user=False`, а значения, подтверждённые
или введённые человеком (`verified_by_user=True` / `source=manual_entry`), повторной
экстракцией не перезаписываются.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.manufacturer import CharacteristicSource, Product, ProductCharacteristic
from app.models.user import User
from app.seed.characteristics_data import fields_for_source, resolve_field
from app.services.audit import log_action
from app.services.ai_client import chunk_text, run_structured

# Кусок текста на один запрос к модели. Контекст YandexGPT Pro — 32k токенов (раздел 5.4 ТЗ
# упоминает облачную модель без указания лимита); 12k символов кириллицы — консервативная
# оценка, оставляющая место под системный промпт со списком полей (он сам по себе объёмный:
# ~120 полей Приложения C) и под ответ модели.
MAX_CHUNK_CHARS = 12_000

_SYSTEM_PROMPT_TEMPLATE = """Ты извлекаешь технические характеристики приборов учёта \
(счётчиков электроэнергии и других коммунальных ресурсов) из технической документации.

Правила:
1. Извлекай ТОЛЬКО те характеристики, значения которых явно указаны в тексте. \
Ничего не додумывай и не рассчитывай.
2. `group_name` — название группы ровно как в справочнике ниже.
3. `field_name` — название ТОЛЬКО САМОГО ПОЛЯ, ровно как в справочнике ниже. \
НЕ добавляй в `field_name` название группы и никаких разделителей. \
Правильно: "Номинальное напряжение". Неправильно: \
"Электрические характеристики → Номинальное напряжение".
4. Если характеристика есть в тексте, но подходящего поля в справочнике нет — пропусти её.
5. Если в тексте характеристика указана слитно, а в справочнике разделена (например, \
«Номинальный (максимальный) ток: 5(100) А» → поля «Номинальный ток» и «Максимальный ток»), \
раздели значение по соответствующим полям справочника.
6. `confidence` — насколько ты уверен, что значение относится именно к этому полю: \
1.0 — точное совпадение формулировки, 0.5 — значение выведено из контекста.
7. Если в тексте нет ни одной характеристики из справочника — верни пустой список.

Справочник допустимых полей:
{fields_list}"""


class ExtractedCharacteristic(pydantic.BaseModel):
    group_name: str
    field_name: str
    value: str
    confidence: float = 1.0


class ExtractionResult(pydantic.BaseModel):
    characteristics: list[ExtractedCharacteristic]


class _SaveVerdict(enum.Enum):
    SAVED = "saved"
    SKIPPED_UNKNOWN_FIELD = "skipped_unknown_field"
    SKIPPED_PROTECTED = "skipped_protected"


@dataclass
class ExtractionOutcome:
    """Итог экстракции для отчёта пользователю (раздел 5.3 ТЗ — результат уходит на проверку
    человеком, поэтому важно показать не только «сколько сохранено», но и что отброшено).

    `saved` считает сохранённые ЗНАЧЕНИЯ, а не уникальные поля: модель может вернуть одно и то
    же поле дважды (в одном ответе или из соседних кусков с перекрытием), и повторное
    сохранение обновляет уже созданную запись. Поэтому `saved` бывает больше, чем итоговое
    число характеристик у модели прибора — это норма, а не признак дублей в каталоге
    (от них защищает `uq_product_characteristics_field`)."""

    saved: int = 0
    skipped_unknown_field: int = 0
    skipped_protected: int = 0
    chunks_processed: int = 0
    chunks_failed: int = 0


def _build_system_prompt(source: str) -> str:
    # Поля сгруппированы по группам, а не перечислены плоско как «группа → поле»: при плоском
    # формате модель воспроизводила всю строку целиком в `field_name`
    # («Электрические характеристики → Номинальное напряжение») — проверено на реальном вызове,
    # все 13 извлечённых характеристик отбраковывались фильтром по справочнику.
    grouped: dict[str, list[str]] = {}
    # `field_name`, а не `field`: имя `field` здесь затенило бы `dataclasses.field`,
    # импортированный в этом модуле.
    for group, field_name in fields_for_source(source):
        grouped.setdefault(group, []).append(field_name)

    blocks = []
    for group, group_fields in grouped.items():
        listed = "\n".join(f"  - {field_name}" for field_name in group_fields)
        blocks.append(f'Группа "{group}", её поля:\n{listed}')
    return _SYSTEM_PROMPT_TEMPLATE.format(fields_list="\n\n".join(blocks))


def _normalise_field_name(group_name: str, field_name: str) -> str:
    """Страховка от рецидива описанной выше ошибки: если модель всё же вернула
    «Группа → Поле» или «Группа: Поле» в `field_name`, берём часть после разделителя,
    а не выбрасываем корректно извлечённое значение целиком."""

    cleaned = field_name.strip()
    for separator in ("→", "->", ":", "—"):
        head, found, tail = cleaned.partition(separator)
        # Сравниваем со снятыми пробелами: модель ставит их вокруг разделителя произвольно
        # («Группа → Поле», «Группа→Поле», «Группа: Поле»).
        if found and head.strip().lower() == group_name.strip().lower() and tail.strip():
            return tail.strip()
    return cleaned


@dataclass
class DocumentReading:
    """Что модель вычитала из одного документа. Отделено от записи в справочник, потому что
    один и тот же документ описывает несколько моделей: руководство «Меркурий 200/201»
    покрывает десяток исполнений, и гонять его через модель для каждого — платить за один и
    тот же разбор столько раз, сколько у семейства позиций."""

    characteristics: list["ExtractedCharacteristic"] = field(default_factory=list)
    chunks_processed: int = 0
    chunks_failed: int = 0


def read_characteristics(db: Session, *, text: str, document_source: str) -> DocumentReading:
    """Извлекает характеристики из текста, ничего не сохраняя."""

    reading = DocumentReading()
    system_prompt = _build_system_prompt(document_source)

    for chunk in chunk_text(text, max_chars=MAX_CHUNK_CHARS):
        try:
            result = run_structured(
                db,
                system_prompt=system_prompt,
                user_text=chunk,
                response_model=ExtractionResult,
            )
        except Exception as exc:  # noqa: BLE001 - сбой одного куска не должен терять уже извлечённое из остальных
            reading.chunks_failed += 1
            logger.warning(f"Экстракция характеристик: кусок текста не обработан: {exc}")
            continue

        reading.chunks_processed += 1
        reading.characteristics.extend(result.characteristics)
    return reading


def apply_characteristics(
    db: Session,
    product: Product,
    characteristics: list["ExtractedCharacteristic"],
    *,
    characteristic_source: CharacteristicSource,
    outcome: ExtractionOutcome,
) -> None:
    """Записывает вычитанное конкретной модели, считая исходы в `outcome`."""

    for item in characteristics:
        verdict = _save_characteristic(
            db, product, item, characteristic_source=characteristic_source
        )
        if verdict is _SaveVerdict.SAVED:
            outcome.saved += 1
        elif verdict is _SaveVerdict.SKIPPED_UNKNOWN_FIELD:
            outcome.skipped_unknown_field += 1
        else:
            outcome.skipped_protected += 1


def extract_characteristics_for_product(
    db: Session,
    product: Product,
    *,
    text: str,
    document_source: str,
    characteristic_source: CharacteristicSource,
    actor: User,
) -> ExtractionOutcome:
    """Извлекает характеристики из `text` и сохраняет их для `product`.

    `document_source` — какой набор полей Приложения C искать (`fgis` / `manufacturer`,
    см. `PRIMARY_SOURCE`); `characteristic_source` — что записать в `product_characteristics.
    source` как происхождение значения.
    """

    outcome = ExtractionOutcome()
    if not text or not text.strip():
        log_action(
            db,
            component="product_catalog",
            action=f"extract_characteristics:{product.id}",
            result="skipped",
            level=LogLevel.WARNING,
            details="Пустой текст документа — нечего извлекать",
            user_id=actor.id,
        )
        db.commit()
        return outcome

    reading = read_characteristics(db, text=text, document_source=document_source)
    outcome.chunks_processed = reading.chunks_processed
    outcome.chunks_failed = reading.chunks_failed
    apply_characteristics(
        db,
        product,
        reading.characteristics,
        characteristic_source=characteristic_source,
        outcome=outcome,
    )

    log_action(
        db,
        component="product_catalog",
        action=f"extract_characteristics:{product.id}",
        result="success" if outcome.saved else ("error" if outcome.chunks_failed else "empty"),
        level=LogLevel.INFO if outcome.saved else LogLevel.WARNING,
        details=(
            f"Сохранено {outcome.saved}, пропущено (неизвестное поле) "
            f"{outcome.skipped_unknown_field}, пропущено (ручные/подтверждённые) "
            f"{outcome.skipped_protected}; кусков обработано {outcome.chunks_processed}, "
            f"с ошибкой {outcome.chunks_failed}"
        ),
        user_id=actor.id,
    )
    db.commit()
    return outcome


def _save_characteristic(
    db: Session,
    product: Product,
    item: ExtractedCharacteristic,
    *,
    characteristic_source: CharacteristicSource,
) -> _SaveVerdict:
    # Модель, несмотря на явный список, называет поля своими словами: «Протокол DLMS/COSEM»
    # вместо «DLMS/COSEM», «Вид монтажа» вместо «Тип монтажа», кладёт «Максимальный ток» в
    # «Интерфейсы и связь». `resolve_field` приводит такое к справочнику — вместе с группой,
    # потому что группа однозначно определяется полем. Что опознать не удалось, по-прежнему
    # отбрасывается: иначе схема Приложения C перестанет быть предсказуемой.
    if not item.value or not item.value.strip():
        return _SaveVerdict.SKIPPED_UNKNOWN_FIELD

    normalised_name = _normalise_field_name(item.group_name, item.field_name)
    resolved = resolve_field(item.group_name, normalised_name)
    if resolved is None:
        # Поля нет в Приложении C — но значение не выбрасывается, а откладывается в
        # `extra_specifications`, как это давно делает обход сайта. Новые характеристики
        # приборов (замечание заказчика 15.09.2026) появляются в документации раньше, чем в
        # справочнике, и отброшенное здесь значение пришлось бы добывать заново после
        # расширения справочника; отложенное — видно в карточке и в сводке кандидатов на
        # новые поля (`unknown_fields_summary`).
        _stash_unknown_field(product, normalised_name, item.value.strip())
        logger.debug(
            f"Экстракция: поле вне справочника Приложения C отложено: "
            f"{item.group_name!r} → {normalised_name!r}"
        )
        return _SaveVerdict.SKIPPED_UNKNOWN_FIELD
    group_name, field_name = resolved

    existing = db.scalar(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )

    if existing is not None:
        # Раздел 5.3 ТЗ: ручной ввод имеет приоритет; подтверждённое человеком значение
        # не должен перетирать повторный автоматический прогон.
        if existing.verified_by_user or existing.source == CharacteristicSource.MANUAL_ENTRY.value:
            return _SaveVerdict.SKIPPED_PROTECTED
        existing.value = item.value.strip()
        existing.source = characteristic_source.value
        existing.confidence = _clamp_confidence(item.confidence)
        return _SaveVerdict.SAVED

    db.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name=group_name,
            field_name=field_name,
            value=item.value.strip(),
            source=characteristic_source.value,
            confidence=_clamp_confidence(item.confidence),
            verified_by_user=False,  # раздел 5.3 ТЗ — обязательная проверка человеком
        )
    )
    # Flush обязателен: куски текста режутся с перекрытием (см. `chunk_text`), поэтому одна и
    # та же характеристика закономерно приходит из двух соседних кусков. Без flush `select`
    # выше не увидел бы только что добавленный объект (он ещё в сессии, не в БД), и вторая
    # вставка упала бы на `uq_product_characteristics_field`, уронив всю транзакцию целиком —
    # то есть потерялись бы и все остальные характеристики документа.
    db.flush()
    return _SaveVerdict.SAVED


# Предел числа отложенных полей у одной модели: модель иногда возвращает документ целиком
# построчно, и без предела JSON-поле разрасталось бы на сотни строк.
MAX_EXTRA_SPECIFICATIONS = 120


def _stash_unknown_field(product: Product, field_name: str, value: str) -> None:
    key = field_name.strip()[:150]
    if not key:
        return
    extra = dict(product.extra_specifications or {})
    if key not in extra and len(extra) >= MAX_EXTRA_SPECIFICATIONS:
        return
    extra[key] = value[:500]
    # Новый словарь, а не правка на месте: SQLAlchemy замечает изменение JSONB только при
    # переприсваивании атрибута.
    product.extra_specifications = extra


def unknown_fields_summary(db: Session, manufacturer_id: uuid.UUID | None = None, *, limit: int = 50) -> list[dict]:
    """Сводка характеристик вне Приложения C по всем моделям — кандидаты на расширение
    справочника. Считается по `extra_specifications`: сколько моделей несут поле и пример
    значения. Именно так 06.09.2026 в справочник попали СПОДЭС и Bluetooth — но тогда список
    собирали руками из лога, теперь он на виду."""

    from collections import Counter

    query = select(Product).where(Product.extra_specifications != {})
    if manufacturer_id is not None:
        query = query.where(Product.manufacturer_id == manufacturer_id)

    counter: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for product in db.scalars(query):
        for key, value in (product.extra_specifications or {}).items():
            normalised = key.strip().lower()
            counter[normalised] += 1
            samples.setdefault(normalised, f"{key}: {value}")
    return [
        {"field_name": samples[key].split(":", 1)[0], "products": count, "sample": samples[key]}
        for key, count in counter.most_common(limit)
    ]


def _clamp_confidence(value: float | None) -> float | None:
    """Модель иногда возвращает confidence вне [0;1] (например, 95 вместо 0.95) — колонка
    `Numeric(4,3)` такое не примет, а падать из-за косметики ответа незачем."""

    if value is None:
        return None
    if value > 1:
        value = value / 100 if value <= 100 else 1.0
    return round(max(0.0, min(1.0, float(value))), 3)


@dataclass
class ManualExtractionOutcome:
    """Итог экстракции с сайта производителя (раздел 5.3 ТЗ, п.3 алгоритма). Помимо счётчиков
    самой экстракции содержит найденный документ — пользователю важно видеть, из какого именно
    файла заполнен каталог, чтобы проверить результат."""

    manual_url: str | None = None
    manual_title: str | None = None
    extraction: ExtractionOutcome = field(default_factory=ExtractionOutcome)
    message: str | None = None


def extract_characteristics_from_manufacturer_site(
    db: Session,
    product: Product,
    *,
    website: str,
    actor: User,
    adapter=None,
) -> ManualExtractionOutcome:
    """Полный цикл п.3 алгоритма раздела 5.3 ТЗ: найти на сайте производителя «Руководство
    пользователя» нужной модели → скачать → извлечь текст → достать характеристики тех групп,
    которых нет в «Описании типа» ФГИС.

    Все неудачи — штатный результат с текстом причины, а не исключение: сайт производителя
    внешний и может не содержать руководства вовсе (раздел 5.9 ТЗ)."""

    from app.adapters.manufacturer_site import (
        ManualCandidate,
        ManufacturerSiteAdapter,
        download_document_text,
    )
    from app.models.manufacturer import Manufacturer
    from app.services import document_discovery

    outcome = ManualExtractionOutcome()
    adapter = adapter or ManufacturerSiteAdapter()

    # Сначала — поисковик по официальному сайту (замечание заказчика 15.09.2026): документ
    # на новое исполнение лежит на сайте, но каталог на него не ссылается, и обход вслепую
    # его не найдёт. Обход остаётся запасным путём — когда поиск не настроен или ничего не
    # дал.
    candidates: list = []
    manufacturer = db.get(Manufacturer, product.manufacturer_id)
    if manufacturer is not None:
        found = document_discovery.find_manual_for_product(db, product, manufacturer)
        if found is not None:
            document_discovery.save_manual_link(db, product, found.url)
            candidates = [
                ManualCandidate(
                    url=found.url, title=found.title, found_on="поиск Яндекс", score=float(found.score)
                )
            ]

    if not candidates:
        try:
            candidates = adapter.find_user_manual(website, product.model_name)
        except Exception as exc:  # noqa: BLE001 - внешний сайт не должен ронять запрос
            logger.warning(f"Сайт производителя: обход {website} не удался: {exc}")
            candidates = []

    if not candidates:
        outcome.message = (
            f"На сайте {website} не найдено руководство пользователя для модели "
            f"«{product.model_name}». Документ можно указать вручную."
        )
        log_action(
            db,
            component="product_catalog",
            action=f"extract_from_manufacturer_site:{product.id}",
            result="empty",
            level=LogLevel.WARNING,
            details=outcome.message,
            user_id=actor.id,
        )
        db.commit()
        return outcome

    best = candidates[0]
    outcome.manual_url, outcome.manual_title = best.url, best.title

    text = download_document_text(best.url)
    if not text:
        outcome.message = f"Документ найден ({best.url}), но текст из него не извлечён."
        log_action(
            db,
            component="product_catalog",
            action=f"extract_from_manufacturer_site:{product.id}",
            result="error",
            level=LogLevel.WARNING,
            details=outcome.message,
            user_id=actor.id,
        )
        db.commit()
        return outcome

    outcome.extraction = extract_characteristics_for_product(
        db,
        product,
        text=text,
        document_source="manufacturer",
        characteristic_source=CharacteristicSource.USER_MANUAL,
        actor=actor,
    )
    return outcome


def list_characteristics(db: Session, product_id: uuid.UUID) -> list[ProductCharacteristic]:
    return list(
        db.scalars(
            select(ProductCharacteristic)
            .where(ProductCharacteristic.product_id == product_id)
            .order_by(ProductCharacteristic.group_name, ProductCharacteristic.field_name)
        )
    )
