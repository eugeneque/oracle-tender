"""Смешанный лот, требования к ТТ/ТН и ошибочный номер параметра (15.10.2026).

Закупка 32616469643 «Россети Тюмень»: ВШУ в сборе с однофазным ПУ и ВШУ с трёхфазным ПУ
полукосвенного включения, ТЗ под Завод Нартис. Нартис — эталон ТЗ — получил 79% и «не
проходит»: «400 В» сравнили с однофазной моделью, «Класс точности ТН» — с классом счётчика,
«Максимальный ток 10 А» — со счётчиком прямого включения, а «Дискретные входы» сработали как
правило трёхпозиционного реле."""

from __future__ import annotations

import uuid

from app.models.analysis import Criticality, Requirement, RequirementKind
from app.models.manufacturer import Manufacturer
from app.services.compliance_service import (
    RequirementVerdict,
    apply_duration_check,
    apply_evidence_check,
    apply_expert_rules,
    apply_lot_variants,
    apply_reference_rules,
    apply_transformer_scope,
    decide_verdict,
    is_transformer_requirement,
)
from app.services.tz_reference import TzReference

NARTIS = Manufacturer(id=uuid.uuid4(), brand_name="Нартис", legal_name="ООО «Завод Нартис»")
MIRTEK = Manufacturer(id=uuid.uuid4(), brand_name="МИРТЕК", legal_name="ООО «МИРТЕК»", is_mirtek=True)

CRITICAL = Criticality.CRITICAL.value
IMPORTANT = Criticality.IMPORTANT.value


def _requirement(normalized: str, criticality: str = IMPORTANT, **fields) -> Requirement:
    data = {
        "text": normalized,
        "normalized_text": normalized,
        "kind": RequirementKind.PRODUCT.value,
        "criticality": criticality,
        "vendor": None,
        "vendor_exclusive": False,
    }
    data.update(fields)
    return Requirement(**data)


def _verdict(status: str, explanation: str = "", source: str = "catalog") -> RequirementVerdict:
    return RequirementVerdict(number=1, status=status, source=source, explanation=explanation, confidence=0.7)


def test_transformer_requirement_judged_by_meter_becomes_no_data():
    requirements = [
        _requirement("Класс точности ТН: не ниже 0,5", CRITICAL),
        _requirement("Межповерочный интервал ТТ: не менее 8 лет"),
        _requirement("Класс точности ТТ: 0,5S"),
        _requirement("Счётчик трансформаторного включения: да", CRITICAL),
        _requirement("Подключение через ТТ: да"),
    ]
    verdicts = {
        1: _verdict("not_meets", "Класс точности A1R1 не соответствует требуемому 0.5"),
        2: _verdict("meets", "Межповерочный интервал НАРТИС-И100 составляет 192 месяца", "si_type"),
        3: _verdict("meets", "В составе ПКУ трансформаторы тока класса 0,5S"),
        4: _verdict("not_meets", "Только прямого включения"),
        5: _verdict("meets", "Есть исполнение 5(10) А"),
    }
    apply_transformer_scope(requirements, verdicts)

    assert verdicts[1].status == "no_data" and "трансформатор" in verdicts[1].explanation
    assert verdicts[2].status == "no_data"
    assert verdicts[3].status == "meets"  # пояснение о самом трансформаторе
    assert verdicts[4].status == "not_meets"  # свойство счётчика
    assert verdicts[5].status == "meets"
    assert not is_transformer_requirement(requirements[3])


def test_variant_of_another_lot_position_does_not_fail():
    requirements = [
        _requirement("Номинальное напряжение переменного тока: 230 В", CRITICAL),
        _requirement("Номинальное напряжение переменного тока: 400 В", CRITICAL),
        _requirement("Наличие сменного GSM/GPRS-модуля: нет"),
        _requirement("Наличие сменного GSM/GPRS-модуля: да"),
        _requirement("Тип интерфейса: Ethernet"),
        _requirement("Тип интерфейса: USB"),
        _requirement("Максимальный ток: 10 А", CRITICAL),
    ]
    verdicts = {
        1: _verdict("meets", "230 В"),
        2: _verdict("not_meets", "Номинальное напряжение 230 В не соответствует требуемому 400 В"),
        3: _verdict("not_meets", "Есть GSM/GPRS", "manual"),
        4: _verdict("meets", "Есть GSM/GPRS", "manual"),
        5: _verdict("meets", "Ethernet есть"),
        6: _verdict("not_meets", "USB нет"),
        7: _verdict("not_meets", "Максимальный ток 100 А"),
    }
    apply_lot_variants(requirements, verdicts)

    assert verdicts[2].status == "no_data" and "несколько позиций" in verdicts[2].explanation
    assert verdicts[3].status == "no_data"
    assert verdicts[6].status == "not_meets"  # перечень интерфейсов — не противоречие
    assert verdicts[7].status == "not_meets"  # пары нет — отказ остаётся
    assert decide_verdict(requirements, verdicts)[0] == "fails"


