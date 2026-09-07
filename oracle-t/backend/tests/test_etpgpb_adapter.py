"""Юнит-тесты разбора ответа JSON API ЭТП ГПБ — без сети."""

from app.adapters.etpgpb import EtpgpbAdapter

ITEM_WITH_REGISTRY_NUMBER = {
    "id": "2033265",
    "type": "procedure",
    "attributes": {
        "registry_number": "1003898",
        "stage": "accepting",
        "title": "Счетчик электрической энергии трехфазный",
        "platform_url": "https://etp.gpb.ru/#nsi/priceorder/directCustomer/orderId/1003898",
        "procedure_type_name": "Исследование рынка",
        "company_name": 'филиал Нижегородский ПАО "Т Плюс"',
        "amount": "586055.5",
        "currency_name": "RUB",
        "date_published": "2026-08-25T11:57:00.000+03:00",
        "end_registration": "2026-09-01T08:00:00.000+03:00",
    },
}

ITEM_WITHOUT_REGISTRY_NUMBER = {
    "id": "2037014",
    "type": "procedure",
    "attributes": {
        "registry_number": None,
        "stage": "accepting",
        "title": "Маркетинговое исследование",
    },
}


def test_parse_item_maps_all_fields():
    adapter = EtpgpbAdapter()
    errors = []
    summary = adapter._parse_item(ITEM_WITH_REGISTRY_NUMBER, errors)
    assert errors == []
    assert summary.external_id == "1003898"
    assert summary.title == "Счетчик электрической энергии трехфазный"
    assert summary.status == "collecting_bids"
    assert summary.customer_name == 'филиал Нижегородский ПАО "Т Плюс"'
    assert str(summary.price) == "586055.5"
    assert summary.publish_date.isoformat() == "2026-08-25"
    assert summary.application_end.date().isoformat() == "2026-09-01"
    assert summary.source_url == "https://etp.gpb.ru/#nsi/priceorder/directCustomer/orderId/1003898"


def test_parse_item_without_registry_number_falls_back_to_api_id():
    """Часть закупок публикуется без реестрового номера (маркетинговые исследования,
    процедуры ГК «Газпром» на собственной площадке) — это не сбой разбора, и терять их
    нельзя. Идентификатором становится `id` записи API: он стабилен между опросами, а
    префикс источника исключает совпадение с реестровыми номерами других площадок."""

    adapter = EtpgpbAdapter()
    errors = []
    summary = adapter._parse_item(ITEM_WITHOUT_REGISTRY_NUMBER, errors)
    assert summary is not None
    assert summary.external_id == "etpgpb-2037014"
    assert errors == []


def test_parse_item_without_any_identifier_reports_error_not_crash():
    adapter = EtpgpbAdapter()
    errors = []
    assert adapter._parse_item({"attributes": {"title": "Без идентификаторов"}}, errors) is None
    assert len(errors) == 1
