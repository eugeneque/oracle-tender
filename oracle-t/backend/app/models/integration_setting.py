import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class YandexAiStudioSettings(Base):
    """Учётные данные для Yandex AI Studio (раздел 5.4 ТЗ — ИИ-модуль, плюс fallback-OCR
    в разделе 5.2). Хранятся в БД, а не в `.env` — по запросу заказчика ключ должен
    обновляться из админ-панели (`/settings`), без правки кода/конфигурации на сервере.

    Таблица — синглтон: всегда ровно одна строка (см. `app/services/yandex_ai_service.py`),
    поэтому отдельного бизнес-ключа не нужно, только `id`."""

    __tablename__ = "yandex_ai_studio_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class AiProviderSettings(Base):
    """Какая модель обслуживает ИИ-модуль и учётные данные RouterAI (OpenAI-совместимый шлюз,
    через него — DeepSeek и Qwen; до 20.10.2026 ещё Claude). Появилось 18.09.2026 по просьбе заказчика: до этого
    единственным провайдером был YandexGPT, и переключаться было не на что.

    `active_provider` — переключатель. Активен ровно один провайдер: все запросы генерации
    (`app/services/ai_client.py`) идут через него, прежний в этот момент не вызывается.
    Учётные данные Yandex по-прежнему лежат в `yandex_ai_studio_settings` — на них, помимо
    генерации, держатся OCR-fallback, эмбеддинги «похожих» и Яндекс-поиск, которые от
    переключателя не зависят (у Claude нет ни OCR, ни модели эмбеддингов).

    Таблица — синглтон, как и соседняя (см. `app/services/ai_provider_service.py`)."""

    __tablename__ = "ai_provider_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # "yandex" | "deepseek" | "qwen" — см. PROVIDER_LABELS в app/services/ai_provider_service.py.
    active_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="yandex")
    routerai_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Путь до модели в терминах RouterAI («deepseek/deepseek-v4-pro-0813»). Настраивается,
    # чтобы смена версии не требовала правки кода — как и `DEFAULT_MODEL` у Yandex. Ключ и
    # адрес шлюза у моделей общие.
    deepseek_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Qwen (20.10.2026, вместо Claude).
    qwen_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    routerai_base_url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Модели, выключенные администратором намертво (29.09.2026): `{"deepseek": {"at": ISO,
    # "by": "ФИО"}}`. В выключенную модель не уходит ни один запрос, а задачи, начатые до
    # выключения, останавливаются на следующем обращении (см. `app/services/ai_client.py`).
    disabled_providers: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Окно контекста и потолок ответа по моделям (14.10.2026): `{"yandex": {"context_tokens":
    # 32768, "max_output_tokens": 8000}}`. Пустое — значения по умолчанию
    # (`ai_provider_service.DEFAULT_MODEL_LIMITS`). От окна считается, сколько текста
    # помещается в запрос: карточка производителя в матрице, куски документации.
    model_limits: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class RusprofileSettings(Base):
    """Учётная запись rusprofile.ru (18.09.2026). Под ней система читает то, что без подписки
    скрыто: полный список госзакупок компании с проигрышами, лицензии, численность, финансы —
    и заполняет ими раздел «Моя компания» вместо ручного ввода (см.
    `app/services/rusprofile_service.py`).

    Хранится в БД по тем же соображениям, что и ключи ИИ: пароль меняет заказчик из
    интерфейса, а не администратор сервера. Таблица — синглтон.

    `last_sync_*` — итог последней синхронизации: интерфейс показывает его рядом с кнопкой,
    чтобы было видно, когда данные обновлялись и чем закончилась попытка."""

    __tablename__ = "rusprofile_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "ok" | "error" — и текст, который увидит администратор.
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class Bitrix24Settings(Base):
    """Подключение к порталу Bitrix24 МИРТЕК (08.10.2026): тендеры уходят в CRM сделками.

    Входящий вебхук (`https://<портал>/rest/<пользователь>/<токен>/`) — это и адрес, и пароль
    сразу: с ним можно читать и менять всю CRM от имени пользователя, поэтому он хранится
    зашифрованным (`app/core/crypto.py`) и наружу не отдаётся, только маска.

    Портал у заказчика один и сразу боевой (тестового нет, копия снимается ночью), поэтому
    отправка по умолчанию выключена (`push_enabled`): настроить, проверить подключение и
    посмотреть, какая сделка получится, можно без единой записи в CRM. Включает отправку
    администратор отдельно и осознанно.

    Таблица — синглтон, как и соседние."""

    __tablename__ = "bitrix24_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    webhook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Воронка и стадия, куда встают новые сделки. По умолчанию — тестовая воронка «Тендеры —
    # тест ИИ» (5) и стадия «Парсинг опубликованных тендеров», которые завёл администратор
    # портала под эту интеграцию.
    category_id: Mapped[int] = mapped_column(nullable=False, default=5, server_default="5")
    stage_id: Mapped[str] = mapped_column(
        String(50), nullable=False, default="C5:PARSING", server_default="C5:PARSING"
    )
    push_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    # Обработчик приложения «ИИ анализ и решения» (20.10.2026, миграция 0078): пакет анализа
    # во вкладку сделки, подпись HMAC SHA-256 общим секретом — см. `bitrix_ai_service`.
    # Секрет зашифрован, наружу не отдаётся; отправка выключена, пока её не включили.
    ai_receiver_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_push_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_check_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
