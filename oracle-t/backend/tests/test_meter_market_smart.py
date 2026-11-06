"""Фильтр «соответствие ПП 890» в отчёте «Рынок ПУ» (06.11.2026): неразмеченное — «не определено»."""

from app.services.meter_market_report import Filters, _keep, smart_label


def _cell(smart, unmapped=False):
    return {"manufacturer_id": 1, "design": "din", "phase": "1ф", "interface": None, "unmapped": unmapped, "smart": smart}


def test_smart_label():
    assert smart_label(_cell(True)) == "yes"
    assert smart_label(_cell(False)) == "no"
    assert smart_label(_cell(None)) == "unknown"
    assert smart_label(_cell(True, unmapped=True)) == "unknown"


def test_keep_filters_by_smart():
    assert _keep(_cell(True), Filters(smart="yes"), {}, "")
    assert not _keep(_cell(False), Filters(smart="yes"), {}, "")
    assert _keep(_cell(False), Filters(), {}, "")


def test_requirements_text_loaded():
    from app.services.meter_market_smart import REQUIREMENTS

    assert "интеллектуальной системе учета" in REQUIREMENTS and "28." in REQUIREMENTS
