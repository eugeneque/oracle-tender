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


def _create_user(client, admin_token, role: str = "user") -> dict:
    username = f"deluser_{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/users",
        json={"username": username, "password": "SomePass123", "full_name": "Del User", "role": role},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_can_delete_user_and_reuse_username(client, admin_token):
    """Удаление полное (замечание 28.10.2026): логин освобождается, журнал входов
    удалённого пользователя остаётся без подписи, а не мешает удалению."""

    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.log import Log

    user = _create_user(client, admin_token)
    login = client.post(
        "/auth/login", json={"username": user["username"], "password": "SomePass123"}
    )
    assert login.status_code == 200
    user_token = login.json()["access_token"]

    response = client.delete(f"/users/{user['id']}", headers=_auth_headers(admin_token))
    assert response.status_code == 204, response.text

    users = client.get("/users", headers=_auth_headers(admin_token)).json()
    assert all(u["id"] != user["id"] for u in users)
    # Выданный ранее токен больше не действует, войти нельзя.
    assert client.get("/auth/me", headers=_auth_headers(user_token)).status_code == 401
    assert (
        client.post(
            "/auth/login", json={"username": user["username"], "password": "SomePass123"}
        ).status_code
        == 401
    )

    with SessionLocal() as db:
        login_logs = db.scalars(
            select(Log).where(Log.action == f"login:{user['username']}", Log.result == "success")
        ).all()
        assert login_logs and all(log.user_id is None for log in login_logs)
        assert db.scalar(select(Log).where(Log.action == f"delete_user:{user['username']}"))

    again = client.post(
        "/users",
        json={
            "username": user["username"],
            "password": "OtherPass123",
            "full_name": "New Owner",
            "role": "user",
        },
        headers=_auth_headers(admin_token),
    )
    assert again.status_code == 201, again.text


def test_admin_cannot_delete_self(client, admin_token):
    me = client.get("/auth/me", headers=_auth_headers(admin_token)).json()
    response = client.delete(f"/users/{me['id']}", headers=_auth_headers(admin_token))
    assert response.status_code == 400


def test_delete_unknown_user_404(client, admin_token):
    response = client.delete(f"/users/{uuid.uuid4()}", headers=_auth_headers(admin_token))
    assert response.status_code == 404


def test_admin_can_change_role_of_existing_user(client, admin_token):
    user = _create_user(client, admin_token)
    promoted = client.patch(
        f"/users/{user['id']}", json={"role": "admin"}, headers=_auth_headers(admin_token)
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    demoted = client.patch(
        f"/users/{user['id']}", json={"role": "user"}, headers=_auth_headers(admin_token)
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"
    client.delete(f"/users/{user['id']}", headers=_auth_headers(admin_token))


def test_admin_cannot_demote_self(client, admin_token):
    me = client.get("/auth/me", headers=_auth_headers(admin_token)).json()
    response = client.patch(
        f"/users/{me['id']}", json={"role": "user"}, headers=_auth_headers(admin_token)
    )
    assert response.status_code == 400
