"""Юнит-тесты разбора HTML Росэлторг — без сети. Фикстура — упрощённая версия реальной
карточки, зафиксированной при разработке адаптера."""

from bs4 import BeautifulSoup

from app.adapters.roseltorg import RoseltorgAdapter

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
