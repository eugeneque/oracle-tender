import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationSettingsOut(BaseModel):
    """Пароль почтового ящика наружу не отдаётся ни в каком виде — только признак
    `has_password` (как и с ключом Yandex AI Studio)."""

    is_enabled: bool
    is_configured: bool
    smtp_host: str | None
    smtp_port: int
    smtp_security: str
    smtp_username: str | None
    has_password: bool
    from_address: str | None
    recipients: str | None
    admin_recipients: str | None
    trigger_new_relevant: bool
    trigger_high_ai_score: bool
    trigger_deadline_soon: bool
    trigger_critical_error: bool
    ai_score_threshold: int
    deadline_days_threshold: int
    updated_at: datetime | None
    updated_by: str | None


class NotificationSettingsUpdate(BaseModel):
    """PATCH-семантика: отсутствующее поле не меняется (см. `model_fields_set` в сервисе)."""

    is_enabled: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = Field(default=None, pattern="^(ssl|starttls|none)$")
    smtp_username: str | None = None
    smtp_password: str | None = None
    from_address: str | None = None
    recipients: str | None = None
    admin_recipients: str | None = None
    trigger_new_relevant: bool | None = None
    trigger_high_ai_score: bool | None = None
    trigger_deadline_soon: bool | None = None
    trigger_critical_error: bool | None = None
    ai_score_threshold: int | None = Field(default=None, ge=0, le=100)
    deadline_days_threshold: int | None = Field(default=None, ge=1, le=60)


class NotificationOut(BaseModel):
    """Запись журнала уведомлений. Тело письма отдаётся целиком: в интерфейсе это
    единственный способ понять, что именно ушло адресату."""

    id: uuid.UUID
    created_at: datetime
    trigger: str
    trigger_title: str
    status: str
    channel: str
    recipients: str | None
    subject: str
    body: str | None
    # Оформленное тело — только у писем, написанных администратором в редакторе; у писем по
    # триггерам его нет. HTML очищен на сервере по белому списку тегов
    # (`app/services/email_html.py`), поэтому его безопасно показывать в журнале как разметку.
    body_html: str | None
    error: str | None
    tender_id: uuid.UUID | None
    sent_by: str | None


class NotificationTestResult(BaseModel):
    success: bool
    message: str


class KnownRecipient(BaseModel):
    """Адрес для подсказок в поле получателей: кому уже писали, когда и сколько раз."""

    address: str
    name: str | None
    last_sent_at: datetime | None
    sent_count: int


class NotificationBroadcastRequest(BaseModel):
    """Произвольное письмо администратора.

    Тело приходит одним полем HTML: текстовую версию сервер получает из него сам
    (`email_html.html_to_text`) — заставлять человека писать письмо дважды бессмысленно.
    Получатели — списком, а не строкой, как в настройках: здесь их выбирают по одному в
    поле с подсказками, и разбирать обратно склеенную строку было бы лишним шагом,
    на котором теряются имена вида «Иванов Иван <ivanov@example.ru>»."""

    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1, max_length=100_000)
    recipients: list[str] = Field(min_length=1, max_length=200)
