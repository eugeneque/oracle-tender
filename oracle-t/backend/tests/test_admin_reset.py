import uuid
from pathlib import Path

from app.core.env_file import update_env_file
from app.core.security import verify_password
from app.db.session import SessionLocal
from app.services.user_service import get_user_by_username, reset_admin


def test_reset_admin_creates_then_overwrites_password():
    username = f"reset_admin_test_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        created = reset_admin(db, username=username, password="FirstPass1", full_name="A")
        assert created.role == "admin"
        assert verify_password("FirstPass1", created.password_hash)

        updated = reset_admin(db, username=username, password="SecondPass2", full_name="B")
        assert updated.id == created.id
        assert updated.full_name == "B"
        assert verify_password("SecondPass2", updated.password_hash)
        assert not verify_password("FirstPass1", updated.password_hash)

        reloaded = get_user_by_username(db, username)
        assert reloaded is not None
        assert reloaded.is_active is True
    finally:
        db.close()


def test_update_env_file_replaces_only_matching_keys(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\nBOOTSTRAP_ADMIN_PASSWORD=old\n# comment\n", encoding="utf-8")

    updated = update_env_file(env_path, {"BOOTSTRAP_ADMIN_PASSWORD": "new", "NEW_KEY": "value"})

    assert updated is True
    content = env_path.read_text(encoding="utf-8")
    assert "FOO=bar" in content
    assert "BOOTSTRAP_ADMIN_PASSWORD=new" in content
    assert "old" not in content
    assert "# comment" in content
    assert "NEW_KEY=value" in content


def test_update_env_file_returns_false_when_missing(tmp_path: Path):
    assert update_env_file(tmp_path / "does_not_exist.env", {"A": "B"}) is False
