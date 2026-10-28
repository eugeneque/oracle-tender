"""Доступ к реестрам допуска (замечание 28.10.2026): ГИСП — запросом грида с фильтром по
нашим ОКПД2, выгрузка ГИСП XLSX в нынешней шапке, PDF перечня ПАО «Россети» — по колонкам
таблицы. Сайты не опрашиваются: HTTP подменён, страницы PDF собраны из строк редакции
22.10.2026 (страница 28: записи 47–49), как их отдаёт разбор текста с координатами."""

from __future__ import annotations

import io
import json
import uuid
from datetime import date

import httpx
import pytest

from app.adapters import admission_registries
from app.adapters.admission_registries import (
    RegistryFile,
    RegistryRow,
    RegistryUnavailable,
    _PdfPage,
    _pdf_records,
    _rows_from_records,
)
from app.db.session import SessionLocal
from app.models.manufacturer import Manufacturer, Product, ProductDataSource
from app.models.registry_record import ProductRegistryRecord
from app.services import registry_polling

GISP = "industrial_products"
ZAK = "rosseti_attestation"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- ГИСП: запрос грида


def test_okpd2_prefixes_drop_nested_codes():
    assert admission_registries.okpd2_prefixes(["26.51.63.130", "26.51.63", " ", "27.12.31.000"]) == [
        "26.51.63",
        "27.12.31.000",
    ]


def test_gisp_item_takes_number_dates_link_and_score():
    row = admission_registries.gisp_item_row(
        {
            "_product_name": 'Счетчики электрической энергии однофазные "МИРТЕК-12-РУ". Корпус D17',
            "_org_name": 'ООО "МИРТЕК"',
            "_product_okpd2": "26.51.63.130",
            "_product_reg_number_2022": None,
            "_product_reg_number_2023": "10509251",
            "_res_date": "2025-01-10",
            "_res_valid_till": "2026-12-10",
            # Действие прекращено досрочно — запись действовала до этой даты.
            "_res_end_date": "2025-08-13",
            "_product_score_value": 76,
            "_product_percentage": None,
            "_product_spec": "РИТМ.411152.010ТУ",
            "_product_gisp_url": "https://gisp.gov.ru/goods/#/product/4106965",
        }
    )
    assert row.record_number == "10509251"
    assert row.issued_at == date(2025, 1, 10)
    assert row.valid_to == date(2025, 8, 13)
    assert row.url == "https://gisp.gov.ru/goods/#/product/4106965"
    assert "баллов по ПП 719: 76" in row.details and "РИТМ.411152.010ТУ" in row.details


def _gisp_items(count: int) -> list[dict]:
    return [
        {
            "_product_name": f"Счётчик электрической энергии ТЕСТ-{index}",
            "_org_name": "ООО «Тест»",
            "_product_okpd2": "26.51.63.130",
            "_product_reg_number_2023": str(10000 + index),
            "_res_valid_till": "2030-01-01",
        }
        for index in range(count)
    ]


def _mock_client(handler):
    def factory(url, timeout=admission_registries.REQUEST_TIMEOUT_SECONDS):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    return factory


def test_fetch_gisp_filters_by_okpd2_and_pages(monkeypatch):
    items = _gisp_items(7)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        opt = body["opt"]
        page = items[opt["skip"] : opt["skip"] + opt["take"]]
        return httpx.Response(200, json={"ok": True, "items": page, "total_count": len(items)})

    monkeypatch.setattr(admission_registries, "_client", _mock_client(handler))
    monkeypatch.setattr(admission_registries, "GISP_PAGE_SIZE", 3)

    result = admission_registries.fetch_gisp(["26.51.63.130", "26.51.63.131", "26.51.63.130.000"])
    assert result.rows is not None and len(result.rows) == 7 and result.total == 7
    assert [body["opt"]["skip"] for body in bodies] == [0, 3, 6]
    assert bodies[0]["opt"]["filter"] == [
        ["_product_okpd2", "startswith", "26.51.63.130"],
        "or",
        ["_product_okpd2", "startswith", "26.51.63.131"],
    ]
    assert "26.51.63.130" in result.file_name