def test_lot_variant_does_not_soften_code_rules():
    requirements = [
        _requirement("Номинальное напряжение: 230 В", CRITICAL),
        _requirement("Номинальное напряжение: 400 В", CRITICAL),
    ]
    verdicts = {1: _verdict("meets", "230 В"), 2: _verdict("not_meets", "правило", "rule")}
    apply_lot_variants(requirements, verdicts)
    assert verdicts[2].status == "not_meets"


def test_parameter_rule_needs_matching_wording():
    requirements = [
        _requirement("Дискретные входы: In – дискретные входы, 6 шт.", parameter_no=26),
        _requirement("Трёхпозиционный переключатель реле: да", parameter_no=26),
    ]
    verdicts = {1: _verdict("meets", "6 входов"), 2: _verdict("meets", "есть")}
    apply_expert_rules(MIRTEK, requirements, verdicts)

    assert verdicts[1].status == "meets"  # номер параметра ошибочный — правило молчит
    assert verdicts[2].status == "not_meets" and verdicts[2].source == "rule"


def test_reference_assembly_from_32616469643_is_not_failed():
    """Повтор закупки по выгрузке с сервера: эталон больше не «не проходит», конкурент — по
    фирменному УСПД Нартиса — по-прежнему «не проходит»."""

    requirements = [
        _requirement("Номинальное напряжение переменного тока: 230 В", CRITICAL),
        _requirement("Номинальное напряжение переменного тока: 400 В", CRITICAL),
        _requirement("Класс точности ТН: не ниже 0,5", CRITICAL),
        _requirement("Максимальный ток: 10 А", CRITICAL),
        _requirement("Дискретные входы: In – дискретные входы, 6 шт.", parameter_no=26),
        _requirement("Наличие сменного GSM/GPRS-модуля: нет"),
        _requirement("Наличие сменного GSM/GPRS-модуля: да"),
        _requirement("Межповерочный интервал ТТ: не менее 8 лет"),
        _requirement(
            "Модель прибора: ВШУ в сборе с 1-фазным ПУ (ООО «Завод Нартис»)",
            CRITICAL,
            vendor="ООО «Завод Нартис»",
        ),
        _requirement(
            "В комплект шкафа входят УСПД (ООО «Завод Нартис»)",
            CRITICAL,
            vendor="ООО «Завод Нартис»",
            vendor_exclusive=True,
        ),
    ]
    reference = TzReference(
        manufacturer=NARTIS,
        designations=["ВШУ в сборе с 1-фазным ПУ (ООО «Завод Нартис»)"],
    )

    def server_verdicts() -> dict[int, RequirementVerdict]:
        return {
            1: _verdict("meets", "Номинальное напряжение 230 В указано для однофазного исполнения", "si_type"),
            2: _verdict("not_meets", "Номинальное напряжение 230 В не соответствует требуемому 400 В"),
            3: _verdict("not_meets", "Класс точности A1R1 не соответствует требуемому 0.5"),
            4: _verdict(
                "not_meets",
                "Максимальный ток 10 А не указан, максимальный ток для трёхфазного исполнения — 100 А",
                "si_type",
            ),
            5: _verdict("meets", "6 дискретных входов"),
            6: _verdict("not_meets", "В руководстве указано наличие GSM/GPRS", "manual"),
            7: _verdict("meets", "В руководстве указано наличие GSM/GPRS", "manual"),
            8: _verdict("meets", "Межповерочный интервал НАРТИС-И100 — 192 месяца (16 лет)", "si_type"),
        }

    def run(manufacturer: Manufacturer) -> dict[int, RequirementVerdict]:
        verdicts = server_verdicts()
        verdicts.update({index: _verdict("no_data") for index in (9, 10)})
        apply_transformer_scope(requirements, verdicts)
        apply_duration_check(requirements, verdicts)
        apply_evidence_check(requirements, verdicts)
        apply_expert_rules(manufacturer, requirements, verdicts)
        apply_lot_variants(requirements, verdicts)
        apply_reference_rules(manufacturer, requirements, verdicts, reference, [NARTIS, MIRTEK])
        return verdicts

    nartis = run(NARTIS)
    assert all(nartis[index].status != "not_meets" for index in nartis)
    assert nartis[4].status == "partial" and nartis[4].confidence < 0.6
    assert decide_verdict(requirements, nartis)[0] == "caveats"

    mirtek = run(MIRTEK)
    assert mirtek[10].status == "not_meets"
    assert decide_verdict(requirements, mirtek)[0] == "fails"
