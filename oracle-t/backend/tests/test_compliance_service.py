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
from app.services.compliance_numeric import ENABLED as NUMERIC_ENABLED
from app.services.compliance_numeric import CatalogModel, prejudge
from app.services.compliance_service import (
    ComplianceOutcome,
    ComplianceResult,
    RequirementVerdict,
    build_manufacturer_context,
    calculate_percentage,
    description_excerpt,
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


def _add_requirement(
    db_session, tender, text="Класс точности не хуже 1,0", criticality="critical", kind="product"
):
    requirement = Requirement(tender_id=tender.id, text=text, criticality=criticality, kind=kind)
    db_session.add(requirement)
    db_session.commit()
    return requirement


def test_matrix_is_built_only_from_product_requirements(
    db_session, admin_user: User, monkeypatch
):
    """Требования к работам и участнику в матрицу не идут (18.09.2026): сравнивать модель
    счётчика с «группой допуска персонала» бессмысленно, а «нет данных» по ним обнуляло бы
    процент. Закупка только на услуги — матрица не строится, и об этом сказано прямо."""

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender, text="Гарантия на работы 12 месяцев", kind="service")
    _add_requirement(db_session, tender, text="Группа допуска не ниже IV", kind="participant")
    _make_manufacturer(db_session, with_catalog=True, is_mirtek=True)
    calls: list[str] = []
    monkeypatch.setattr(
        compliance_module,
        "run_structured",
        lambda db, **kwargs: calls.append("model") or ComplianceResult(verdicts=[]),
    )

    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert outcome.manufacturers_processed == 0
    assert calls == []
    assert any("Матрица не строится" in message for message in outcome.messages)

    # Появилось требование к товару — матрица считается по нему одному.
    _add_requirement(db_session, tender, text="Класс точности не хуже 1,0", kind="product")
    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    assert outcome.requirements_total == 1
    assert outcome.manufacturers_processed > 0


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


def test_resumed_matrix_skips_manufacturers_already_counted(
    db_session, admin_user: User, monkeypatch
):
    """Продолжение после перезапуска сервера (28.09.2026): матрица сохраняется после каждого
    производителя, и повторный запуск с `skip_fresh_since` не зовёт модель по тем, чей итог
    уже посчитан по актуальным требованиям. Без этого матрица на 55 требований (10-15 минут)
    начиналась заново после каждого `--reload` и не доходила до конца."""

    from datetime import datetime, timezone

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender)
    _make_manufacturer(db_session, with_catalog=True, is_mirtek=True)
    monkeypatch.setattr(
        compliance_module, "run_structured", lambda db, **kwargs: ComplianceResult(verdicts=[])
    )
    first = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)
    assert first.manufacturers_processed > 0

    def _fail(*args, **kwargs):
        raise AssertionError("посчитанного производителя не пересчитывают")

    monkeypatch.setattr(compliance_module, "run_structured", _fail)
    resumed = evaluate_tender(
        db_session,
        tender,
        actor=admin_user,
        use_manual_fallback=False,
        skip_fresh_since=datetime.min.replace(tzinfo=timezone.utc),
    )
    assert resumed.manufacturers_processed == 0
    assert resumed.manufacturers_skipped == first.manufacturers_processed

    # Требования переизвлечены после расчёта — итог устарел и пересчитывается.
    _add_requirement(db_session, tender, text="Интерфейс RS-485")
    monkeypatch.setattr(
        compliance_module, "run_structured", lambda db, **kwargs: ComplianceResult(verdicts=[])
    )
    stale = evaluate_tender(
        db_session,
        tender,
        actor=admin_user,
        use_manual_fallback=False,
        skip_fresh_since=datetime.min.replace(tzinfo=timezone.utc),
    )
    assert stale.manufacturers_processed == first.manufacturers_processed


def test_context_takes_si_types_of_selected_models(db_session):
    """В карточку идут типы СИ отобранных моделей, а не первые по дате записи: у Энергомеры
    первыми были фиктивный «12345-19», ЦЭ6827М и поверочная установка СУ203, а описание
    типа СЕ207 — прибора, под который написано ТЗ, — не попадало вовсе."""

    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    noise = (
        db_session.query(SiType).filter(SiType.manufacturer_id == manufacturer.id).one()
    )
    noise.verified_by_user = True
    wanted = SiType(
        manufacturer_id=manufacturer.id,
        si_code="72632-18",
        notation="СЕ207",
        description_type_text="Таблица 2. Q Реле управления нагрузкой потребителя",
    )
    db_session.add(wanted)
    db_session.flush()
    product = (
        db_session.query(Product).filter(Product.manufacturer_id == manufacturer.id).one()
    )
    product.si_type_id = wanted.id
    db_session.commit()

    context, _ = build_manufacturer_context(db_session, manufacturer, include_manual=False)

    assert "72632-18" in context and "Реле управления нагрузкой" in context
    assert noise.si_code not in context


