"""Тесты матрицы соответствия и процента победителя (раздел 5.5 ТЗ — Этап 6).

Формула процента проверяется чистой арифметикой, без БД и без модели: это то место, где
тихая ошибка меняет вывод по тендеру, а увидеть её в интерфейсе невозможно. Остальное —
поведение вокруг модели: не приписывать «не соответствует» из-за отсутствия данных, не
принимать вердикты по несуществующим требованиям, не падать при сбое.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import app.services.compliance_service as compliance_module
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceSource,
    ComplianceStatus,
    Criticality,
    Requirement,
    WinPercentage,
)
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, SiType
from app.models.source import Source
from app.models.tender import Tender
from app.models.user import User
from app.services.compliance_service import (
    ComplianceOutcome,
    ComplianceResult,
    RequirementVerdict,
    build_manufacturer_context,
    calculate_percentage,
    evaluate_tender,
)


def _verdict(number: int, status: str, **overrides) -> RequirementVerdict:
    """Полный набор полей: в схеме ответа они обязательные (см. докстринг
    `RequirementVerdict`) — модель обязана заполнить каждое."""

    data = {"number": number, "status": status, "source": "", "explanation": "", "confidence": 0.9}
    data.update(overrides)
    return RequirementVerdict(**data)


def _requirement(criticality: str) -> Requirement:
    return Requirement(id=uuid.uuid4(), tender_id=uuid.uuid4(), text="т", criticality=criticality)


def test_percentage_weighs_requirements_by_criticality():
    """Критичное весит 3, важное 2, второстепенное 1 (раздел 5.5 ТЗ, п.1)."""

    requirements = [
        _requirement(Criticality.CRITICAL.value),
        _requirement(Criticality.MINOR.value),
    ]
    verdicts = {
        1: _verdict(1, ComplianceStatus.MEETS.value),
        2: _verdict(2, ComplianceStatus.NOT_MEETS.value),
    }

    percentage, scored = calculate_percentage(requirements, verdicts)

    # (3×1 + 1×0) / (3+1) = 75%
    assert percentage == Decimal("75.00")
    assert scored == 2


def test_partial_counts_as_half():
    requirements = [_requirement(Criticality.IMPORTANT.value)]
    verdicts = {1: _verdict(1, ComplianceStatus.PARTIAL.value)}

    assert calculate_percentage(requirements, verdicts)[0] == Decimal("50.00")


def test_no_data_is_excluded_from_denominator():
    """`no_data` не занижает процент: неполнота каталога — не свойство прибора
    (раздел 5.5 ТЗ, п.2). Но число оценённых требований возвращается отдельно, чтобы
    100% по одному требованию из трёх не выглядели полным соответствием."""

    requirements = [
        _requirement(Criticality.CRITICAL.value),
        _requirement(Criticality.CRITICAL.value),
        _requirement(Criticality.CRITICAL.value),
    ]
    verdicts = {
        1: _verdict(1, ComplianceStatus.MEETS.value),
        2: _verdict(2, ComplianceStatus.NO_DATA.value),
        3: _verdict(3, ComplianceStatus.NO_DATA.value),
    }

    percentage, scored = calculate_percentage(requirements, verdicts)

    assert percentage == Decimal("100.00")
    assert scored == 1  # оценено одно требование из трёх


def test_percentage_is_zero_when_nothing_scored():
    requirements = [_requirement(Criticality.CRITICAL.value)]
    verdicts = {1: _verdict(1, ComplianceStatus.NO_DATA.value)}

    assert calculate_percentage(requirements, verdicts) == (Decimal("0.00"), 0)


def _make_tender(db_session) -> Tender:
    suffix = uuid.uuid4().hex[:8]
    source = Source(
        key=f"cmp_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis"
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"C-{suffix}", title="Поставка счетчиков")
    db_session.add(tender)
    db_session.commit()
    db_session.refresh(tender)
    return tender


def _make_manufacturer(db_session, *, with_catalog: bool, is_mirtek: bool = False) -> Manufacturer:
    suffix = uuid.uuid4().hex[:8]
    manufacturer = Manufacturer(
        legal_name=f"ООО «Тест {suffix}»", brand_name=f"Тест{suffix}", is_mirtek=is_mirtek
    )
    db_session.add(manufacturer)
    db_session.flush()
    if with_catalog:
        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code="61891-15",
            notation="ТЕСТ-12",
            mpi_months=192,
        )
        product = Product(manufacturer_id=manufacturer.id, model_name="ТЕСТ-12-РУ")
        db_session.add_all([si_type, product])
        db_session.flush()
        db_session.add(
            ProductCharacteristic(
                product_id=product.id,
                group_name="Метрологические характеристики",
                field_name="Класс точности",
                value="1,0",
                source="fgis_description_type",
            )
        )
    db_session.commit()
    db_session.refresh(manufacturer)
    return manufacturer


def _add_requirement(db_session, tender, text="Класс точности не хуже 1,0", criticality="critical"):
    requirement = Requirement(tender_id=tender.id, text=text, criticality=criticality)
    db_session.add(requirement)
    db_session.commit()
    return requirement


def test_manufacturer_without_catalog_gets_no_data_without_calling_model(
    db_session, admin_user: User, monkeypatch
):
    """Пустая карточка производителя — честный `no_data` и ни одного обращения к модели:
    спрашивать не о чем, а вызовы платные."""

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    _make_manufacturer(db_session, with_catalog=False)

    def _fail(*args, **kwargs):
        raise AssertionError("модель не должна вызываться при пустом каталоге")

    monkeypatch.setattr(compliance_module, "run_structured", _fail)

    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert outcome.entries_saved >= 1
    entries = (
        db_session.query(ComplianceMatrixEntry)
        .filter(ComplianceMatrixEntry.tender_id == tender.id)
        .all()
    )
    assert entries and all(entry.status == ComplianceStatus.NO_DATA.value for entry in entries)


def test_verdicts_are_saved_with_source_and_percentage(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    manufacturer = _make_manufacturer(db_session, with_catalog=True, is_mirtek=True)

    monkeypatch.setattr(
        compliance_module,
        "run_structured",
        lambda db, **kwargs: ComplianceResult(
            verdicts=[
                _verdict(
                    1,
                    ComplianceStatus.MEETS.value,
                    source="si_type",
                    explanation="Класс точности 1,0 подтверждён Описанием типа",
                    confidence=0.95,
                )
            ]
        ),
    )

    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    entry = (
        db_session.query(ComplianceMatrixEntry)
        .filter(
            ComplianceMatrixEntry.tender_id == tender.id,
            ComplianceMatrixEntry.manufacturer_id == manufacturer.id,
        )
        .one()
    )
    assert entry.status == ComplianceStatus.MEETS.value
    assert entry.source == ComplianceSource.SI_TYPE.value
    assert entry.needs_human_review is False

    percentage = (
        db_session.query(WinPercentage)
        .filter(
            WinPercentage.tender_id == tender.id,
            WinPercentage.manufacturer_id == manufacturer.id,
        )
        .one()
    )
    assert percentage.percentage == Decimal("100.00")
    assert percentage.requirements_scored == 1


def test_low_confidence_is_flagged_for_human_review(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    _make_manufacturer(db_session, with_catalog=True)

    monkeypatch.setattr(
        compliance_module,
        "run_structured",
        lambda db, **kwargs: ComplianceResult(
            verdicts=[
                _verdict(1, ComplianceStatus.MEETS.value, confidence=0.2)
            ]
        ),
    )

    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert outcome.needs_review >= 1


def test_verdict_for_unknown_requirement_number_is_ignored(
    db_session, admin_user: User, monkeypatch
):
    """Модель иногда возвращает номера, которых не было в запросе — такие вердикты
    относятся неизвестно к чему."""

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    _make_manufacturer(db_session, with_catalog=True)

    monkeypatch.setattr(
        compliance_module,
        "run_structured",
        lambda db, **kwargs: ComplianceResult(
            verdicts=[
                _verdict(42, ComplianceStatus.MEETS.value, confidence=1.0)
            ]
        ),
    )

    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    entry = (
        db_session.query(ComplianceMatrixEntry)
        .filter(ComplianceMatrixEntry.tender_id == tender.id)
        .first()
    )
    assert entry.status == ComplianceStatus.NO_DATA.value


def test_model_failure_does_not_break_calculation(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    _make_manufacturer(db_session, with_catalog=True)

    def _raise(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(compliance_module, "run_structured", _raise)

    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert outcome.messages
    assert outcome.entries_saved >= 1


def test_tender_without_requirements_is_reported(db_session, admin_user: User):
    tender = _make_tender(db_session)

    outcome = evaluate_tender(db_session, tender, actor=admin_user)

    assert outcome.entries_saved == 0
    assert any("нет извлечённых требований" in message for message in outcome.messages)


def test_context_excludes_manual_until_fallback(db_session):
    """Руководство — третий шаг сопоставления и подключается только когда первые два не
    дали ответа (раздел 5.5 ТЗ, п.3)."""

    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    product = (
        db_session.query(Product).filter(Product.manufacturer_id == manufacturer.id).one()
    )
    db_session.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Интерфейсы и связь",
            field_name="Тип интерфейса",
            value="RS-485",
            source="user_manual",
        )
    )
    db_session.commit()

    without_manual, _ = build_manufacturer_context(db_session, manufacturer, include_manual=False)
    with_manual, _ = build_manufacturer_context(db_session, manufacturer, include_manual=True)

    assert "RS-485" not in without_manual
    assert "[manual]" in with_manual and "RS-485" in with_manual


def test_requirements_are_asked_in_batches(db_session, admin_user: User, monkeypatch):
    """Все требования сразу не помещаются в ответ модели: на живом тендере с 83 требованиями
    JSON обрывался по лимиту токенов, и производитель терял матрицу целиком. Проверяем, что
    запросы идут пачками, а сквозная нумерация вердиктов при склейке сохраняется.

    Проверяется `_ask_in_batches`, а не `evaluate_tender`: последний обходит всех
    производителей справочника, и счётчик пачек смешал бы вызовы по разным карточкам."""

    monkeypatch.setattr(compliance_module, "REQUIREMENTS_PER_REQUEST", 2)
    tender = _make_tender(db_session)
    requirements = [
        _add_requirement(db_session, tender, text=f"Требование {index}") for index in range(5)
    ]
    manufacturer = _make_manufacturer(db_session, with_catalog=True)

    batch_sizes: list[int] = []

    def _fake(db, *, system_prompt, user_text, response_model, **kwargs):
        size = sum(1 for line in user_text.splitlines() if line[:1].isdigit() and ". " in line)
        batch_sizes.append(size)
        return ComplianceResult(
            verdicts=[
                _verdict(number, ComplianceStatus.MEETS.value, confidence=1.0)
                for number in range(1, size + 1)
            ]
        )

    monkeypatch.setattr(compliance_module, "run_structured", _fake)

    verdicts = compliance_module._ask_in_batches(
        db_session, manufacturer, requirements, "[si_type] карточка", ComplianceOutcome()
    )

    assert batch_sizes == [2, 2, 1]
    # Номера сквозные: локальные 1-2 третьей пачки должны стать 5, а не остаться 1.
    assert sorted(verdicts) == [1, 2, 3, 4, 5]
    assert all(v.status == ComplianceStatus.MEETS.value for v in verdicts.values())


def test_failed_batch_does_not_lose_other_batches(db_session, admin_user: User, monkeypatch):
    monkeypatch.setattr(compliance_module, "REQUIREMENTS_PER_REQUEST", 2)
    tender = _make_tender(db_session)
    requirements = [
        _add_requirement(db_session, tender, text=f"Требование {index}") for index in range(4)
    ]
    manufacturer = _make_manufacturer(db_session, with_catalog=True)

    calls = {"n": 0}

    def _fake(db, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ответ модели оборван")
        return ComplianceResult(
            verdicts=[
                _verdict(1, ComplianceStatus.MEETS.value, confidence=1.0),
                _verdict(2, ComplianceStatus.MEETS.value, confidence=1.0),
            ]
        )

    monkeypatch.setattr(compliance_module, "run_structured", _fake)

    verdicts = compliance_module._ask_in_batches(
        db_session, manufacturer, requirements, "[si_type] карточка", ComplianceOutcome()
    )

    # Первая пачка потеряна целиком, вторая сохранилась — вердикты 3 и 4.
    assert sorted(verdicts) == [3, 4]


def test_low_coverage_percentage_is_marked_as_unreliable(db_session, admin_user: User, monkeypatch):
    """100% по трём требованиям из десяти — методически верное число (раздел 5.5 ТЗ), но
    рядом с честно оценёнными сорока оно вводит в заблуждение. Само значение не меняем,
    но в пояснении обязана быть оговорка."""

    tender = _make_tender(db_session)
    for index in range(10):
        _add_requirement(db_session, tender, text=f"Требование {index}", criticality="important")
    manufacturer = _make_manufacturer(db_session, with_catalog=True)

    monkeypatch.setattr(
        compliance_module,
        "run_structured",
        lambda db, **kwargs: ComplianceResult(
            verdicts=[_verdict(1, ComplianceStatus.MEETS.value, confidence=1.0)]
        ),
    )

    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    record = (
        db_session.query(WinPercentage)
        .filter(
            WinPercentage.tender_id == tender.id,
            WinPercentage.manufacturer_id == manufacturer.id,
        )
        .one()
    )
    assert record.percentage == Decimal("100.00")
    assert record.requirements_scored == 1
    assert "ненадёжна" in (record.reason_summary or "")
