"""Юнит-тесты разбора HTML Сбербанк-АСТ — без сети. Фикстура воспроизводит два реальных бага
вёрстки площадки, из-за которых поначалу не парсились цена и статус (см. докстринг адаптера):
атрибут `class` записан как `lass` у суммы, и дублирующийся `id` у обёртки тултипа и значения
статуса."""

from bs4 import BeautifulSoup

from app.adapters.sberbank_ast import SberbankAstAdapter

CARD_HTML = """
<div class="purchase-card purchase-card_neutral">
  <div class="purchase-card__tags">
    <span id="PurchaseStageTerm">
      <div id="el-popover-1">Этап проведения процедуры</div>
      <span class="el-popover__reference-wrapper">
        <div id="PurchaseStageTerm" class="a-tag a-tag_tooltip">Подача заявок</div>
      </span>
    </span>
  </div>
  <a class="purchase-card__purchcode" href="/Trade/View/1">№ SBR003-1</a>
  <div class="content-body">
    <span class="purchase-object__content">Поставка счетчиков электрической энергии</span>
    <span class="purchase-organizer__content">ОАО "КОММУНЭНЕРГО"</span>
  </div>
  <div class="amount-item__wrapper">
    <span lass="amount-item__value">623 019,99 &#8381;</span>
  </div>
  <div class="purchase-stats__dates">
    <div class="purchase-date__row">
      <span class="purchase-date__label">Опубликовано:</span>
      <span class="purchase-date__value">29.07.2026 14:42</span>
    </div>
    <div class="purchase-date__row">
      <span class="purchase-date__label">Подача заявок по:</span>
      <span class="purchase-date__value">06.08.2026 10:00</span>
    </div>
  </div>
</div>
"""


def test_parse_card_extracts_price_despite_broken_class_attribute():
    """Атрибут `class` на площадке иногда записан как `lass` (баг вёрстки, не наш) — цена
    должна извлекаться через родительскую обёртку, а не молча теряться."""

    soup = BeautifulSoup(CARD_HTML, "lxml")
    card = soup.select_one("div.purchase-card")
    adapter = SberbankAstAdapter()
    errors = []
    summary = adapter._parse_card(card, errors)
    assert errors == []
    assert str(summary.price) == "623019.99"


def test_parse_card_status_ignores_duplicate_id_tooltip_wrapper():
    """`id="PurchaseStageTerm"` дублируется на внешнем <span>-тултипе и внутреннем <div> со
    значением — без тега-квалификатора `div#...` статус потерялся бы в склеенном тексте."""

    soup = BeautifulSoup(CARD_HTML, "lxml")
    card = soup.select_one("div.purchase-card")
    adapter = SberbankAstAdapter()
    errors = []
    summary = adapter._parse_card(card, errors)
    assert summary.status == "collecting_bids"


def test_parse_card_extracts_dates_and_customer():
    soup = BeautifulSoup(CARD_HTML, "lxml")
    card = soup.select_one("div.purchase-card")
    adapter = SberbankAstAdapter()
    errors = []
    summary = adapter._parse_card(card, errors)
    assert summary.external_id == "SBR003-1"
    assert summary.customer_name == 'ОАО "КОММУНЭНЕРГО"'
    assert summary.publish_date.isoformat() == "2026-07-29"
    assert summary.application_end.date().isoformat() == "2026-08-06"
