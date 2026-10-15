"""Позиции спецификации в матрице соответствия (15.10.2026, раздел 61 ARCHITECTURE.md).

Закупка 32616469643 «Россети Тюмень»: ВШУ с однофазным ПУ и ВШУ с трёхфазным ПУ
полукосвенного включения в одном лоте. Требования обеих позиций шли одним списком с одной
карточкой производителя, и «400 В» сравнивалось с однофазной моделью — «не соответствует»
у всех. Теперь у требования есть позиция, и каждая позиция сверяется с моделями своего
типа. Модель здесь подменена «наивной»: она, как и живая, сравнивает требование с тем, что
лежит в карточке, — поэтому тест ловит именно смешение позиций, а не качество модели.
"""

from __future__ import annotations

import re
import uuid

import app.services.compliance_service as compliance_module
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceStatus,
    Criticality,
    Requirement,
    RequirementKind,
    WinPercentage,
    WinVerdict,
)
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic
from app.models.source import Source
from app.models.tender import Tender
from app.models.user import User
from app.services.compliance_service import (
    ComplianceResult,
    RequirementVerdict,
    apply_lot_variants,
    evaluate_tender,
)
from app.services.lot_position import lot_groups, position_key
from app.services.tender_analysis import AnalysisOutcome, ExtractedRequirement, _drop_exact_duplicates

CRITICAL = Criticality.CRITICAL.value
IMPORTANT = Criticality.IMPORTANT.value

ONE_PHASE = "1. Счётчик однофазный"
THREE_PHASE = "2. Счётчик трёхфазный полукосвенного включения"


# ------------------------------------------------------------------- позиция без БД


def test_position_key_joins_spellings_of_one_position():
    assert position_key("1. ВШУ с однофазным ПУ") == position_key("Позиция 1") == "1"
    assert position_key("поз. №2 — ВШУ с трёхфазным ПУ") == "2"
    assert position_key("Лот 03") == "3"
    assert position_key("ВШУ с однофазным ПУ") == position_key("вшу  с однофазным ПУ.")
    assert position_key("1.2 Шкаф") != "1"
    assert position_key(None) == position_key("  ") == ""


def test_lot_groups_split_only_when_there_are_two_positions():
    assert lot_groups(["1. А", None, "1. А"]) == [(None, [1, 2, 3])]
    assert lot_groups(["1. А", None, "Позиция 2", "Позиция 1"]) == [
        ("1. А", [1, 4]),
        ("Позиция 2", [3]),
        (None, [2]),
    ]


def _extracted(normalized: str, position: str) -> ExtractedRequirement:
    return ExtractedRequirement(
        text=normalized,
        normalized_text=normalized,
        kind=RequirementKind.PRODUCT.value,
        criticality=CRITICAL,
        group_name="",
        parameter_no=0,
        vendor="",
        vendor_exclusive=False,
        position=position,
    )


def test_same_value_in_two_positions_is_not_a_duplicate():
    items = [
        _extracted("Класс точности: 1", "1. ВШУ с однофазным ПУ"),
        _extracted("Класс точности: 1", "2. ВШУ с трёхфазным ПУ"),
        _extracted("Класс точности: 1", "Позиция 1"),
    ]
    outcome = AnalysisOutcome()
    kept = _drop_exact_duplicates(items, outcome)
    assert [item.position for item in kept] == ["1. ВШУ с однофазным ПУ", "2. ВШУ с трёхфазным ПУ"]
    assert outcome.duplicates_removed == 1


def test_lot_variant_heuristic_leaves_positioned_requirements_alone():
    """Эвристика смешанного лота — страховка для требований без позиции: у требования с
    позицией отказ — честный вердикт по модели своей позиции."""

    requirements = [
        Requirement(text="н", normalized_text="Номинальное напряжение: 230 В", criticality=CRITICAL, position=ONE_PHASE),
        Requirement(text="н", normalized_text="Номинальное напряжение: 400 В", criticality=CRITICAL, position=THREE_PHASE),
    ]
    verdicts = {
        1: RequirementVerdict(number=1, status="meets", source="catalog", explanation="230 В", confidence=0.9),
        2: RequirementVerdict(number=2, status="not_meets", source="catalog", explanation="230 В", confidence=0.9),
    }
    apply_lot_variants(requirements, verdicts)
    assert verdicts[2].status == "not_meets"


