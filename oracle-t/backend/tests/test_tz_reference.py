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
    calculate_percentage,
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

    assert verdicts[1].status == ComplianceStatus.PARTIAL.value  # эквивалент — не эталон
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


def test_competitors_do_not_pass_on_par_with_reference():
    """Закупка 32616451908 (13.10.2026): ПКУ-10 кВ под «МИРТЕК-135-РУ… (или эквивалент)», в
    матрице два требования — и все производители «проходили» со 100%, как сам эталон.
    Чужой прибор — эквивалент: «частично» по модели, итог «с оговорками», процент ниже."""

    requirements = [
        _requirement(
            "Модель прибора: МИРТЕК-135-РУ-SPHV1-A0,5R1-10K-5-100A (или эквивалент)",
            vendor="МИРТЕК",
        ),
        _requirement("Класс напряжения: 10 кВ", criticality=Criticality.CRITICAL.value),
    ]
    reference = TzReference(
        manufacturer=MIRTEK, designations=["МИРТЕК-135-РУ"], equivalent_allowed=True
    )
    manufacturers = [MIRTEK, ENERGOMERA, MIR]

    def evaluate(manufacturer: Manufacturer) -> dict[int, RequirementVerdict]:
        verdicts = _no_data(2)
        verdicts[2] = RequirementVerdict(
            number=2, status=ComplianceStatus.MEETS.value, source="catalog", explanation="", confidence=0.9
        )
        apply_reference_rules(manufacturer, requirements, verdicts, reference, manufacturers)
        return verdicts

    own = evaluate(MIRTEK)
    assert decide_verdict(requirements, own)[0] == "passes"
    assert calculate_percentage(requirements, own)[0] == 100

    other = evaluate(ENERGOMERA)
    assert other[1].status == ComplianceStatus.PARTIAL.value
    assert "МИРТЕК" in other[1].explanation
    verdict, reason = decide_verdict(requirements, other)
    assert verdict == "caveats" and "Модель прибора" in reason
    assert calculate_percentage(requirements, other)[0] < 100


def test_silent_documentation_keeps_competitor_in_caveats():
    reference = TzReference(manufacturer=ENERGOMERA, designations=["CE207"], equivalent_allowed=None)
    verdicts = _no_data(3)
    verdicts[2] = RequirementVerdict(
        number=2, status=ComplianceStatus.MEETS.value, source="rule", explanation="", confidence=0.9
    )
    verdicts[3] = RequirementVerdict(
        number=3, status=ComplianceStatus.MEETS.value, source="catalog", explanation="", confidence=0.9
    )
    requirements = [REQUIREMENTS[0], _requirement("Класс точности 1"), REQUIREMENTS[2]]
    apply_reference_rules(MIRTEK, requirements, verdicts, reference, [MIRTEK, ENERGOMERA])

    assert verdicts[1].status == ComplianceStatus.PARTIAL.value
    assert "проверить" in verdicts[1].explanation
    assert decide_verdict(requirements, verdicts)[0] == "caveats"


def _verdict(status: str, explanation: str) -> RequirementVerdict:
    return RequirementVerdict(number=1, status=status, source="catalog", explanation=explanation, confidence=0.7)


def test_duration_arithmetic_overrides_model_mistake():
    """13.10.2026: «192 месяца, что меньше требуемых 16 лет» — 192 месяца и есть 16 лет."""

    from app.services.compliance_service import apply_duration_check

    requirement = _requirement("Межповерочный интервал: 16 лет", criticality=Criticality.CRITICAL.value)
    cases = [
        ("not_meets", "Межповерочный интервал 192 месяца, что меньше требуемых 16 лет", "meets"),
        ("partial", "Межповерочный интервал 192 месяца для M2M-3", "meets"),
        ("meets", "Межповерочный интервал 96 месяцев (8 лет) для модели Альфа", "not_meets"),
        # Повтор требуемого значения — не значение прибора.
        ("not_meets", "Межповерочный интервал менее 16 лет", "not_meets"),
        # Про часть моделей — решает модель.
        ("partial", "Межповерочный интервал 192 месяца (16 лет), но не для всех", "partial"),
        ("partial", "Межповерочный интервал для некоторых моделей составляет 96 месяцев", "partial"),
        # Пояснение не о том сроке.
        ("partial", "Гарантийный срок 120 месяцев", "partial"),
    ]
    for status, explanation, expected in cases:
        verdicts = {1: _verdict(status, explanation)}
        apply_duration_check([requirement], verdicts)
        assert verdicts[1].status == expected, explanation


def test_duration_check_skips_transformers_and_at_most():
    from app.services.compliance_service import apply_duration_check

    transformer = _requirement("Межповерочный интервал ТТ: не менее 8 лет")
    verdicts = {1: _verdict("not_meets", "Межповерочный интервал 192 месяца")}
    apply_duration_check([transformer], verdicts)
    assert verdicts[1].status == "not_meets"

    warranty = _requirement("Гарантийный срок: не менее 84 месяцев")
    verdicts = {1: _verdict("not_meets", "Гарантийный срок 7 лет")}
    apply_duration_check([warranty], verdicts)
    assert verdicts[1].status == "meets"


def _catalog_manufacturer(db_session, brand: str, models: list[str]) -> Manufacturer:
    manufacturer = Manufacturer(legal_name=f"ООО «{brand} {uuid.uuid4().hex[:6]}»", brand_name=brand)
    db_session.add(manufacturer)
    db_session.flush()
    for model in models:
        db_session.add(Product(manufacturer_id=manufacturer.id, model_name=model, model_code=model))
    db_session.flush()
    return manufacturer


