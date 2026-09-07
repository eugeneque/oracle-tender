"""Юнит-тесты разбора HTML ЭТП РФ — без сети. Фикстура — реальная строка, зафиксированная
при разработке адаптера (тот же движок таблиц "ORM", что и у ЕЭТП/zakazrf)."""

from bs4 import BeautifulSoup

from app.adapters.etprf import EtprfAdapter

ROW_PUBLISHED = """
<table class="reporttable">
<tr class="orm-grid-table-header"><th>Тип процедуры</th></tr>
<tr>
    <td>Коммерческие</td>
    <td>ЭТП</td>
    <td>EX25120100008</td>
    <td>17321/724 Поставка счетчика частиц (в соответствии с ТЗ)</td>
    <td>986 880</td>
    <td>9868,80</td>
    <td>Не требуется</td>
    <td>АКЦИОНЕРНОЕ ОБЩЕСТВО "КОНЦЕРН "КАЛАШНИКОВ"</td>
    <td>01.12.2025</td>
    <td>11.12.2025 10:00 (+03:00)</td>
    <td>Опубликован</td>
    <td><a href="/NotificationEX/id/225514">Открыть</a></td>
</tr>
</table>
"""

ROW_CANCELLED = ROW_PUBLISHED.replace(">Опубликован<", ">Отменен<")


def _parse_first_data_row(html: str):
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.reporttable")
    rows = [r for r in table.find_all("tr") if "orm-grid-table-header" not in (r.get("class") or [])]
    adapter = EtprfAdapter()
    errors = []
    summary = adapter._parse_row(rows[0], errors)
    return summary, errors


def test_parse_row_published_leaves_status_unmapped():
    """"Опубликован" не означает "сейчас идёт приём заявок" (площадка не меняет статус после
    закрытия приёма) — status должен остаться пустым, а не угадываться как collecting_bids."""

    summary, errors = _parse_first_data_row(ROW_PUBLISHED)
    assert errors == []
    assert summary.external_id == "EX25120100008"
    assert summary.status is None
    assert summary.customer_name == 'АКЦИОНЕРНОЕ ОБЩЕСТВО "КОНЦЕРН "КАЛАШНИКОВ"'
    assert str(summary.price) == "986880"
    assert summary.source_url == "https://web.etprf.ru/NotificationEX/id/225514"


def test_parse_row_cancelled_maps_to_cancelled_status():
    summary, errors = _parse_first_data_row(ROW_CANCELLED)
    assert errors == []
    assert summary.status == "cancelled"
