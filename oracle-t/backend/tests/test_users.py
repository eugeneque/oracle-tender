import uuid


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_users_list_requires_admin(client):
    assert client.get("/users").status_code == 401


def test_admin_can_create_list_and_block_user(client, admin_token):
    username = f"testuser_{uuid.uuid4().hex[:8]}"

    create_resp = client.post(
        "/users",
        json={
            "username": username,
            "password": "SomePass123",
            "full_name": "Test User",
            "role": "user",
        },
        headers=_auth_headers(admin_token),
    )
    assert create_resp.status_code == 201, create_resp.text
    user = create_resp.json()
    assert user["role"] == "user"
    assert user["is_active"] is True

    list_resp = client.get("/users", headers=_auth_headers(admin_token))
    assert list_resp.status_code == 200
    assert any(u["username"] == username for u in list_resp.json())

    login_resp = client.post(
        "/auth/login", json={"username": username, "password": "SomePass123"}
    )
    assert login_resp.status_code == 200
    user_token = login_resp.json()["access_token"]

    # обычный пользователь не может листать пользователей
    assert client.get("/users", headers=_auth_headers(user_token)).status_code == 403

    block_resp = client.post(f"/users/{user['id']}/block", headers=_auth_headers(admin_token))
    assert block_resp.status_code == 200
    assert block_resp.json()["is_active"] is False

    # заблокированный пользователь больше не может войти
    blocked_login = client.post(
        "/auth/login", json={"username": username, "password": "SomePass123"}
    )
    assert blocked_login.status_code == 401

    unblock_resp = client.post(f"/users/{user['id']}/unblock", headers=_auth_headers(admin_token))
    assert unblock_resp.status_code == 200
    assert unblock_resp.json()["is_active"] is True


def test_duplicate_username_conflicts(client, admin_token):
    username = f"dupuser_{uuid.uuid4().hex[:8]}"
    payload = {
        "username": username,
        "password": "SomePass123",
        "full_name": "Dup User",
        "role": "user",
    }
    first = client.post("/users", json=payload, headers=_auth_headers(admin_token))
    assert first.status_code == 201
    second = client.post("/users", json=payload, headers=_auth_headers(admin_token))
    assert second.status_code == 409


def test_admin_cannot_block_self(client, admin_token):
    me = client.get("/auth/me", headers=_auth_headers(admin_token)).json()
    response = client.post(f"/users/{me['id']}/block", headers=_auth_headers(admin_token))
    assert response.status_code == 400
