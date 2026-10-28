"""Пользователя можно удалить из системы полностью (замечание 28.10.2026).

Раньше в разделе «Пользователи» была только блокировка: учётная запись и логин оставались,
занять логин заново было нельзя. Удаление упиралось в ссылки на `users.id` без `ON DELETE`
(журнал, история тендеров, «кто менял настройки», фоновые задания, сделки Bitrix24…) —
Postgres отказывал в удалении.

Все такие ссылки — подписи «кто сделал», они необязательны (`NULL`) и сами записи терять
нельзя: журнал и история тендера должны пережить уход сотрудника. Поэтому `SET NULL`, а не
`CASCADE`. Ссылки, где решение уже принято раньше (закладки — `CASCADE`, профили отбора,
ответственный по тендеру, вердикты — `SET NULL`), не трогаем.

Revision ID: 0086_user_delete_set_null
Revises: 0085_admission_registry_access
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0086_user_delete_set_null"
down_revision: Union[str, None] = "0085_admission_registry_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (таблица, колонка) — имя ограничения по умолчанию Postgres: <таблица>_<колонка>_fkey.
REFERENCES = [
    ("logs", "user_id"),
    ("tender_history", "user_id"),
    ("background_jobs", "created_by_id"),
    ("source_credentials", "updated_by_id"),
    ("region_responsibles", "updated_by_id"),
    ("notification_settings", "updated_by_id"),
    ("api_clients", "created_by_id"),
    ("ai_provider_settings", "updated_by_id"),
    ("yandex_ai_studio_settings", "updated_by_id"),
    ("rusprofile_settings", "updated_by_id"),
    ("bitrix24_settings", "updated_by_id"),
    ("bitrix_deal_links", "pushed_by_id"),
    ("bitrix_ai_requests", "sent_by_id"),
]


def _recreate(ondelete: str | None) -> None:
    for table, column in REFERENCES:
        name = f"{table}_{column}_fkey"
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, "users", [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    _recreate("SET NULL")


def downgrade() -> None:
    _recreate(None)