# ------------------------------------------------------------------ матрица целиком


def _make_tender(db_session) -> Tender:
    suffix = uuid.uuid4().hex[:8]
    source = Source(key=f"lot_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis")
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"L-{suffix}", title="Поставка ВШУ с ПУ")
    db_session.add(tender)
    db_session.commit()
    db_session.refresh(tender)
    return tender


def _add_product(db_session, manufacturer: Manufacturer, name: str, values: dict[str, str]) -> None:
    product = Product(manufacturer_id=manufacturer.id, model_name=name)
    db_session.add(product)
    db_session.flush()
    for field_name, value in values.items():
        db_session.add(
            ProductCharacteristic(
                product_id=product.id,
                group_name="Электрические характеристики",
                field_name=field_name,
                value=value,
                source="manufacturer_site",
            )
        )


SINGLE = {"Количество фаз": "1", "Номинальное напряжение": "230 В", "Максимальный ток": "60 А"}
THREE_SEMI = {
    "Количество фаз": "3",
    "Номинальное напряжение": "3×230/400 В",
    "Номинальный ток": "5 А",
    "Максимальный ток": "10 А",
    "Тип включения цепей": "полукосвенного включения",
}


def _make_manufacturer(db_session, label: str, models: list[tuple[str, dict[str, str]]]) -> Manufacturer:
    suffix = uuid.uuid4().hex[:6]
    manufacturer = Manufacturer(legal_name=f"ООО «{label} {suffix}»", brand_name=f"{label}{suffix}")
    db_session.add(manufacturer)
    db_session.flush()
    for name, values in models:
        _add_product(db_session, manufacturer, f"{name}-{suffix}", values)
    db_session.commit()
    db_session.refresh(manufacturer)
    return manufacturer


def _add_requirement(db_session, tender, normalized, criticality=CRITICAL, position=None, parameter_no=None):
    db_session.add(
        Requirement(
            tender_id=tender.id,
            text=normalized,
            normalized_text=normalized,
            criticality=criticality,
            kind=RequirementKind.PRODUCT.value,
            position=position,
            parameter_no=parameter_no,
        )
    )
    db_session.commit()


_THREE_PHASE_WANTED = re.compile(r"тр[её]хфаз|400|5\(10\)", re.IGNORECASE)
_ONE_PHASE_WANTED = re.compile(r"однофаз|230 В", re.IGNORECASE)


def _naive_model(db, *, user_text, **kwargs):
    """Как живая модель на 32616469643: сравнивает требование с типом прибора, который
    лежит в карточке. Трёхфазное требование при однофазной карточке — «не соответствует»."""

    card, listing = user_text.split("ТРЕБОВАНИЯ ЗАКУПКИ:", 1)
    has_three = "Трёхфазный полукосвенного" in card
    has_one = "Однофазный прямого" in card
    verdicts = []
    for line in listing.strip().splitlines():
        number, text = line.split(".", 1)
        if _THREE_PHASE_WANTED.search(text):
            fits = has_three
        elif _ONE_PHASE_WANTED.search(text):
            fits = has_one
        else:
            fits = True
        verdicts.append(
            RequirementVerdict(
                number=int(number),
                status="meets" if fits else "not_meets",
                source="catalog",
                explanation="по карточке" if fits else "в карточке прибор другого типа",
                confidence=0.9,
            )
        )
    return ComplianceResult(verdicts=verdicts)


def _matrix(db_session, tender, manufacturer) -> dict[str, ComplianceMatrixEntry]:
    rows = (
        db_session.query(ComplianceMatrixEntry, Requirement)
        .join(Requirement, Requirement.id == ComplianceMatrixEntry.requirement_id)
        .filter(
            ComplianceMatrixEntry.tender_id == tender.id,
            ComplianceMatrixEntry.manufacturer_id == manufacturer.id,
        )
        .all()
    )
    return {requirement.normalized_text: entry for entry, requirement in rows}


