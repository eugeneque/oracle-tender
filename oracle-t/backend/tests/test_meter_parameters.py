"""Параметры для ПУ и типы счётчиков (файл тендерного отдела «Параметры для ПУ», 25.09.2026)."""

import uuid

import pytest

from app.adapters import astra_compatible
from app.adapters.astra_compatible import AstraCatalog, parse_catalog
from app.db.session import SessionLocal
from app.models.analysis import ComplianceStatus, Criticality, Requirement, WinVerdict
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import Tender
from app.seed.meter_parameters import METER_PARAMETERS, detect_parameter
from app.services.compliance_service import (
    RequirementVerdict,
    _requirements_block,
    apply_expert_rules,
    astra_facts,
    decide_verdict,
)
from app.services.meter_kind import kinds_from_text, tender_kinds


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Фазность без включения и крепления — все однофазные виды.
        ("Поставка счетчиков электроэнергии однофазных", ["1ph_direct_din", "1ph_direct_split", "1ph_direct_panel"]),
        # 230/400 В и 10 А — полукосвенное; DIN-рейка.
        ("Счетчик трехфазный, 3х230/400 В, 5(10) А, установка на DIN-рейку", ["3ph_semi_din"]),
        # 57,7 В — трансформаторное; «на три винта» — шкафной.
        ("Счетчик трехфазный 3х57,7/100 В, 5(10) А, крепление на три винта", ["3ph_indirect_panel"]),
        # 60 А — прямое; выносной индикатор — сплит.
        ("Счётчик однофазный 230 В 5(60) А с выносным индикаторным устройством", ["1ph_direct_split"]),
        # Универсальный «3×(57,7–230)/(100–400) В, 10 А» — оба вида включения.
        (
            "Номинальное напряжение: 3×(57,7-230)/(100-400) В; Максимальный ток: 10 А; Тип монтажа: DIN-рейка",
            ["3ph_semi_din", "3ph_indirect_din"],
        ),
        ("Прибор учёта электроэнергии высоковольтный 10 кВ", ["hv"]),
        # «ТП 10 кВ» — место установки, а не ВПУ.
        (
            "Установка счётчиков в ТП 10 кВ, трехфазные прямого включения 5(100) А",
            ["3ph_direct_din", "3ph_direct_split", "3ph_direct_panel"],
        ),
        # «Рабочее напряжение 10 кВ» — у указателя напряжения из той же закупки, не ВПУ.
        ("Указатель высокого напряжения, рабочее напряжение не менее 10 кВ", []),
        ("Полукосвенного включения", ["3ph_semi_din", "3ph_semi_panel"]),
        # Ни фазности, ни включения — тип не определён, а не «все одиннадцать».
        ("Поставка счётчиков электроэнергии", []),
    ],
)
def test_kinds_from_text(text, expected):
    assert kinds_from_text(text) == expected


def test_tender_kinds_is_none_when_unknown():
    assert tender_kinds("Счетчик электроэнергии") is None
    assert tender_kinds("Счетчик", ["Количество фаз: однофазный", "Тип монтажа: сплит"]) == [
        "1ph_direct_split"
    ]


def test_all_39_parameters_from_file():
    assert [item.no for item in METER_PARAMETERS] == list(range(1, 40))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Трехпозиционный переключатель реле управления нагрузкой", 26),
        ("Наличие в реестре промышленной продукции согласно ПП РФ № 719", 35),
        ("Конфигуратор должен работать под ОС Astra Linux", 39),
        ("Класс точности не хуже 1,0", None),  # параметр без правила кода — решает модель
    ],
)
def test_detect_parameter(text, expected):
    assert detect_parameter(text) == expected


def _manufacturer(brand: str) -> Manufacturer:
    return Manufacturer(legal_name=f"ООО «{brand}»", brand_name=brand)


def _requirement(text: str, criticality: str = Criticality.CRITICAL.value, parameter_no=None) -> Requirement:
    return Requirement(text=text, normalized_text=text, criticality=criticality, parameter_no=parameter_no)


def _verdict(number: int, status: str) -> RequirementVerdict:
    return RequirementVerdict(number=number, status=status, source="catalog", explanation="", confidence=0.9)


def test_three_position_relay_only_three_manufacturers():
    requirements = [_requirement("Трёхпозиционный переключатель реле", parameter_no=26)]

    mirtek = {1: _verdict(1, ComplianceStatus.MEETS.value)}
    apply_expert_rules(_manufacturer("МИРТЕК"), requirements, mirtek)
    assert mirtek[1].status == ComplianceStatus.NOT_MEETS.value
    assert mirtek[1].source == "rule"

    taipit = {1: _verdict(1, ComplianceStatus.NO_DATA.value)}
    apply_expert_rules(_manufacturer("Тайпит"), requirements, taipit)
    assert taipit[1].status == ComplianceStatus.MEETS.value

    # Вердикт модели по фактам у производителя из тройки сохраняется.
    energomera = {1: _verdict(1, ComplianceStatus.NOT_MEETS.value)}
    apply_expert_rules(_manufacturer("Энергомера"), requirements, energomera)
    assert energomera[1].status == ComplianceStatus.NOT_MEETS.value
    assert energomera[1].source == "catalog"


