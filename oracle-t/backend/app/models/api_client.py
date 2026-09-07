import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiClient(Base):
    """Внешняя система, которой разрешено читать данные через API (раздел 5.10 ТЗ).

    Отдельная сущность, а не учётная запись пользователя: интеграции нужен постоянный доступ
    без входа по паролю и без срока жизни токена в восемь часов, а её действия не должны
    выглядеть в журнале как действия человека.

    **Ключ хранится только хешем** — тем же алгоритмом, что и пароли пользователей. Показать
    его повторно нельзя: если ключ потерян, выпускается новый. Иначе дамп базы давал бы
    доступ к выгрузке всех тендеров.
    """

    __tablename__ = "api_clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Префикс ключа (первые символы) — чтобы администратор понимал, какой именно ключ он
    # видит в списке, не имея самого ключа.
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # Когда ключом пользовались в последний раз: единственный способ понять, что интеграция
    # жива, и безопасно ли отзывать старый ключ.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
