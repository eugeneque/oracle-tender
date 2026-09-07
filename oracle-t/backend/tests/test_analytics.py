"""Дашборд «Аналитика» (раздел 5.6 ТЗ)."""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.models.analysis import Requirement, WinPercentage
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import Tender
from app.services.analytics_service import (
    build_summary_context,
    get_by_region,
    get_monthly_series,
    get_summary,
    get_top_customers,
    get_widgets,
)
from app.services.tender_service import TenderFilters


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_source(db) -> Source:
    source = Source(
        key=f"analytics_{uuid.uuid4().hex[:8]}",
        name="Площадка для аналитики",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _make_tender(db, source, **kwargs) -> Tender:
    tender = Tender(
        source_id=source.id,
        external_id=kwargs.pop("external_id", f"EXT-{uuid.uuid4().hex[:8]}"),
        title=kwargs.pop("title", "Поставка приборов учёта"),
        currency="RUB",
        **kwargs,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


def test_overview_requires_auth(client):
    assert client.get("/analytics/overview").status_code == 401


def test_overview_returns_all_sections(client, admin_token):
    response = client.get("/analytics/overview", headers=_auth_headers(admin_token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "summary",
        "monthly",
        "regions",
        "customers",
        "win_breakdown",
        "widgets",
    }
    # Виджеты состояния системы отдаются всегда, даже на пустой базе: пустая сетка плиток
    # выглядела бы как поломка дашборда, а не как «данных ещё нет».
    assert {widget["key"] for widget in body["widgets"]} == {
        "sources",
        "documents",
        "catalog",
        "analysis",
        "compliance",
        "errors",
    }


def test_summary_counts_only_filtered_tenders(db_session):
    source = _make_source(db_session)
    _make_tender(db_session, source, price=Decimal("1000.00"), publish_date=date(2026, 5, 1))
    _make_tender(db_session, source, price=Decimal("3000.00"), publish_date=date(2026, 6, 1))

    filters = TenderFilters(source_keys=[source.key])
    summary = get_summary(db_session, filters)

    assert summary["total"] == 2
    assert summary["total_amount"] == Decimal("4000.00")
    assert summary["with_price"] == 2
    assert summary["average_amount"] == Decimal("2000.00")


def test_summary_average_ignores_tenders_without_price(db_session):
    """Средний чек считается по тендерам с ценой, а не по всем: часть площадок не
    публикует НМЦК до вскрытия, и деление на общее число занижало бы средний чек."""

    source = _make_source(db_session)
    _make_tender(db_session, source, price=Decimal("1000.00"))
    _make_tender(db_session, source, price=None)

    summary = get_summary(db_session, TenderFilters(source_keys=[source.key]))

    assert summary["total"] == 2
    assert summary["with_price"] == 1
    assert summary["average_amount"] == Decimal("1000.00")


def test_deadline_soon_counts_only_open_tenders_within_five_days(db_session):
    source = _make_source(db_session)
    now = datetime.now(timezone.utc)
    _make_tender(db_session, source, application_end=now + timedelta(days=2))
    _make_tender(db_session, source, application_end=now + timedelta(days=20))
    # Срок уже истёк — в «истекают в ближайшие 5 дней» такой тендер попадать не должен,
    # иначе счётчик срочного превращается в счётчик просроченного.
    _make_tender(db_session, source, application_end=now - timedelta(days=1))

    summary = get_summary(db_session, TenderFilters(source_keys=[source.key]))

    assert summary["deadline_soon"] == 1


def test_monthly_series_fills_gaps_with_zeroes(db_session):
    """Месяцы без закупок возвращаются нулями — иначе линия графика перепрыгнула бы
    пустой период и нарисовала непрерывный рост там, где данных не было."""

    source = _make_source(db_session)
    _make_tender(db_session, source, publish_date=date(2026, 3, 1), price=Decimal("100.00"))
    _make_tender(db_session, source, publish_date=date(2026, 5, 1), price=Decimal("200.00"))

    series = get_monthly_series(db_session, TenderFilters(source_keys=[source.key]), months=3)

    assert [point["month"] for point in series] == [
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
    ]
    assert [point["count"] for point in series] == [1, 0, 1]
    assert series[1]["amount"] == Decimal("0")


def test_monthly_series_anchors_on_last_month_with_data(db_session):
    """Ряд строится от последнего месяца с данными, а не от «сегодня»: на базе без свежих
    закупок график из одних пустых месяцев читался бы как неработающая система."""

    source = _make_source(db_session)
    _make_tender(db_session, source, publish_date=date(2026, 2, 1))

    series = get_monthly_series(db_session, TenderFilters(source_keys=[source.key]), months=2)

    assert series[-1]["month"] == date(2026, 2, 1)
    assert series[-1]["count"] == 1


def test_region_breakdown_keeps_undetected_region_visible(db_session):
    """Тендеры без определённого региона попадают в отдельную строку, а не отбрасываются:
    сейчас это большинство записей, и молча спрятать их значило бы нарисовать картинку
    по одному проценту данных."""

    source = _make_source(db_session)
    _make_tender(db_session, source, region_organizer_code=None)
    _make_tender(db_session, source, region_organizer_code="23")

    rows = get_by_region(db_session, TenderFilters(source_keys=[source.key]))
    by_code = {row["code"]: row for row in rows}

    assert by_code[None]["name"] == "Регион не определён"
    assert by_code[None]["count"] == 1
    assert by_code["23"]["federal_district"] == "Южный"


def test_top_customers_sorted_by_count(db_session):
    source = _make_source(db_session)
    for _ in range(3):
        _make_tender(db_session, source, customer_name="Заказчик А")
    _make_tender(db_session, source, customer_name="Заказчик Б")

    rows = get_top_customers(db_session, TenderFilters(source_keys=[source.key]))

    assert rows[0]["name"] == "Заказчик А"
    assert rows[0]["count"] == 3


def test_widget_tone_reflects_coverage(db_session):
    widgets = {widget["key"]: widget for widget in get_widgets(db_session)}

    # Значение и подпись всегда заполнены — плитка без значения на дашборде читается
    # как сбой загрузки.
    for widget in widgets.values():
        assert widget["value"]
        assert widget["tone"] in {"ok", "warn", "danger", "neutral"}

    assert widgets["sources"]["progress"] is not None
    # У виджета ошибок шкалы нет: важен сам факт и количество, а не доля от чего-либо.
    assert widgets["errors"]["progress"] is None


def test_summary_context_mentions_all_sections(db_session):
    """Контекст для модели собирается кодом и обязан покрывать все разделы: если раздел
    выпадет из выжимки, сводка «по всей системе» молча перестанет его учитывать."""

    context = build_summary_context(db_session, TenderFilters())

    assert "РАЗДЕЛ «ТЕНДЕРЫ»" in context
    assert "СОСТОЯНИЕ СИСТЕМЫ" in context
    assert "Источники:" in context
    assert "Каталог заполнен:" in context


def test_average_win_percentage_uses_mirtek_only(db_session):
    """Средний процент победителя — по МИРТЕК, а не по всем производителям: показатель
    отвечает на вопрос «наши шансы», и усреднение с конкурентами лишает его смысла."""

    source = _make_source(db_session)
    tender = _make_tender(db_session, source)

    mirtek = db_session.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    assert mirtek is not None, "МИРТЕК должен быть в справочнике производителей (сид)"

    requirement = Requirement(tender_id=tender.id, text="Класс точности не хуже 1,0")
    db_session.add(requirement)
    db_session.add(
        WinPercentage(
            tender_id=tender.id,
            manufacturer_id=mirtek.id,
            percentage=Decimal("77.00"),
            requirements_total=1,
            requirements_scored=1,
        )
    )
    db_session.commit()

    summary = get_summary(db_session, TenderFilters(source_keys=[source.key]))

    assert summary["average_win_percentage"] == Decimal("77.00")
    assert summary["analysed"] == 1
