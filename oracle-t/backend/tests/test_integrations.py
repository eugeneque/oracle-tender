import io
import uuid


def test_probe_png_is_a_valid_decodable_image():
    """Регрессионный тест на реальный инцидент: захардкоженные байты пинг-картинки для
    `test_connection` однажды оказались повреждены (неверный CRC/IDAT) — Yandex OCR отвечал
    `INVALID_ARGUMENT: Can't decode image`, и это выглядело как проблема с ключом/Folder ID
    пользователя, хотя ей не являлось. Проверяем, что байты — валидный PNG, до любого сетевого
    вызова."""

    from PIL import Image

    from app.services.yandex_ai_service import _PROBE_PNG

    image = Image.open(io.BytesIO(_PROBE_PNG))
    image.load()
    assert image.format == "PNG"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_regular_user(client, admin_token) -> str:
    username = f"integtest_{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "T", "role": "user"},
        headers=_auth_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    login = client.post("/auth/login", json={"username": username, "password": "SomePass123"})
    return login.json()["access_token"]


def test_yandex_settings_require_admin(client, admin_token):
    assert client.get("/integrations/yandex-ai-studio").status_code == 401

    user_token = _create_regular_user(client, admin_token)
    assert (
        client.get("/integrations/yandex-ai-studio", headers=_auth_headers(user_token)).status_code
        == 403
    )


def test_yandex_settings_starts_unconfigured(client, admin_token):
    headers = _auth_headers(admin_token)
    # Настройки — синглтон в БД, а не создаваемая заново для каждого теста запись: явно
    # очищаем перед проверкой, не полагаясь на то, что до этого теста никто не сохранял
    # значения (тестовая БД не пересоздаётся между отдельными запусками pytest).
    client.patch("/integrations/yandex-ai-studio", json={"api_key": "", "folder_id": ""}, headers=headers)

    resp = client.get("/integrations/yandex-ai-studio", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_configured"] is False
    assert body["api_key_masked"] is None


def test_yandex_settings_update_masks_key_and_supports_partial_patch(client, admin_token):
    headers = _auth_headers(admin_token)

    resp = client.patch(
        "/integrations/yandex-ai-studio",
        json={"api_key": "AQVNsecretvalue1234", "folder_id": "b1gfirstfolder"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_configured"] is True
    assert body["folder_id"] == "b1gfirstfolder"
    assert body["api_key_masked"] == "••••••••1234"  # хвост виден, само значение — нет
    assert "secret" not in body["api_key_masked"]
    assert body["updated_by"]  # ФИО администратора, выполнившего изменение

    # PATCH только folder_id не должен затирать ранее сохранённый api_key
    resp2 = client.patch(
        "/integrations/yandex-ai-studio",
        json={"folder_id": "b1gsecondfolder"},
        headers=headers,
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["folder_id"] == "b1gsecondfolder"
    assert body2["api_key_masked"] == "••••••••1234"  # ключ не изменился

    # явная очистка пустой строкой
    resp3 = client.patch(
        "/integrations/yandex-ai-studio",
        json={"api_key": ""},
        headers=headers,
    )
    assert resp3.status_code == 200, resp3.text
    body3 = resp3.json()
    assert body3["api_key_masked"] is None
    assert body3["is_configured"] is False
    assert body3["folder_id"] == "b1gsecondfolder"  # folder_id не тронут


def test_yandex_test_connection_reports_missing_configuration(client, admin_token):
    headers = _auth_headers(admin_token)
    client.patch("/integrations/yandex-ai-studio", json={"api_key": "", "folder_id": ""}, headers=headers)

    resp = client.post("/integrations/yandex-ai-studio/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is False
    assert "не настроено" in body["message"]


def test_yandex_test_connection_success_and_failure(client, admin_token, monkeypatch):
    import app.services.yandex_ai_service as yandex_ai_service_module

    headers = _auth_headers(admin_token)
    client.patch(
        "/integrations/yandex-ai-studio",
        json={"api_key": "AQVNworking", "folder_id": "b1gworking"},
        headers=headers,
    )

    monkeypatch.setattr(
        yandex_ai_service_module,
        "_run_ocr_ping",
        lambda api_key, folder_id: yandex_ai_service_module.YandexConnectionTestResult(
            success=True, message="Подключение работает."
        ),
    )
    ok_resp = client.post("/integrations/yandex-ai-studio/test", headers=headers)
    assert ok_resp.status_code == 200
    assert ok_resp.json()["success"] is True

    monkeypatch.setattr(
        yandex_ai_service_module,
        "_run_ocr_ping",
        lambda api_key, folder_id: yandex_ai_service_module.YandexConnectionTestResult(
            success=False, message="Не удалось подключиться: permission denied"
        ),
    )
    fail_resp = client.post("/integrations/yandex-ai-studio/test", headers=headers)
    assert fail_resp.status_code == 200
    assert fail_resp.json()["success"] is False
    assert "permission denied" in fail_resp.json()["message"]
