import uuid

from app.adapters.base import PollOutcome, SourceAdapter, TenderSummary
from app.models.source import Source
from app.services.tender_service import poll_source


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_sources_list_requires_auth(client):
    assert client.get("/sources").status_code == 401


def test_sources_list_includes_seeded_eis(client, admin_token):
    response = client.get("/sources", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    sources = response.json()
    eis = next((s for s in sources if s["key"] == "eis"), None)
    assert eis is not None
    assert eis["adapter_status"] == "implemented"

    # Неподключаемые площадки удалены из системы (миграция 0038): в списке не должно
    # быть ни их, ни вообще строк со статусом «заблокировано» — они путали пользователя.
    keys = {s["key"] for s in sources}
    assert not keys & {"rts_tender", "astgoz", "etpgpb_strateg"}
    assert all(s["adapter_status"] != "blocked" for s in sources)


def test_poll_source_requires_admin(client, admin_token):
    sources = client.get("/sources", headers=_auth_headers(admin_token)).json()
    eis_id = next(s["id"] for s in sources if s["key"] == "eis")

    username = f"pollster_{uuid.uuid4().hex[:8]}"
    client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "P", "role": "user"},
        headers=_auth_headers(admin_token),
    )
    user_token = client.post(
        "/auth/login", json={"username": username, "password": "SomePass123"}
    ).json()["access_token"]

    response = client.post(f"/sources/{eis_id}/poll", headers=_auth_headers(user_token))
    assert response.status_code == 403


def test_poll_source_unknown_adapter_is_skipped_not_error(client, admin_token):
    """Источник без реализованного адаптера не должен приводить к ошибке — просто
    пропускается с пометкой в логе (раздел 5.1, 5.9 ТЗ)."""

    from app.db.session import SessionLocal

    key = f"noadapter_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        source = Source(
            key=key,
            name="Площадка без адаптера",
            url="https://example.test",
            type="etp_federal_commercial",
            adapter_key=None,
            adapter_status="not_implemented",
        )
        db.add(source)
        db.commit()
        source_id = source.id
    finally:
        db.close()

    response = client.post(f"/sources/{source_id}/poll", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "source_key": key,
        "found": 0,
        "created": 0,
        "updated": 0,
        "errors": 0,
    }


class _StubDuplicateAdapter(SourceAdapter):
    """Отдаёт две записи с одинаковым `external_id` в одной пачке — имитирует пересечение
    страниц выдачи источника. Используется, чтобы проверить, что дедупликация переживает
    дубль внутри одного вызова `list_new_tenders`, а не только между вызовами."""

    source_key = "stub"

    def list_new_tenders(self, since):
        return PollOutcome(
            tenders=[
                TenderSummary(external_id="DUP-1", title="Первая версия", source_url="https://example.test/1"),
                TenderSummary(external_id="DUP-1", title="Обновлённая версия", source_url="https://example.test/1"),
                TenderSummary(external_id="UNIQUE-1", title="Другой тендер", source_url="https://example.test/2"),
            ]
        )

    def get_tender_details(self, external_id):
        raise NotImplementedError

    def download_documents(self, external_id):
        raise NotImplementedError


def test_poll_source_dedups_within_single_batch(client, admin_token, monkeypatch):
    import app.services.tender_service as tender_service_module

    monkeypatch.setattr(
        tender_service_module,
        "get_adapter",
        lambda key, **kwargs: _StubDuplicateAdapter(),
    )

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        source = Source(
            key=f"stub_{uuid.uuid4().hex[:8]}",
            name="Тестовый источник",
            url="https://example.test",
            type="etp_federal_commercial",
            adapter_key="stub",
            adapter_status="implemented",
        )
        db.add(source)
        db.commit()
        db.refresh(source)

        result = poll_source(db, source)
    finally:
        db.close()

    assert result.found == 3  # адаптер отдал 3 записи, включая дубль DUP-1
    assert result.created == 2  # DUP-1 создан один раз, второй экземпляр стал обновлением
    assert result.updated == 1
    assert result.errors == 0


