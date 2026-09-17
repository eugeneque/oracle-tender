"""ПО верхнего уровня: разбор списков, связь со справочником, факты для сопоставления
(замечание тестировщика 16.09.2026).

Фикстуры — фрагменты живых страниц (разведка 16.09.2026), а не придуманная разметка:
у каждой площадки своя ловушка (rowspan у Пирамиды, незакрытые <li> у АльфаЦЕНТР,
сокращённые перечисления у Энергосферы), и тест обязан ловить именно их.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.adapters import upper_software as adapter
from app.adapters.upper_software import SupportedDevice, split_device_names
from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, SiType
from app.models.source import Source
from app.models.tender import Tender
from app.models.upper_software import UpperSoftwareDevice, UpperSoftwareProductLink
from app.services import upper_software_service as service
from app.services.compliance_service import build_manufacturer_context

FIXTURES = Path(__file__).parent / "fixtures" / "upper_software"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------- разбор


def test_split_device_names_handles_notes_and_shorthand():
    """Скобочные пометки и хвосты-описания — не часть обозначения; «-105» у Энергосферы
    раскрывается по предыдущему обозначению; «HDLC» из описания каналов — не модель."""

    assert split_device_names("МИРТЕК-32-РУ (протокол СПОДЭС), МИРТЕК-135-РУ (СПОДЭС)") == [
        "МИРТЕК-32-РУ",
        "МИРТЕК-135-РУ",
    ]
    assert split_device_names("МИЛУР-104, -105, -107S") == ["МИЛУР-104", "МИЛУР-105", "МИЛУР-107S"]
    assert split_device_names(
        "Миртек-12-РУ (СПОДЭС 4.0) включая получение инициативных сообщений по каналам TCP/UDP, HDLC"
    ) == ["Миртек-12-РУ"]
    assert split_device_names("ПУЛЬСАР 1,ПУЛЬСАР 3 - многофункциональные") == ["ПУЛЬСАР 1", "ПУЛЬСАР 3"]
    assert split_device_names("Вектор-101, однофазные") == ["Вектор-101"]


def test_sicon_expands_rowspans_and_reads_support_flags():
    """Производитель и тип оборудования у Пирамиды стоят один раз на семейство (rowspan);
    флаги колонок — точки в ячейках; номер ГРСИ — отдельная колонка."""

    devices = adapter.parse_sicon(_fixture("sicon.html"))

    assert [d.device_raw for d in devices] == [
        "МИРТЕК-1-РУ",
        "МИРТЕК-3-РУ",
        "МИРТЕК-12-РУ",
        "МИРТЕК-12-РУ (СПОДЭС-4)",
        "МИРТЕК-32-РУ",
    ]
    assert all(d.manufacturer_raw == 'ООО "МИРТЕК"' for d in devices)
    assert all(d.device_type == "Счётчик электрической энергии" for d in devices)
    assert devices[2].si_codes == ["61891-15"]
    assert devices[3].device_names == ["МИРТЕК-12-РУ"]
    support = devices[3].details["support"]
    assert support["Пирамида 2.0"] is True
    assert support["Режим взаимодействия: через промежуточное ПО"] is False
    assert "встроенное ПО" in devices[3].details["note"]


def test_nforceit_groups_models_under_manufacturer_heading():
    devices = adapter.parse_nforceit(_fixture("nforceit.html"))

    mirtek = [d for d in devices if d.manufacturer_raw == 'ООО "МИРТЕК"']
    assert len(mirtek) == 17
    assert mirtek[0].device_raw == "МИРТЕК-1-РУ-W3"
    assert mirtek[-1].device_names == ["Миртек-135-РУ"]
    assert [d.manufacturer_raw for d in devices if d.device_raw == "КАСКАД-1-МТ"] == ["АО «Каскад»"]


def test_prosoft_reads_sections_si_codes_and_generic_rows():
    """У Энергосферы производителя нет: есть устройства через запятую, номера ГРСИ (иногда
    объединённые на несколько строк) и флаги функций. Строка «СПОДЭС-A…» — поддержка по
    протоколу. Секция «Расходомеры…» — не электросчётчики."""

    devices = adapter.parse_prosoft(_fixture("prosoft.html"))
    by_raw = {d.device_raw: d for d in devices}

    mirtek = next(d for d in devices if d.device_raw.startswith("МИРТЕК-1 "))
    assert mirtek.section == "ПК «Энергосфера»: Счётчики электроэнергии"
    assert "61891-15" in mirtek.si_codes
    assert "МИРТЕК-12-РУ" in mirtek.device_names
    assert "управление нагрузкой" in mirtek.details["functions"]

    generic = next(d for d in devices if d.is_generic)
    assert generic.device_raw.startswith("СПОДЭС-A")

    milur = [d for d in devices if d.device_raw.startswith("МИЛУР")]
    assert len(milur) == 3
    assert milur[1].si_codes == milur[0].si_codes  # rowspan раскрыт на все строки группы
    assert "МИЛУР-307S" in milur[1].device_names

    assert by_raw["МИРТ-881"].section == "ПК «Энергосфера»: УСПД"
    pulsar = next(d for d in devices if d.device_raw == "Пульсар-2М")
    assert pulsar.section.endswith("Расходомеры, тепло- и газосчетчики")


def test_yaenergetik_reads_manufacturer_and_features():
    devices = adapter.parse_yaenergetik(_fixture("yaenergetik.html"))

    mirtek = [d for d in devices if d.manufacturer_raw == "ООО «МИРТЕК»"]
    assert [d.device_raw for d in mirtek] == ["Миртек-3-РУ", "Миртек-12-ру"]
    assert "профиль мощности" in mirtek[0].details["functions"]
    assert "RS485" in mirtek[0].details["channels"]
    uspd = [d for d in devices if d.device_type == "УСПД"]
    assert uspd and uspd[0].device_raw == "CE805M (B,E)"


def test_alphacenter_separates_generic_protocol_block():
    """Пункты после «Все счетчики, поддерживающие протокол СПОДЭС … В том числе:» — это
    поддержка по протоколу; сам заголовочный пункт — generic-запись."""

    devices = adapter.parse_alphacenter(_fixture("alphacnt.html"))

    assert all(d.section == "ПО «АльфаЦЕНТР»: Счетчики электрической энергии" for d in devices)
    first = devices[0]
    assert first.device_raw == "A1 (Альфа)"
    assert first.manufacturer_raw.startswith('"Эльстер Метроника"')
    assert first.details == {}
    generic = [d for d in devices if d.is_generic]
    assert len(generic) == 1 and generic[0].details["protocol"] == "СПОДЭС"
    mirtek = next(d for d in devices if "МирТек" in d.device_raw)
    assert mirtek.manufacturer_raw == "ООО МирТек"
    assert mirtek.details["via_protocol"] == "СПОДЭС"
    assert mirtek.device_names == ["МирТек-32-РУ", "МирТек-12-РУ"]


def test_nekta_reads_ajax_rows():
    devices = adapter.parse_nekta(_fixture("nekta.html"))

    assert [d.device_raw for d in devices] == ["ЦЭ2726А", "ЦЭ2727А", "МИРТЕК-12-РУ-D1"]
    assert devices[2].manufacturer_raw == "МИРТЕК"
    assert "СПОДЭС" in devices[2].details["channels"]


def test_lers_reads_only_electricity_section():
    devices = adapter.parse_lers(_fixture("lers.html"))

    assert len(devices) == 3
    assert all(d.section == "ЛЭРС УЧЁТ: Электросчётчики" for d in devices)
    assert "ЕК-270" not in [d.device_raw for d in devices]
    assert next(d for d in devices if "МИРТЕК" in d.device_raw).device_names == [
        "МИРТЕК-32-РУ",
        "МИРТЕК-232-РУ",
    ]


def test_every_platform_has_alias_and_source_seed():
    from app.seed.sources_data import UPPER_SOFTWARE_SOURCES

    seeded = {row[4] for row in UPPER_SOFTWARE_SOURCES}
    assert seeded == set(adapter.PLATFORMS)
    assert all(profile.aliases for profile in adapter.PLATFORMS.values())


# ------------------------------------------------------------------------ обнаружение


def test_detects_named_platforms_and_generic_integration():
    assert service.detect_platforms("Поддержка ИВК верхнего уровня «Пирамида 2.0»") == ["piramida"]
    assert service.detect_platforms("совместимость с ПК Энергосфера и ПО Альфа центр") == [
        "energosphere",
        "alphacenter",
    ]
    assert service.detect_platforms("Класс точности 1,0") == []

    assert service.mentions_integration("счётчики должны быть интегрированы в систему АСКУЭ")
    assert service.mentions_integration("оборудование должно быть совместимо с существующими ИВК ИСУЭ")
    assert not service.mentions_integration("Шкаф УСПД АИИС КУЭ")
    assert not service.mentions_integration("Заменить блоки питания УСПД Арис МТ210")


# ------------------------------------------------------------------------ связь и факты


def _make_manufacturer(db_session, *, brand: str, is_mirtek: bool = False) -> Manufacturer:
    """Бренд уникален на каждый вызов и не содержит реальных брендов: в тестовой базе
    живут сиды и остатки соседних тестов, и второй «МИРТЕК» (или «МИРТЕК…» с хвостом —
    шаблон сида ловит его как подстроку) сделал бы разрешение производителя неоднозначным.
    Узнавание проверяется общим правилом «бренд в названии / в начале обозначения»;
    шаблоны реальных брендов — живым прогоном по семи площадкам (см. ARCHITECTURE.md)."""

    brand = f"ЗАВОД{uuid.uuid4().hex[:6].upper()}"
    manufacturer = Manufacturer(
        legal_name=f"ООО «{brand}»", brand_name=brand, is_mirtek=is_mirtek
    )
    db_session.add(manufacturer)
    db_session.flush()
    return manufacturer


def _model(manufacturer: Manufacturer, tail: str) -> str:
    return f"{manufacturer.brand_name}-{tail}"


def _make_product(db_session, manufacturer, *, model_name, model_code=None, si_type=None) -> Product:
    product = Product(
        manufacturer_id=manufacturer.id,
        model_name=model_name,
        model_code=model_code,
        si_type_id=si_type.id if si_type else None,
    )
    db_session.add(product)
    db_session.flush()
    return product


def _sync(db_session, key: str, devices: list[SupportedDevice]):
    return service.sync_platform(db_session, key, devices=devices)


def test_link_by_si_code_manufacturer_name_and_designation(db_session):
    """Три признака связи: номер ГРСИ, название производителя, бренд в обозначении.
    Граница префикса: «МИРТЕК-1» не покрывает «МИРТЕК-12-РУ-D1»."""

    mirtek = _make_manufacturer(db_session, brand="МИРТЕК", is_mirtek=True)
    # Код уникальный: в тестовой базе живут остатки соседних тестов с реальными кодами
    # (61891-15), и совпадение по ним увело бы запись к чужому производителю.
    si_type = SiType(manufacturer_id=mirtek.id, si_code="99992-26", notation="МИРТЕК-12-РУ")
    db_session.add(si_type)
    db_session.flush()
    d1 = _make_product(db_session, mirtek, model_name=_model(mirtek, "12-РУ-D1"), model_code=_model(mirtek, "12-РУ-D1"), si_type=si_type)
    w32 = _make_product(db_session, mirtek, model_name=_model(mirtek, "32-РУ-W32"), model_code=_model(mirtek, "32-РУ-W32"))
    db_session.commit()
    m1, m12, m32 = _model(mirtek, "1"), _model(mirtek, "12-РУ"), _model(mirtek, "32-РУ")

    outcome = _sync(
        db_session,
        "energosphere",
        [
            SupportedDevice(
                section="ПК «Энергосфера»: Счётчики электроэнергии",
                device_raw=f"{m1}, {m12}",
                device_names=[m1, m12],
                si_codes=["99993-26", "99992-26"],
            ),
            SupportedDevice(
                section="ПК «Энергосфера»: Счётчики электроэнергии",
                device_raw=m32,
                device_names=[m32],
            ),
            SupportedDevice(
                section="ПК «Энергосфера»: Счётчики электроэнергии",
                device_raw=m1,
                device_names=[m1],
            ),
        ],
    )
    assert outcome.devices_read == 3 and outcome.created == 3

    source = service.resolve_source(db_session, "energosphere")
    rows = {
        row.device_raw: row
        for row in db_session.scalars(
            select(UpperSoftwareDevice).where(UpperSoftwareDevice.source_id == source.id)
        )
    }
    assert rows[f"{m1}, {m12}"].manufacturer_matched_by == "si_code"
    assert rows[m32].manufacturer_matched_by == "device_brand"
    assert rows[m1].manufacturer_id == mirtek.id

    def linked(row):
        return {
            (link.product_id, link.matched_by)
            for link in db_session.scalars(
                select(UpperSoftwareProductLink).where(UpperSoftwareProductLink.device_id == row.id)
            )
        }

    assert linked(rows[f"{m1}, {m12}"]) == {(d1.id, "si_code")}
    assert linked(rows[m32]) == {(w32.id, "designation")}
    assert linked(rows[m1]) == set()  # граница префикса

    # Повторное чтение идемпотентно: записей не прибавилось, пропавшая — не удалена.
    again = _sync(db_session, "energosphere", [SupportedDevice(
        section="ПК «Энергосфера»: Счётчики электроэнергии",
        device_raw=m32, device_names=[m32],
    )])
    assert again.created == 0 and again.updated == 1 and again.disappeared == 2
    assert db_session.scalar(
        select(UpperSoftwareDevice).where(UpperSoftwareDevice.source_id == source.id, UpperSoftwareDevice.device_raw == m1)
    ) is not None


def test_manufacturer_support_statuses_and_facts(db_session):
    """Три статуса: есть в списке, в списке нет, только по протоколу. Факты для
    сопоставления — только когда требование об интеграции есть, и прямо говорят
    «в списке НЕТ», а не молчат."""

    mirtek = _make_manufacturer(db_session, brand="МИРТЕК", is_mirtek=True)
    d1 = _model(mirtek, "12-РУ-D1")
    _make_product(db_session, mirtek, model_name=d1, model_code=d1)
    rival = _make_manufacturer(db_session, brand="Нартис")
    rival_product = _make_product(db_session, rival, model_name=_model(rival, "И100"), model_code=_model(rival, "И100"))
    db_session.add(
        ProductCharacteristic(
            product_id=rival_product.id, group_name="Протоколы обмена", field_name="СПОДЭС",
            value="да", source="manufacturer_site",
        )
    )
    db_session.commit()

    m12 = _model(mirtek, "12-РУ")
    _sync(db_session, "piramida", [SupportedDevice(
        section="ПО «Пирамида 2.0» / «Пирамида-Сети»", device_raw=m12,
        device_names=[m12], manufacturer_raw=f'ООО "{mirtek.brand_name}"', si_codes=["99991-26"],
        details={"support": {"Пирамида 2.0": True, "Пирамида-Сети": True}},
    )])
    _sync(db_session, "alphacenter", [
        SupportedDevice(section="ПО «АльфаЦЕНТР»", device_raw="Все счетчики, поддерживающие протокол СПОДЭС",
                        device_names=[], is_generic=True, details={"protocol": "СПОДЭС"}),
        SupportedDevice(section="ПО «АльфаЦЕНТР»", device_raw=f"{_model(mirtek, '32-РУ')}, {m12}",
                        device_names=[_model(mirtek, "32-РУ"), m12], manufacturer_raw=f"ООО {mirtek.brand_name.title()}",
                        details={"via_protocol": "СПОДЭС"}),
    ])

    by_key = {item.key: item for item in service.manufacturer_support(db_session, mirtek)}
    assert by_key["piramida"].status == "supported"
    assert by_key["piramida"].devices[0]["products"] == [d1]
    assert by_key["alphacenter"].status == "supported"
    assert by_key["lers"].status in {"not_synced", "not_listed"}

    rival_keys = {item.key: item for item in service.manufacturer_support(db_session, rival)}
    assert rival_keys["piramida"].status == "not_listed"
    assert rival_keys["alphacenter"].status == "protocol_only"

    tender_id = uuid.uuid4()
    plain = Requirement(tender_id=tender_id, text="Класс точности 1,0", criticality="critical")
    named = Requirement(
        tender_id=tender_id, text="Поддержка ИВК верхнего уровня «Пирамида 2.0»", criticality="critical"
    )
    assert service.software_facts(db_session, mirtek, [plain]) == []

    facts = service.software_facts(db_session, mirtek, [named])
    assert len(facts) == 1 and facts[0].startswith("[software] ПО «Пирамида»")
    assert f"{m12} (ГРСИ 99991-26; Пирамида 2.0; Пирамида-Сети)" in facts[0]

    rival_facts = service.software_facts(db_session, rival, [named])
    assert len(rival_facts) == 1 and "в списке поддерживаемого оборудования НЕТ" in rival_facts[0]

    # Общее требование об интеграции — факты по всем прочитанным площадкам, включая
    # поддержку только по протоколу.
    generic = Requirement(tender_id=tender_id, text="Счётчики должны быть интегрированы в АСКУЭ", criticality="important")
    generic_facts = service.software_facts(db_session, rival, [generic])
    assert any("только по протоколу" in fact for fact in generic_facts)
    assert not any("ЛЭРС" in fact for fact in generic_facts) or by_key["lers"].status == "not_listed"


def test_compliance_context_includes_software_facts_and_tender_overview(db_session):
    mirtek = _make_manufacturer(db_session, brand="МИРТЕК", is_mirtek=True)
    d1 = _model(mirtek, "12-РУ-D1")
    _make_product(db_session, mirtek, model_name=d1, model_code=d1)
    db_session.commit()
    _sync(db_session, "nekta", [SupportedDevice(
        section="ПК «Некта»", device_raw=d1, device_names=[d1],
        manufacturer_raw=mirtek.brand_name, details={"channels": ["СПОДЭС", "RS-485"]},
    )])

    suffix = uuid.uuid4().hex[:8]
    source = Source(key=f"sw_t_{suffix}", name="Источник", url=f"https://example.test/{suffix}", type="eis")
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"SW-{suffix}", title="Поставка счётчиков")
    db_session.add(tender)
    db_session.flush()
    requirement = Requirement(tender_id=tender.id, text="Интеграция в ПК «Некта»", criticality="critical")
    db_session.add(requirement)
    db_session.commit()

    context, _ = build_manufacturer_context(
        db_session, mirtek, include_manual=False, requirements=[requirement]
    )
    assert "[software] ПК «Некта»" in context
    assert d1 in context

    overview = service.tender_overview(db_session, tender)
    assert overview["named_platforms"] == ["nekta"]
    assert overview["requirements"][0]["platforms"] == ["nekta"]
    row = next(m for m in overview["manufacturers"] if m["manufacturer_id"] == mirtek.id)
    assert row["support"]["nekta"]["status"] == "supported"
    assert next(p for p in overview["platforms"] if p["adapter_key"] == "nekta")["mentioned"] is True
