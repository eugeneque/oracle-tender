"""ИИ-разметка неразмеченных написаний «Рынка ПУ» по структуре условного обозначения
(замечание аналитика 12.11.2026: «НЕВА МТ 114 AS E4PC» — «нет данных — не размечено», хотя
описание типа даёт и исполнение, и интерфейс, и несоответствие ПП 890)."""

import uuid

from sqlalchemy import select, text

from app.models.meter_market import MeterMarketModification, MeterMarketType, MeterMarketVariant
from app.services import meter_market_markup as markup
from app.services.meter_market_normalize import modification_key

DESCRIPTION = (
    "Титульный лист\nОписание средства измерений\nСчетчики однофазные многотарифные НЕВА МТ 1.\n"
    + "Общий текст описания. " * 200
    + "\nРисунок 1 – Структура условного обозначения\nТип корпуса: 1 – для крепления винтами; "
    "2 – для установки на рейку ТН 35\nВид измеряемой энергии: A – активная; 2AR – активная в прямом, "
    "обратном направлениях и реактивная\nТип интерфейса: O – без интерфейса удалённого доступа; E4 – интерфейс EIA 485"
)


def _type(db, mods):
    from app.models.manufacturer import Manufacturer

    code = f"9{uuid.uuid4().int % 10000:04d}-15"
    maker = Manufacturer(legal_name=f"АО «Тайпит {code}»", brand_name="Тайпит", is_mirtek=False)
    db.add(maker)
    db.flush()
    db.execute(
        text("INSERT INTO si_types (manufacturer_id, si_code, description_type_text) VALUES (:m, :c, :t)"),
        {"m": maker.id, "c": code, "t": DESCRIPTION},
    )
    meter_type = MeterMarketType(mit_number=code, notation="НЕВА МТ 1", in_report=True, mpi_years=16)
    db.add(meter_type)
    db.flush()
    created = []
    for name in mods:
        mod = MeterMarketModification(type_id=meter_type.id, name=name, phase="1ф", design="cabinet", interface="RS")
        db.add(mod)
        created.append(mod)
    db.flush()
    return meter_type, created


def _variant(db, type_id, raw):
    variant = MeterMarketVariant(type_id=type_id, raw=raw, norm_key=modification_key(raw))
    db.add(variant)
    db.flush()
    db.execute(
        text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, '2026-07-01', 10)"),
        {"v": variant.id},
    )
    return variant


def test_excerpt_reaches_designation_structure(db_session):
    meter_type, _ = _type(db_session, [])
    excerpt = markup.designation_excerpt(db_session, meter_type.mit_number)
    assert "Описание средства измерений" in excerpt
    # Расшифровка дальше первых 2,5 тыс. знаков — окно вокруг неё всё равно попадает модели.
    assert "E4 – интерфейс EIA 485" in excerpt and "1 – для крепления винтами" in excerpt


def test_markup_creates_modification_and_maps_spellings(db_session, monkeypatch):
    meter_type, (mt115,) = _type(db_session, ["НЕBA MT 115 2AR E4"])
    a = _variant(db_session, meter_type.id, "НЕВА МТ 114 AS E4PC 5(60)A")
    b = _variant(db_session, meter_type.id, "НЕВА МТ 114 AS Е4 РС")
    bare = _variant(db_session, meter_type.id, "НЕВА МТ 1")
    known = _variant(db_session, meter_type.id, "НЕВА МТ 115 2AR2S E4PC 5(80)A")
    seen = {}

    def fake(db, *, system_prompt, user_text, response_model, temperature):
        seen["text"] = user_text
        return response_model(
            items=[
                {"id": a.id, "name": "НЕВА МТ 114 AS E4PC", "phase": "1ф", "design": "cabinet", "interface": "RS",
                 "smart_890": "no", "confidence": "high", "reason": "114 — винты (шкаф); AS — только активная; E4 — EIA 485"},
                {"id": b.id, "name": "НЕВА МТ 114 AS Е4РС", "phase": "1ф", "design": "cabinet", "interface": "RS",
                 "smart_890": "no", "confidence": "high", "reason": "то же исполнение"},
                {"id": bare.id, "name": "НЕВА МТ 1", "confidence": "low", "reason": "без кодов"},
                {"id": known.id, "modification_id": mt115.id, "confidence": "high"},
                {"id": 10**9, "name": "выдумано", "confidence": "high"},
            ]
        )

    monkeypatch.setattr(markup, "run_structured", fake)
    stats = markup.markup_type(db_session, meter_type)

    assert "E4 – интерфейс EIA 485" in seen["text"] and "НЕBA MT 115 2AR E4" in seen["text"]
    assert stats["created"] == 1 and stats["mapped"] == 3 and stats["skipped"] == 1
    new = db_session.scalars(
        select(MeterMarketModification).where(
            MeterMarketModification.type_id == meter_type.id, MeterMarketModification.name == "НЕВА МТ 114 AS E4PC"
        )
    ).one()
    assert (new.phase, new.design, new.interface, new.smart_890, new.smart_890_source) == ("1ф", "cabinet", "RS", False, "ai")
    assert new.mpi_years == 16 and "шкаф" in new.comment
    for variant in (a, b):
        db_session.refresh(variant)
        # «Е4 РС» кириллицей — тот же ключ, что у заведённой модификации: одно исполнение, не два.
        assert (variant.modification_id, variant.mapped_by) == (new.id, "ai")
    db_session.refresh(known)
    # Шаблоны справочника аналитика — раньше модели: «115 2AR2S E4PC» нашёл шаблон «115 2AR E4».
    assert (known.modification_id, known.mapped_by) == (mt115.id, "auto")
    db_session.refresh(bare)
    assert bare.modification_id is None  # низкая уверенность — остаётся человеку


def test_run_stops_when_model_not_configured(db_session, monkeypatch):
    from app.services.ai_provider_service import AiNotConfiguredError

    meter_type, _ = _type(db_session, [])
    _variant(db_session, meter_type.id, "НЕВА МТ 124 AS O 230 5(60)A")
    calls = []

    def fake(db, **kwargs):
        calls.append(1)
        raise AiNotConfiguredError("ключ не задан")

    monkeypatch.setattr(markup, "run_structured", fake)
    stats = markup.run(db_session, mit_numbers=[meter_type.mit_number])
    assert stats["stopped"] == 1 and len(calls) == 1
