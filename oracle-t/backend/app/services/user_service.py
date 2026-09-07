import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.log import LogLevel
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserUpdate
from app.services.audit import log_action


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_user(db: Session, data: UserCreate, actor: User) -> User:
    user = User(
        username=data.username,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        role=data.role.value,
    )
    db.add(user)
    db.flush()
    log_action(
        db,
        component="users",
        action=f"create_user:{user.username}",
        result="success",
        details=f"Пользователь '{user.username}' (роль {user.role}) создан",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(user)
    return user


def update_user(db: Session, user: User, data: UserUpdate, actor: User) -> User:
    changes: list[str] = []
    if data.full_name is not None and data.full_name != user.full_name:
        user.full_name = data.full_name
        changes.append("full_name")
    if data.role is not None and data.role.value != user.role:
        user.role = data.role.value
        changes.append("role")
    if data.password:
        user.password_hash = hash_password(data.password)
        changes.append("password")

    if changes:
        db.add(user)
        db.flush()
        log_action(
            db,
            component="users",
            action=f"update_user:{user.username}",
            result="success",
            details=f"Изменены поля: {', '.join(changes)}",
            user_id=actor.id,
        )
    db.commit()
    db.refresh(user)
    return user


def set_user_active(db: Session, user: User, is_active: bool, actor: User) -> User:
    user.is_active = is_active
    db.add(user)
    db.flush()
    log_action(
        db,
        component="users",
        action=f"{'unblock' if is_active else 'block'}_user:{user.username}",
        result="success",
        level=LogLevel.WARNING if not is_active else LogLevel.INFO,
        details=f"Пользователь '{user.username}' {'разблокирован' if is_active else 'заблокирован'}",
        user_id=actor.id,
    )
    db.commit()
    db.refresh(user)
    return user


def bootstrap_admin(db: Session, username: str, password: str, full_name: str) -> User | None:
    """Создаёт первого администратора, если в системе ещё нет ни одного пользователя.
    Идемпотентно: повторный вызов при уже существующих пользователях ничего не делает."""

    if db.scalar(select(User.id).limit(1)) is not None:
        return None

    admin = User(
        username=username,
        password_hash=hash_password(password),
        full_name=full_name,
        role="admin",
    )
    db.add(admin)
    db.flush()
    log_action(
        db,
        component="bootstrap",
        action=f"create_admin:{username}",
        result="success",
        details="Первый администратор создан автоматически при первом запуске системы",
        user_id=admin.id,
    )
    db.commit()
    db.refresh(admin)
    return admin


def reset_admin(db: Session, username: str, password: str, full_name: str) -> User:
    """Создаёт администратора с данным логином либо, если он уже существует, перезаписывает
    его пароль/ФИО и гарантирует роль admin + активный статус. В отличие от `bootstrap_admin`
    (который отрабатывает один раз при самом первом запуске системы), эта функция — инструмент
    для разработки/эксплуатации: её можно запускать многократно, каждый раз она приводит учётную
    запись администратора к переданным значениям."""

    user = get_user_by_username(db, username)
    is_new = user is None
    if user is None:
        user = User(username=username)
        db.add(user)

    user.password_hash = hash_password(password)
    user.full_name = full_name
    user.role = UserRole.ADMIN.value
    user.is_active = True
    db.flush()

    log_action(
        db,
        component="bootstrap",
        action=f"{'create_admin' if is_new else 'reset_admin'}:{username}",
        result="success",
        details="Учётная запись администратора создана/сброшена командой reset-admin",
        user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return user