def test_fetch_gisp_explains_page_instead_of_data(monkeypatch):
    monkeypatch.setattr(
        admission_registries,
        "_client",
        _mock_client(lambda request: httpx.Response(200, text="<html>Проверка</html>", headers={"content-type": "text/html"})),
    )
    with pytest.raises(RegistryUnavailable, match="XLSX"):
        admission_registries.fetch_gisp(["26.51.63"])


def test_fetch_gisp_retries_slow_daytime_answers(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("медленно", request=request)
        return httpx.Response(200, json={"ok": True, "items": _gisp_items(2), "total_count": 2})

    monkeypatch.setattr(admission_registries, "_client", _mock_client(handler))
    monkeypatch.setattr(admission_registries, "GISP_RETRY_PAUSE_SECONDS", 0)
    assert len(admission_registries.fetch_gisp(["26.51.63"]).rows) == 2
    assert calls["n"] == 2


# --------------------------------------------------------------------------- ГИСП: выгрузка XLSX


GISP_HEADER = [
    "Предприятие", "ИНН", "ОГРН", "Фактический адрес производителя", "Адрес производственных помещений",
    "Первичный регистрационный номер реестровой записи", "Реестровый номер", "Номер цифрового паспорта",
    "Дата внесения в реестр", "Срок действия", "Фактическая дата прекращения действия реестровой записи",
    "Наименование продукции", "ОКПД2", "ТН ВЭД", "Изготовлена по", "Баллы", "Процентный показатель",
]


def _gisp_export(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Продукция"
    sheet.append(["Время выгрузки: 26.10.2026, 01:00:09"])
    sheet.append([])
    sheet.append(GISP_HEADER)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _export_row(name, okpd2, number, *, valid="15.10.2031", ended=None, score=None, org='ООО "МИРТЕК"'):
    return [org, "6154000000", "1000000000000", None, None, None, number, None, "15.10.2026", valid, ended,
            name, okpd2, "9028 30 110 0", "РИТМ.411152.010ТУ", score, None]


def test_gisp_export_uses_registry_number_and_our_okpd2():
    content = _gisp_export(
        [
            _export_row("Счетчики электрической энергии трехфазные МИРТЕК-32-РУ-D37-2", "26.51.63.130", "10935914", score=113),
            _export_row("Высоковольтные приборы учета МИРТЕК-135-РУ", "26.51.63.139", "10634656", ended="01.03.2026"),
            # Код вне наших и без «счётчика» в наименовании — не наша продукция.
            _export_row("Полотно трикотажное", "13.91.19.110", "10873072"),
            # Шкаф учёта под кодом, которого нет в 26.51.63, но он есть в профилях.
            _export_row("Шкаф учёта ШУ-МИРТЕК", "27.12.31.000", "10000001"),
        ]
    )
    rows, total = admission_registries.parse_xlsx(
        content, relevant=registry_polling._RELEVANT_RE, okpd2_codes=["26.51.63.130", "26.51.63.139", "27.12.31"]
    )
    assert total == 4
    numbers = {row.record_number: row for row in rows}
    assert set(numbers) == {"10935914", "10634656", "10000001"}
    assert numbers["10935914"].valid_to == date(2031, 10, 15)
    assert "баллов по ПП 719: 113" in numbers["10935914"].details
    # Прекращённая запись действует до даты прекращения.
    assert numbers["10634656"].valid_to == date(2026, 3, 1)


# --------------------------------------------------------------------------- Россети: PDF


# Страница 28 редакции 22.10.2026: (x, текст) по строкам сверху вниз.
PAGE_28 = [
    [(416, "28")],
    [(24, "47"), (42, "ООО «Прософт-Системы»"), (188, "Контроллеры многофункциональные в качестве устройств сбора и передачи данных (УСПД) без"), (607, "22.07.2025"), (663, "26.03.2029"), (739, "IIД-39/25")],
    [(42, "г.Екатеринбург, ул. Волгоградская,"), (188, "функций информационно-вычислительного комплекса (ИВК), с размещением в шкафах со степенью")],
    [(42, "стр. 194а"), (188, "защиты IP54:"), (749, "ЗАК")],
    [(188, "ARIS-28хх ПБКМ.424359.016 ТУ (от 28.10.2022), утверждены как тип СИ, внесены в реестр под № 86480-"), (740, "IIЗ-11/24")],
    [(188, "КОЛИЧЕСТВО БАЛЛОВ ПО СТО 34.01-22-002-2023 – 85 баллов")],
    [(24, "49"), (42, "ООО «МИРТЕК», Ростовская обл.,"), (188, "Счетчик электрической энергии однофазный многофункциональный «МИРТЕК-12-РУ»,"), (607, "31.08.2022"), (663, "23.08.2027"), (736, "IIПД-49/22")],
    [(42, "г. Таганрог, ш. Поляковское, 15-к."), (188, "РИТМ.411152.010ТУ. Версия ПО: 1.0 в модификациях:")],
    [(188, "МИРТЕК-12-РУ-SP1-A1R1-230-хх-ххх-SS(ST)-хххх-хххх-Р1(Р2)-HKMOV3Х-D;")],
    # Объединённые ячейки: у записи 50 нет ни производителя, ни ЗАК.
    [(24, "50"), (188, "Счетчик электрической энергии однофазный «МИРТЕК-1-РУ»")],
    # Запись 51 переходит на следующую страницу; номер ЗАК и даты — там.
    [(24, "51"), (42, "ООО «МИРТЕК»"), (188, "Высоковольтные приборы учёта электрической энергии трехфазные многофункциональные МИРТЕК-")],
]
PAGE_29 = [
    [(416, "29")],
    [(42, "Ростовская область, г. Таганрог"), (188, "135-РУ, регистрационный номер 79527-20."), (607, "22.11.2024"), (663, "22.11.2029"), (740, "IIЗ-46/24")],
]


def _page(lines: list[list[tuple[int, str]]], *, with_rules: bool = True) -> _PdfPage:
    built = []
    separators = [5.0]
    for index, segments in enumerate(lines):
        top = 10.0 + index * 10
        if with_rules and index and segments[0][0] < 40 and segments[0][1].isdigit():
            separators.append(top - 2)
        built.append((top, [(float(x), float(x) + 6 * len(text), text) for x, text in segments]))
    separators.append(10.0 + len(lines) * 10)
    return _PdfPage(width=842.04, lines=built, separators=separators if with_rules else [])


def _rosseti_rows(with_rules: bool = True) -> dict[str, RegistryRow]:
    pages = [_page(PAGE_28, with_rules=with_rules), _page(PAGE_29, with_rules=with_rules)]
    rows = _rows_from_records(_pdf_records(pages), 842.04)
    return {row.details.split(";")[0].removeprefix("№ п/п ").removesuffix(" в перечне"): row for row in rows}


@pytest.mark.parametrize("with_rules", [True, False])
def test_rosseti_pdf_rows_by_table_columns(with_rules):
    rows = _rosseti_rows(with_rules)
    assert set(rows) == {"47", "49", "50", "51"}

    uspd = rows["47"]
    # Основной номер ЗАК — из строки записи, а не протокол продления ниже.
    assert uspd.record_number == "IIД-39/25"
    assert uspd.valid_to == date(2029, 3, 26)
    assert uspd.organization.startswith("ООО «Прософт-Системы»")
    assert "баллов по СТО 34.01-22-002: 85" in uspd.details
    # Номер страницы не попадает в наименование.
    assert not uspd.product_name.rstrip().endswith("28")

    mirtek = rows["49"]
    assert mirtek.record_number == "IIПД-49/22"
    assert mirtek.issued_at == date(2022, 8, 31) and mirtek.valid_to == date(2027, 8, 23)
    assert "МИРТЕК" in mirtek.organization and "МИРТЕК-12-РУ-SP1" in mirtek.product_name

    merged = rows["50"]
    assert merged.organization == mirtek.organization
    assert merged.record_number == "IIПД-49/22"
    assert "объединённой ячейки" in merged.details


def test_rosseti_pdf_record_continues_on_next_page():
    high_voltage = _rosseti_rows()["51"]
    # Перенос «МИРТЕК-» + «135-РУ» склеен: обозначение находится целиком.
    assert "МИРТЕК-135-РУ" in high_voltage.product_name
    assert high_voltage.record_number == "IIЗ-46/24"
    assert high_voltage.valid_to == date(2029, 11, 22)
    assert registry_polling._designation_pattern("МИРТЕК-135-РУ").search(registry_polling._fold(high_voltage.raw))


def test_rosseti_browser_check_is_explained(monkeypatch):
    challenge = (
        '<html><head><title>Проверка безопасности</title></head><body>'
        '<form id="form"><input id="challenge"></form><script>const nonce = "x";</script></body></html>'
    )
    monkeypatch.setattr(
        admission_registries,
        "_client",
        _mock_client(lambda request: httpx.Response(200, text=challenge, headers={"content-type": "text/html"})),
    )
    with pytest.raises(RegistryUnavailable) as error:
        admission_registries.download_rosseti(known_url="https://www.rosseti.ru/upload/razdel-ii.pdf")
    assert "Проверка безопасности" in str(error.value)
    assert "загрузите PDF" in str(error.value)


def test_rosseti_link_is_found_by_caption():
    html = """
      <a href="/upload/a/Раздел I. Первичное оборудование на 22.10.2026.pdf">Раздел I. Первичное оборудование…</a>
      <a href="/upload/b/Раздел II. Вторичное оборудование на 22.10.2026.pdf">Раздел II. Вторичное оборудование…</a>
      <a href="/upload/c/Методика.pdf">Методика проведения аттестации</a>
    """
    link = admission_registries.find_download_link(
        html, admission_registries.ROSSETI_EQUIPMENT_PAGE, extensions=(".pdf",), prefer=("вторичн", "раздел ii")
    )
    assert link == "https://www.rosseti.ru/upload/b/Раздел II. Вторичное оборудование на 22.10.2026.pdf"


# --------------------------------------------------------------------------- опрос целиком


def test_gisp_poll_stores_card_link_and_score(client, admin_token, monkeypatch):
    tag = uuid.uuid4().hex[:4].upper()
    designation = f"ПРВ-{tag}7"
    with SessionLocal() as db:
        manufacturer = Manufacturer(legal_name=f"ООО «Реестр{tag}»", brand_name=f"Реестр{tag}", is_mirtek=False)
        db.add(manufacturer)
        db.flush()
        product = Product(
            manufacturer_id=manufacturer.id, model_name=f"Счётчик {designation}", model_code=designation,
            data_source=ProductDataSource.MANUFACTURER_SITE.value,
        )
        db.add(product)
        db.commit()
        product_id, brand = product.id, manufacturer.brand_name

    seen_codes: list[list[str]] = []

    def fake_download(registry, *, okpd2_codes):
        seen_codes.append(okpd2_codes)
        row = admission_registries.gisp_item_row(
            {
                "_product_name": f"Счётчик электрической энергии {designation}",
                "_org_name": f"ООО «{brand}»",
                "_product_okpd2": "26.51.63.130",
                "_product_reg_number_2023": "10999999",
                "_res_valid_till": "2031-01-01",
                "_product_score_value": 80,
                "_product_gisp_url": "https://gisp.gov.ru/goods/#/product/1",
            }
        )
        return RegistryFile(content=b"", file_name="ГИСП, ОКПД2 26.51.63", url=None, rows=[row], total=1)

    monkeypatch.setattr(registry_polling, "_download", fake_download)
    with SessionLocal() as db:
        poll = registry_polling.run_poll(db, GISP, method=registry_polling.PollMethod.DOWNLOAD)
        assert poll.status == "ok", poll.message
        assert "Записей по ОКПД2" in poll.message
        record = db.query(ProductRegistryRecord).filter_by(product_id=product_id, registry=GISP).one()
        assert record.record_number == "10999999"
        assert record.url == "https://gisp.gov.ru/goods/#/product/1"
        assert "баллов по ПП 719: 80" in record.note
        assert record.score == 80
    assert seen_codes and seen_codes[0]


def test_brand_in_product_name_beats_contract_manufacturer():
    """МИРТЕК-32-РУ в ГИСП записан на ООО «РИМ-РУС» — запись всё равно наша, а чужое
    обозначение без бренда у другого завода по-прежнему не засчитывается."""

    manufacturer = Manufacturer(legal_name="ООО «МИРТЕК»", brand_name="МИРТЕК", is_mirtek=True)
    product = Product(model_code="МИРТЕК-32-РУ", model_name="МИРТЕК-32-РУ")
    keys = registry_polling._ProductKeys(
        product=product,
        patterns=[registry_polling._designation_pattern("МИРТЕК-32-РУ")],
        manufacturer_tokens=registry_polling._manufacturer_tokens(manufacturer),
    )
    contract = RegistryRow(
        record_number="10935914",
        product_name="Счетчики электрической энергии трехфазные многофункциональные МИРТЕК-32-РУ-D37-2",
        organization='ООО "РИМ-РУС"',
        valid_to=date(2031, 10, 15),
    )
    contract.raw = f"{contract.product_name} {contract.organization}"
    prepared = [(contract, registry_polling._fold(contract.raw), registry_polling._fold(contract.organization))]
    assert registry_polling._best_row(keys, prepared) is contract


def test_record_with_113_points_wins_over_longer_80_point_record():
    keys = registry_polling._ProductKeys(
        product=Product(model_code="ТЕСТ-113"),
        patterns=[registry_polling._designation_pattern("ТЕСТ-113")],
        manufacturer_tokens=[],
    )
    weak = RegistryRow(record_number="1", product_name="Счётчик ТЕСТ-113", valid_to=date(2032, 1, 1), score=80)
    strong = RegistryRow(record_number="2", product_name="Счётчик ТЕСТ-113", valid_to=date(2030, 1, 1), score=113)
    prepared = [(row, registry_polling._fold(row.product_name), "") for row in (weak, strong)]
    assert registry_polling._best_row(keys, prepared) is strong


def test_low_score_is_spelled_out_for_matching():
    from app.services.registry_records_service import describe_record

    record = ProductRegistryRecord(
        registry=GISP, presence="present", record_number="10362901", valid_to=date(2031, 1, 1), score=61
    )
    text = describe_record(record, today=date(2026, 10, 28))
    assert "баллов по ПП 719: 61 — меньше 113, для закупок не подходит" in text
    record.score = 114
    assert describe_record(record, today=date(2026, 10, 28)).endswith("баллов по ПП 719: 114")


def test_gisp_card_tells_when_and_how_long_it_is_polled(client, admin_token):
    with SessionLocal() as db:
        manufacturer = Manufacturer(legal_name=f"ООО «Срок{uuid.uuid4().hex[:4]}»", brand_name="Срок", is_mirtek=False)
        db.add(manufacturer)
        db.flush()
        product = Product(
            manufacturer_id=manufacturer.id, model_name="Счётчик СРОК-101", model_code="СРОК-101",
            data_source=ProductDataSource.MANUFACTURER_SITE.value,
        )
        db.add(product)
        db.commit()
        product_id = product.id
    resp = client.get(f"/products/{product_id}/data-sources", headers=_auth(admin_token))
    assert resp.status_code == 200, resp.text
    gisp = {source["key"]: source for source in resp.json()}["gisp"]
    assert "по понедельникам в 02:30" in gisp["detail"]
    assert "до 5 мин, до 3 попыток" in gisp["detail"]
