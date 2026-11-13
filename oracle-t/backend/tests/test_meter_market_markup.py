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


def test_recheck_splits_grouped_modification_by_analyst_codes(db_session, monkeypatch):
    """Замечание 13.11.2026 (Тайпит): «НЕВА СТ221 …BCS-G3» привязан шаблоном к сведённой строке
    «G4/G3» — «GSM и RS», хотя G3 — NB-IoT (по РЭ). Перепроверка с расшифровкой кодов аналитика
    переносит написание в свою модификацию, ровную строку правит, ручную привязку не трогает."""

    meter_type, (group, g3e) = _type(db_session, ["НЕВА СТ221 G4/G3", "НЕВА СТ221 G3E"])
    meter_type.code_notes = "G3 — NB-IoT; G3E — NB-IoT и RS"
    g3 = _variant(db_session, meter_type.id, "НЕВА СТ221 2AR2S 29 BCS-G3")
    g4 = _variant(db_session, meter_type.id, "НЕВА СТ221 2AR2S 29 BCS-G4")
    exact = _variant(db_session, meter_type.id, "НЕВА СТ221 2AR2S 29 BCS-G3E")
    manual = _variant(db_session, meter_type.id, "НЕВА СТ221 G3 вручную")
    for variant, mod, by in ((g3, group, "auto"), (g4, group, "auto"), (exact, g3e, "auto"), (manual, group, "manual")):
        variant.modification_id, variant.mapped_by = mod.id, by
    db_session.flush()
    seen = {}

    def fake(db, *, system_prompt, user_text, response_model, temperature):
        seen["text"] = user_text
        return response_model(
            items=[
                {"id": g3.id, "name": "НЕВА СТ221 G3", "phase": "1ф", "design": "din", "interface": "NB-IoT",
                 "smart_890": "yes", "confidence": "high", "reason": "G3 — NB-IoT по расшифровке аналитика"},
                {"id": g4.id, "modification_id": group.id, "interface": "RS", "confidence": "high"},
                {"id": exact.id, "name": "НЕВА СТ221 G3E", "interface": "NB-IoT и RS", "confidence": "high",
                 "reason": "G3E — NB-IoT и RS"},
            ]
        )

    monkeypatch.setattr(markup, "run_structured", fake)
    stats = markup.markup_type(db_session, meter_type, recheck=True)

    assert "G3 — NB-IoT" in seen["text"] and "сейчас: НЕВА СТ221 G4/G3" in seen["text"]
    assert "вручную" not in seen["text"]
    assert stats["created"] == 1 and stats["mapped"] == 1 and stats["corrected"] == 1
    for variant in (g3, g4, exact, manual):
        db_session.refresh(variant)
    new = db_session.get(MeterMarketModification, g3.modification_id)
    assert (new.name, new.interface, g3.mapped_by) == ("НЕВА СТ221 G3", "NB-IoT", "ai")
    assert g4.modification_id == group.id  # интерфейс противоречит строке — привязку не меняем
    db_session.refresh(g3e)
    assert exact.modification_id == g3e.id and g3e.interface == "NB-IoT и RS" and "RS» → «NB-IoT и RS" in g3e.comment
    assert (manual.modification_id, manual.mapped_by) == (group.id, "manual")