def test_description_excerpt_keeps_designation_tables():
    """У СЕ207 таблица «G — GSM, Q — реле» стоит на 9 400-м знаке описания типа, а в карточку
    уходили первые 6 000: исполнение «…OG.QUVLF» было не прочитать."""

    text = (
        "Назначение. " * 900
        + "Обслуживание — программа «Admin Tools».\n"
        + "Структура условного обозначения модификаций приведена на рисунке 1.\n"
        + "G GSM\nQ Реле управления нагрузкой потребителя\n"
        + "Комплектность. " * 900
    )

    excerpt = description_excerpt(text)

    assert len(excerpt) <= 6_100
    assert excerpt.startswith("Назначение.")
    assert "G GSM" in excerpt and "Q Реле управления" in excerpt and "Admin Tools" in excerpt
    assert description_excerpt("короткое описание") == "короткое описание"


def test_quota_error_stops_matrix_instead_of_saving_empty_cells(db_session, admin_user: User, monkeypatch):
    """06.10.2026: баланс RouterAI кончился посреди расчёта, а задача обошла всех
    производителей, на каждый запрос получила отказ и завершилась «успешно» с пустой
    матрицей. Теперь отказ из-за денег останавливает расчёт ошибкой."""

    import pytest

    from app.services.ai_provider_service import AiQuotaExceededError

    tender = _make_tender(db_session)
    _make_manufacturer(db_session, with_catalog=True)
    _add_requirement(db_session, tender, "Класс точности 1,0", Criticality.CRITICAL.value)

    def broke(*args, **kwargs):
        raise AiQuotaExceededError("RouterAI отказал: исчерпан лимит расходов ключа или баланс")

    monkeypatch.setattr(compliance_module, "run_structured", broke)

    with pytest.raises(AiQuotaExceededError):
        evaluate_tender(db_session, tender, actor=admin_user)


# ------------------------------------------------- предрешение числовых требований (14.10.2026)


def _model(name: str, groups: set[str] | None = None, **values: str) -> CatalogModel:
    fields = {
        "accuracy": "Класс точности",
        "voltage": "Номинальное напряжение",
        "nominal": "Номинальный ток",
        "maximum": "Максимальный ток",
        "frequency": "Частота сети",
        "mtbf": "Средняя наработка на отказ",
        "life": "Срок службы",
        "mpi": "Межповерочный интервал",
        "tariffs": "Количество тарифов",
        "temperature": "Диапазон рабочих температур",
        "ip": "Степень защиты",
    }
    return CatalogModel(name, {fields[key]: value for key, value in values.items()}, groups or {"3ph_direct"})


def _decide(text: str, *models: CatalogModel, groups: set[str] | None = None) -> str | None:
    found = prejudge(text, list(models), groups if groups is not None else {"3ph_direct"}, NUMERIC_ENABLED)
    return found.status if found else None


def test_numeric_requirement_meets_by_any_model_of_purchased_type():
    """«Межповерочный интервал: 16 лет» при «192 мес.» в каталоге — соответствует (модель
    путала месяцы с годами); одной подходящей модели производителю достаточно."""

    assert _decide("Межповерочный интервал: 16 лет", _model("А", mpi="120 мес."), _model("Б", mpi="192 мес.")) == "meets"
    assert _decide("Средний срок службы: не менее 30 лет", _model("А", life="32 года")) == "meets"
    assert _decide("Средняя наработка на отказ: 280 000 часов", _model("А", mtbf="не менее 400 000 ч")) == "meets"
    assert _decide("Диапазон рабочих температур: от -40 до +70 °C", _model("А", temperature="от минус 45 до плюс 70 °С")) == "meets"
    assert _decide("Номинальная частота: 50 ± 2,5 Гц", _model("А", frequency="50±7,5 Гц")) == "meets"
    assert _decide("Количество тарифов: 4", _model("А", tariffs="4/12")) == "meets"
    assert _decide("Степень защиты корпуса: IP51", _model("А", ip="IP54")) == "meets"


def test_accuracy_better_class_meets_and_worse_goes_to_model():
    """CRITERIA.md, п. 3.1: лучшее значение — соответствует. 0,5S/1 против 0,5S/0,5 —
    реактивная хуже; «не соответствует» код не ставит — решает модель."""

    assert _decide("Класс точности активной/реактивной энергии: ½", _model("А", accuracy="1/1")) == "meets"
    assert _decide("Класс точности активной энергии: 1, реактивной энергии: 2", _model("А", accuracy="0,5S/1")) == "meets"
    assert _decide("Класс точности: 0,5S/0,5", _model("А", accuracy="0,5S/1")) is None
    # Одно число у модели — класс активной энергии; о реактивной оно не говорит.
    assert _decide("Класс точности реактивной энергии: 2,0", _model("А", accuracy="1")) is None
    # Классы ТТ и ТН — не свойство счётчика.
    assert _decide("Класс точности ТТ: не ниже 0,5S", _model("А", accuracy="0,5S/0,5")) is None


