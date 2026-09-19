"""Теги закупок и настройки своей учётной записи (замечания 17.09.2026).

Теги общие для команды: созданный одним виден другому и ставится на закупку полным
набором — сервер сам считает разницу и пишет её в историю. Учётная запись: имя и аватар
пользователь меняет сам, логин и пароль через `/auth/me` не меняются.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_tender(title: str) -> Tender:
    db = SessionLocal()
    try:
        source = Source(
            key=f"tg_{uuid.uuid4().hex[:8]}", name="Площадка", url="https://example.test", type="eis"
        )
        db.add(source)
        db.flush()
        tender = Tender(
            source_id=source.id,
            external_id=f"TG-{uuid.uuid4().hex[:8]}",
            title=title,
            status="collecting_bids",
            currency="RUB",
            source_url="https://example.test/t",
            application_end=datetime.now(timezone.utc) + timedelta(days=3),
        )
        db.add(tender)
        db.commit()
        db.refresh(tender)
        return tender
    finally:
        db.close()


# ------------------------------------------------------------------------------------ теги


def test_tags_crud_and_assignment(client, admin_token):
    name = f"срочно-{uuid.uuid4().hex[:6]}"
    created = client.post("/tags", json={"name": f"  {name}  ", "color": "red"}, headers=_auth(admin_token))
    assert created.status_code == 201, created.text
    tag = created.json()
    assert tag["name"] == name
    assert tag["color"] == "red"

    # Имя уникально без учёта регистра.
    dup = client.post("/tags", json={"name": name.upper()}, headers=_auth(admin_token))
    assert dup.status_code == 409

    # Неизвестный цвет — ошибка, а не молчаливый zinc.
    bad = client.post("/tags", json={"name": f"x-{uuid.uuid4().hex[:6]}", "color": "neon"}, headers=_auth(admin_token))
    assert bad.status_code == 409

    assert any(t["id"] == tag["id"] for t in client.get("/tags", headers=_auth(admin_token)).json())

    tender = _make_tender("Поставка счётчиков с тегами")
    put = client.put(f"/tenders/{tender.id}/tags", json={"tag_ids": [tag["id"]]}, headers=_auth(admin_token))
    assert put.status_code == 200, put.text
    assert [t["name"] for t in put.json()["tags"]] == [name]

    # Список и фильтр по тегу видят метку.
    listed = client.get(f"/tenders?tag={tag['id']}&limit=1000", headers=_auth(admin_token)).json()
    row = next(r for r in listed["items"] if r["id"] == str(tender.id))
    assert [t["id"] for t in row["tags"]] == [tag["id"]]

    # История получила запись о смене тегов.
    history = client.get(f"/tenders/{tender.id}/history", headers=_auth(admin_token)).json()
    assert any(entry.get("field_name") == "tags" for entry in history)

    # Пустой набор снимает всё; удаление тега снимает его с закупок каскадом.
    cleared = client.put(f"/tenders/{tender.id}/tags", json={"tag_ids": []}, headers=_auth(admin_token))
    assert cleared.json()["tags"] == []
    client.put(f"/tenders/{tender.id}/tags", json={"tag_ids": [tag["id"]]}, headers=_auth(admin_token))
    assert client.delete(f"/tags/{tag['id']}", headers=_auth(admin_token)).status_code == 204
    assert client.get(f"/tenders/{tender.id}", headers=_auth(admin_token)).json()["tags"] == []

    # Ссылка на удалённый тег — 422, а не 500.
    gone = client.put(f"/tenders/{tender.id}/tags", json={"tag_ids": [tag["id"]]}, headers=_auth(admin_token))
    assert gone.status_code == 422


def test_tag_rename_keeps_links(client, admin_token):
    tag = client.post("/tags", json={"name": f"Россети-{uuid.uuid4().hex[:6]}"}, headers=_auth(admin_token)).json()
    tender = _make_tender("Закупка Россетей")
    client.put(f"/tenders/{tender.id}/tags", json={"tag_ids": [tag["id"]]}, headers=_auth(admin_token))

    renamed = client.patch(f"/tags/{tag['id']}", json={"name": "ЗАК Россети", "color": "sky"}, headers=_auth(admin_token))
    assert renamed.status_code == 200, renamed.text
    card = client.get(f"/tenders/{tender.id}", headers=_auth(admin_token)).json()
    assert card["tags"][0]["name"] == "ЗАК Россети"
    assert card["tags"][0]["color"] == "sky"
    client.delete(f"/tags/{tag['id']}", headers=_auth(admin_token))


# --------------------------------------------------------------------------- учётная запись


def test_me_rename_and_avatar(client, admin_token):
    me = client.get("/auth/me", headers=_auth(admin_token)).json()
    original_name = me["full_name"]
    assert me["avatar_updated_at"] is None or isinstance(me["avatar_updated_at"], str)

    renamed = client.patch("/auth/me", json={"full_name": "  Иван   Петров "}, headers=_auth(admin_token))
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["full_name"] == "Иван Петров"
    # Логин через /auth/me не меняется — поля просто нет в схеме.
    assert renamed.json()["username"] == me["username"]

    # Аватар: не картинка — отказ.
    bad = client.put(
        "/auth/me/avatar",
        files={"file": ("avatar.txt", b"hello", "text/plain")},
        headers=_auth(admin_token),
    )
    assert bad.status_code == 422

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ok = client.put(
        "/auth/me/avatar",
        files={"file": ("avatar.png", png, "image/png")},
        headers=_auth(admin_token),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["avatar_updated_at"] is not None

    served = client.get("/auth/me/avatar", headers=_auth(admin_token))
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content == png

    by_id = client.get(f"/users/{me['id']}/avatar", headers=_auth(admin_token))
    assert by_id.status_code == 200
    assert by_id.content == png

    cleared = client.delete("/auth/me/avatar", headers=_auth(admin_token))
    assert cleared.json()["avatar_updated_at"] is None
    assert client.get("/auth/me/avatar", headers=_auth(admin_token)).status_code == 404

    client.patch("/auth/me", json={"full_name": original_name}, headers=_auth(admin_token))
