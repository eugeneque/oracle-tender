"""Учётные данные для входа на площадки-источники (раздел 4.1, 5.1 ТЗ).

Часть площадок (корпоративные разделы Росэлторга, Сбербанк-АСТ и др.) отдаёт закупки только
авторизованному пользователю — без учётной записи адаптер видит лишь публичную витрину.
Логин и пароль вводятся администратором в разделе настроек и хранятся здесь: пароль — только
в зашифрованном виде (`app/core/crypto.py`), наружу через API он не отдаётся ни в каком виде,
только маска.

Несколько записей на один источник разрешены сознательно: у площадки бывает отдельная
учётка на каждое юрлицо, и держать их в одной строке значило бы переписывать пароль при
каждом переключении.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.source import Source


class SourceCredential(Base):
    __tablename__ = "source_credentials"
    __table_args__ = (
        UniqueConstraint("source_id", "label", name="uq_source_credentials_source_label"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Список учёток всегда показывается вместе с названием площадки — подгружаем сразу,
    # чтобы не делать запрос на строку.
    source: Mapped[Source] = relationship(lazy="joined")
    # Название блока, которое даёт человек («Основная учётка», «ООО Ромашка») — по нему он
    # различает записи одной площадки.
    label: Mapped[str] = mapped_column(String(150), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    # Шифротекст Fernet, а не пароль. Text, а не String(n): длина токена зависит от длины
    # пароля, и упереться в лимит колонки на длинной парольной фразе — глупая авария.
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
