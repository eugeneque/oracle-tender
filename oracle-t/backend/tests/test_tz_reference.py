"""Эталон ТЗ и фирменное ПО производителя в матрице соответствия (06.10.2026).

Закупка 32616436166: ТЗ написано под «CE207 R7.849.2.OG.QUVLF GS01 SPDs (или
эквивалент)» и требует ПО «AdminTools» и сервер «CE Net-Connection» Энергомеры. Матрица
ставила Энергомере «не хватает данных», а МИРТЕК — «с оговорками 92%»."""

from __future__ import annotations

import uuid

from app.models.analysis import ComplianceStatus, Criticality, Requirement, RequirementKind
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic
from app.services.compliance_service import (
    RequirementVerdict,
    apply_reference_rules,
    decide_verdict,
)
from app.services.tz_reference import TzReference, find_reference, match_manufacturer


def _manufacturer(brand: str, legal: str, *, mirtek: bool = False) -> Manufacturer:
    return Manufacturer(id=uuid.uuid4(), brand_name=brand, legal_name=legal, is_mirtek=mirtek)


ENERGOMERA = _manufacturer("Энергомера", "АО «Энергомера»")
MIRTEK = _manufacturer("МИРТЕК", "ООО «МИРТЕК»", mirtek=True)
MIR = _manufacturer("МИР", "ООО «НПО МИР»")


def _requirement(normalized: str, **fields) -> Requirement:
    data = {
        "text": normalized,
        "normalized_text": normalized,
        "kind": RequirementKind.PRODUCT.value,
        "criticality": Criticality.IMPORTANT.value,
        "vendor": None,
        "vendor_exclusive": False,
    }
    data.update(fields)
    return Requirement(**data)


def _no_data(count: int) -> dict[int, RequirementVerdict]:
    return {
        index: RequirementVerdict(
            number=index, status=ComplianceStatus.NO_DATA.value, source="", explanation="", confidence=1.0
        )
        for index in range(1, count + 1)
    }


REQUIREMENTS = [
    _requirement(
        "Модель прибора: CE207 R7.849.2.OG.QUVLF GS01 SPDs (или эквивалент)",
        vendor="Энергомера",
        criticality=Criticality.CRITICAL.value,
    ),
    _requirement(
        "ПО для работы со счётчиком: AdminTools",
        vendor="АО «Энергомера»",
        vendor_exclusive=True,
        criticality=Criticality.CRITICAL.value,
    ),
    _requirement("Встроенный GSM/GPRS-модем", criticality=Criticality.CRITICAL.value),
]
REFERENCE = TzReference(
    manufacturer=ENERGOMERA,
    designations=["CE207 R7.849.2.OG.QUVLF GS01 SPDs (или эквивалент)"],
    equivalent_allowed=True,
)


def test_manufacturer_is_matched_by_name_without_legal_form():
    manufacturers = [MIRTEK, MIR, ENERGOMERA]
    assert match_manufacturer("АО «Энергомера»", manufacturers) is ENERGOMERA
    assert match_manufacturer("энергомера", manufacturers) is ENERGOMERA
    assert match_manufacturer("МИРТЕК", manufacturers) is MIRTEK
    assert match_manufacturer("МИР", manufacturers) is MIR
    assert match_manufacturer("", manufacturers) is None
    assert match_manufacturer("Пирамида", manufacturers) is None


def test_other_manufacturer_fails_on_vendor_software_not_caveats():
    """МИРТЕК: модель и модем — по данным, но «AdminTools» — фирменное ПО Энергомеры, и
    итог «не проходит», а не «с оговорками»."""

    verdicts = _no_data(3)
    verdicts[3] = RequirementVerdict(
        number=3, status=ComplianceStatus.MEETS.value, source="catalog", explanation="", confidence=0.9
    )
    apply_reference_rules(MIRTEK, REQUIREMENTS, verdicts, REFERENCE, [MIRTEK, ENERGOMERA])

    assert verdicts[1].status == ComplianceStatus.MEETS.value  # эквивалент допускается
    assert verdicts[2].status == ComplianceStatus.NOT_MEETS.value
    assert "Энергомера" in verdicts[2].explanation
    verdict, reason = decide_verdict(REQUIREMENTS, verdicts)
    assert verdict == "fails" and "AdminTools" in reason


