"""Юнит-тесты разбора HTML ЕЭТП (zakazrf) — без сети и без Playwright: фикстуры взяты из
реального ответа площадки, зафиксированного при разработке адаптера. Сквозная проверка
через реальный Playwright-запрос — `python -m app.cli poll-source zakazrf` (см. README)."""

from bs4 import BeautifulSoup

from app.adapters.zakazrf import ZakazrfAdapter

# Строка с реальным аукционом по 44-ФЗ (Заказчик заполнен отдельно от Организатора).
ROW_44FZ = """
<table class="reporttable">
<tr class="orm-grid-table-header"><th>ФЗ</th></tr>
<tr>
    <td>44-ФЗ</td>
    <td><a href="/NotificationEx/id/2076625">0711200008326000266</a></td>
    <td>Идет подача заявок на участие<br/></td>
    <td>Аукцион</td>
    <td>Оказание услуг по электрическим испытаниям электроустановок</td>
    <td>1 311 270,00</td>
    <td>ГБУ "РКОД"</td>
    <td>ГАУЗ "РКОД МЗ РТ"</td>
    <td>Иванов И.И.</td>
    <td>27.08.2026</td>
    <td>27.08.2026</td>
    <td>04.09.2026 10:00 (+03:00)</td>
    <td>09.09.2026 10:00 (+03:00)</td>
    <td>09.09.2026 10:00 (+03:00)</td>
    <td>11.09.2026</td>
</tr>
</table>
"""

# Строка с извещением по 223-ФЗ: колонка "Заказчик" пустая, заполнен только "Организатор".
ROW_223FZ_NO_CUSTOMER = """
<table class="reporttable">
<tr class="orm-grid-table-header"><th>ФЗ</th></tr>
<tr>
    <td>223-ФЗ</td>
    <td><a href="/NotificationEx/id/2076749">32616328708</a></td>
    <td></td>
    <td>Извещение Иное</td>
    <td>Изготовление и поставка систем освещения</td>
    <td>3 342 020,00</td>
    <td>ГАУ "Технопарк"</td>
    <td></td>
    <td>Максимова А.В.</td>
    <td>27.08.2026</td>
    <td>27.08.2026</td>
    <td>04.09.2026 10:00 (+03:00)</td>
    <td></td>
    <td>09.09.2026 10:00 (+03:00)</td>
    <td>11.09.2026</td>
</tr>
</table>
"""


def _parse_first_data_row(html: str):
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.reporttable")
    rows = [r for r in table.find_all("tr") if "orm-grid-table-header" not in (r.get("class") or [])]
    adapter = ZakazrfAdapter()
    errors = []
    summary = adapter._parse_row(rows[0], errors)
    return summary, errors


def test_parse_row_44fz_with_status_and_customer():
    summary, errors = _parse_first_data_row(ROW_44FZ)
    assert errors == []
    assert summary.external_id == "0711200008326000266"
    assert summary.status == "collecting_bids"
    assert summary.customer_name == 'ГАУЗ "РКОД МЗ РТ"'
    assert summary.organizer_name == 'ГБУ "РКОД"'
    assert str(summary.price) == "1311270.00"
    assert summary.source_url == "https://etp.zakazrf.ru/NotificationEx/id/2076625"


def test_parse_row_223fz_falls_back_customer_to_organizer():
    summary, errors = _parse_first_data_row(ROW_223FZ_NO_CUSTOMER)
    assert errors == []
    assert summary.external_id == "32616328708"
    assert summary.status is None  # площадка не сообщает состояние для таких извещений
    assert summary.customer_name == 'ГАУ "Технопарк"'  # fallback на организатора
    assert summary.organizer_name == 'ГАУ "Технопарк"'


def test_parse_row_handles_implicit_tbody_from_rendered_dom():
    """После AJAX-обновления `page.content()` сериализует DOM с неявным `<tbody>`, которого
    нет в исходном серверном HTML — строки всё равно должны находиться."""

    html = ROW_44FZ.replace("<table class=\"reporttable\">", "<table class=\"reporttable\"><tbody>")
    html = html.replace("</table>", "</tbody></table>")
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.reporttable")
    rows = [r for r in table.find_all("tr") if "orm-grid-table-header" not in (r.get("class") or [])]
    assert len(rows) == 1
