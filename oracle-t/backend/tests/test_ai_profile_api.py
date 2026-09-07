"""HTTP-контракт нового функционала 03.09.2026: профиль компании, AI-оценка, этапы.

Проверяется то, чего не видно из сервисных тестов: что незаполненный профиль отказывает
понятным текстом ДО постановки фоновой задачи, что этап меняется отдельной ручкой и
попадает в историю, и что старое поле `relevance_status` по-прежнему приходит в ответе,
хотя колонки в БД больше нет.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.ai_profile import AiProfileScore, Verdict
from app.models.company_profile import CompanyProfile
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import Tender


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_tender(db) -> Tender:
    source = Source(
        key=f"aiapi_{uuid.uuid4().hex[:8]}",
        name="Площадка для теста API",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    tender = Tender(
        source_id=source.id,
        external_id=f"API-{uuid.uuid4().hex[:6]}",
        title="Поставка приборов учёта",
        currency="RUB",
        status="collecting_bids",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


def _clear_profile(db) -> None:
    for profile in db.scalars(select(CompanyProfile)):
        db.delete(profile)
    db.commit()


def test_company_profile_roundtrip(client, admin_token):
    response = client.put(
        "/company-profile",
        json={
            "years_of_experience": 18,
            "licenses": [{"name": "СРО на проектирование", "number": "СРО-П-001"}],
            "past_projects": [{"work_type": "Поставка приборов учёта", "year": 2024}],
        },
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["years_of_experience"] == 18
    assert saved["is_filled"] is True

    fetched = client.get("/company-profile", headers=_auth_headers(admin_token)).json()
    assert fetched["licenses"][0]["name"] == "СРО на проектирование"
    # Профиль привязан к записи МИРТЕК, а не висит сам по себе.
    db = SessionLocal()
    try:
        mirtek = db.get(Manufacturer, uuid.UUID(fetched["manufacturer_id"]))
        assert mirtek is not None and mirtek.is_mirtek
    finally:
        db.close()


def test_ai_score_refuses_without_filled_profile(client, admin_token):
    """Пустой профиль — понятная подсказка сразу, а не упавшая через минуту фоновая задача."""

    db = SessionLocal()
    try:
        _clear_profile(db)
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.post(f"/tenders/{tender_id}/ai-score", headers=_auth_headers(admin_token))
    assert response.status_code == 400
    assert "Профиль компании" in response.json()["detail"]


def test_ai_score_endpoint_returns_null_until_calculated(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.get(f"/tenders/{tender_id}/ai-score", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    assert response.json() is None


def test_ai_score_is_exposed_in_list_and_card(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db)
        db.add(
            AiProfileScore(
                tender_id=tender.id,
                task_score=Decimal("88.00"),
                competencies_score=Decimal("92.00"),
                overall_score=Decimal("90.00"),
                verdict=Verdict.GO.value,
                weak_points=[{"severity": "moderate", "text": "Короткий срок подачи"}],
            )
        )
        db.commit()
        tender_id = str(tender.id)
    finally:
        db.close()

    card = client.get(f"/tenders/{tender_id}", headers=_auth_headers(admin_token)).json()
    assert card["ai_score"] == "90.00"
    assert card["ai_verdict"] == "go"
    # Старое поле продолжает приходить, хотя колонки в БД уже нет (раздел 7 ТЗ).
    assert card["relevance_status"] == "new"
    assert card["stage"] == "ai_selected"

    score = client.get(
        f"/tenders/{tender_id}/ai-score", headers=_auth_headers(admin_token)
    ).json()
    assert score["verdict_label"] == "ИДТИ"
    assert score["weak_points"][0]["severity_label"] == "умеренно"
    assert score["history_score"] is None  # «нет данных», а не ноль


def test_stage_change_is_recorded_in_history(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.patch(
        f"/tenders/{tender_id}/stage",
        json={"stage": "application_submitted"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["stage"] == "application_submitted"
    # Поданная заявка для внешнего API — по-прежнему «подтверждённый» тендер.
    assert response.json()["relevance_status"] == "confirmed"

    history = client.get(
        f"/tenders/{tender_id}/history", headers=_auth_headers(admin_token)
    ).json()
    assert any(entry["field_name"] == "stage" for entry in history)

    bad = client.patch(
        f"/tenders/{tender_id}/stage",
        json={"stage": "не-этап"},
        headers=_auth_headers(admin_token),
    )
    assert bad.status_code == 422


def test_extra_sections_return_all_nine_even_when_empty(client, admin_token):
    """Пустой раздел приходит пустым списком, а не пропадает: «в документации об этом не
    сказано» — это сведение (раздел 5.6 ТЗ)."""

    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.get(
        f"/tenders/{tender_id}/extra-sections", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["sections"]) == 9
    assert body["generated_at"] is None
    assert all(section["points"] == [] for section in body["sections"])


def test_niche_statistics_and_similar_are_honest_when_empty(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    niche = client.get(
        f"/tenders/{tender_id}/niche-statistics", headers=_auth_headers(admin_token)
    )
    assert niche.status_code == 200 and niche.json() is None

    similar = client.get(f"/tenders/{tender_id}/similar", headers=_auth_headers(admin_token))
    assert similar.status_code == 200 and similar.json() == []


def test_ai_score_range_filter(client, admin_token):
    db = SessionLocal()
    try:
        low = _make_tender(db)
        high = _make_tender(db)
        db.add(AiProfileScore(tender_id=low.id, overall_score=Decimal("40.00")))
        db.add(AiProfileScore(tender_id=high.id, overall_score=Decimal("95.00")))
        db.commit()
        low_id, high_id = str(low.id), str(high.id)
    finally:
        db.close()

    response = client.get(
        "/tenders?ai_score_min=80&limit=1000", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert high_id in ids and low_id not in ids
