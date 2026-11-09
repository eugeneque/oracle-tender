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
