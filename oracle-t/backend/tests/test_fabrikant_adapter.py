"""Юнит-тесты разбора HTML Фабрикант — без сети. Фикстуры — реальные карточки `div[data-slot=
"card"]`, снятые со страницы поиска (`/procedure/search`) при разработке: карточка без
заказчика/цены/срока подачи, карточка со всеми полями заполненными и карточка отменённой
процедуры (бейдж статуса — не первый по счёту среди бейджей карточки, см. докстринг адаптера)."""

from bs4 import BeautifulSoup

from app.adapters.fabrikant import FabrikantAdapter

CARD_MINIMAL_HTML = """
<div data-slot="card" data-id="679205017" class="border-border block rounded-lg border bg-white p-4">
  <div class="mb-6 flex w-full flex-wrap items-start justify-between gap-x-6 gap-y-3 lg:flex-nowrap">
    <div class="flex flex-wrap items-center gap-4 md:flex-nowrap">
      <span class="flex min-h-6 flex-wrap text-sm lg:inline">
        <span class="text-bali-hai-700 mr-2">Мониторинг цен</span>
        <span class="text-bali-hai-700 mr-2 whitespace-nowrap font-medium">№ 3490451</span>
      </span>
    </div>
    <div class="flex min-h-8 flex-wrap items-center gap-1 lg:justify-end">
      <span data-slot="badge">Идёт приём заявок</span>
      <span data-slot="badge">Закупки Росатом</span>
    </div>
  </div>
  <div class="flex flex-wrap justify-between gap-6 md:flex-nowrap">
    <div>
      <a data-slot="anchor" href="https://fabrikant.ru/trades/atom/PriceMonitoring/?action=view&id=1011973">Поставка строительных материалов</a>
      <div class="mt-6 flex flex-col gap-2">
        <div class="flex flex-wrap gap-2">
          <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Организатор</div>
          <div data-slot="text" class="text-sm">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ЭНЕРГОСБЫТ ЗАПОРОЖЬЕ"</div>
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm">
          <div class="flex flex-wrap gap-2">
            <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Дата публикации</div>
            <div data-slot="text" class="text-sm">28.08.2026 14:19</div>
          </div>
        </div>
      </div>
    </div>
    <div class="flex w-full shrink-0 flex-col items-start md:w-auto md:items-end">
      <div class="mb-6">
        <div data-slot="text" class="font-heading text-bali-hai-300 text-base font-bold">Цена не указана</div>
      </div>
      <a href="https://fabrikant.ru/trades/atom/PriceMonitoring/?action=view&id=1011973" role="button">Бесплатное участие</a>
    </div>
  </div>
</div>
"""

CARD_FULL_HTML = """
<div data-slot="card" data-id="679197122" class="border-border block rounded-lg border bg-white p-4">
  <div class="mb-6 flex w-full flex-wrap items-start justify-between gap-x-6 gap-y-3 lg:flex-nowrap">
    <div class="flex flex-wrap items-center gap-4 md:flex-nowrap">
      <span class="flex min-h-6 flex-wrap text-sm lg:inline">
        <span class="text-bali-hai-700 mr-2">Запрос предложений</span>
        <span class="text-bali-hai-700 mr-2 whitespace-nowrap font-medium">№ 3490184-1</span>
      </span>
    </div>
    <div class="flex min-h-8 flex-wrap items-center gap-1 lg:justify-end">
      <span data-slot="badge">Идёт приём заявок</span>
      <span data-slot="badge">Закупки Росатом</span>
    </div>
  </div>
  <div class="flex flex-wrap justify-between gap-6 md:flex-nowrap">
    <div>
      <a data-slot="anchor" href="https://fabrikant.ru/trades/atom/ProposalRequest/?action=view&id=49997">Поставка, монтаж и ввод в эксплуатацию оборудования для накопления <mark>электрической энергии</mark></a>
      <div class="mt-6 flex flex-col gap-2">
        <div class="flex flex-wrap gap-2">
          <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Организатор</div>
          <div data-slot="text" class="text-sm">АКЦИОНЕРНОЕ ОБЩЕСТВО "АТОМКОМПЛЕКТ"</div>
        </div>
        <div class="flex flex-wrap gap-2">
          <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Заказчик</div>
          <span class="text-sm">ООО "РЭНЕРА"</span>
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm">
          <div class="flex flex-wrap gap-2">
            <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Дата публикации</div>
            <div data-slot="text" class="text-sm">27.08.2026 19:45</div>
          </div>
          <div class="flex flex-wrap gap-2">
            <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Дата окончания приёма заявок</div>
            <div data-slot="text" class="text-sm">08.09.2026 11:00</div>
          </div>
        </div>
      </div>
    </div>
    <div class="flex w-full shrink-0 flex-col items-start md:w-auto md:items-end">
      <div class="mb-6">
        <div class="flex items-center justify-end gap-2">
          <div data-slot="text" class="font-heading text-base font-bold">1&nbsp;289&nbsp;696&nbsp;141<span class="text-bali-hai-500">,70 RUB</span></div>
        </div>
        <div data-slot="text" class="text-bali-hai-500 text-xs font-medium md:text-right">начальная цена</div>
      </div>
      <a href="https://fabrikant.ru/trades/atom/ProposalRequest/?action=view&id=49997" role="button">Платное участие</a>
    </div>
  </div>
</div>
"""

