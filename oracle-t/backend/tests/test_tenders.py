import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.adapters.base import PollOutcome, SourceAdapter, TenderSummary
from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_source_with_tender(db) -> tuple[Source, Tender]:
    source = Source(
        key=f"tendertest_{uuid.uuid4().hex[:8]}",
        name="Тестовая площадка",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    tender = Tender(
        source_id=source.id,
        external_id="EXT-1",
        title="Поставка счётчиков электрической энергии",
        status="collecting_bids",
        currency="RUB",
        source_url="https://example.test/tender/1",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return source, tender


def _make_tender(db, source, *, external_id, title, price=None, deadline=None, publish_date=None, customer_name=None):
    tender = Tender(
        source_id=source.id,
        external_id=external_id,
        title=title,
        customer_name=customer_name,
        status="collecting_bids",
        currency="RUB",
        price=price,
        application_end=deadline,
        publish_date=publish_date,
        source_url="https://example.test/tender",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


def test_tenders_list_requires_auth(client):
    assert client.get("/tenders").status_code == 401


def test_tenders_list_includes_source_name(client, admin_token):
    db = SessionLocal()
    try:
        source, tender = _make_source_with_tender(db)
    finally:
        db.close()

    response = client.get("/tenders", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    row = next(t for t in body["items"] if t["id"] == str(tender.id))
    assert row["source"]["name"] == source.name
    assert row["source_url"] == "https://example.test/tender/1"


def test_tenders_filter_by_search(client, admin_token):
    db = SessionLocal()
    try:
        source = Source(
            key=f"searchtest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        matching = _make_tender(db, source, external_id="A-1", title="Поставка счётчиков воды")
        matching_id = str(matching.id)  # читаем сразу — следующий commit() истощит атрибуты объекта
        _make_tender(db, source, external_id="A-2", title="Ремонт медицинской техники")
    finally:
        db.close()

    response = client.get(
        "/tenders", params={"search": "счётчиков"}, headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    body = response.json()["items"]
    ids = {t["id"] for t in body}
    assert matching_id in ids
    assert all(
        "счётчиков" in t["title"].lower() or "счётчиков" in (t["customer_name"] or "").lower()
        for t in body
    )


def test_tenders_filter_by_source(client, admin_token):
    db = SessionLocal()
    try:
        source_a = Source(
            key=f"srcA_{uuid.uuid4().hex[:8]}", name="Площадка А", url="https://a.test",
            type="etp_federal_commercial",
        )
        source_b = Source(
            key=f"srcB_{uuid.uuid4().hex[:8]}", name="Площадка Б", url="https://b.test",
            type="etp_federal_commercial",
        )
        db.add_all([source_a, source_b])
        db.commit()
        db.refresh(source_a)
        db.refresh(source_b)
        tender_a = _make_tender(db, source_a, external_id="B-1", title="Тендер А")
        tender_a_id = str(tender_a.id)
        source_a_key = source_a.key
        _make_tender(db, source_b, external_id="B-2", title="Тендер Б")
    finally:
        db.close()

    response = client.get(
        "/tenders", params={"source": source_a_key}, headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    body = response.json()["items"]
    assert all(t["source"]["key"] == source_a_key for t in body)
    assert any(t["id"] == tender_a_id for t in body)


def test_tenders_filter_by_price_range(client, admin_token):
    db = SessionLocal()
    try:
        source = Source(
            key=f"pricetest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        cheap = _make_tender(db, source, external_id="C-1", title="Дешёвый", price=Decimal("1000"))
        cheap_id = str(cheap.id)
        mid = _make_tender(db, source, external_id="C-2", title="Средний", price=Decimal("50000"))
        mid_id = str(mid.id)
        expensive = _make_tender(db, source, external_id="C-3", title="Дорогой", price=Decimal("1000000"))
        expensive_id = str(expensive.id)
    finally:
        db.close()

    response = client.get(
        "/tenders",
        params={"price_min": "10000", "price_max": "100000"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["items"]}
    assert mid_id in ids
    assert cheap_id not in ids
    assert expensive_id not in ids


def test_tenders_hide_expired(client, admin_token):
    db = SessionLocal()
    try:
        source = Source(
            key=f"expiredtest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        past = _make_tender(
            db, source, external_id="D-1", title="Просроченный",
            deadline=datetime.now(timezone.utc) - timedelta(days=5),
        )
        past_id = str(past.id)
        future = _make_tender(
            db, source, external_id="D-2", title="Актуальный",
            deadline=datetime.now(timezone.utc) + timedelta(days=5),
        )
        future_id = str(future.id)
        no_deadline = _make_tender(db, source, external_id="D-3", title="Без срока")
        no_deadline_id = str(no_deadline.id)
    finally:
        db.close()

    response = client.get(
        "/tenders", params={"hide_expired": "true"}, headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["items"]}
    assert past_id not in ids
    assert future_id in ids
    assert no_deadline_id in ids  # неизвестный срок — не скрываем


def test_hide_expired_drops_finished_tenders_without_deadline(client, admin_token):
    """Площадки массово отдают закупки без срока подачи, и почти все они уже завершены.
    Проверка одной только даты пропускала их в выдачу, и при включённой галочке в списке
    висели «Завершено» — колонка набиралась именно такими записями."""

    db = SessionLocal()
    try:
        source = Source(
            key=f"finishedtest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_key = source.key

        done = _make_tender(db, source, external_id="F-1", title="Завершён без срока")
        done.status = "completed"
        cancelled = _make_tender(db, source, external_id="F-2", title="Отменён без срока")
        cancelled.status = "cancelled"
        open_one = _make_tender(db, source, external_id="F-3", title="Сбор заявок без срока")
        db.commit()
        done_id, cancelled_id, open_id = str(done.id), str(cancelled.id), str(open_one.id)
    finally:
        db.close()

    response = client.get(
        "/tenders",
        params={"hide_expired": "true", "source": source_key},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["items"]}
    assert done_id not in ids
    assert cancelled_id not in ids
    assert open_id in ids  # неизвестный срок без финального статуса — не скрываем


def test_status_counts_cover_whole_selection_not_just_the_page(client, admin_token):
    """Счётчики колонок Kanban считаются по всей выборке. Раньше их считал фронтенд по
    полученной странице, и сумма по колонкам всегда равнялась её размеру — из-за чего
    выдача в сотни тендеров выглядела как ровно пятьдесят."""

    db = SessionLocal()
    try:
        source = Source(
            key=f"countstest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_key = source.key

        for index in range(3):
            _make_tender(db, source, external_id=f"C-{index}", title=f"Сбор заявок {index}")
        evaluated = _make_tender(db, source, external_id="C-EV", title="На оценке")
        evaluated.status = "evaluation"
        unclassified = _make_tender(db, source, external_id="C-UN", title="Без статуса")
        unclassified.status = None
        db.commit()
    finally:
        db.close()

    response = client.get(
        "/tenders",
        params={"source": source_key, "limit": 1},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    page = response.json()
    assert len(page["items"]) == 1  # страница действительно короче выборки
    assert page["status_counts"] == {"collecting_bids": 3, "evaluation": 1, "unclassified": 1}
    assert sum(page["status_counts"].values()) == page["total"]


def test_board_columns_are_filled_independently_of_each_other(client, admin_token):
    """Колонка Kanban набирается своей выборкой, а не срезом общей страницы.

    Раньше доска брала страницу списка: при сортировке по сроку подачи по возрастанию её
    занимали давно закрытые закупки, и «отобранные AI» на доску просто не попадали, хотя в
    выборке были. Здесь это воспроизведено в миниатюре — `per_column=1` при трёх отклонённых
    тендерах с более ранним сроком, чем у единственного свежего.

    Колонки — этапы пайплайна (`stage`), а не статусы площадки (раздел 5.6 ТЗ,
    решение 03.09.2026)."""

    db = SessionLocal()
    try:
        source = Source(
            key=f"boardtest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_key = source.key

        now = datetime.now(timezone.utc)
        for index in range(3):
            old = _make_tender(
                db, source, external_id=f"B-OLD-{index}", title=f"Давно завершён {index}",
                deadline=now - timedelta(days=900 + index),
            )
            old.status = "completed"
            old.stage = "rejected"
        fresh = _make_tender(
            db, source, external_id="B-NEW", title="Приём заявок идёт",
            deadline=now + timedelta(days=7),
        )
        db.commit()
        fresh_id = str(fresh.id)
    finally:
        db.close()

    response = client.get(
        "/tenders/board",
        params={
            "source": source_key,
            "per_column": 1,
            "sort_by": "application_end",
            "sort_dir": "asc",
        },
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    board = response.json()

    # Свежий тендер стоит в выдаче по сроку подачи последним, но свою колонку занимает.
    assert [t["id"] for t in board["columns"]["ai_selected"]] == [fresh_id]
    assert len(board["columns"]["rejected"]) == 1  # обрезано по per_column, а не потеряно
    assert board["counts"] == {"ai_selected": 1, "rejected": 3}
    assert board["total"] == 4


def test_board_respects_hide_expired_without_emptying_open_column(client, admin_token):
    """Снятие галочки «скрывать закрытые» обязано только ДОБАВЛЯТЬ тендеры.

    На доске выходило наоборот: без фильтра колонку вытесняли закрытые закупки, и снятие
    галочки её опустошало. Фильтр по-прежнему работает по `status` площадки — это состояние
    закупки, а не наш этап, — а колонки собираются по `stage`."""

    db = SessionLocal()
    try:
        source = Source(
            key=f"boardhide_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_key = source.key

        now = datetime.now(timezone.utc)
        for index in range(5):
            done = _make_tender(
                db, source, external_id=f"H-{index}", title=f"Завершён {index}",
                deadline=now - timedelta(days=500 + index),
            )
            done.status = "completed"
            done.stage = "lost"
        _make_tender(
            db, source, external_id="H-OPEN", title="Приём заявок",
            deadline=now + timedelta(days=3),
        )
        db.commit()
    finally:
        db.close()

    def board(hide_expired: bool) -> dict:
        response = client.get(
            "/tenders/board",
            params={
                "source": source_key,
                "sort_by": "application_end",
                "sort_dir": "asc",
                "hide_expired": str(hide_expired).lower(),
            },
            headers=_auth_headers(admin_token),
        )
        assert response.status_code == 200
        return response.json()

    with_filter = board(True)
    without_filter = board(False)

    assert with_filter["counts"].get("ai_selected") == 1
    assert with_filter["counts"].get("lost", 0) == 0  # закрытые скрыты
    # Снятие фильтра открытую колонку не трогает и возвращает закрытые.
    assert without_filter["counts"].get("ai_selected") == 1
    assert without_filter["counts"].get("lost") == 5
    assert len(without_filter["columns"]["ai_selected"]) == 1


def test_tenders_filter_by_deadline_range(client, admin_token):
    db = SessionLocal()
    try:
        source = Source(
            key=f"deadlinetest_{uuid.uuid4().hex[:8]}", name="Тест", url="https://example.test",
            type="etp_federal_commercial",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        today = date.today()
        in_range = _make_tender(
            db, source, external_id="E-1", title="В диапазоне",
            deadline=datetime.combine(today + timedelta(days=3), datetime.min.time(), tzinfo=timezone.utc),
        )
        in_range_id = str(in_range.id)
        out_of_range = _make_tender(
            db, source, external_id="E-2", title="Вне диапазона",
            deadline=datetime.combine(today + timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc),
        )
        out_of_range_id = str(out_of_range.id)
    finally:
        db.close()

    response = client.get(
        "/tenders",
        params={
            "deadline_from": today.isoformat(),
            "deadline_to": (today + timedelta(days=7)).isoformat(),
        },
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    ids = {t["id"] for t in response.json()["items"]}
    assert in_range_id in ids
    assert out_of_range_id not in ids


def test_tenders_stats(client, admin_token):
    db = SessionLocal()
    try:
        _make_source_with_tender(db)
    finally:
        db.close()

    response = client.get("/tenders/stats", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["by_status"].get("collecting_bids", 0) >= 1


class _StubAdapter(SourceAdapter):
    source_key = "stub"

    def list_new_tenders(self, since):
        return PollOutcome(
            tenders=[
                TenderSummary(external_id="S-1", title="Тендер 1", source_url="https://example.test/1"),
            ]
        )

    def get_tender_details(self, external_id):
        raise NotImplementedError

    def download_documents(self, external_id):
        raise NotImplementedError


def test_poll_sources_bulk_any_authenticated_user(client, admin_token, monkeypatch):
    """В отличие от поточечного /sources/{id}/poll в «Настройках» (только admin), массовый
    опрос выбранных источников со страницы тендеров доступен любому вошедшему пользователю."""

    import app.services.tender_service as tender_service_module

    monkeypatch.setattr(
        tender_service_module, "get_adapter", lambda key, **kwargs: _StubAdapter()
    )

    db = SessionLocal()
    try:
        source = Source(
            key=f"bulkstub_{uuid.uuid4().hex[:8]}",
            name="Bulk stub",
            url="https://example.test",
            type="etp_federal_commercial",
            adapter_key="stub",
            adapter_status="implemented",
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_key = source.key
    finally:
        db.close()

    username = f"syncuser_{uuid.uuid4().hex[:8]}"
    client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "S", "role": "user"},
        headers=_auth_headers(admin_token),
    )
    user_token = client.post(
        "/auth/login", json={"username": username, "password": "SomePass123"}
    ).json()["access_token"]

    response = client.post(
        "/sources/poll",
        json={"source_keys": [source_key, "unknown_key"]},
        headers=_auth_headers(user_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1  # неизвестный ключ молча пропущен
    assert body[0]["source_key"] == source_key
    assert body[0]["created"] == 1
