"""Юнит-тесты разбора выдачи ТЭК-Торг — без сети. Фикстура повторяет форму состояния Next.js
со страницы поиска (`/procedures?name=...`), снятую 28.09.2026: записи лежат в
`listingProcedures.data`, ссылки на карточки — только в `<a href>` той же страницы. Закупки и
продажа имущества приходят в одной выдаче и различаются разделом (`sectionAlias`)."""

import json

import pytest

from app.adapters.tektorg import TektorgAdapter, UnrecognizedPageError, parse_listing

PROCUREMENT = {
    "id": 19815106,
    "registryNumber": "ЗП609927",
    "title": "Техническое обслуживание АИИСКУЭ ЦОТУиЭ ВЭС",
    "statusName": "Приём заявок",
    "typeName": "Запрос предоставления ценовой информации",
    "organizerName": 'Публичное акционерное общество "Форвард Энерго"',
    "dates": {
        "datePublished": "2026-09-10T11:38:55+03:00",
        "dateEndRegistration": "2026-09-21T15:35:00+03:00",
    },
    "sectionAlias": "zakupki",
    "sumPrice": "1271275.88 ₽",
}

WITHOUT_LINK = {
    **PROCUREMENT,
    "id": 19545375,
    "registryNumber": "ИР607359",
    "sectionAlias": "interrao",
    "sumPrice": "НМЦ не установлена",
}

PROPERTY_SALE = {
    **PROCUREMENT,
    "id": 13908574,
    "registryNumber": "ПИ607124",
    "title": "Продажа бывш. АЗС (сарай-склад, счетчик электрической энергии)",
    "sectionAlias": "sale",
}


def _page(items: list[dict], total_pages: int = 1, links: str = "") -> str:
    state = {
        "props": {
            "pageProps": {
                "initialReduxState": {
                    "listingProcedures": {
                        "data": items,
                        "total": len(items),
                        "totalPages": total_pages,
                        "currentPage": 1,
                    }
                }
            }
        }
    }
    return (
        f"<html><body>{links}"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script>'
        "</body></html>"
    )


def _parse(item: dict, links: str = ""):
    _items, _pages, hrefs = parse_listing(_page([item], links=links))
    errors: list = []
    summary = TektorgAdapter()._parse_item(item, hrefs, errors)
    assert errors == []
    return summary


def test_parse_procurement_record():
    summary = _parse(
        PROCUREMENT,
        links='<a href="/223-fz/procedures/19815106">x</a>'
        '<a href="/223-fz/procedures/19815106#lots">x</a>',
    )
    assert summary.external_id == "ЗП609927"
    assert summary.status == "collecting_bids"
    assert summary.organizer_name == 'Публичное акционерное общество "Форвард Энерго"'
    assert summary.procurement_method == "Запрос предоставления ценовой информации"
    assert str(summary.price) == "1271275.88"
    assert summary.publish_date.isoformat() == "2026-09-10"
    assert summary.application_end.isoformat() == "2026-09-21T15:35:00+03:00"
    assert summary.source_url == "https://www.tektorg.ru/223-fz/procedures/19815106"


def test_link_falls_back_to_section_path():
    """Раздел `interrao` живёт по пути `/inter_rao/` — если ссылки на странице нет,
    путь собирается по таблице разделов."""

    summary = _parse(WITHOUT_LINK)
    assert summary.source_url == "https://www.tektorg.ru/inter_rao/procedures/19545375"
    assert summary.price is None


def test_property_sale_is_skipped():
    """Торги по продаже имущества стабильно попадают в выдачу по ключевым словам: «Продажа
    бывш. АЗС (сарай-склад, счетчик электрической энергии…)». Для тендерного отдела это шум."""

    assert _parse(PROPERTY_SALE) is None


def test_empty_listing_is_not_an_error(monkeypatch):
    adapter = TektorgAdapter(search_keywords=["нет такого"])
    monkeypatch.setattr(adapter, "_search_html", lambda keyword, page_number=1: _page([], 0))
    outcome = adapter.list_new_tenders(since=None)
    assert outcome.tenders == []
    assert outcome.errors == []


def test_unrecognized_page_is_reported(monkeypatch):
    """Страница без JSON выдачи — это смена разметки или заглушка, а не «ничего не найдено»:
    28.09.2026 смена классов styled-components полторы недели маскировалась под пустую выдачу."""

    adapter = TektorgAdapter(search_keywords=["АИИС КУЭ"])
    monkeypatch.setattr(
        adapter, "_search_html", lambda keyword, page_number=1: "<html>captcha</html>"
    )
    outcome = adapter.list_new_tenders(since=None)
    assert len(outcome.errors) == 1
    assert "не распознана" in outcome.errors[0].message


def test_pages_stop_at_total_pages(monkeypatch):
    adapter = TektorgAdapter(search_keywords=["АИИС КУЭ"])
    requested: list[int] = []

    def fake_html(keyword, page_number=1):
        requested.append(page_number)
        return _page([{**PROCUREMENT, "id": page_number, "registryNumber": f"N{page_number}"}], 2)

    monkeypatch.setattr(adapter, "_search_html", fake_html)
    outcome = adapter.list_new_tenders(since=None)
    assert requested == [1, 2]
    assert {t.external_id for t in outcome.tenders} == {"N1", "N2"}


def test_parse_listing_rejects_page_without_state():
    with pytest.raises(UnrecognizedPageError):
        parse_listing("<html></html>")
