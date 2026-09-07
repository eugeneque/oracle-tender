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

# Вес прямого упоминания модели в требованиях. Заведомо больше любой суммы совпадений по
# характеристикам: если закупка называет прибор по имени («счётчик типа CE208»), эта модель
# должна попасть в карточку, чем бы ни отличались остальные.
NAMED_MODEL_SCORE = 100.0

# Совпадение и расхождение по числу фаз. Требование «трёхфазный» отсекает однофазные приборы
# целиком — это не одна характеристика из многих, а граница применимости.
PHASE_MATCH_SCORE = 12.0
PHASE_MISMATCH_SCORE = -12.0

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
) -> list[ScoredProduct]:
    """Модели производителя, наиболее отвечающие требованиям закупки, — не более `limit`.

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
    wanted_phases = _phases_in(needle)
    wanted_values = _values_in(needle)

    scored = [
        ScoredProduct(
            product=product,
            score=_score(product, by_product[product.id], needle, wanted_phases, wanted_values),
            characteristics=by_product[product.id],
        )
        for product in described
    ]
    # Сортировка стабильная, а исходный список уже упорядочен по названию: модели с равным
    # счётом идут по алфавиту, то есть отбор воспроизводим от прогона к прогону.
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


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
    needle: str,
    wanted_phases: set[str],
    wanted_values: set[str],
) -> float:
    score = 0.0

    if _is_named(product, needle):
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


def _is_named(product: Product, needle: str) -> bool:
    """Названа ли модель в требованиях прямо. Сравнивается код модели, а не полное
    наименование: закупка пишет «CE208», а в справочнике стоит «Счетчик электроэнергии
    трехфазный CE208 S31». Слишком короткие коды не берём — «МИР» нашлось бы в «мире»."""

    for candidate in (product.model_code, product.article):
        value = (candidate or "").strip().lower()
        if len(value) >= 4 and value in needle:
            return True
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