class _StubRegistryAdapter(SourceAdapter):
    """Отдаёт одну закупку под реестровым номером ЕИС.

    Наименование и заказчик задаются на конструкторе: вторая площадка обычно знает о той же
    закупке не то же самое, и тест проверяет, что чужие данные дозаполняют пустые поля, а не
    переписывают заполненные.
    """

    source_key = "stub_registry"

    def __init__(self, registry_number: str, *, title: str, customer: str | None = None):
        self._registry_number = registry_number
        self._title = title
        self._customer = customer

    def list_new_tenders(self, since):
        return PollOutcome(
            tenders=[
                TenderSummary(
                    external_id=self._registry_number,
                    registry_number=self._registry_number,
                    title=self._title,
                    customer_name=self._customer,
                    source_url="https://example.test/registry",
                )
            ]
        )

    def get_tender_details(self, external_id):
        raise NotImplementedError

    def download_documents(self, external_id):
        raise NotImplementedError


def _unique_registry_number() -> str:
    """Свежий 19-значный номер на каждый прогон.

    Тесты опроса пишут в базу через собственную сессию и коммитят, а база между прогонами не
    очищается: фиксированный номер на втором запуске находил бы запись, оставшуюся от
    первого, и проверка дедупликации проходила бы по чужим данным.
    """

    return f"01583000123{uuid.uuid4().int % 100_000_000:08d}"


def _make_source(db, adapter_key: str) -> Source:
    source = Source(
        key=f"{adapter_key}_{uuid.uuid4().hex[:8]}",
        name="Тестовый источник",
        url="https://example.test",
        type="etp_federal_commercial",
        adapter_key=adapter_key,
        adapter_status="implemented",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def test_poll_dedups_same_purchase_across_sources(monkeypatch):
    """Одна закупка с двух площадок — одна запись (раздел 5.1 ТЗ).

    Реестровый номер ЕИС — единственный идентификатор, общий для источников: без этой
    ступени дедупликации закупка попала бы в список дважды, и AI-оценка считалась бы по ней
    два раза.
    """

    import app.services.tender_service as tender_service_module
    from app.db.session import SessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select as sa_select

    registry_number = _unique_registry_number()
    db = SessionLocal()
    try:
        eis = _make_source(db, "stub_eis")
        etp = _make_source(db, "stub_etp")

        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(
                registry_number, title="Поставка приборов учёта", customer="МУП «Водоканал»"
            ),
        )
        first = poll_source(db, eis)

        # Вторая площадка знает ту же закупку, но заказчика не публикует.
        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(registry_number, title="Поставка ПУ (ЭТП)"),
        )
        second = poll_source(db, etp)

        rows = db.scalars(
            sa_select(Tender).where(Tender.registry_number == registry_number)
        ).all()
        assert len(rows) == 1
        assert rows[0].source_id == eis.id
        # Наименование первой площадки не перетёрто вариантом второй.
        assert rows[0].title == "Поставка приборов учёта"
    finally:
        db.close()

    assert first.created == 1
    assert second.created == 0 and second.updated == 1


def test_cross_source_dedup_fills_only_empty_fields(monkeypatch):
    """Вторая площадка дозаполняет пустое, но не переписывает уже известное."""

    import app.services.tender_service as tender_service_module
    from app.db.session import SessionLocal
    from app.models.tender import Tender
    from sqlalchemy import select as sa_select

    registry_number = _unique_registry_number()
    db = SessionLocal()
    try:
        first_source = _make_source(db, "stub_first")
        second_source = _make_source(db, "stub_second")

        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(registry_number, title="Закупка без заказчика"),
        )
        poll_source(db, first_source)

        monkeypatch.setattr(
            tender_service_module,
            "get_adapter",
            lambda key, **kwargs: _StubRegistryAdapter(
                registry_number, title="Другое наименование", customer="ГУП «Энергосбыт»"
            ),
        )
        poll_source(db, second_source)

        row = db.scalars(
            sa_select(Tender).where(Tender.registry_number == registry_number)
        ).one()
        assert row.title == "Закупка без заказчика"
        assert row.customer_name == "ГУП «Энергосбыт»"
    finally:
        db.close()


def test_registry_number_is_derived_from_eis_style_external_id(monkeypatch):
    """19-значный идентификатор сам является реестровым номером.

    Часть площадок публикует закупку под номером ЕИС, не выделяя его отдельным полем. Ждать,
    пока каждый адаптер научится его отдавать, значит оставить дедупликацию между
    источниками неработающей ровно там, где она нужнее всего.
    """

    import app.services.tender_service as tender_service_module
    from app.adapters.base import TenderSummary as Summary

    summary = Summary(
        external_id="0158300012325000999",
        title="Закупка",
        source_url="https://example.test",
    )
    assert tender_service_module._registry_number(summary) == "0158300012325000999"

    internal = Summary(external_id="etpgpb-4711", title="Закупка", source_url="https://e.test")
    assert tender_service_module._registry_number(internal) is None
