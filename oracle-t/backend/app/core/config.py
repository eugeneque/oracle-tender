from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    log_dir: str = "logs"

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "oraclet"
    postgres_user: str = "oraclet"
    postgres_password: str = "oraclet"

    jwt_secret_key: str = "insecure-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin"
    bootstrap_admin_full_name: str = "Администратор системы"

    # --- Сбор тендеров: планировщик и повторные попытки (раздел 5.1, 6.2, 9 Этап 2 ТЗ) ---
    scheduler_enabled: bool = True
    # Время запусков — "утро" и "обед" (раздел 4.1 ТЗ), формат "HH:MM", часовой пояс сервера.
    scheduler_morning_time: str = "09:00"
    scheduler_afternoon_time: str = "14:00"
    http_retry_attempts: int = 3
    http_retry_backoff_base: float = 2.0

    # --- Пополнение справочника продукции из внешних источников (раздел 5.3 ТЗ) ---
    # ФГИС опрашивается не как тендерная площадка «всё подряд», а двумя триггерами:
    # ревалидация сохранённых карточек по расписанию (эти настройки) и запрос по событию из
    # модуля сопоставления, который идёт вне расписания — см.
    # `app/services/catalog_queue_service.py`.
    fgis_revalidation_enabled: bool = True
    # Раз в сутки: «Описание типа» переиздаётся приказами Росстандарта, это событие месяцев,
    # а не часов, и чаще ходить в нестабильный сервис ФГИС незачем.
    fgis_revalidation_time: str = "04:00"
    # Сколько карточек ставить в очередь за один плановый прогон. Каждый запрос к ФГИС —
    # до 45 секунд (см. таймауты в адаптере), и весь справочник за одну ночь не обойти.
    fgis_revalidation_batch: int = 100

    # Каталоги на сайтах производителей (свой и конкурентов) меняются редко —
    # еженедельного обхода достаточно. `weekday` в формате cron: 0 — понедельник.
    catalog_sites_sync_enabled: bool = True
    catalog_sites_sync_weekday: int = 0
    catalog_sites_sync_time: str = "05:00"

    # --- Документы тендеров (раздел 5.2, 6.2 ТЗ) ---
    storage_dir: str = "storage"
    # OCR-fallback для сканов PDF без текстового слоя (раздел 5.2 ТЗ). Языки — коды tesseract,
    # через "+"; тендерная документация встречается и на русском, и изредка с англ. вставками.
    ocr_enabled: bool = True
    ocr_languages: str = "rus+eng"
    ocr_resolution: int = 300

    # --- Почтовые уведомления (раздел 5.8 ТЗ) ---
    # Это только первичная настройка ящика: значения переносятся в БД при первом запуске
    # (`notification_service.bootstrap_from_env`), дальше единственный источник правды —
    # таблица `notification_settings` и раздел «Уведомления» в `/settings`. Иначе правка
    # адресатов из интерфейса откатывалась бы при каждом перезапуске.
    # Ящик-отправитель, в отличие от ключа Yandex AI Studio, задаётся здесь: он один на
    # установку, заводится админом почтового домена вместе с сервером и не должен теряться
    # при пересоздании базы.
    notify_enabled: bool = True
    notify_smtp_host: str = ""
    notify_smtp_port: int = 465
    # "ssl" (465), "starttls" (587) или "none" — см. модель NotificationSettings.
    notify_smtp_security: str = "ssl"
    notify_smtp_username: str = ""
    notify_smtp_password: str = ""
    notify_from_address: str = ""
    notify_recipients: str = ""
    notify_admin_recipients: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
