import uuid

from app.adapters.fgis import SiSearchResult
from app.db.session import SessionLocal
from app.models.manufacturer import Manufacturer
from app.seed.manufacturers_data import MANUFACTURERS, MANUFACTURERS_ADDED_2026_09, MARKET_SHARES_2024


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_regular_user(client, admin_token) -> str:
    username = f"manuftest_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "T", "role": "user"},
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/auth/login", json={"username": username, "password": "SomePass123"})
    return login.json()["access_token"]


def _make_test_manufacturer(db) -> Manufacturer:
    manufacturer = Manufacturer(legal_name=f'ООО «Тест {uuid.uuid4().hex[:8]}»', is_mirtek=False)
    db.add(manufacturer)
    db.commit()
    db.refresh(manufacturer)
    return manufacturer


class _StubFgisAdapter:
    def __init__(self, results: list[SiSearchResult]) -> None:
        self._results = results

    def search_by_manufacturer(
        self, legal_name: str, *, brand_name: str | None = None, fetch_cards: bool = True
    ) -> list[SiSearchResult]:
        return self._results


def test_search_flags_si_types_that_are_not_electricity_meters(client, admin_token, monkeypatch):
    """Реестр возвращает по производителю всё, что тот утверждал: у МИРТЕК из 28 типов
    электросчётчиков лишь половина, остальное — теплосчётчики, счётчики воды и газа, УСПД.
    Такие записи не удаляются (они законные), но помечаются: справочник проекта ограничен
    электросчётчиками, и к моделям каталога они не привязываются (п.1.2 задания)."""

    from app.db.session import SessionLocal
    from app.services import product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
    finally:
        db.close()

    meter = SiSearchResult(
        si_code="61891-15",
        type_name="Счетчики электрической энергии однофазные многофункциональные",
        notation="ТЕСТ-12-РУ",
        manufacturer_name=manufacturer.legal_name,
    )
    heat_meter = SiSearchResult(
        si_code="64908-16",
        type_name="Теплосчетчики",
        notation="ТЕСТ-42-РУ",
        manufacturer_name=manufacturer.legal_name,
    )
    monkeypatch.setattr(
        catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([meter, heat_meter])
    )

    resp = client.post(
        f"/manufacturers/{manufacturer.id}/si-types/search", headers=_auth_headers(admin_token)
    )
    assert resp.status_code == 200, resp.text
    by_code = {item["si_code"]: item for item in resp.json()}

    assert by_code["61891-15"]["review_status"] == "ok"
    assert by_code["64908-16"]["review_status"] == "needs_review"
    assert "теплосчётчик" in by_code["64908-16"]["review_reason"]


def test_manufacturers_list_requires_auth(client):
    assert client.get("/manufacturers").status_code == 401


