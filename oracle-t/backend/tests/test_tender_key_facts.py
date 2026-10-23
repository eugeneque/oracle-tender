"""Ключевые сведения и порядок требований во вкладке сделки Bitrix24 (23.10.2026).

Замечания тендерного отдела «Что нужно видеть менеджеру в Битрикс»: под чей прибор ТЗ,
связь, критичные параметры, габариты, срок поставки, уровни системы, криптозащита,
интеграция с ПО и конкретный параметр, по которому наш прибор не проходит; общие нормы
(паспорт, методика поверки) — не показывать.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.analysis import ComplianceMatrixEntry, Requirement
from app.models.manufacturer import Manufacturer, Product
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import bitrix_ai_service as service


def _tender(db, requirements: list[dict], document: str | None = None) -> tuple[Tender, list[Requirement]]:
    source = Source(key=f"facts_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3261{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков электрической энергии",
        currency="RUB",
        price=Decimal("100000"),
    )
    db.add(tender)
    db.flush()
    rows = []
    for fields in requirements:
        row = Requirement(tender_id=tender.id, **{"criticality": "important", "kind": "product", **fields})
        db.add(row)
        rows.append(row)
    if document:
        db.add(
            TenderDocument(
                tender_id=tender.id,
                file_name="ТЗ.docx",
                source_url="https://example.test/tz.docx",
                extracted_text=document,
            )
        )
    db.flush()
    return tender, rows


def _summary(db, tender: Tender) -> dict:
    """Строка «Краткая выжимка по ТЗ» — первая в разделе (портал 1.4.1)."""

    rows = service.build_sections(db, tender)["technical_specification"]
    assert rows[0]["type"] == "technical_summary" and rows[0]["key"] == "technical-summary"
    assert sum(1 for row in rows if row["type"] == "technical_summary") == 1
    return rows[0]


def _mirtek_cell(db, tender: Tender, requirement: Requirement, status: str, explanation: str) -> None:
    manufacturer = db.query(Manufacturer).filter(Manufacturer.is_mirtek.is_(True)).first()
    if manufacturer is None:
        manufacturer = Manufacturer(brand_name="МИРТЕК", legal_name="ООО «МИРТЕК»", is_mirtek=True)
        db.add(manufacturer)
        db.flush()
    product = Product(manufacturer_id=manufacturer.id, model_name=f"МИРТЕК-32-РУ-{uuid.uuid4().hex[:4]}")
    db.add(product)
    db.flush()
    db.add(
        ComplianceMatrixEntry(
            tender_id=tender.id,
            requirement_id=requirement.id,
            manufacturer_id=manufacturer.id,
            product_id=product.id,
            status=status,
            explanation=explanation,
            source="ai",
        )
    )
    db.flush()


def test_summary_collected_from_requirements(db_session):
    tender, _ = _tender(
        db_session,
        [
            {"text": "Интерфейсы связи: RS-485, оптопорт", "category": "Интерфейсы и связь"},
            {"text": "Дополнительный модуль связи GSM (опционально)"},
            {"text": "Поддержка протокола СПОДЭС"},
            {"text": "Шифрование данных: ГОСТ Р 34.12"},
            {"text": "Интеграция с ИВК верхнего уровня: Пирамида 2.0"},
            {
                "text": "Срок поставки не должен превышать 30 (Тридцать) календарных дней с даты получения Заявки",
                "kind": "service",
            },
        ],
    )
    summary = _summary(db_session, tender)
    data = summary["data"]

    # Имена полей — ровно как в документации портала, в том же порядке.
    assert list(data) == [
        "specification_target",
        "delivery_terms",
        "system_level",
        "crypto_protection",
        "communication_interfaces",
        "software_integration",
    ]
    assert data["communication_interfaces"] == "RS-485, Оптопорт, GSM/GPRS (опционально) — протоколы: СПОДЭС"
    assert data["crypto_protection"].startswith("Требуется — шифрование данных")
    assert data["delivery_terms"].startswith("Срок поставки не должен превышать 30")
    assert "Пирамида" in data["software_integration"]
    # Названное ПО верхнего уровня без УСПД — признак двухуровневой системы, вывод «по признакам».
    assert data["system_level"].startswith("Двухуровневая — по признакам")
    assert summary["status"] == "requires_review"
    assert data["specification_target"] == "Не определено — модели конкретного производителя в ТЗ не названы"


def test_explicit_system_level_and_missing_facts(db_session):
    tender, _ = _tender(
        db_session,
        [{"text": "Класс точности: 1"}],
        document="Создание трехуровневой АИИС КУЭ: ИИК, ИВКЭ (УСПД), ИВК.",
    )
    summary = _summary(db_session, tender)
    data = summary["data"]

    assert data["system_level"] == "Трёхуровневая — прямо указано в документации"
    assert any(item.get("document") == "ТЗ.docx" for item in summary["evidence"])
    assert data["crypto_protection"] == "В документации не упоминается"
    assert data["delivery_terms"] == "В документации не найден"


def test_delivery_term_skips_penalty_clauses(db_session):
    """32616298746: первым упоминанием срока поставки был пункт о нарушении сроков."""

    tender, _ = _tender(
        db_session,
        [{"text": "Класс точности: 1"}],
        document=(
            "Если Поставщиком нарушены сроки подписания заявки, срок поставки товара исчисляется в "
            "течение 2 (двух) календарных дней с даты направления Заявки.\n"
            "Поставка товара осуществляется в течение 45 календарных дней с даты заключения договора."
        ),
    )
    data = _summary(db_session, tender)["data"]
    assert data["delivery_terms"].startswith("Поставка товара осуществляется в течение 45")


def test_mismatch_reasons_line_per_parameter_and_rows_go_by_priority(db_session):
    tender, rows = _tender(
        db_session,
        [
            {"text": "Паспорт на прибор в комплекте", "category": "Документация", "criticality": "critical"},
            {"text": "Базовый (максимальный) ток: 5 (100) А", "criticality": "critical"},
            {"text": "Класс точности: 0,5S", "criticality": "critical"},
            {"text": "Рабочая температура: от -40 до +70 °C", "criticality": "minor"},
            {"text": "Товар должен быть новым, не бывшим в употреблении", "kind": "supply"},
        ],
    )
    passport, current, accuracy, temperature, _ = rows
    _mirtek_cell(db_session, tender, current, "meets", "5(100) А")
    _mirtek_cell(db_session, tender, accuracy, "not_meets", "1,0")
    _mirtek_cell(db_session, tender, temperature, "not_meets", "от -40 до +60 °C")
    _mirtek_cell(db_session, tender, passport, "meets", "есть")

    sections = service.build_sections(db_session, tender)
    tz = sections["technical_specification"]
    reasons = tz[0]["data"]["mismatch_reasons"].split("\n")
    # Каждая причина — с новой строки, критичные первыми, с требуемым значением и нашим.
    assert len(reasons) == 2
    assert reasons[0].startswith("Критично: Класс точности — требуется 0,5S → МИРТЕК-32-РУ-")
    assert reasons[0].endswith(": 1,0")
    assert reasons[1].startswith("Рабочая температура — требуется от -40 до +70 °C →")

    requirements = [row for row in tz if row["type"] == "requirement"]
    # Параметр и требуемое значение раздельно (`required_value`), статус — по матрице.
    assert [(row["data"]["requirement"], row["data"].get("required_value"), row["status"]) for row in requirements] == [
        ("Класс точности", "0,5S", "not_matches"),
        ("Рабочая температура", "от -40 до +70 °C", "not_matches"),
        ("Базовый (максимальный) ток", "5 (100) А", "matches"),
    ]


def test_generic_norm_kept_when_our_device_fails_it(db_session):
    tender, rows = _tender(
        db_session,
        [{"text": "Методика поверки: МП 123-2024", "category": "Документация"}],
    )
    _mirtek_cell(db_session, tender, rows[0], "not_meets", "методика другая")
    tz = service.build_sections(db_session, tender)["technical_specification"]
    assert any(row["data"].get("required_value") == "МП 123-2024" for row in tz)


def test_typical_participant_requirements_are_dropped(db_session):
    tender, _ = _tender(
        db_session,
        [
            {"text": "Участник не должен находиться в процессе ликвидации", "kind": "participant", "criticality": "critical"},
            {"text": "Опыт поставок счётчиков не менее трёх договоров", "kind": "participant"},
        ],
    )
    prep = service.build_sections(db_session, tender)["preparation"]
    assert [row["data"]["requirement"] for row in prep if row["type"] == "qualification"] == [
        "Опыт поставок счётчиков не менее трёх договоров"
    ]