def test_currents_and_voltage_are_compared_exactly():
    assert _decide("Номинальный ток: 5 А; Максимальный ток: 100 А", _model("А", nominal="5 (100) А")) == "meets"
    assert _decide("Базовый ток: 5 А", _model("А", nominal="5(60)")) == "meets"
    assert _decide("Номинальное напряжение: 3×230/400 В", _model("А", voltage="3x230/400 В")) == "meets"
    assert _decide("Номинальный ток: 10 А", _model("А", nominal="5 (10)")) is None
    # «230 В» против «3×230/400 В» — разные записи, сравнивать нечего.
    assert _decide("Номинальное напряжение: 230 В", _model("А", voltage="3x230/400 В")) is None


def test_kind_bound_parameters_need_single_purchased_type():
    """Ток в многопозиционной закупке (однофазные и трёхфазные) или без типа — модели:
    «Базовый ток: 5 А» однофазного не ответ трёхфазной позиции."""

    model = _model("А", {"1ph_direct"}, nominal="5(60)")
    assert _decide("Базовый ток: 5 А", model, groups={"1ph_direct", "3ph_direct"}) is None
    assert _decide("Базовый ток: 5 А", model, groups=set()) is None
    # Срок службы от типа не зависит.
    assert _decide("Срок службы: 30 лет", _model("А", life="35 лет"), groups=set()) == "meets"
    # Модели другого типа не сравниваются.
    assert _decide("Срок службы: 30 лет", _model("А", {"1ph_direct"}, life="35 лет")) is None


def test_ambiguous_requirements_and_values_go_to_model():
    one = _model("А", mpi="192 мес.", maximum="60, 80 или 100 А", temperature="от -40 до +70 °С (блок); от -10 до +50")
    # Требование не целиком числовое.
    assert _decide("Номинальное напряжение: 230 В; рабочий диапазон: 170–255 В", one) is None
    assert _decide("Межповерочный интервал ТТ: не менее 8 лет", one) is None
    assert _decide("Номинальный ток: 5 А ± 0,1 А", one) is None
    # «Не более» у срока — не «больше лучше».
    assert _decide("Межповерочный интервал: не более 16 лет", one) is None
    # Подходит лишь одно из исполнений — «частично» решает модель.
    assert _decide("Максимальный ток: 100 А", one) is None
    # Два диапазона у разных блоков — неоднозначно.
    assert _decide("Диапазон рабочих температур: от -40 до +70 °C", one) is None
    # Значения нет — нет и вердикта кодом.
    assert _decide("Срок службы: 30 лет", one) is None


def test_prejudged_requirements_are_not_sent_to_model(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    _add_requirement(db_session, tender, text="Класс точности не хуже 1,0")
    _add_requirement(db_session, tender, text="Средний срок службы: не менее 30 лет")
    manufacturer = _make_manufacturer(db_session, with_catalog=True)
    product = db_session.query(Product).filter(Product.manufacturer_id == manufacturer.id).one()
    db_session.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name="Надёжность",
            field_name="Срок службы",
            value="35 лет",
            source="manufacturer_site",
        )
    )
    db_session.commit()

    asked: list[str] = []

    def fake_run(db, **kwargs):
        # В тестовой базе есть производители других тестов — считаем только свой.
        if manufacturer.brand_name in kwargs["user_text"]:
            asked.append(kwargs["user_text"].split("ТРЕБОВАНИЯ ЗАКУПКИ:", 1)[1])
        return ComplianceResult(verdicts=[_verdict(1, ComplianceStatus.MEETS.value, source="catalog")])

    monkeypatch.setattr(compliance_module, "run_structured", fake_run)
    outcome = evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    assert len(asked) == 1
    assert "Класс точности" in asked[0] and "срок службы" not in asked[0].lower()
    assert outcome.prejudged >= 1
    entries = {
        (entry.explanation or "").split(":", 1)[0]: entry
        for entry in db_session.query(ComplianceMatrixEntry).filter(
            ComplianceMatrixEntry.manufacturer_id == manufacturer.id
        )
    }
    coded = entries["ТЕСТ-12-РУ"]
    assert coded.status == ComplianceStatus.MEETS.value
    assert coded.source == ComplianceSource.PRODUCT_CATALOG.value
    assert "35 лет" in coded.explanation and "сверено кодом" in coded.explanation
