"""Несколько компаний в разделе «Моя компания» (решение 07.09.2026).

Проверяется главное, ради чего вводился признак `is_primary`: сколько бы карточек ни
завели, «наша компания» для AI-оценки и синхронизации истории участий остаётся ровно одна, и
случайно остаться без неё нельзя — ни удалением, ни добавлением.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.company_profile import CompanyProfile
from app.services import company_profile_service


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _clear_profiles() -> None:
    with SessionLocal() as db:
        for profile in db.scalars(select(CompanyProfile)):
            db.delete(profile)
        db.commit()


@pytest.fixture(autouse=True)
def _clean_companies():
    """Тесты здесь ходят по HTTP и коммитят в общую тестовую базу, а не в откатываемую
    сессию. Компании убираются и до, и после: оставленная основная компания без привязки к
    производителю ломает соседние тесты, которые ждут автосозданный профиль МИРТЕК."""

    _clear_profiles()
    yield
    _clear_profiles()


def test_first_company_becomes_primary(client, admin_token):
    """Первая заведённая компания — основная сразу.

    Иначе система стояла бы с заполненным профилем и всё равно не считала «Задачу» и
    «Компетенции»: им нужна именно основная компания.
    """

    _clear_profiles()
    response = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Первая»", "inn": "7700000001"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 201, response.text
    assert response.json()["is_primary"] is True

    second = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Вторая»", "inn": "7700000002"},
        headers=_auth_headers(admin_token),
    )
    assert second.status_code == 201, second.text
    # Вторая основной не становится: смена основной меняет входы оценки по всем тендерам
    # и делается отдельным действием.
    assert second.json()["is_primary"] is False


def test_list_returns_primary_first(client, admin_token):
    _clear_profiles()
    for name in ("ООО «Раз»", "ООО «Два»", "ООО «Три»"):
        client.post(
            "/company-profiles",
            json={"legal_name": name},
            headers=_auth_headers(admin_token),
        )
    listed = client.get("/company-profiles", headers=_auth_headers(admin_token))
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert len(rows) == 3
    assert rows[0]["is_primary"] is True
    assert [row["legal_name"] for row in rows] == ["ООО «Раз»", "ООО «Два»", "ООО «Три»"]


def test_switching_primary_leaves_exactly_one(client, admin_token):
    _clear_profiles()
    first = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Раз»"},
        headers=_auth_headers(admin_token),
    ).json()
    second = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Два»"},
        headers=_auth_headers(admin_token),
    ).json()

    switched = client.post(
        f"/company-profiles/{second['id']}/primary", headers=_auth_headers(admin_token)
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["is_primary"] is True

    rows = client.get("/company-profiles", headers=_auth_headers(admin_token)).json()
    assert [row["id"] for row in rows if row["is_primary"]] == [second["id"]]
    assert rows[0]["id"] == second["id"]

    # И то же самое глазами расчёта: он спрашивает одну компанию, а не список.
    with SessionLocal() as db:
        assert str(company_profile_service.get_profile(db).id) == second["id"]
    assert first["id"] != second["id"]


def test_primary_company_cannot_be_deleted(client, admin_token):
    """Удалить основную нельзя, пока роль не передана: без неё молча отключаются два
    измерения AI-оценки из трёх."""

    _clear_profiles()
    primary = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Основная»"},
        headers=_auth_headers(admin_token),
    ).json()
    spare = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Запасная»"},
        headers=_auth_headers(admin_token),
    ).json()

    refused = client.delete(
        f"/company-profiles/{primary['id']}", headers=_auth_headers(admin_token)
    )
    assert refused.status_code == 400
    assert "основная" in refused.json()["detail"].lower()

    # Неосновную — пожалуйста.
    removed = client.delete(
        f"/company-profiles/{spare['id']}", headers=_auth_headers(admin_token)
    )
    assert removed.status_code == 204
    rows = client.get("/company-profiles", headers=_auth_headers(admin_token)).json()
    assert [row["id"] for row in rows] == [primary["id"]]


def test_update_by_id_touches_only_that_company(client, admin_token):
    _clear_profiles()
    first = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Раз»", "years_of_experience": 5},
        headers=_auth_headers(admin_token),
    ).json()
    second = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Два»", "years_of_experience": 9},
        headers=_auth_headers(admin_token),
    ).json()

    updated = client.put(
        f"/company-profiles/{second['id']}",
        json={
            "years_of_experience": 12,
            "licenses": [{"name": "ISO 9001"}],
        },
        headers=_auth_headers(admin_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["years_of_experience"] == 12
    assert updated.json()["is_filled"] is True

    rows = {row["id"]: row for row in client.get("/company-profiles", headers=_auth_headers(admin_token)).json()}
    assert rows[first["id"]]["years_of_experience"] == 5
    assert rows[first["id"]]["licenses"] == []


def test_unknown_company_is_404(client, admin_token):
    missing = client.put(
        f"/company-profiles/{uuid.uuid4()}",
        json={"legal_name": "ООО «Нет такой»"},
        headers=_auth_headers(admin_token),
    )
    assert missing.status_code == 404


def test_singular_endpoint_follows_primary(client, admin_token):
    """`/company-profile` остался «про основную»: на него опираются места, где компания
    должна быть одна, и их поведение от появления списка не изменилось."""

    _clear_profiles()
    client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Раз»"},
        headers=_auth_headers(admin_token),
    )
    second = client.post(
        "/company-profiles",
        json={"legal_name": "ООО «Два»", "inn": "7700000002"},
        headers=_auth_headers(admin_token),
    ).json()
    client.post(
        f"/company-profiles/{second['id']}/primary", headers=_auth_headers(admin_token)
    )

    current = client.get("/company-profile", headers=_auth_headers(admin_token))
    assert current.status_code == 200, current.text
    assert current.json()["id"] == second["id"]
    assert current.json()["has_inn"] is True