def test_manufacturers_list_includes_seeded_13(client, admin_token):
    # Не проверяем len(body) == 13 строго: тестовая БД — персистентная Postgres, другие тесты
    # этого файла создают собственных временных производителей и не удаляют их (та же причина,
    # что и в test_integrations.py) — сравниваем множества названий, это устойчиво к мусору
    # от прошлых прогонов.
    resp = client.get("/manufacturers", headers=_auth_headers(admin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    legal_names = {m["legal_name"] for m in body}
    assert legal_names.issuperset(legal_name for legal_name, *_ in MANUFACTURERS)
    assert legal_names.issuperset(legal_name for legal_name, *_ in MANUFACTURERS_ADDED_2026_09)
    assert sum(1 for m in body if m["is_mirtek"]) == 1


def test_manufacturers_ordered_by_market_share(client, admin_token):
    """Список — по убыванию доли рынка (замечание тестировщика 18.09.2026); у кого доля не
    опубликована — после всех, кто с долей. Источник оценки хранится рядом с цифрой."""
    resp = client.get("/manufacturers", headers=_auth_headers(admin_token))
    body = resp.json()
    with_share = [m for m in body if m["market_share_pct"] is not None]
    shares = [m["market_share_pct"] for m in with_share]
    assert shares == sorted(shares, reverse=True)
    first_without = next(i for i, m in enumerate(body) if m["market_share_pct"] is None)
    assert first_without == len(with_share)
    by_name = {m["legal_name"]: m for m in body}
    for legal_name, pct in MARKET_SHARES_2024.items():
        assert by_name[legal_name]["market_share_pct"] == pct
        assert by_name[legal_name]["market_share_source"]


def test_manufacturer_create_and_update_market_share(client, admin_token):
    name = f'ООО «Новый {uuid.uuid4().hex[:8]}»'
    created = client.post(
        "/manufacturers",
        json={"legal_name": name, "brand_name": "Новый", "website": "https://example.com/"},
        headers=_auth_headers(admin_token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["market_share_pct"] is None

    patched = client.patch(
        f"/manufacturers/{created.json()['id']}",
        json={"market_share_pct": 99.5, "market_share_source": "тест"},
        headers=_auth_headers(admin_token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["market_share_pct"] == 99.5
    assert patched.json()["brand_name"] == "Новый"  # не присланное поле не тронуто

    body = client.get("/manufacturers", headers=_auth_headers(admin_token)).json()
    assert body[0]["legal_name"] == name

    # Обычному пользователю запись запрещена.
    user_token = _create_regular_user(client, admin_token)
    assert client.post("/manufacturers", json={"legal_name": "x"}, headers=_auth_headers(user_token)).status_code == 403
    assert client.patch(
        f"/manufacturers/{created.json()['id']}", json={"brand_name": "y"}, headers=_auth_headers(user_token)
    ).status_code == 403


def test_si_types_search_requires_admin(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    user_token = _create_regular_user(client, admin_token)
    resp = client.post(
        f"/manufacturers/{manufacturer_id}/si-types/search", headers=_auth_headers(user_token)
    )
    assert resp.status_code == 403


def test_si_types_search_saves_results_unverified(client, admin_token, monkeypatch):
    import app.services.product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    stub = _StubFgisAdapter(
        [
            SiSearchResult(
                si_code="AB-123",
                type_name="Счётчик Тест",
                manufacturer_name="ООО «Тест»",
                description_type_url="https://fgis.gost.ru/doc/ab123.pdf",
                raw={},
            )
        ]
    )
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: stub)

    headers = _auth_headers(admin_token)
    resp = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["si_code"] == "AB-123"
    assert body[0]["source"] == "auto_search"
    assert body[0]["verified_by_user"] is False  # раздел 5.3 ТЗ — ждёт проверки человеком
    assert body[0]["has_description_type_text"] is False

    # список кодов СИ производителя отражает сохранённое
    list_resp = client.get(f"/manufacturers/{manufacturer_id}/si-types", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1


def test_si_types_search_upserts_without_duplicates(client, admin_token, monkeypatch):
    import app.services.product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    result = SiSearchResult(si_code="XY-1", type_name="Т1", manufacturer_name="М", description_type_url=None, raw={})
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([result]))

    headers = _auth_headers(admin_token)
    first = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    second = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    assert first.status_code == 200 and second.status_code == 200

    list_resp = client.get(f"/manufacturers/{manufacturer_id}/si-types", headers=headers)
    assert len(list_resp.json()) == 1  # не задублировалось при повторном автопоиске


def test_manual_confirmation_survives_repeated_auto_search(client, admin_token, monkeypatch):
    """Раздел 5.3 ТЗ: «ручной ввод имеет приоритет перед автопоиском при конфликте» —
    подтверждённая человеком запись не должна перезаписываться повторным автопоиском."""

    import app.services.product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    result = SiSearchResult(
        si_code="ZZ-1", type_name="Т", manufacturer_name="М", description_type_url="https://old.example/doc.pdf", raw={}
    )
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([result]))

    headers = _auth_headers(admin_token)
    first = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    si_type_id = first.json()[0]["id"]

    confirm = client.patch(f"/si-types/{si_type_id}", json={"verified_by_user": True}, headers=headers)
    assert confirm.status_code == 200
    assert confirm.json()["verified_by_user"] is True

    # повторный автопоиск с другой ссылкой на документ не должен тронуть подтверждённую запись
    result2 = SiSearchResult(
        si_code="ZZ-1", type_name="Т", manufacturer_name="М", description_type_url="https://new.example/doc.pdf", raw={}
    )
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([result2]))
    second = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    assert second.json()[0]["description_type_url"] == "https://old.example/doc.pdf"


def test_products_crud_and_manual_characteristics(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    headers = _auth_headers(admin_token)

    create = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "МИРТЕК-32-РУ", "device_type": "счётчик эл.энергии"},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    product_id = create.json()["id"]
    assert create.json()["status"] == "active"

    patch = client.patch(f"/products/{product_id}", json={"article": "АРТ-1"}, headers=headers)
    assert patch.status_code == 200
    assert patch.json()["article"] == "АРТ-1"
    assert patch.json()["model_name"] == "МИРТЕК-32-РУ"  # не затёрлось частичным PATCH

    # ручной ввод характеристики (раздел 5.3 ТЗ, источник 3)
    put = client.put(
        f"/products/{product_id}/characteristics",
        json={
            "group_name": "Электрические характеристики",
            "field_name": "Класс точности",
            "value": "1",
        },
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["source"] == "manual_entry"
    assert put.json()["verified_by_user"] is True  # ручной ввод сразу считается проверенным

    listed = client.get(f"/products/{product_id}/characteristics", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_manual_characteristic_rejects_field_outside_appendix_c(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    headers = _auth_headers(admin_token)
    product_id = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "Тест"},
        headers=headers,
    ).json()["id"]

    resp = client.put(
        f"/products/{product_id}/characteristics",
        json={"group_name": "Своя группа", "field_name": "Своё поле", "value": "x"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "Приложение C" in resp.json()["detail"]


def test_extraction_endpoint_requires_loaded_description_type(client, admin_token, monkeypatch):
    """Понятная 422 вместо 500, если текст «Описание типа» ещё не загружен."""

    import app.services.product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    headers = _auth_headers(admin_token)
    result = SiSearchResult(
        si_code="EX-1", type_name="Т", manufacturer_name="М", description_type_url="https://x/y.pdf", raw={}
    )
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([result]))
    si_type_id = client.post(
        f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers
    ).json()[0]["id"]

    product_id = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "Тест", "si_type_id": si_type_id},
        headers=headers,
    ).json()["id"]

    resp = client.post(f"/products/{product_id}/extract-characteristics/from-si-type", headers=headers)
    assert resp.status_code == 422
    assert "не загружен" in resp.json()["detail"]


def test_extraction_endpoint_requires_si_type_link(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    headers = _auth_headers(admin_token)
    product_id = client.post(
        f"/manufacturers/{manufacturer_id}/products", json={"model_name": "Без СИ"}, headers=headers
    ).json()["id"]

    resp = client.post(f"/products/{product_id}/extract-characteristics/from-si-type", headers=headers)
    assert resp.status_code == 422
    assert "не указан код СИ" in resp.json()["detail"]


def test_characteristic_verify_toggles_flag(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    headers = _auth_headers(admin_token)
    product_id = client.post(
        f"/manufacturers/{manufacturer_id}/products", json={"model_name": "Тест"}, headers=headers
    ).json()["id"]
    characteristic_id = client.put(
        f"/products/{product_id}/characteristics",
        json={"group_name": "Применение", "field_name": "Отрасли", "value": "ЖКХ"},
        headers=headers,
    ).json()["id"]

    resp = client.post(
        f"/characteristics/{characteristic_id}/verify",
        json={"verified_by_user": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["verified_by_user"] is False


def test_products_write_requires_admin(client, admin_token):
    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    user_token = _create_regular_user(client, admin_token)
    resp = client.post(
        f"/manufacturers/{manufacturer_id}/products",
        json={"model_name": "Х"},
        headers=_auth_headers(user_token),
    )
    assert resp.status_code == 403


def test_fetch_description_type_updates_flag(client, admin_token, monkeypatch):
    import app.services.product_catalog_service as catalog_module

    db = SessionLocal()
    try:
        manufacturer = _make_test_manufacturer(db)
        manufacturer_id = str(manufacturer.id)
    finally:
        db.close()

    result = SiSearchResult(
        si_code="DT-1", type_name="Т", manufacturer_name="М", description_type_url="https://fgis.gost.ru/doc/dt1.pdf", raw={}
    )
    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubFgisAdapter([result]))
    headers = _auth_headers(admin_token)
    search_resp = client.post(f"/manufacturers/{manufacturer_id}/si-types/search", headers=headers)
    si_type_id = search_resp.json()[0]["id"]

    class _StubAdapterWithText(_StubFgisAdapter):
        def fetch_description_type_text(self, url: str, *, mirror_url: str | None = None) -> str:
            return f"текст документа по ссылке {url}"

    monkeypatch.setattr(catalog_module, "FgisAdapter", lambda: _StubAdapterWithText([]))
    fetch_resp = client.post(f"/si-types/{si_type_id}/fetch-description-type", headers=headers)
    assert fetch_resp.status_code == 200, fetch_resp.text
    assert fetch_resp.json()["has_description_type_text"] is True
