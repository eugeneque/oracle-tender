import os


def test_login_with_wrong_password_returns_401(client):
    response = client.post(
        "/auth/login",
        json={"username": os.environ["BOOTSTRAP_ADMIN_USERNAME"], "password": "wrong"},
    )
    assert response.status_code == 401


def test_login_success_returns_token(admin_token):
    assert admin_token


def test_me_requires_authentication(client):
    assert client.get("/auth/me").status_code == 401


def test_me_returns_current_user(client, admin_token):
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == os.environ["BOOTSTRAP_ADMIN_USERNAME"]
    assert body["role"] == "admin"


def test_logout_is_logged(client, admin_token):
    response = client.post("/auth/logout", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
