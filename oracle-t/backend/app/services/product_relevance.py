"""Отбор моделей производителя в контекст сопоставления (раздел 5.5 ТЗ).

Зачем отдельный модуль. В карточку производителя, которая уходит модели вместе с
требованиями закупки, помещается лишь несколько моделей: у Энергомеры их 221, и весь
каталог в один запрос не влезет ни по токенам, ни по здравому смыслу. Раньше эти несколько
брались первыми по алфавиту — то есть в сравнение с требованиями тендера на трёхфазные
счётчики прямого включения уходили первые шесть названий по алфавиту, а подходящая модель
могла быть двухсотой. Вердикт при этом получался честным по форме и бессмысленным по сути:
«no_data» на требование, которому в каталоге отвечает конкретный прибор.

Отбор здесь детерминированный, кодом, а не моделью, и это осознанно: какие модели вообще
показать — решение, которое должно быть воспроизводимым и объяснимым, иначе разбирать
расхождения в матрице соответствия невозможно. Модель отвечает на вопрос «подходит ли
прибор», а не «какой прибор посмотреть».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, ProductStatus
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services.meter_kind import kind_group, kinds_from_text, product_kinds

# Вес прямого упоминания модели в требованиях. Заведомо больше любой суммы совпадений по
# характеристикам: если закупка называет прибор по имени («счётчик типа CE208»), эта модель
# должна попасть в карточку, чем бы ни отличались остальные.
NAMED_MODEL_SCORE = 100.0

# Совпадение и расхождение по числу фаз. Требование «трёхфазный» отсекает однофазные приборы
# целиком — это не одна характеристика из многих, а граница применимости.
PHASE_MATCH_SCORE = 12.0
PHASE_MISMATCH_SCORE = -12.0

# Совпадение и расхождение по типу прибора (11 видов файла «Параметры для ПУ»: фазность ×
# включение × крепление). Сильнее фазности: тип её включает и различает, например,
# трёхфазный прямого включения и полукосвенного — у них разные токи и разные ТЗ.
KIND_MATCH_SCORE = 15.0
KIND_MISMATCH_SCORE = -15.0

# Совпадение отдельного значения характеристики со значением из требований.
VALUE_MATCH_SCORE = 1.0

# Снятая с производства модель годится для сравнения, но проигрывает действующей при прочих
# равных: поставить её по итогам закупки нельзя.
DISCONTINUED_PENALTY = -3.0

# Полнота карточки как решающий признак при равенстве остального: из двух моделей, одинаково
# отвечающих требованиям, полезнее та, о которой в справочнике больше известно.
COMPLETENESS_WEIGHT = 0.01

_PHASE_PATTERNS = (
    (re.compile(r"трех|трёх|3-?\s?фаз|3х230|3\s?[хx]\s?230", re.IGNORECASE), "3"),
    (re.compile(r"однофаз|1-?\s?фаз", re.IGNORECASE), "1"),
)

# Значения, по которым имеет смысл искать совпадение: числа (класс точности, ток, напряжение,
# межповерочный интервал) и обозначения интерфейсов и протоколов. Отдельные слова русского
# языка не берутся — «счётчик» и «прибор» есть у всех, и совпадение по ним ничего не значит.
_VALUE_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?|(?:RS|PLC|GSM|NB|LTE|G3|СПОДЭС|SPODES)[\w-]*", re.IGNORECASE)

# Токены, которые встречаются в любой закупке приборов учёта и потому ничего не различают.
_STOP_VALUES = frozenset({"1", "2", "3", "4", "5", "0", "10", "100"})


@dataclass(frozen=True)
class ScoredProduct:
    product: Product
    score: float
    characteristics: list[ProductCharacteristic]


def select_products_for_context(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    *,
    limit: int,
    named_text: str = "",
) -> list[ScoredProduct]:
    """Модели производителя, наиболее отвечающие требованиям закупки, — не более `limit`.

    `named_text` — текст документации закупки (`tender_named_text`): прямое упоминание модели
    ищется и в нём, а не только в требованиях. Требования — пересказ модели, и обозначение
    прибора она выбрасывает: на закупке 32616436166 ТЗ называет «CE207 R7.849.2.OG.QUVLF GS01
    SPDs (или эквивалент)», а из 90 извлечённых требований его нет ни в одном — в карточку
    Энергомеры уходили CE101 и CE102M, и эталон ТЗ «не проходил» по габаритам CE101.

    Модели без единой характеристики отбрасываются: в карточке производителя от них ровно
    ноль пользы, а место они занимают. Исключение — производитель, у которого характеристик
    нет ни у одной модели: тогда возвращаются первые по алфавиту, чтобы он вовсе не исчез из
    матрицы соответствия.
    """

    products = list(
        db.scalars(
            select(Product)
            .where(Product.manufacturer_id == manufacturer.id)
            .order_by(Product.model_name)
        )
    )
    if not products:
        return []

    by_product: dict[object, list[ProductCharacteristic]] = {}
    for characteristic in db.scalars(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id.in_([p.id for p in products])
        )
    ):
        by_product.setdefault(characteristic.product_id, []).append(characteristic)

    described = [p for p in products if by_product.get(p.id)]
    if not described:
        return [ScoredProduct(product, 0.0, []) for product in products[:limit]]

    needle = _requirements_text(requirements)
    named = _NamedNeedle(_fold_lookalikes(needle), _fold_lookalikes(named_text.lower()))
    wanted_phases = _phases_in(needle)
    wanted_values = _values_in(needle)
    # Не по `needle`: он в нижнем регистре, а «А» (амперы) от союза «а» отличает регистр.
    wanted_kinds = set(
        kinds_from_text(
            " ".join((item.normalized_text or item.text or "") for item in requirements)
        )
    )

    scored = [
        ScoredProduct(
            product=product,
            score=_score(
                product, by_product[product.id], named, wanted_phases, wanted_values
            )
            + _kind_score(product, by_product[product.id], wanted_kinds),
            characteristics=by_product[product.id],
        )
        for product in described
    ]
    # Сортировка стабильная, а исходный список уже упорядочен по названию: модели с равным
    # счётом идут по алфавиту, то есть отбор воспроизводим от прогона к прогону.
    scored.sort(key=lambda item: item.score, reverse=True)
    return _cover_kinds(scored, limit, wanted_kinds)


def named_products(db: Session, manufacturer: Manufacturer, named_text: str) -> list[Product]:
    """Модели производителя, прямо названные в документации закупки (код модели или
    артикул отдельным словом). ТЗ часто называет прибор без марки — «CE207 R7.849.2.OG…
    (или эквивалент)», — и по марке заточку под производителя не найти."""

    named = _NamedNeedle("", _fold_lookalikes(named_text.lower()))
    return [
        product
        for product in db.scalars(
            select(Product)
            .where(Product.manufacturer_id == manufacturer.id)
            .order_by(Product.model_name)
        )
        if _is_named(product, named)
    ]


def _cover_kinds(scored: list[ScoredProduct], limit: int, wanted: set[str]) -> list[ScoredProduct]:
    """Первые `limit` по счёту плюс лучшая модель каждой закупаемой группы типов
    (фазность × включение), которой среди них нет.

    Закупка бывает многопозиционной: в одной спецификации СЕ101, СЕ208 и СЕ308 — однофазные
    и трёхфазные. Шесть лучших по сумме совпадений оказывались одного типа, и позиции
    другого типа получали «не соответствует» у всех производителей (живой прогон 25.09.2026,
    закупка 32616309303: «не проходит» даже у Энергомеры, чьи приборы названы в ТЗ)."""

    chosen = scored[:limit]
    wanted_groups = {kind_group(code) for code in wanted}
    covered = {
        kind_group(code)
        for item in chosen
        for code in product_kinds(item.product, item.characteristics)
    }
    for group in sorted(wanted_groups - covered):
        for item in scored[limit:]:
            if item in chosen:
                continue
            groups = {kind_group(code) for code in product_kinds(item.product, item.characteristics)}
            if group in groups:
                chosen.append(item)
                covered |= groups
                break
    return chosen


def _kind_score(
    product: Product, characteristics: list[ProductCharacteristic], wanted: set[str]
) -> float:
    if not wanted:
        return 0.0
    own = set(product_kinds(product, characteristics))
    if not own:
        return 0.0
    return KIND_MATCH_SCORE if own & wanted else KIND_MISMATCH_SCORE


# Сколько текста документации просматривать в поисках названия модели. Обозначение прибора
# стоит в заголовке ТЗ и в спецификации — в начале документов; хвост в мегабайты (сканы
# договоров, приложения) поиск только замедлит.
NAMED_TEXT_LIMIT = 400_000


def tender_named_text(db: Session, tender: Tender) -> str:
    """Наименование закупки и текст её документов — где искать прямо названные модели."""

    ids = [tender.id] + ([tender.analysis_tender_id] if tender.analysis_tender_id else [])
    texts = db.scalars(
        select(TenderDocument.extracted_text).where(
            TenderDocument.tender_id.in_(ids), TenderDocument.extracted_text.is_not(None)
        )
    )
    return "\n".join([tender.title or "", *texts])[:NAMED_TEXT_LIMIT]


# Кириллические буквы, неотличимые на вид от латинских. В обозначениях приборов их путают
# постоянно: в каталоге Энергомеры есть и «CE207» латиницей, и «СЕ207» кириллицей, а ТЗ
# пишет как придётся. Для поиска упоминаний обе стороны приводятся к латинице.
_LOOKALIKES = str.maketrans("авекмнорстух", "abekmhopctyx")


def _fold_lookalikes(text: str) -> str:
    return text.translate(_LOOKALIKES)


def _requirements_text(requirements: list[Requirement]) -> str:
    return " ".join(
        (requirement.normalized_text or requirement.text or "") for requirement in requirements
    ).lower()


def _phases_in(text: str) -> set[str]:
    return {phases for pattern, phases in _PHASE_PATTERNS if pattern.search(text)}


def _values_in(text: str) -> set[str]:
    tokens = {
        token.replace(",", ".").lower() for token in _VALUE_TOKEN_RE.findall(text)
    }
    return tokens - _STOP_VALUES


def _score(
    product: Product,
    characteristics: list[ProductCharacteristic],
    named: _NamedNeedle,
    wanted_phases: set[str],
    wanted_values: set[str],
) -> float:
    score = 0.0

    if _is_named(product, named):
        score += NAMED_MODEL_SCORE

    if wanted_phases:
        own = _product_phases(product, characteristics)
        if own:
            score += PHASE_MATCH_SCORE if own & wanted_phases else PHASE_MISMATCH_SCORE

    if wanted_values:
        own_values: set[str] = set()
        for characteristic in characteristics:
            own_values |= _values_in(characteristic.value or "")
        score += VALUE_MATCH_SCORE * len(own_values & wanted_values)

    if product.status == ProductStatus.DISCONTINUED.value:
        score += DISCONTINUED_PENALTY

    return score + COMPLETENESS_WEIGHT * len(characteristics)


@dataclass(frozen=True)
class _NamedNeedle:
    """Где искать название модели: в требованиях и в документации закупки (обе строки в нижнем
    регистре, буквы-двойники приведены к латинице)."""

    requirements: str
    documents: str


# Обозначение, которое можно искать в полном тексте документации: буквы и цифры вместе
# («ce207 r7.849…», «ce208-c2-849»), не короче пяти знаков. Артикулы «114» и «1325» нашлись
# бы в любом договоре среди сумм и номеров пунктов, а коды-слова «нева», «дельта» — в тексте.
_DOCUMENT_CODE_MIN = 5


def _is_named(product: Product, named: _NamedNeedle) -> bool:
    """Названа ли модель в закупке прямо. Сравнивается код модели, а не полное
    наименование: закупка пишет «CE208», а в справочнике стоит «Счетчик электроэнергии
    трехфазный CE208 S31». Слишком короткие коды не берём — «МИР» нашлось бы в «мире».

    В требованиях хватает вхождения кода; в документации — только отдельным словом и только
    код из букв и цифр (`_DOCUMENT_CODE_MIN`)."""

    for candidate in (product.model_code, product.article):
        value = _fold_lookalikes((candidate or "").strip().lower())
        if len(value) >= 4 and value in named.requirements:
            return True
        if _searchable_in_documents(value) and _has_word(named.documents, value):
            return True
    return False


def _searchable_in_documents(code: str) -> bool:
    return (
        len(code) >= _DOCUMENT_CODE_MIN
        and any(ch.isdigit() for ch in code)
        and any(ch.isalpha() for ch in code)
    )


def _has_word(text: str, code: str) -> bool:
    """Вхождение `code` в `text`, не являющееся частью более длинного обозначения:
    «ce207» не находится в «ce2070», «ce207 r7.849.2.oa» — в «…oag»."""

    start = text.find(code)
    while start != -1:
        end = start + len(code)
        before = text[start - 1] if start else " "
        after = text[end] if end < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = text.find(code, start + 1)
    return False


def _product_phases(product: Product, characteristics: list[ProductCharacteristic]) -> set[str]:
    """Сколько фаз у прибора: сначала по характеристике справочника, затем по типу прибора и
    наименованию — заполнено поле «Количество фаз» не у всех производителей."""

    for characteristic in characteristics:
        if characteristic.field_name == "Количество фаз" and characteristic.value:
            digits = re.findall(r"[13]", characteristic.value)
            if digits:
                return set(digits)

    return _phases_in(f"{product.device_type or ''} {product.model_name or ''}")
