"""Национальный режим (ПП РФ № 1875) в заключении ИИ (замечание 22.10.2026).

Закупка SBR003-260026413800036.1: по ПП 1875 к поставке допускаются только приборы с записью
в реестре российской промышленной продукции (ГИСП) — у МИРТЕК это D17-2, D37-2, SP17-2 и
SP37-2. Заключение же предлагало W3 и другие модели: про ограничение модель не знала, а
кандидаты в «наши приборы» подбирались по характеристикам, без допуска.

Теперь ограничение находит код — по требованиям, наименованию и документации — и сам решает,
какие наши приборы можно предлагать. Модели остаётся только выбрать из допустимых; ответ,
который их нарушает, поправляется при сверке (`ai_conclusion_service._clean_products`).

Ограничение применяется по-разному (там же): прямо — к закупке **поставки** приборов (в том
числе с монтажом), а если приборы входят в состав шкафа учёта или закупаются только работы,
в реестре должно быть изделие или работы, а не прибор, — тогда приборы не отсекаются, а
запись прибора выносится в проверку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product, ProductStatus
from app.models.registry_record import AdmissionRegistry
from app.models.tender import Tender, TenderType
from app.services.registry_records_service import (
    STATE_ABSENT,
    STATE_ACTIVE,
    STATE_EXPIRED,
    STATE_EXPIRING,
    manufacturer_records,
    record_state,
    registries_for_requirements,
)

_REGIME_RE = re.compile(
    r"(?:постановлени\w*|ПП)\s*(?:Правительства\s*(?:РФ|Российской\s+Федерации)?\s*)?"
    r"(?:от\s*[\d.]+\s*(?:г\.?)?\s*)?(?:№\s*)?1875\b|национальн\w+\s+режим\w*",
    re.IGNORECASE,
)
# Прибор в составе изделия: шкаф (ВШУ, ШУЭ), щит или бокс учёта.
_ASSEMBLY_RE = re.compile(
    r"шкаф\w*|\bВШУ\b|\bШУЭ?\b|\bШУЧ\b|щит\w*\s+(?:учет|учёт)|бокс\w*\s+(?:учет|учёт)",
    re.IGNORECASE,
)

SCOPE_SUPPLY = "supply"
SCOPE_ASSEMBLY = "assembly"
SCOPE_WORKS = "works"


@dataclass
class NationalRegime:
    basis: str
    scope: str
    # Наши выпускаемые приборы по состоянию записи в реестре промпродукции.
    allowed: list[Product] = field(default_factory=list)
    denied: list[Product] = field(default_factory=list)
    unknown: list[Product] = field(default_factory=list)

    @property
    def strict(self) -> bool:
        return self.scope == SCOPE_SUPPLY

    def blocks(self, product: Product) -> bool:
        """Прибор предлагать нельзя: поставка, а записи у него нет, тогда как у других
        наших приборов она есть (значит, реестр по нам сверен)."""

        return self.strict and bool(self.allowed) and all(
            item.id != product.id for item in self.allowed
        )


def detect_basis(tender: Tender, requirements: list[Requirement], documents_text: str) -> str | None:
    """Откуда известно об ограничении; None — закупка его не упоминает."""

    pieces = [tender.title or "", *(f"{item.text or ''} {item.normalized_text or ''}" for item in requirements)]
    for piece in pieces:
        if _REGIME_RE.search(piece):
            return "ПП РФ № 1875 (национальный режим) в требованиях"
    if AdmissionRegistry.INDUSTRIAL_PRODUCTS.value in registries_for_requirements(requirements):
        return "требование о записи в реестре российской промышленной продукции"
    if documents_text and _REGIME_RE.search(documents_text):
        return "ПП РФ № 1875 (национальный режим) в документации"
    return None


def detect_scope(tender: Tender, requirements: list[Requirement]) -> str:
    if tender.tender_type == TenderType.WORKS_ONLY.value:
        return SCOPE_WORKS
    positions = " ".join(item.position or "" for item in requirements)
    if _ASSEMBLY_RE.search(f"{tender.title or ''} {positions}"):
        return SCOPE_ASSEMBLY
    return SCOPE_SUPPLY


def national_regime(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer | None,
    requirements: list[Requirement],
    documents_text: str = "",
) -> NationalRegime | None:
    basis = detect_basis(tender, requirements, documents_text)
    if basis is None:
        return None
    regime = NationalRegime(basis=basis, scope=detect_scope(tender, requirements))
    if manufacturer is None:
        return regime
    states = {
        product.id: record_state(record)
        for product, record in manufacturer_records(db, manufacturer)
        if record.registry == AdmissionRegistry.INDUSTRIAL_PRODUCTS.value
    }
    for product in db.scalars(
        select(Product)
        .where(
            Product.manufacturer_id == manufacturer.id,
            Product.status == ProductStatus.ACTIVE.value,
        )
        .order_by(Product.model_name)
    ):
        state = states.get(product.id)
        if state in (STATE_ACTIVE, STATE_EXPIRING):
            regime.allowed.append(product)
        elif state in (STATE_ABSENT, STATE_EXPIRED):
            regime.denied.append(product)
        else:
            regime.unknown.append(product)
    return regime


def _names(products: list[Product], limit: int = 12) -> str:
    names = [product.model_name for product in products[:limit]]
    rest = len(products) - len(names)
    return "; ".join(names) + (f" и ещё {rest}" if rest > 0 else "")


def regime_block(regime: NationalRegime) -> str:
    """Раздел входа модели: что требует закупка и какие наши приборы можно предлагать."""

    lines = [f"ОГРАНИЧЕНИЕ ДОПУСКА ИНОСТРАННЫХ ТОВАРОВ: {regime.basis}."]
    if regime.strict:
        lines.append(
            "  Закупка — поставка приборов: допускаются только приборы с действующей записью в "
            "реестре российской промышленной продукции (ГИСП)."
        )
        if regime.allowed:
            lines.append(f"  Наши приборы с записью: {_names(regime.allowed)}.")
            lines.append(
                "  Предлагай ТОЛЬКО их; остальные наши приборы — \"not_fits\" с причиной «нет "
                "записи в реестре промпродукции»."
            )
        else:
            lines.append(
                "  Записи наших приборов в реестре в справочнике не заведены — статус наших "
                "приборов не выше \"partial\", в note — «проверить запись в ГИСП»."
            )
    else:
        where = (
            "приборы входят в состав шкафа (щита) учёта"
            if regime.scope == SCOPE_ASSEMBLY
            else "закупаются работы, а не поставка приборов"
        )
        lines.append(
            f"  {where.capitalize()}: ограничение относится к изделию или работам, а не к "
            "прибору напрямую. Приборы по реестру не отсекай, но назови в rationale, что "
            "применимость ограничения к прибору нужно проверить по документации."
        )
        if regime.allowed:
            lines.append(f"  Наши приборы с записью в реестре: {_names(regime.allowed)}.")
    if regime.denied:
        lines.append(f"  Проверено: записи нет или истекла — {_names(regime.denied)}.")
    return "\n".join(lines)