def test_reference_manufacturer_gets_meets_where_catalog_is_silent():
    verdicts = _no_data(3)
    apply_reference_rules(ENERGOMERA, REQUIREMENTS, verdicts, REFERENCE, [MIRTEK, ENERGOMERA])

    assert [verdicts[index].status for index in (1, 2, 3)] == [ComplianceStatus.MEETS.value] * 3
    assert all(verdicts[index].source == "tz" for index in (1, 2, 3))
    assert decide_verdict(REQUIREMENTS, verdicts)[0] == "passes"


def test_reference_keeps_catalog_contradiction():
    """«Не соответствует» по фактам каталога у эталона не затирается — противоречие ТЗ и
    прибора должен увидеть человек."""

    verdicts = _no_data(3)
    verdicts[3] = RequirementVerdict(
        number=3, status=ComplianceStatus.NOT_MEETS.value, source="catalog", explanation="нет модема", confidence=0.9
    )
    apply_reference_rules(ENERGOMERA, REQUIREMENTS, verdicts, REFERENCE, [ENERGOMERA])

    assert verdicts[3].status == ComplianceStatus.NOT_MEETS.value


def test_equivalent_forbidden_fails_everyone_but_reference():
    reference = TzReference(manufacturer=ENERGOMERA, designations=["CE207"], equivalent_allowed=False)
    verdicts = _no_data(3)
    apply_reference_rules(MIRTEK, REQUIREMENTS, verdicts, reference, [MIRTEK, ENERGOMERA])

    assert verdicts[1].status == ComplianceStatus.NOT_MEETS.value


def test_reference_is_found_by_model_code_in_documents(db_session):
    manufacturer = Manufacturer(legal_name=f"АО «Тест {uuid.uuid4().hex[:6]}»", brand_name="Тестомера")
    other = Manufacturer(legal_name=f"ООО «Другой {uuid.uuid4().hex[:6]}»", brand_name="Другой")
    db_session.add_all([manufacturer, other])
    db_session.flush()
    product = Product(manufacturer_id=manufacturer.id, model_name="CE207", model_code="CE207 R7.849.2.OG.QUVLF GS01 SPDS")
    db_session.add(product)
    db_session.flush()
    db_session.add(
        ProductCharacteristic(
            product_id=product.id, group_name="Основные", field_name="Модель", value="CE207", source="manufacturer_site"
        )
    )
    db_session.flush()
    text = "ТЕХНИЧЕСКОЕ ЗАДАНИЕ на поставку счётчиков «CE207 R7.849.2.OG.QUVLF GS01 SPDs» (или эквивалент)."

    reference = find_reference([], [other, manufacturer], db=db_session, named_text=text)

    assert reference is not None and reference.manufacturer.id == manufacturer.id
    assert reference.equivalent_allowed is True
    assert [item.id for item in reference.products] == [product.id]


def test_model_failure_is_not_masked_by_reference():
    """Модель не ответила (кончился баланс RouterAI) — это сбой, а не пустота каталога:
    эталону такая ячейка не засчитывается как «соответствует»."""

    from app.services.compliance_service import MODEL_NO_VERDICT

    verdicts = _no_data(3)
    verdicts[3] = RequirementVerdict(
        number=3, status=ComplianceStatus.NO_DATA.value, source="", explanation=MODEL_NO_VERDICT, confidence=0.0
    )
    apply_reference_rules(ENERGOMERA, REQUIREMENTS, verdicts, REFERENCE, [ENERGOMERA])

    assert verdicts[3].status == ComplianceStatus.NO_DATA.value
