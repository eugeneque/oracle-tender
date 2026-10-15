"""Классы ошибок из сверки матрицы на сервере (15.10.2026, раздел 62 ARCHITECTURE.md).

`scripts/compliance_audit.sql` по 25 закупкам нашёл, кроме закрытого ранее: модель, названную
не через «Модель прибора:», номер Госреестра СИ у эталона РиМ и «не соответствует» с
уверенностью 0, решавшее «не проходит»."""

from __future__ import annotations

import uuid

from app.models.analysis import Criticality, Requirement, RequirementKind
from app.models.manufacturer import Manufacturer
from app.services.compliance_service import (
    RequirementVerdict,
    apply_confidence_floor,
    apply_reference_rules,
    decide_verdict,
)
from app.services.tz_reference import TzReference, is_model_requirement, is_registry_requirement

RIM = Manufacturer(id=uuid.uuid4(), brand_name="РиМ", legal_name="АО «Радио и Микроэлектроника»")
MIRTEK = Manufacturer(id=uuid.uuid4(), brand_name="МИРТЕК", legal_name="ООО «МИРТЕК»", is_mirtek=True)
CRITICAL = Criticality.CRITICAL.value


def _requirement(normalized: str, **fields) -> Requirement:
    data = {
        "text": normalized,
        "normalized_text": normalized,
        "kind": RequirementKind.PRODUCT.value,
        "criticality": CRITICAL,
        "vendor": None,
        "vendor_exclusive": False,
    }
    data.update(fields)
    return Requirement(**data)


def _verdict(status: str, explanation: str = "", confidence: float = 0.7, source: str = "catalog") -> RequirementVerdict:
    return RequirementVerdict(number=1, status=status, source=source, explanation=explanation, confidence=confidence)


def test_designation_is_recognised_under_other_parameter_names():
    models = [
        "Наименование товара: счётчик электрической энергии МИР С-04.10-230-5(100)-GZ1B",
        "Модель однофазного счётчика: НАРТИС-И100",
        "Счётчик трёхфазный 307S.52-ZZ-3-D",
        "Номер в Госреестре СИ: 55522-13",
        "Модель прибора: ВШУ в сборе с 1-фазным ПУ",
    ]
    not_models = [
        "Тип прибора: трёхфазный прямого включения на DIN-рейку",
        "Тип прибора: 3-фазный 3х230/400 В 5(10) А",
        "Наименование товара: Счетчик электроэнергии 220В",
        "Тип интерфейса: RS-485",
        "Номинальный ток: 5(100) А",
        "Тип прибора: 1-фазный прибор учёта",
    ]
    assert all(is_model_requirement(_requirement(text)) for text in models)
    assert not any(is_model_requirement(_requirement(text)) for text in not_models)
    assert is_registry_requirement(_requirement("Номер в Госреестре СИ: 55522-13"))


def test_named_model_without_owner_is_equivalent_not_failure():
    """Ни производителя у требования, ни эталона — «модель не та» становится «частично»."""

    requirements = [_requirement("Наименование товара: счётчик МИР С-04.10-230-5(100)-GZ1B")]
    verdicts = {1: _verdict("not_meets", "Модель счётчика не соответствует указанной", 0.7)}
    apply_reference_rules(MIRTEK, requirements, verdicts, None, [MIRTEK, RIM])

    assert verdicts[1].status == "partial" and "эквивалент" in verdicts[1].explanation
    assert decide_verdict(requirements, verdicts)[0] == "caveats"

    verdicts = {1: _verdict("meets", "МИР С-04 в каталоге")}
    apply_reference_rules(MIRTEK, requirements, verdicts, None, [MIRTEK, RIM])
    assert verdicts[1].status == "meets"  # совпадение модели не трогаем


