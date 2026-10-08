"""Канал «Тендерплан» (решение 30.09.2026): разбор выдачи и отдельность каналов.

Без сети: форма ответа взята из OpenAPI Тендерплана (tenderplan.ru/api/doc/, 30.09.2026).
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.adapters.tenderplan import TenderplanAdapter, tenderplan_id
from app.models.source import FEED_PATTERN, SEPARATE_FEED_SOURCE_TYPES, Source
from app.services.tender_service import feed_condition_for, poll_source
from tests.test_sources import _StubRegistryAdapter, _unique_registry_number

ITEM_44 = {
    "_id": "66f9a1c2e4b0a1b2c3d4e5f6",
    "number": "0711200005826000190",
    "orderName": "Поставка счетчиков электрической энергии",
    "type": 1,
    "placingWay": 3,
    "status": 1,
    "maxPrice": 80426,
    "currency": "RUB",
    "publicationDateTime": 1790596220000,
    "submissionCloseDateTime": 1791259200000,
    "customers": [{"guid": "x", "name": "ГБУЗ «Больница»", "region": "16"}],
    "region": 16,
}

ITEM_COMMERCIAL = {
    "_id": "518651_1",
    "number": "7966374",
    "orderName": "Счетчики учёта электроэнергии",
    "type": 17,
    "placingWay": 0,
    "status": 2,
    "maxPrice": 120000.5,
    "customers": [],
    "region": 47,
}


def _adapter() -> TenderplanAdapter:
    adapter = TenderplanAdapter(api_key="token")
    adapter._placing_ways = {0: "Иной способ", 3: "Открытый аукцион в электронной форме"}
    adapter._types = {1: "44-ФЗ", 17: "ТЭК-Торг"}
    return adapter


def test_eis_tender_is_stored_under_registry_number():
    """Закупка ЕИС — под реестровым номером: карточка и документы берутся адаптером ЕИС."""

    summary = _adapter()._summary(ITEM_44)

    assert summary.external_id == summary.registry_number == "0711200005826000190"
    assert tenderplan_id(summary.external_id) is None
    assert summary.title == "Поставка счетчиков электрической энергии"
    assert summary.price == Decimal("80426.00")
    assert summary.status == "collecting_bids"
    assert summary.procurement_method == "Открытый аукцион в электронной форме (44-ФЗ)"
    assert summary.customer_name == "ГБУЗ «Больница»"
    assert summary.application_end == datetime.fromtimestamp(1791259200, tz=timezone.utc)
    assert summary.region_code == "16"
    assert "zakupki.gov.ru" in summary.source_url


def test_commercial_tender_keeps_tenderplan_id():
    """У коммерческой площадки номера ЕИС нет — документы берутся из API Тендерплана."""

    summary = _adapter()._summary(ITEM_COMMERCIAL)

    assert summary.external_id == "tp-518651_1"
    assert tenderplan_id(summary.external_id) == "518651_1"
    assert summary.registry_number == "7966374"
    assert summary.status == "evaluation"
    assert summary.procurement_method == "Иной способ (ТЭК-Торг)"
    assert summary.region_code == "47"
    assert summary.customer_name is None


def test_token_goes_to_bearer_header():
    adapter = TenderplanAdapter()

    class _Credential:
        password = " pat-token "
        username = "user"

    adapter.use_credentials([_Credential()])
    assert adapter._client().headers["Authorization"] == "Bearer pat-token"


def test_tenderplan_without_token_explains_itself(monkeypatch):
    adapter = TenderplanAdapter()
    monkeypatch.setattr(adapter, "use_credentials", lambda credentials: None)

    outcome = adapter.list_new_tenders(None)

    assert outcome.tenders == []
    assert "токен" in outcome.errors[0].message


def test_feeds_cover_new_channels():
    assert "tenderplan" in SEPARATE_FEED_SOURCE_TYPES
    assert "tenderplan" in FEED_PATTERN
    # Селдон убран 08.10.2026 — канал с таким именем больше не принимается.
    assert "seldon" not in SEPARATE_FEED_SOURCE_TYPES
    assert "seldon" not in FEED_PATTERN


def test_each_external_feed_is_separate(monkeypatch):
    """Одна закупка из ЕИС, Госплана и Тендерплана — три строки, по одной в своём канале."""

    import app.services.tender_service as tender_service_module
    from app.db.session import SessionLocal
    from app.models.tender import Tender

    registry_number = _unique_registry_number()
    db = SessionLocal()
    try:
        sources = [
            Source(
                key=f"stub_{type_}_{uuid.uuid4().hex[:8]}",
                name=f"Тестовый {type_}",
                url="https://example.test",
                type=type_,
                adapter_key="stub",
                adapter_status="implemented",
            )
            for type_ in ("eis", "gosplan", "tenderplan")
        ]
        db.add_all(sources)
        db.commit()

        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(registry_number, title="Поставка ПУ"),
        )
        results = [poll_source(db, source) for source in sources]

        def count(feed):
            return db.scalar(
                select(func.count())
                .select_from(Tender)
                .where(Tender.registry_number == registry_number, feed_condition_for(feed))
            )

        assert [result.created for result in results] == [1, 1, 1]
        assert count("standard") == 1
        assert count("gosplan") == 1
        assert count("tenderplan") == 1
    finally:
        db.close()


class _FakeTenderplan(TenderplanAdapter):
    """Отвечает вместо API: ключи аккаунта и по одной странице выдачи на каждый путь."""

    def __init__(self, keys):
        super().__init__(api_key="token", classifiers=("26.51.63",), words=())
        self.keys = keys
        self.paths: list[str] = []

    def _load_dictionaries(self, client):
        pass

    def _request(self, client, method, path, **kwargs):
        self.paths.append(path)
        if path == "/api/keys/getall":
            if isinstance(self.keys, Exception):
                raise self.keys
            return self.keys
        page = (kwargs.get("params") or {}).get("page", 0)
        if page > 0:
            return {"tenders": []}
        if path == "/api/tenders/v2/getlist":
            return {"tenders": [ITEM_44]}
        return {"count": 1, "tenders": [ITEM_COMMERCIAL]}


def test_account_keys_drive_collection():
    """Ключи в аккаунте есть — берётся выдача по ним, свой поиск не нужен."""

    adapter = _FakeTenderplan(keys=[{"_id": "k1", "name": "sova"}])
    outcome = adapter.list_new_tenders(None)

    assert [t.external_id for t in outcome.tenders] == ["0711200005826000190"]
    assert "/api/search/v2/list" not in adapter.paths
    assert outcome.errors == []


def test_without_keys_adapter_searches_itself():
    adapter = _FakeTenderplan(keys=[])
    outcome = adapter.list_new_tenders(None)

    assert [t.external_id for t in outcome.tenders] == ["tp-518651_1"]
    assert "/api/tenders/v2/getlist" not in adapter.paths
