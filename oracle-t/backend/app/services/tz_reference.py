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
from app.models.manufacturer import Manufacturer, Product
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

    @property
    def label(self) -> str:
        return "; ".join(self.designations[:3]) or (
            self.manufacturer.brand_name or self.manufacturer.legal_name
        )


def is_model_requirement(requirement: Requirement) -> bool:
    return (requirement.normalized_text or "").strip().lower().startswith(MODEL_REQUIREMENT_PREFIX)


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

    best: TzReference | None = None
    best_score = 0
    for manufacturer in manufacturers:
        products = named_products(db, manufacturer, named_text) if named_text else []
        score = 3 * len(products)
        designations: list[str] = []
        for requirement in requirements:
            if match_manufacturer(requirement.vendor, [manufacturer]) is None:
                continue
            if is_model_requirement(requirement):
                score += 3
                designation = (requirement.normalized_text or "").split(":", 1)[-1].strip()
                if designation and designation not in designations:
                    designations.append(designation)
            else:
                score += 1
        if score <= best_score:
            continue
        for product in products:
            code = product.model_code or product.model_name
            if not any(code.casefold() in item.casefold() for item in designations):
                designations.append(code)
        best = TzReference(manufacturer=manufacturer, products=products, designations=designations)
        best_score = score
    if best is None:
        return None

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