def _total(db_session, tender, manufacturer) -> WinPercentage:
    return (
        db_session.query(WinPercentage)
        .filter(
            WinPercentage.tender_id == tender.id,
            WinPercentage.manufacturer_id == manufacturer.id,
            WinPercentage.is_current.is_(True),
        )
        .one()
    )


def test_mixed_lot_is_judged_per_position(db_session, admin_user: User, monkeypatch):
    """Однофазный 230 В + трёхфазный 400 В 5(10) А: производитель с обоими типами не
    «не проходит» из-за чужой позиции, производитель без трёхфазного полукосвенного —
    «не проходит» именно по ней."""

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender, "Тип прибора: однофазный прямого включения", position=ONE_PHASE, parameter_no=1)
    _add_requirement(db_session, tender, "Номинальное напряжение: 230 В", position=ONE_PHASE)
    _add_requirement(db_session, tender, "Тип прибора: трёхфазный полукосвенного включения", position=THREE_PHASE, parameter_no=1)
    _add_requirement(db_session, tender, "Номинальное напряжение: 3×230/400 В", position=THREE_PHASE)
    _add_requirement(db_session, tender, "Номинальный (максимальный) ток: 5(10) А", position=THREE_PHASE)
    _add_requirement(db_session, tender, "Наличие оптопорта: да", IMPORTANT)

    both = _make_manufacturer(db_session, "Оба", [("ОФ-1", SINGLE), ("ТФ-3", THREE_SEMI)])
    single = _make_manufacturer(db_session, "Однофазник", [("ОФ-1", SINGLE)])

    monkeypatch.setattr(compliance_module, "run_structured", _naive_model)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    both_matrix = _matrix(db_session, tender, both)
    assert len(both_matrix) == 6
    assert all(entry.status != ComplianceStatus.NOT_MEETS.value for entry in both_matrix.values()), {
        text: (entry.status, entry.explanation) for text, entry in both_matrix.items()
    }
    assert _total(db_session, tender, both).verdict != WinVerdict.FAILS.value

    single_matrix = _matrix(db_session, tender, single)
    for text in ("Тип прибора: однофазный прямого включения", "Номинальное напряжение: 230 В"):
        assert single_matrix[text].status == ComplianceStatus.MEETS.value, text
    kind_entry = single_matrix["Тип прибора: трёхфазный полукосвенного включения"]
    assert kind_entry.status == ComplianceStatus.NOT_MEETS.value
    assert "нет приборов типа" in kind_entry.explanation
    # Справочник бывает неполным — отказ по отсутствию типа уходит на проверку человеку.
    assert kind_entry.needs_human_review is True
    total = _total(db_session, tender, single)
    assert total.verdict == WinVerdict.FAILS.value
    assert "поз. 2" in total.reason_summary


def test_position_card_holds_only_models_of_its_kind(db_session, admin_user: User, monkeypatch):
    """Карточка позиции — только модели её типа: при шести местах однофазная модель
    раньше оставалась в карточке трёхфазной позиции и путала модель."""

    tender = _make_tender(db_session)
    _add_requirement(db_session, tender, "Номинальное напряжение: 230 В", position=ONE_PHASE)
    # Не числовое: числовые сверяет код, и запроса к модели могло бы не быть вовсе.
    _add_requirement(db_session, tender, "Тип прибора: трёхфазный полукосвенного включения", position=THREE_PHASE)
    both = _make_manufacturer(db_session, "Карточка", [("ОФ-1", SINGLE), ("ТФ-3", THREE_SEMI)])

    cards: list[tuple[str, str]] = []

    def spy(db, *, user_text, **kwargs):
        if both.brand_name in user_text:
            cards.append((user_text.split("ТРЕБОВАНИЯ ЗАКУПКИ:", 1)[0], user_text))
        return _naive_model(db, user_text=user_text, **kwargs)

    monkeypatch.setattr(compliance_module, "run_structured", spy)
    evaluate_tender(db_session, tender, actor=admin_user, use_manual_fallback=False)

    three_phase_cards = [card for card, text in cards if "ПОЗИЦИЯ СПЕЦИФИКАЦИИ: 2." in text]
    assert three_phase_cards, "трёхфазная позиция не ушла модели отдельным запросом"
    for card in three_phase_cards:
        assert "ТФ-3" in card and "ОФ-1" not in card
