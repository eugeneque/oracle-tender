import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationTrigger(str, enum.Enum):
    """Четыре триггера раздела 5.8 ТЗ. Значения — машинные ключи: по ним же настраивается
    включение/выключение каждого триггера и фильтруется журнал отправок."""

    NEW_RELEVANT_TENDER = "new_relevant_tender"
    HIGH_AI_SCORE = "high_ai_score"
    DEADLINE_SOON = "deadline_soon"
    CRITICAL_ERROR = "critical_error"
    # Пятый триггер — не из раздела 5.8: письмо, написанное администратором вручную
    # (новости платформы, регламентные работы). Значение того же перечисления, потому что
    # уходит тем же каналом и должно попадать в тот же журнал с теми же фильтрами.
    MANUAL_BROADCAST = "manual_broadcast"
    # Шестой — отчёт еженедельной сверки документов по СИ и руководств с источниками
    # (правка по итогам показа 15.09.2026): что переиздано, что появилось, что пропало.
    DOCUMENTS_UPDATED = "documents_updated"


TRIGGER_TITLES: dict[str, str] = {
    NotificationTrigger.NEW_RELEVANT_TENDER.value: "Новый релевантный тендер",
    NotificationTrigger.HIGH_AI_SCORE.value: "Высокая AI-оценка по профилю",
    NotificationTrigger.DEADLINE_SOON.value: "Приём заявок скоро закрывается",
    NotificationTrigger.CRITICAL_ERROR.value: "Критическая ошибка системы",
    NotificationTrigger.MANUAL_BROADCAST.value: "Письмо администратора",
    NotificationTrigger.DOCUMENTS_UPDATED.value: "Обновление документов по СИ и руководств",
}


class NotificationStatus(str, enum.Enum):
    SENT = "sent"
    FAILED = "failed"
    # Канал не настроен или триггер выключен: событие всё равно записывается в журнал —
    # иначе после включения почты невозможно понять, что система «хотела» отправить.
    SKIPPED = "skipped"


class NotificationSettings(Base):
    """Настройки почтового канала уведомлений (раздел 5.8 ТЗ).

    Хранятся в БД и правятся из `/settings`, как и ключ Yandex AI Studio: заказчик меняет
    почтовый ящик без доступа к серверу. Таблица-синглтон — всегда ровно одна строка.

    Пароль лежит зашифрованным (`app/core/crypto.py`, тот же ключ `.credentials_key`, что и
    у паролей площадок) и никогда не возвращается наружу — только признак «задан»."""

    __tablename__ = "notification_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=465)
    # "ssl" — соединение сразу в TLS (порт 465, так работает Яндекс.Почта и Mail.ru),
    # "starttls" — обычное соединение с апгрейдом (587), "none" — без шифрования.
    smtp_security: Mapped[str] = mapped_column(String(10), nullable=False, default="ssl")
    smtp_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    smtp_password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Получатели — через запятую/перенос строки. Отдельная таблица здесь избыточна:
    # список правится целиком одним полем и ни с чем не связывается.
    recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_recipients: Mapped[str | None] = mapped_column(Text, nullable=True)

    trigger_new_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger_high_ai_score: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger_deadline_soon: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger_critical_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger_documents_updated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Порог итоговой AI-оценки по профилю (раздел 5.5.1 ТЗ), а не процента победителя:
    # с 03.09.2026 в список и в уведомления идёт `overall_score`.
    ai_score_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    deadline_days_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class Notification(Base):
    """Журнал уведомлений (раздел 5.8 + 5.9 ТЗ) — отдельно от общего журнала `logs`.

    Почему отдельная таблица, а не запись в `logs`: у уведомления своя структура (триггер,
    адресаты, тема, тендер) и свой читатель — в интерфейсе это отдельный свёрнутый блок.
    В общем журнале такие записи утонули бы среди технических строк."""

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # clock_timestamp() — по той же причине, что и в журнале: несколько писем одной
    # рассылки уходят в одной транзакции и должны сохранить порядок.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.clock_timestamp(),
        nullable=False,
        index=True,
    )
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="SET NULL"), nullable=True
    )
    # Оформленная версия письма — только у писем, написанных в редакторе. У писем по
    # триггерам её нет и не будет: они собираются из полей тендера, форматировать там нечего.
    # `body` при этом заполнен всегда — это текстовая версия, она же и уходит адресату
    # второй частью письма.
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Автор ручной рассылки. У автоматических писем пусто: их «отправила система», и
    # приписывать их администратору, который последним правил настройки, было бы враньём.
    sent_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