CARD_CANCELLED_HTML = """
<div data-slot="card" data-id="678354607" class="border-border block rounded-lg border bg-white p-4">
  <div class="mb-6 flex w-full flex-wrap items-start justify-between gap-x-6 gap-y-3 lg:flex-nowrap">
    <div class="flex flex-wrap items-center gap-4 md:flex-nowrap">
      <span class="flex min-h-6 flex-wrap text-sm lg:inline">
        <span class="text-bali-hai-700 mr-2">Мониторинг цен</span>
        <span class="text-bali-hai-700 mr-2 whitespace-nowrap font-medium">№ 3459583</span>
      </span>
    </div>
    <div class="flex min-h-8 flex-wrap items-center gap-1 lg:justify-end">
      <span data-slot="badge">Организатор отказался</span>
      <span data-slot="badge">Закупки Росатом</span>
    </div>
  </div>
  <div class="flex flex-wrap justify-between gap-6 md:flex-nowrap">
    <div>
      <a data-slot="anchor" href="https://fabrikant.ru/trades/atom/PriceMonitoring/?action=view&id=981710">Поставка приборов учёта электрической энергии</a>
      <div class="mt-6 flex flex-col gap-2">
        <div class="flex flex-wrap gap-2">
          <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Организатор</div>
          <div data-slot="text" class="text-sm">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ЭНЕРГОСБЫТ ДОНЕЦК"</div>
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm">
          <div class="flex flex-wrap gap-2">
            <div data-slot="text" class="text-sm text-bali-hai-500 font-semibold">Дата публикации</div>
            <div data-slot="text" class="text-sm">25.05.2026 09:58</div>
          </div>
        </div>
      </div>
    </div>
    <div class="flex w-full shrink-0 flex-col items-start md:w-auto md:items-end">
      <div class="mb-6">
        <div data-slot="text" class="font-heading text-bali-hai-300 text-base font-bold">Цена не указана</div>
      </div>
    </div>
  </div>
</div>
"""


def _parse(html: str):
    soup = BeautifulSoup(html, "lxml")
    card = soup.select_one('div[data-slot="card"]')
    adapter = FabrikantAdapter()
    errors: list = []
    summary = adapter._parse_card(card, errors)
    assert errors == []
    return summary


def test_parse_card_minimal_fields():
    summary = _parse(CARD_MINIMAL_HTML)
    assert summary.external_id == "3490451"
    assert summary.procurement_method == "Мониторинг цен"
    assert summary.organizer_name == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ЭНЕРГОСБЫТ ЗАПОРОЖЬЕ"'
    assert summary.customer_name == summary.organizer_name
    assert summary.status == "collecting_bids"
    assert summary.price is None
    assert summary.publish_date.isoformat() == "2026-08-28"
    assert summary.application_end is None
    assert summary.source_url == "https://fabrikant.ru/trades/atom/PriceMonitoring/?action=view&id=1011973"


def test_parse_card_extracts_customer_price_and_deadline():
    """Заказчик, отдельный от организатора, и цена — в отличие от минимальной карточки, где
    заполнен только организатор (см. `_field_value` в адаптере: ищет по подписи, а не по
    индексу, так как набор подписей у карточек разный)."""

    summary = _parse(CARD_FULL_HTML)
    assert summary.external_id == "3490184-1"
    assert summary.organizer_name == 'АКЦИОНЕРНОЕ ОБЩЕСТВО "АТОМКОМПЛЕКТ"'
    assert summary.customer_name == 'ООО "РЭНЕРА"'
    assert str(summary.price) == "1289696141.70"
    assert summary.application_end.date().isoformat() == "2026-09-08"
    assert "электрической энергии" in summary.title


def test_parse_card_maps_cancelled_status():
    """Другая формулировка статуса («Организатор отказался», не «Отменена») — статус ищется по
    словарю формулировок среди бейджей карточки, а не берётся по фиксированному индексу (второй
    бейдж карточки — категория закупки «Закупки Росатом», не статус)."""

    summary = _parse(CARD_CANCELLED_HTML)
    assert summary.status == "cancelled"
    assert summary.price is None
