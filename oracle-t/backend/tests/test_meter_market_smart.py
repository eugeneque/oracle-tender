"""Фильтр «соответствие ПП 890» в отчёте «Рынок ПУ» (06.11.2026): неразмеченное — «нет данных» (09.11.2026)."""

from app.services.meter_market_report import Filters, _keep, smart_label


def _cell(smart, unmapped=False):
    return {"manufacturer_id": 1, "design": "din", "phase": "1ф", "interface": None, "unmapped": unmapped, "smart": smart}


def test_smart_label():
    assert smart_label(_cell(True)) == "yes"
    assert smart_label(_cell(False)) == "no"
    assert smart_label(_cell(None)) == "nodata"
    assert smart_label(_cell(True, unmapped=True)) == "nodata"


def test_keep_filters_by_smart():
    assert _keep(_cell(True), Filters(smart="yes"), {}, "")
    assert not _keep(_cell(False), Filters(smart="yes"), {}, "")
    assert _keep(_cell(False), Filters(), {}, "")


def test_requirements_text_loaded():
    from app.services.meter_market_smart import REQUIREMENTS

    assert "интеллектуальной системе учета" in REQUIREMENTS and "28." in REQUIREMENTS


def test_multi_period_merges_adjacent_and_builds_sql():
    from datetime import date

    from app.services.meter_market_report import parse_period

    quarters = parse_period("quarters:2026-1,2026-2,2025-4", date(2026, 10, 1))
    assert quarters.ranges == ((date(2025, 10, 1), date(2026, 7, 1)),)
    months = parse_period("months:2026-01,2026-03", date(2026, 10, 1))
    assert len(months.ranges) == 2 and months.label == "01.2026, 03.2026"
    sql, params = months.where()
    assert sql.count("OR") == 1 and params["ps1"] == date(2026, 3, 1)


def test_model_sees_type_description_and_optical_port_rule(db_session, monkeypatch):
    """Замечание 11.11.2026: КПЗ M2M-3 «GSM» ИИ счёл несоответствующим ПП 890 — в справочнике
    оптопорт не указывают, а в описании типа он есть (оптопорт + GSM = два интерфейса)."""

    import uuid

    from sqlalchemy import text

    from app.models.meter_market import MeterMarketModification, MeterMarketType
    from app.services import meter_market_smart as smart

    assert "Оптический порт в нём НЕ указывают" in smart.SYSTEM_PROMPT
    code = f"9{uuid.uuid4().int % 10000:04d}-20"
    from app.models.manufacturer import Manufacturer

    maker = Manufacturer(legal_name=f"ООО «КПЗ {code}»", brand_name="КПЗ", is_mirtek=False)
    db_session.add(maker)
    db_session.flush()
    manufacturer = maker.id
    db_session.execute(
        text("INSERT INTO si_types (manufacturer_id, si_code, description_type_text) VALUES (:m, :c, :t)"),
        {"m": manufacturer, "c": code, "t": "Титульный лист\nОписание средства измерений\nСчетчик имеет оптический порт, "
                                              "основной канал связи: 2G (GPRS)."},
    )
    meter_type = MeterMarketType(mit_number=code, in_report=True)
    db_session.add(meter_type)
    db_session.flush()
    mod = MeterMarketModification(type_id=meter_type.id, name="М2М-3 2G", phase="3ф", interface="GSM")
    db_session.add(mod)
    db_session.flush()

    seen = {}

    def fake_run_structured(db, *, system_prompt, user_text, response_model, temperature):
        seen["text"] = user_text
        return response_model(verdicts=[{"id": mod.id, "verdict": "yes", "confidence": "high", "reason": "оптопорт + GSM"}])

    monkeypatch.setattr(smart, "run_structured", fake_run_structured)
    assert smart.classify_type(db_session, meter_type, [mod]) == 1
    assert seen["text"].index("Описание средства измерений") and "Титульный лист" not in seen["text"]
    assert "оптический порт" in seen["text"]
    assert mod.smart_890 is True and "оптопорт" in mod.smart_890_note