def test_list_of_installed_meter_types_is_not_a_reference(db_session):
    """32616392406 (13.10.2026): перечень типов счётчиков в АИИС КУЭ заказчика — три марки,
    «эталоном» становился тот, чьих моделей названо больше."""

    tag = uuid.uuid4().hex[:4].upper()
    first = _catalog_manufacturer(db_session, "Альфатест", [f"АТ{tag}-230", f"АТ{tag}-234"])
    second = _catalog_manufacturer(db_session, "Бетатест", [f"БТ{tag}-500"])
    third = _catalog_manufacturer(db_session, "Гаммотест", [f"ГТ{tag}-135"])
    text = (
        f"В составе АИИС КУЭ счётчики типов: АТ{tag}-230 (рег. № 1), АТ{tag}-234 (рег. № 2), "
        f"БТ{tag}-500 (рег. № 3), ГТ{tag}-135 (рег. № 4)."
    )
    assert find_reference([], [first, second, third], db=db_session, named_text=text) is None


def test_tie_between_two_named_brands_is_not_a_reference(db_session):
    tag = uuid.uuid4().hex[:4].upper()
    first = _catalog_manufacturer(db_session, "Альфатест", [f"АТ{tag}-308"])
    second = _catalog_manufacturer(db_session, "Бетатест", [f"БТ{tag}-135"])
    text = f"Позиция 1: счётчик АТ{tag}-308. Позиция 2: пункт учёта БТ{tag}-135."
    assert find_reference([], [first, second], db=db_session, named_text=text) is None

    # Требование «Модель прибора» от извлечения разрешает ничью.
    requirement = _requirement(f"Модель прибора: БТ{tag}-135", vendor="Бетатест")
    reference = find_reference([requirement], [first, second], db=db_session, named_text=text)
    assert reference is not None and reference.manufacturer.id == second.id


MIR_REFERENCE = TzReference(
    manufacturer=MIR,
    designations=["МИР С-05", "МИР С-04"],
    equivalent_allowed=False,
)
MIR_NAME_REQUIREMENT = _requirement(
    "Наименование: Прибор учета МИР С-05.10-230-5(80)-PZ1В-KNQ-E-D",
    criticality=Criticality.CRITICAL.value,
    parameter_no=1,
)


def test_requirement_naming_reference_model_works_as_model_requirement():
    """32616408289: модель записана как «Наименование: Прибор учета МИР С-05…», эквивалент
    запрещён. Раньше МИР получал «не соответствует» по собственному прибору, а конкуренты —
    «не хватает данных»; теперь МИР проходит, остальные — нет."""

    from app.services.tz_reference import names_reference_model

    assert names_reference_model(MIR_NAME_REQUIREMENT, MIR_REFERENCE)
    assert not names_reference_model(_requirement("Совместимость с ПО «Энергомир»"), MIR_REFERENCE)

    own = {1: _verdict("not_meets", "Наименование не соответствует требуемому")}
    apply_reference_rules(MIR, [MIR_NAME_REQUIREMENT], own, MIR_REFERENCE, [MIR, MIRTEK])
    assert own[1].status == "meets"

    other = {1: _verdict("no_data", "")}
    apply_reference_rules(MIRTEK, [MIR_NAME_REQUIREMENT], other, MIR_REFERENCE, [MIR, MIRTEK])
    assert other[1].status == "not_meets"
    assert decide_verdict([MIR_NAME_REQUIREMENT], other)[0] == "fails"


def test_reference_type_requirement_is_not_failed_by_another_execution():
    """32616394121: «Тип прибора: однофазный сплит» — ТЗ под МИР С-05 (сплит), а модель
    сравнила с исполнением на DIN-рейку и поставила МИР «не соответствует»."""

    product = Product(id=uuid.uuid4(), manufacturer_id=MIR.id, model_name="МИР С-05", model_code="МИР С-05")
    type_requirement = _requirement(
        "Тип прибора: однофазный прямого включения сплит-исполнения",
        criticality=Criticality.CRITICAL.value,
        parameter_no=1,
    )
    reference = TzReference(manufacturer=MIR, products=[product], designations=["МИР С-05"])

    # Каталог тип не подтверждает — «соответствует» на проверку человеком.
    verdicts = {1: _verdict("not_meets", "Тип прибора у производителя — на DIN-рейку")}
    apply_reference_rules(MIR, [type_requirement], verdicts, reference, [MIR])
    assert verdicts[1].status == "meets" and verdicts[1].confidence < 0.6

    # Чужому производителю тип по-прежнему оценивает модель.
    verdicts = {1: _verdict("not_meets", "Тип прибора у производителя — на DIN-рейку")}
    apply_reference_rules(MIRTEK, [type_requirement], verdicts, reference, [MIR, MIRTEK])
    assert verdicts[1].status == "not_meets"


def test_not_meets_without_quoted_value_becomes_no_data():
    from app.services.compliance_service import apply_evidence_check

    requirements = [
        _requirement("Класс точности ТН: не ниже 0,5", criticality=Criticality.CRITICAL.value),
        _requirement("Класс точности активной энергии: 1", criticality=Criticality.CRITICAL.value),
        _requirement("Интерфейс ZigBee 2,4 ГГц"),
    ]
    verdicts = {
        1: _verdict("not_meets", "Класс точности ТН в карточке не соответствует требуемому"),
        2: _verdict("not_meets", "Класс точности 2 по ГОСТ 31819.21"),
        3: _verdict("not_meets", "Нет ZigBee"),
    }
    apply_evidence_check(requirements, verdicts)

    assert verdicts[1].status == "no_data" and "проверить" in verdicts[1].explanation
    assert verdicts[2].status == "not_meets"  # значение названо — вердикт остаётся
    assert verdicts[3].status == "not_meets"  # не числовой параметр