def test_type_mismatch_is_no_data_when_manufacturer_makes_that_type():
    """Многопозиционная закупка: позиция «СЕ 308» сравнилась с однофазной моделью из
    карточки. Производитель трёхфазные делает — это «нет данных», а не «не проходит»."""

    requirements = [_requirement("Наименование, тип прибора: СЕ 308 S34 или эквивалент", parameter_no=1)]
    verdicts = {1: _verdict(1, ComplianceStatus.NOT_MEETS.value)}
    apply_expert_rules(_manufacturer("Нартис"), requirements, verdicts, made_kinds={"1ph_direct", "3ph_direct"})
    assert verdicts[1].status == ComplianceStatus.NO_DATA.value

    # Трёхфазных в каталоге нет — вердикт модели остаётся.
    verdicts = {1: _verdict(1, ComplianceStatus.NOT_MEETS.value)}
    apply_expert_rules(_manufacturer("Нартис"), requirements, verdicts, made_kinds={"1ph_direct"})
    assert verdicts[1].status == ComplianceStatus.NOT_MEETS.value


def test_requirements_block_carries_parameter_rule():
    block = _requirements_block([_requirement("Максимальный ток: 60 А", parameter_no=7)])
    assert "[параметр 7: Максимальный ток" in block
    assert "прямого включения" in block


def test_decide_verdict():
    critical = _requirement("Класс точности 1,0")
    important = _requirement("RS-485", Criticality.IMPORTANT.value)
    requirements = [critical, important]

    fails = decide_verdict(requirements, {1: _verdict(1, "not_meets"), 2: _verdict(2, "meets")})
    assert fails[0] == WinVerdict.FAILS.value
    assert "Класс точности" in fails[1]

    caveats = decide_verdict(requirements, {1: _verdict(1, "meets"), 2: _verdict(2, "not_meets")})
    assert caveats[0] == WinVerdict.CAVEATS.value

    unknown = decide_verdict(requirements, {1: _verdict(1, "no_data"), 2: _verdict(2, "meets")})
    assert unknown[0] == WinVerdict.UNKNOWN.value

    passes = decide_verdict(requirements, {1: _verdict(1, "meets"), 2: _verdict(2, "meets")})
    assert passes[0] == WinVerdict.PASSES.value


@pytest.fixture()
def astra_catalog():
    payload = {
        "DATA": [
            {"NAME": "MeterTools", "vendor": {"NAME": "МИРТЕК"}, "osLine": {"ALSE 1.7": 2}},
            {"NAME": "Конфигуратор счетчиков РОТЕК", "vendor": {"NAME": "ЛИС"}, "osLine": {"ALSE 1.8": 1}},
            {"NAME": "МиР ПиА Процесс+", "vendor": {"NAME": "МиР ПиА"}, "osLine": {}},
        ]
    }
    astra_compatible.set_cache(AstraCatalog(items=parse_catalog(payload), loaded_at=1e12))
    yield
    astra_compatible.set_cache(None)


def test_astra_facts(astra_catalog):
    assert "MeterTools" in astra_facts(_manufacturer("МИРТЕК"))[0]
    # Производитель — в названии ПО, а не у разработчика.
    assert "РОТЕК" in astra_facts(_manufacturer("Ротек"))[0]
    # Граница слова: «МИР» не находит «МИРТЕК».
    assert all("MeterTools" not in line for line in astra_facts(_manufacturer("МИР")))
    assert "НЕ найдено" in astra_facts(_manufacturer("Нартис"))[0]


def test_astra_absence_overrides_model(astra_catalog):
    requirements = [_requirement("Конфигуратор под Astra Linux", parameter_no=39)]
    verdicts = {1: _verdict(1, ComplianceStatus.MEETS.value)}
    apply_expert_rules(_manufacturer("Ленэлектро"), requirements, verdicts)
    assert verdicts[1].status == ComplianceStatus.NOT_MEETS.value
    assert verdicts[1].source == "astra"

    verdicts = {1: _verdict(1, ComplianceStatus.MEETS.value)}
    apply_expert_rules(_manufacturer("МИРТЕК"), requirements, verdicts)
    assert verdicts[1].status == ComplianceStatus.MEETS.value


def test_tenders_filter_by_meter_kind(client, admin_token):
    db = SessionLocal()
    try:
        source = Source(
            key=f"kindtest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        semi = Tender(
            source_id=source.id, external_id="K-1", currency="RUB",
            title="Счётчики трёхфазные полукосвенного включения",
            meter_kinds=["3ph_semi_din", "3ph_semi_panel"],
        )
        single = Tender(
            source_id=source.id, external_id="K-2", currency="RUB",
            title="Счётчики однофазные", meter_kinds=["1ph_direct_din"],
        )
        db.add_all([semi, single])
        db.commit()
        semi_id, single_id = str(semi.id), str(single.id)
    finally:
        db.close()

    response = client.get(
        "/tenders",
        params={"meter_kind": ["3ph_semi_din", "hv"], "page_size": 200},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    items = response.json()["items"]
    ids = {item["id"] for item in items}
    assert semi_id in ids
    assert single_id not in ids
    row = next(item for item in items if item["id"] == semi_id)
    assert row["meter_kinds"] == ["3ph_semi_din", "3ph_semi_panel"]
