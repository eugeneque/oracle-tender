"""Юнит-тесты разбора HTML ТЭК-Торг — без сети. Фикстуры — реальные карточки `div.sc-6c01eeae-0`,
снятые со страницы поиска (`/procedures?name=...`) при разработке: площадка отдаёт закупки и
продажу имущества в одной и той же выдаче, но с разной внутренней раскладкой — в частности,
статус закупки лежит прямо в шапке карточки, а у имущества — в отдельном поле «Статус» внутри
тела карточки (см. докстринг адаптера). Оба варианта должны парситься одинаково, так как статус
ищется по классу бейджа, а не по позиции в дереве."""

from bs4 import BeautifulSoup

from app.adapters.tektorg import TektorgAdapter

CARD_PROCUREMENT_HTML = """
<div class="sc-6c01eeae-0 jtfzxc">
  <div class="sc-6c01eeae-1 fOviDm">
    <div class="sc-6c01eeae-2 ha-dteW">
      <div class="sc-6c01eeae-6 ehGqSP">
        <div class="sc-375e6608-2 kddgYv">
          <span class="sc-375e6608-4 eBkrKS">№ ЗП6081898</span>
          <div class="sc-3e697cd2-0 gVvBeH"><div><span class="sc-3e697cd2-1 iQgLDt"></span><span class="sc-3e697cd2-2 iLrznN">Приём заявок</span></div></div>
          <div class="sc-375e6608-6 eBBRCr">
            <div class="sc-375e6608-3 fwLZwN"><svg></svg></div>
            <span>223-ФЗ и Коммерческие закупки</span>
          </div>
        </div>
        <a href="/223-fz/procedures/19730908" class="sc-6c01eeae-7 gccepd">Поставка приборов учета электрической энергии</a>
      </div>
      <div class="sc-6c01eeae-4 ipEIhq">
        <div class="sc-6c01eeae-8 pXQhH">
          <div class="sc-6c01eeae-9 jiywsu">Организатор</div>
          <div class="sc-6c01eeae-10 hqcmWX">Акционерное общество "Пятигорские электрические сети"</div>
        </div>
      </div>
    </div>
  </div>
  <div class="sc-6c01eeae-5 hlHUjB">
    <div class="sc-6c01eeae-11 bQBzuc">
      <div class="sc-6c01eeae-9 jiywsu">Начальная цена</div>
      <div class="sc-a6b34174-0 cLruXa">7&nbsp;112&nbsp;386 ₽</div>
      <a href="/223-fz/procedures/19730908#lots" class="sc-6c01eeae-23 estfyA">Узнать стоимость участия</a>
    </div>
    <div class="sc-6c01eeae-17 etfHgc">
      <div class="sc-6c01eeae-18 kxxgLZ">
        <div class="sc-6c01eeae-9 jiywsu">Дата публикации</div>
        <time datetime="2026-08-25T09:14:02+03:00" class="sc-7909e12c-2 hriSQm"><span class="sc-7909e12c-0 glSvLE">25.08.2026</span></time>
      </div>
      <div class="sc-6c01eeae-18 kxxgLZ">
        <div class="sc-6c01eeae-9 jiywsu">Дата окончания приема заявок</div>
        <time datetime="2026-09-02T08:00:00+03:00" class="sc-7909e12c-2 fIvbdF"><span class="sc-7909e12c-0 glSvLE">02.09.2026</span></time>
      </div>
    </div>
  </div>
</div>
"""

CARD_PROPERTY_SALE_HTML = """
<div class="sc-6c01eeae-0 jtfzxc">
  <div class="sc-6c01eeae-1 fOviDm">
    <div class="sc-6c01eeae-2 ha-dteW">
      <div class="sc-6c01eeae-6 ehGqSP">
        <div class="sc-375e6608-2 kddgYv">
          <span class="sc-375e6608-4 eBkrKS">№ ПИ607124</span>
          <div class="sc-375e6608-6 eBBRCr">
            <div class="sc-375e6608-3 fwLZwN"><svg></svg></div>
            <span>Продажа имущества</span>
          </div>
        </div>
        <a href="/sale/procedures/19399408" class="sc-6c01eeae-7 gccepd">Продажа автозаправочной станции №63</a>
      </div>
      <div class="sc-6c01eeae-4 ipEIhq">
        <div class="sc-6c01eeae-8 pXQhH">
          <div class="sc-6c01eeae-9 jiywsu">Организатор</div>
          <div class="sc-6c01eeae-10 hqcmWX">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "РН-КРАСНОЯРСКНЕФТЕПРОДУКТ"</div>
        </div>
        <div class="sc-6c01eeae-12 btrzJI">
          <div class="sc-6c01eeae-13 kFfcas">
            <div class="sc-6c01eeae-9 jiywsu">Тип процедуры</div>
            <div class="sc-6c01eeae-14 dZtlRj">Тендер с онлайн подачей ценовых предложений (на повышение)</div>
          </div>
          <div class="sc-6c01eeae-15 bmgRAC">
            <div class="sc-6c01eeae-9 jiywsu">Статус</div>
            <div class="sc-3e697cd2-0 gVvBeH"><div><span class="sc-3e697cd2-1 ittKsi"></span><span class="sc-3e697cd2-2 dIgRPO">Работа комиссии</span></div></div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div class="sc-6c01eeae-3 ivetsx">
    <div class="sc-6c01eeae-11 bQBzuc">
      <div class="sc-6c01eeae-9 jiywsu">Начальная цена</div>
      <div class="sc-a6b34174-0 cLruXa">977&nbsp;525 ₽</div>
    </div>
    <div class="sc-6c01eeae-17 etfHgc">
      <div class="sc-6c01eeae-18 kxxgLZ">
        <div class="sc-6c01eeae-9 jiywsu">Дата публикации</div>
        <time datetime="2026-08-03T08:00:00+03:00" class="sc-7909e12c-2 hriSQm"><span class="sc-7909e12c-0 glSvLE">03.08.2026</span></time>
      </div>
    </div>
  </div>
</div>
"""


def _parse(html: str):
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one("div.sc-6c01eeae-0")
    adapter = TektorgAdapter()
    errors: list = []
    summary = adapter._parse_card(card, errors)
    assert errors == []
    return summary


def test_parse_procurement_card():
    summary = _parse(CARD_PROCUREMENT_HTML)
    assert summary.external_id == "ЗП6081898"
    assert summary.status == "collecting_bids"
    assert summary.organizer_name == 'Акционерное общество "Пятигорские электрические сети"'
    assert summary.procurement_method == "223-ФЗ и Коммерческие закупки"
    assert str(summary.price) == "7112386"
    assert summary.publish_date.isoformat() == "2026-08-25"
    assert summary.application_end.isoformat() == "2026-09-02T08:00:00+03:00"
    assert summary.source_url == "https://www.tektorg.ru/223-fz/procedures/19730908"


def test_property_sale_card_is_skipped():
    """Торги по продаже имущества лежат в той же выдаче, что и закупки, и стабильно попадают
    в неё по ключевым словам: «Продажа бывш. АЗС (сарай-склад, счетчик электрической
    энергии…)» содержит нужные слова в описании объекта. Для тендерного отдела это шум —
    такие карточки отсеиваются по разделу в ссылке (`/sale/`)."""

    assert _parse(CARD_PROPERTY_SALE_HTML) is None