def test_registry_number_of_reference_meets_and_competitor_is_equivalent():
    """32616407181: эталон РиМ «не проходил» по «Номер в Госреестре СИ: 55522-13»."""

    requirements = [_requirement("Номер в Госреестре СИ: 55522-13")]
    reference = TzReference(manufacturer=RIM, designations=["ИПУЭ РиМ 384.02/2"], equivalent_allowed=True)

    verdicts = {1: _verdict("not_meets", "В каталоге номер 55522-18")}
    apply_reference_rules(RIM, requirements, verdicts, reference, [RIM, MIRTEK])
    assert verdicts[1].status == "meets" and "Аршин" in verdicts[1].explanation

    verdicts = {1: _verdict("not_meets", "Номер 12345-20")}
    apply_reference_rules(MIRTEK, requirements, verdicts, reference, [RIM, MIRTEK])
    assert verdicts[1].status == "partial"


def test_not_meets_without_confidence_does_not_decide():
    requirements = [
        _requirement("Максимальный ток: 10 А"),
        _requirement("Номинальное напряжение: 230 В"),
        _requirement("Эквивалент не допускается: модель X"),
    ]
    verdicts = {
        1: _verdict("not_meets", "Не соответствует", 0.0),
        2: _verdict("not_meets", "Номинальное напряжение 400 В", 0.7),
        3: _verdict("not_meets", "правило", 0.0, source="rule"),
    }
    apply_confidence_floor(requirements, verdicts)

    assert verdicts[1].status == "no_data" and "не уверена" in verdicts[1].explanation
    assert verdicts[2].status == "not_meets"
    assert verdicts[3].status == "not_meets"


# --- повторная сверка после пересчёта (15.10.2026) ------------------------------------

METRONIKA = Manufacturer(id=uuid.uuid4(), brand_name="Метроника (АЛЬФА)", legal_name="ООО «Метроника»")


def test_upper_level_software_is_not_vendor_exclusive():
    """32616409293: «Интеграция в ПО: Альфа Центр» как фирменное ПО Метроники — «не
    проходили» все 17 производителей."""

    from app.services.tz_reference import is_vendor_exclusive

    alpha = _requirement("Интеграция в ПО: Альфа Центр", vendor="ООО «Метроника»", vendor_exclusive=True)
    admin_tools = _requirement("ПО для работы со счётчиком: AdminTools", vendor="Метроника", vendor_exclusive=True)
    assert not is_vendor_exclusive(alpha) and is_vendor_exclusive(admin_tools)

    verdicts = {1: _verdict("no_data"), 2: _verdict("no_data")}
    apply_reference_rules(MIRTEK, [alpha, admin_tools], verdicts, None, [MIRTEK, METRONIKA])
    assert verdicts[1].status == "no_data"  # решает сверка с ПО верхнего уровня, не правило
    assert verdicts[2].status == "not_meets"


def test_reference_is_not_failed_for_missing_position_type():
    """32616409293: эталону правило позиций ставило «в каталоге нет высоковольтных ПУ»."""

    from app.services.compliance_service import POSITION_KIND_PREFIX

    requirements = [_requirement("Тип прибора: Высоковольтный прибор учёта", position="1. ВПУ")]
    reference = TzReference(manufacturer=METRONIKA, designations=[])
    explanation = f"{POSITION_KIND_PREFIX}1. ВПУ»: в каталоге производителя нет приборов типа «ВПУ»"

    verdicts = {1: _verdict("not_meets", explanation, 0.6, source="rule")}
    apply_reference_rules(METRONIKA, requirements, verdicts, reference, [METRONIKA, MIRTEK])
    assert verdicts[1].status == "partial" and "ТЗ составлено" in verdicts[1].explanation

    verdicts = {1: _verdict("not_meets", explanation, 0.6, source="rule")}
    apply_reference_rules(MIRTEK, requirements, verdicts, reference, [METRONIKA, MIRTEK])
    assert verdicts[1].status == "not_meets"  # конкуренту отказ по типу остаётся
