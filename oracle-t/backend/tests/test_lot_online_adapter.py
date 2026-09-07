"""Юнит-тесты разбора JSON-ответа Lot-online (`/etp_back/procedure/list`) — без сети. Фикстуры —
реальные записи API, снятые при разработке: регулируемая закупка по 44-ФЗ (пустой `typeName`,
`externalUrl` — заглушка `#`) и коммерческая закупка (запрос цен) из другого раздела площадки,
у которой, в отличие от первой, статус — не короткий код, а произвольный текст, и `typeName`
заполнен (см. докстринг адаптера: разные разделы площадки заполняют поля по-разному)."""

from app.adapters.lot_online import LotOnlineAdapter

ITEM_REGULATED_44FZ = {
    "id": 1000000209865,
    "number": 209865,
    "purchaseNumber": "0187300006526001310",
    "purchaseNumberCustom": "0187300006526001310",
    "purchaseObjectInfo": (
        "Оказание услуг, направленных на энергосбережение и повышение энергетической "
        "эффективности использования электрической энергии на нужды внутреннего и наружного "
        "освещения"
    ),
    "status": "accept",
    "placerFullName": "АДМИНИСТРАЦИЯ ГОРОДА СУРГУТА",
    "customerFullName": 'МУНИЦИПАЛЬНОЕ БЮДЖЕТНОЕ ОБЩЕОБРАЗОВАТЕЛЬНОЕ УЧРЕЖДЕНИЕ СРЕДНЯЯ ОБЩЕОБРАЗОВАТЕЛЬНАЯ ШКОЛА № 20',
    "maxSum": "3 089 270.27",
    "type": "EOK20",
    "typeName": "",
    "direction": "44fz",
    "currencyCode": "",
    "publicationDateTime": "19.08.2026 13:41",
    "requestEndGiveDateTime": "04.09.2026 06:00",
    "externalUrl": "#",
}

ITEM_COMMERCIAL_RFI = {
    "id": 4000963259537,
    "number": 963259537,
    "purchaseNumber": "RAD260025137",
    "purchaseNumberCustom": "",
    "purchaseObjectInfo": "Счетчик активной/реактивной энергии ТЕ2000.01.12.00",
    "status": "Отменен прием коммерческих предложений",
    "placerFullName": 'Филиал Акционерного общества "Дальневосточная распределительная сетевая компания" Хабаровские электрические сети"',
    "customerFullName": '"Филиал Акционерного общества "Дальневосточная распределительная сетевая компания" Хабаровские электрические сети""',
    "maxSum": "0.00",
    "type": "Запрос цен (коммерческих предложений)",
    "typeName": "223-ФЗ / Запрос цен (коммерческих предложений)",
    "direction": "tender223_market",
    "currencyCode": "RUB",
    "publicationDateTime": "27.05.2026 02:49",
    "requestEndGiveDateTime": "02.06.2026 03:00",
    "externalUrl": (
        "https://tender.lot-online.ru/app/section/pochta/SmallPurchaseCard/page?"
        "SmallPurchaseCard.smallPurchaseEntity=MT%3Aprcreq19718u80000psplgs2589b0or8"
    ),
}


def _parse(item: dict):
    adapter = LotOnlineAdapter()
    errors: list = []
    summary = adapter._parse_item(item, errors)
    assert errors == []
    return summary


def test_parse_regulated_44fz_item():
    summary = _parse(ITEM_REGULATED_44FZ)
    assert summary.external_id == "0187300006526001310"
    assert summary.status == "collecting_bids"
    assert summary.organizer_name == "АДМИНИСТРАЦИЯ ГОРОДА СУРГУТА"
    assert summary.customer_name.startswith("МУНИЦИПАЛЬНОЕ БЮДЖЕТНОЕ")
    assert summary.procurement_method == "44fz"  # typeName пуст — используется код direction
    assert str(summary.price) == "3089270.27"
    assert summary.publish_date.isoformat() == "2026-08-19"
    assert summary.application_end.isoformat() == "2026-09-04T06:00:00"
    assert summary.source_url == (
        "https://gz.lot-online.ru/etp_front/procedure/view/procedure/common/0187300006526001310"
    )


def test_parse_commercial_rfi_item_with_free_text_status():
    """Статус этой записи — не короткий код, а произвольный русский текст площадки (другой
    раздел площадки, см. докстринг адаптера) — ищется в словаре без учёта регистра, а не по
    короткому коду."""

    summary = _parse(ITEM_COMMERCIAL_RFI)
    assert summary.external_id == "RAD260025137"
    assert summary.status == "cancelled"
    assert summary.procurement_method == "223-ФЗ / Запрос цен (коммерческих предложений)"
    assert str(summary.price) == "0.00"
    assert summary.currency == "RUB"


def test_parse_item_with_unrecognized_status_leaves_status_empty():
    """Нераспознанная формулировка статуса не угадывается, а оставляет поле пустым — тот же
    принцип, что и в остальных адаптерах."""

    item = dict(ITEM_COMMERCIAL_RFI, status="Какой-то новый статус площадки")
    summary = _parse(item)
    assert summary.status is None


def test_parse_item_falls_back_to_organizer_when_customer_is_quote_placeholder():
    """У части записей `customerFullName` приходит не пустой строкой, а строкой из одних кавычек
    (`""`) — площадочный артефакт отсутствующего значения, не настоящее имя заказчика (см.
    докстринг `_clean_name` в адаптере). Такое значение должно откатываться на организатора так
    же, как при действительно пустом поле."""

    item = dict(
        ITEM_REGULATED_44FZ,
        placerFullName="ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО \"РОССЕТИ СЕВЕРО-ЗАПАД\"",
        customerFullName='""',
    )
    summary = _parse(item)
    assert summary.customer_name == 'ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "РОССЕТИ СЕВЕРО-ЗАПАД"'
    assert summary.organizer_name == summary.customer_name


def test_parse_item_without_purchase_number_reports_error():
    adapter = LotOnlineAdapter()
    errors: list = []
    summary = adapter._parse_item({"purchaseObjectInfo": "Без номера"}, errors)
    assert summary is None
    assert len(errors) == 1
