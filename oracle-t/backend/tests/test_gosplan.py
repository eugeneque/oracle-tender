"""Канал «Госплан» (решение 28.09.2026): разбор ответа API и отдельность канала.

Без сети: ответ API взят из реального запроса к v2test.gosplan.info 28.09.2026.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.adapters.gosplan import FREE_BASE_URL, PAID_BASE_URL, GosplanAdapter
from app.models.source import Source
from app.services.tender_service import feed_condition_for, poll_source
from tests.test_sources import _StubRegistryAdapter, _unique_registry_number

ITEM_44 = {
    "collecting_finished_at": "2026-10-06T04:00:00",
    "currency_code": "RUB",
    "customers": ["1660013417"],
    "max_price": 80426.0,
    "object_info": "Поставка счетчиков электрической энергии",
    "okpd2": ["26.51.63.130"],
    "published_at": "2026-09-28T11:50:20.291000",
    "purchase_number": "0711200005826000190",
    "purchase_type": "epNotificationEF2020",
    "region": 16,
    "stage": 1,
}

ITEM_223 = {
    "submission_close_at": None,
    "currency_code": "RUB",
    "customer": "6376002095",
    "max_price": 451425.0,
    "object_info": "Комплекс узла учёта",
    "okpd2": ["26.51.63.110"],
    "published_at": "2026-09-28T08:40:19",
    "purchase_number": "32616412633",
    "purchase_type": "purchaseNotice",
    "region": 63,
    "stage": 3,
}


def test_summary_44fz_maps_api_fields():
    summary = GosplanAdapter()._summary("fz44", ITEM_44)

    assert summary.external_id == summary.registry_number == "0711200005826000190"
    assert summary.title == "Поставка счетчиков электрической энергии"
    assert summary.price == Decimal("80426.00")
    assert summary.status == "collecting_bids"
    assert summary.procurement_method == "Электронный аукцион (44-ФЗ)"
    assert summary.application_end == datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)
    assert summary.publish_date.isoformat() == "2026-09-28"
    assert summary.okpd2_code == "26.51.63.130"
    assert summary.region_code == "16"
    assert "zakupki.gov.ru" in summary.source_url


def test_summary_223fz_uses_its_own_deadline_and_card():
    summary = GosplanAdapter()._summary("fz223", ITEM_223)

    assert summary.application_end is None
    assert summary.status == "completed"
    assert summary.procurement_method == "Извещение о закупке (223-ФЗ)"
    assert "notice223" in summary.source_url and "32616412633" in summary.source_url


def test_key_switches_to_paid_contour():
    """Без ключа — бесплатный контур, с ключом из «Доступов к площадкам» — платный."""

    adapter = GosplanAdapter()
    assert adapter.base_url == FREE_BASE_URL

    class _Credential:
        password = " secret-key "

    adapter.use_credentials([_Credential()])
    assert adapter.base_url == PAID_BASE_URL
    assert adapter._client().headers["apikey"] == "secret-key"


def test_gosplan_feed_is_not_merged_with_standard_sources(monkeypatch):
    """Закупка, которую уже принесла ЕИС, появляется и в канале Госплана — своей строкой."""

    import app.services.tender_service as tender_service_module
    from app.db.session import SessionLocal
    from app.models.tender import Tender

    registry_number = _unique_registry_number()
    db = SessionLocal()
    try:
        standard = Source(
            key=f"stub_eis_{uuid.uuid4().hex[:8]}",
            name="Тестовая ЕИС",
            url="https://example.test",
            type="eis",
            adapter_key="stub",
            adapter_status="implemented",
        )
        gosplan = Source(
            key=f"stub_gosplan_{uuid.uuid4().hex[:8]}",
            name="Тестовый Госплан",
            url="https://example.test",
            type="gosplan",
            adapter_key="stub",
            adapter_status="implemented",
        )
        db.add_all([standard, gosplan])
        db.commit()

        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(registry_number, title="Поставка ПУ"),
        )
        first = poll_source(db, standard)
        second = poll_source(db, gosplan)

        def count(feed):
            return db.scalar(
                select(func.count())
                .select_from(Tender)
                .where(Tender.registry_number == registry_number, feed_condition_for(feed))
            )

        assert first.created == 1 and second.created == 1
        assert count("standard") == 1
        assert count("gosplan") == 1
    finally:
        db.close()
