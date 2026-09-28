"""Юнит-тесты разбора HTML Росэлторг — без сети. Фикстура — упрощённая версия реальной
карточки, зафиксированной при разработке адаптера."""

import httpx
import pytest
from bs4 import BeautifulSoup

from app.adapters import roseltorg

from app.adapters.roseltorg import RoseltorgAdapter, parse_card_documents

ITEM_HTML = """
<div class="search-results__item autoload-post js-etp-procedure-grid-item"
     data-feature-favorite-lots-procedure-number="32616330728">
  <div class="search-results__subject">
    <a href="/procedure/32616330728/1" class="search-results__link">Поставка приборов учета электрической энергии</a>
  </div>
  <div class="search-results__customer">
    <a href="/companies/resolve/123">АКЦИОНЕРНОЕ ОБЩЕСТВО "АБАКАНСКИЕ ЭЛЕКТРИЧЕСКИЕ СЕТИ"</a>
  </div>
  <p class="search-results__type">Аукцион МСП</p>
  <div class="search-results__statuses">
    <div class="search-results__status status__icon--acceptance">
      <div class="search-results__status-dot"></div>
      Прием заявок
    </div>
  </div>
  <div class="search-results__sum-wrapper">
    <div class="search-results__sum">
      <p class="desktop">9 612 367<sub>,80</sub> &#8381;</p>
    </div>
  </div>
  <div class="search-results__timing">
    <p class="search-results__inline-title">Окончание приема заявок</p>
    <time class="search-results__time">08.09.2026 в 10:00</time>
  </div>
</div>
"""


def _parse(html: str):
    soup = BeautifulSoup(html, "lxml")
    item = soup.select_one("div.search-results__item")
    adapter = RoseltorgAdapter()
    errors = []
    summary = adapter._parse_item(item, errors)
    return summary, errors


def test_parse_item_extracts_all_fields():
    summary, errors = _parse(ITEM_HTML)
    assert errors == []
    assert summary.external_id == "32616330728"
    assert summary.title == "Поставка приборов учета электрической энергии"
    assert summary.customer_name == 'АКЦИОНЕРНОЕ ОБЩЕСТВО "АБАКАНСКИЕ ЭЛЕКТРИЧЕСКИЕ СЕТИ"'
    assert summary.procurement_method == "Аукцион МСП"
    assert summary.status == "collecting_bids"
    assert str(summary.price) == "9612367.80"
    assert summary.application_end is not None
    assert summary.application_end.date().isoformat() == "2026-09-08"
    assert summary.source_url == "https://www.roseltorg.ru/procedure/32616330728/1"


def test_parse_item_without_external_id_reports_error():
    soup = BeautifulSoup(
        '<div class="search-results__item"><a class="search-results__link">Тест</a></div>', "lxml"
    )
    item = soup.select_one("div.search-results__item")
    adapter = RoseltorgAdapter()
    errors = []
    summary = adapter._parse_item(item, errors)
    assert summary is None
    assert len(errors) == 1


# Блок «Документы» карточки процедуры — сокращённая разметка реальной страницы 32110933020,
# сохранённой пользователем 25.09.2026.
CARD_DOCS_HTML = """
<noindex><div><div class="lot__docs">
  <div class="lot-docs__title-box">
    <p class="lot-headline">Документы</p>
    <a class="lot-docs__title-link js-use-auth-check js-download-all-docs"
       href="/procedure/docs/download-archive?number=32110933020"><span>Скачать все документы</span></a>
  </div>
  <div class="lot-docs__block">
    <h5 class="lot-docs__subheader">Приложения к извещению</h5>
    <ul class="lot-docs__list">
      <li class="lot-docs__li"><a class="lot-docs__file file-doc"
        href="https://msp.roseltorg.ru/api/v1/documents/4785f200-59c6-11ec-bc1b-bb4459fbcaf6"
        title="Документация_ЭА_СМСП-_1.doc"><span>Документация_ЭА_СМСП-_1.doc</span></a></li>
    </ul>
  </div>
  <div class="lot-docs__block"><div class="documents-tabs__block _hide">
    <h5 class="lot-docs__subheader">Протоколы</h5>
    <ul class="lot-docs__list">
      <li class="lot-docs__li"><a class="lot-docs__file file-pdf lot__docs-file"
        href="https://msp.roseltorg.ru/api/v1/documents/2c47aa80-6315-11ec-bb77-b7149fc978e8"
        title="Протокол подведения итогов"><span>Протокол подведения итогов</span></a></li>
    </ul>
  </div></div>
</div></div></noindex>
"""


def test_parse_card_documents_takes_files_and_protocols():
    documents = parse_card_documents(CARD_DOCS_HTML)

    assert [(d.file_name, d.file_type) for d in documents] == [
        ("Документация_ЭА_СМСП-_1.doc", ".doc"),
        # У протокола в title нет расширения — оно берётся из класса ссылки.
        ("Протокол подведения итогов.pdf", ".pdf"),
    ]
    assert documents[0].url == (
        "https://msp.roseltorg.ru/api/v1/documents/4785f200-59c6-11ec-bc1b-bb4459fbcaf6"
    )


def _fake_fetch(html: str | None = None, error: Exception | None = None):
    def fetch(client, method, url, **kwargs):
        if error is not None:
            raise error
        return httpx.Response(200, text=html, request=httpx.Request(method, url))

    return fetch


def test_download_documents_prefers_card(monkeypatch):
    monkeypatch.setattr(roseltorg, "fetch_with_retry", _fake_fetch(CARD_DOCS_HTML))
    monkeypatch.setattr(
        roseltorg, "fetch_eis_documents", lambda number: pytest.fail("ЕИС не нужен")
    )

    documents = RoseltorgAdapter().download_documents("RH22092600083")

    assert len(documents) == 2


def test_download_documents_card_failure_without_eis_number_is_an_error(monkeypatch):
    """Коммерческой закупке идти в ЕИС не с чем — сбой карточки нельзя выдать за «документов нет»."""

    monkeypatch.setattr(
        roseltorg, "fetch_with_retry", _fake_fetch(error=httpx.ConnectError("reset"))
    )

    with pytest.raises(RuntimeError, match="карточка Росэлторга не получена"):
        RoseltorgAdapter().download_documents("RH22092600083")


def test_download_documents_falls_back_to_eis(monkeypatch):
    monkeypatch.setattr(
        roseltorg, "fetch_with_retry", _fake_fetch(error=httpx.ConnectError("reset"))
    )
    monkeypatch.setattr(roseltorg, "fetch_eis_documents", lambda number: ["из ЕИС"])

    assert RoseltorgAdapter().download_documents("32110933020") == ["из ЕИС"]
