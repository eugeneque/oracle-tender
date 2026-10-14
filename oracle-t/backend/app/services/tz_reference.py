"""Эталон ТЗ: модель и производитель, под которых написано техническое задание (06.10.2026).

Закупка 32616436166: ТЗ озаглавлено «…„CE207 R7.849.2.OG.QUVLF GS01 SPDs" (или
эквивалент)» и требует работу через «AdminTools» и «CE Net-Connection» — фирменные программу
и сервер Энергомеры. Матрица этого не знала: Энергомере ставила «не хватает данных» (в
карточку уходили чужие модели), МИРТЕК — «с оговорками 92%», хотя заявку с прибором МИРТЕК
по фирменному ПО Энергомеры отклонят.

Эталон определяется кодом, по данным, а не догадкой модели:

1. обозначение модели из каталога найдено в тексте документации (`named_products`) — точное
   совпадение кода модели, самый сильный сигнал;
2. требование «Модель прибора: …» и требования с фирменным ПО (`Requirement.vendor`) — их
   производителя модель называет при извлечении, код сверяет имя со справочником.

ПО верхнего уровня («Пирамида», «Энергосфера») эталоном не считается: эти системы
поддерживают приборы многих производителей и указывают лишь на круг совместимых.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic
from app.services.product_relevance import named_products

# Фраза о закупке товара определённого знака или о запрете эквивалента.
TRADEMARK_CLAUSE_RE = re.compile(
    r"[^.;]{0,160}(?:товар\w*\s+(?:определенн|определённ|конкретн)\w*\s+товарн\w*\s+знак\w*"
    r"|эквивалент\w*\s+не\s+допуска\w*|без\s+(?:права\s+)?(?:предоставлени\w+\s+)?эквивалент\w*"
    r"|несовместимост\w*\s+с\s+товарами)[^.;]{0,200}",
    re.IGNORECASE,
)
EQUIVALENT_ALLOWED_RE = re.compile(r"или\s+эквивалент", re.IGNORECASE)

MODEL_REQUIREMENT_PREFIX = "модель прибора"

# Кириллица ↔ латиница для сравнения имён производителей, набранных вперемешку.
_LOOKALIKES = str.maketrans("авекмнорстух", "abekmhopctyx")
_LEGAL_FORM_RE = re.compile(r"\b(?:ооо|ао|пао|зао|оао|нпо|нпп|ип)\b|[«»\"'()]", re.IGNORECASE)


@dataclass
class TzReference:
    manufacturer: Manufacturer
    # Модели каталога, прямо названные в документации.
    products: list[Product] = field(default_factory=list)
    # Обозначения, как они названы в ТЗ (из требования «Модель прибора: …» или коды моделей).
    designations: list[str] = field(default_factory=list)
    # True — «или эквивалент»; False — эквивалент запрещён / товар определённого знака;
    # None — документация молчит.
    equivalent_allowed: bool | None = None
    clause: str | None = None
    # Группы типов (фазность × включение) названных моделей — по характеристикам каталога.
    # По ним «Тип прибора» у эталона сверяется кодом: модель сравнения то и дело брала из
    # карточки не то исполнение и ставила производителю «не соответствует» по типу его же
    # прибора, названного в ТЗ (32616394121, МИР С-04 сплит, проверка 13.10.2026).
    kind_groups: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        return "; ".join(self.designations[:3]) or (
            self.manufacturer.brand_name or self.manufacturer.legal_name
        )


def is_model_requirement(requirement: Requirement) -> bool:
    return (requirement.normalized_text or "").strip().lower().startswith(MODEL_REQUIREMENT_PREFIX)


def names_reference_model(requirement: Requirement, reference: TzReference | None) -> bool:
    """Требование прямо называет модель эталона, хоть и не в форме «Модель прибора: …».

    До 06.10.2026 извлечение записывало обозначение как «Наименование: Прибор учета МИР
    С-05.10-230-5(80)-…» (32616408289, эквивалент запрещён), и запрет эквивалента на такие
    строки не действовал: конкуренты получали «не хватает данных» вместо «не проходит».
    Сравниваются обозначения из букв и цифр не короче пяти знаков — «МИР» есть в «Энергомир»."""

    if reference is None:
        return False
    text = _fold(f"{requirement.normalized_text or ''} {requirement.text or ''}")
    for designation in reference.designations:
        code = _fold(designation.split("(")[0])
        if (
            len(code) >= 5
            and any(ch.isdigit() for ch in code)
            and any(ch.isalpha() for ch in code)
            and code in text
        ):
            return True
    return False


def _fold(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold().replace("ё", "е").translate(_LOOKALIKES)).strip()


def _kind_groups(db: Session, products: list[Product]) -> set[str]:
    from sqlalchemy import select

    from app.services.meter_kind import kind_group, product_kinds

    groups: set[str] = set()
    for product in products:
        # Только имя и значение поля — больше `product_kinds` не нужно.
        characteristics = db.execute(
            select(ProductCharacteristic.field_name, ProductCharacteristic.value).where(
                ProductCharacteristic.product_id == product.id
            )
        ).all()
        groups |= {kind_group(code) for code in product_kinds(product, characteristics)}
    return groups


def _name_key(value: str) -> str:
    cleaned = _LEGAL_FORM_RE.sub(" ", value or "").casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", cleaned.translate(_LOOKALIKES)).strip()


def match_manufacturer(name: str | None, manufacturers: list[Manufacturer]) -> Manufacturer | None:
    """Производитель из справочника по имени, которое назвала модель: «Энергомера»,
    «АО «Энергомера»», «Энергомера (АО)». Сравнение без организационно-правовой формы и
    кавычек; короче трёх букв не сравниваем."""

    key = _name_key(name or "")
    if len(key) < 3:
        return None
    for manufacturer in manufacturers:
        for candidate in (
            (manufacturer.brand_name or "").split("(")[0],
            manufacturer.legal_name or "",
        ):
            other = _name_key(candidate)
            if len(other) >= 3 and (key == other or re.search(rf"\b{re.escape(other)}\b", key)):
                return manufacturer
    return None


def find_reference(
    requirements: list[Requirement],
    manufacturers: list[Manufacturer],
    *,
    db: Session,
    named_text: str,
) -> TzReference | None:
    """Эталон ТЗ, если он есть. Счёт производителя: названные модели каталога (×3 — точное
    совпадение кода), требование «Модель прибора» с его именем (×3), фирменное ПО (×1).
    Побеждает наибольший счёт; смешанное ТЗ («МИР С-05 или CE208») даёт эталон по тому,
    кого назвали больше."""

    candidates: list[tuple[int, int, TzReference]] = []
    for manufacturer in manufacturers:
        products = named_products(db, manufacturer, named_text) if named_text else []
        score = 3 * len(products)
        from_requirements = 0
        designations: list[str] = []
        for requirement in requirements:
            if match_manufacturer(requirement.vendor, [manufacturer]) is None:
                continue
            if is_model_requirement(requirement):
                from_requirements += 3
                designation = (requirement.normalized_text or "").split(":", 1)[-1].strip()
                if designation and designation not in designations:
                    designations.append(designation)
            else:
                from_requirements += 1
        score += from_requirements
        if score == 0:
            continue
        for product in products:
            code = product.model_code or product.model_name
            if not any(code.casefold() in item.casefold() for item in designations):
                designations.append(code)
        candidates.append(
            (
                score,
                from_requirements,
                TzReference(manufacturer=manufacturer, products=products, designations=designations),
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_from_requirements, best = candidates[0]
    # Упоминание модели в тексте — ещё не эталон (проверка 13.10.2026 по 75 закупкам с
    # документацией: 7 из 18 срабатываний ложные). Перечень типов в АИИС КУЭ («Меркурий 230
    # (рег. № …), НАРТИС-И500 (рег. № …), МИРТЕК-135-РУ …» — 32616392406), таблица точек
    # учёта в договоре на метрологию (3201993) или многопозиционная спецификация с моделями
    # разных марок (32616298746) делали «эталоном» того, чьих моделей названо больше, а при
    # равенстве — того, кто раньше в списке. Без подтверждения извлечением («Модель прибора:
    # …», фирменное ПО) эталон есть, только если марка в тексте одна-две и первая
    # опережает остальные.
    if best_from_requirements == 0:
        named_brands = sum(1 for _, _, candidate in candidates if candidate.products)
        runner_up = candidates[1][0] if len(candidates) > 1 else 0
        if named_brands >= 3 or best_score <= runner_up:
            return None

    best.kind_groups = _kind_groups(db, best.products)
    model_texts = " ".join(
        f"{item.text or ''} {item.normalized_text or ''}"
        for item in requirements
        if is_model_requirement(item)
    )
    clause = TRADEMARK_CLAUSE_RE.search(named_text) or TRADEMARK_CLAUSE_RE.search(model_texts)
    if clause:
        best.clause = re.sub(r"\s+", " ", clause.group(0)).strip()[:400]
        best.equivalent_allowed = False
    elif EQUIVALENT_ALLOWED_RE.search(model_texts) or EQUIVALENT_ALLOWED_RE.search(named_text):
        best.equivalent_allowed = True
    return best
