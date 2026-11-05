"""Интеграции приборов МИРТЕК в ПО верхнего уровня — данные специалиста (05.11.2026).

Публичные списки поддерживаемого оборудования (раздел 35) отвечают только «есть ли прибор в
списке». Старший тендерный специалист ведёт точнее: таблицу «Интеграция в ПО» с
протоколами (МИРТЕК/СПОДЭС, версия, объём), способом идентификации приборов, версией ПО и
официальными письмами разработчиков (ЯЭнергетик, Метроскоп, Пирамида, Энфорс, Nekta). Он
ручается за эти данные и раз-два в месяц их актуализирует.

Три таблицы:

* `software_integrations` — запись на каждое ПО. Правки живут в `draft`, в анализ тендеров
  попадает только `published` — то, что специалист подтвердил кнопкой «Сохранить».
  Содержимое — JSONB (поля, модели, функции с пометкой «проверено»), потому что набор полей
  и функций меняется чаще, чем стоит заводить миграции.
* `software_integration_documents` — загруженные сканы писем и протоколов (содержимое в
  БД: файлов немного, а отдельное хранилище на диске — ещё одна вещь для бэкапа) и
  результат их разбора ИИ.
* `software_integration_publications` — журнал публикаций: кто, когда и что изменил.

Revision ID: 0092_software_integrations
Revises: 0091_remove_metronika
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_software_integrations"
down_revision: Union[str, None] = "0091_remove_metronika"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "software_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("platform_key", sa.String(50), nullable=True),
        sa.Column("aliases", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("draft", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("published", postgresql.JSONB, nullable=True),
        sa.Column("draft_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "published_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "software_integration_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("software_integrations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("analysis_status", sa.String(20), nullable=False, server_default="none"),
        sa.Column("analysis_result", postgresql.JSONB, nullable=True),
        sa.Column("analysis_error", sa.Text, nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "uploaded_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "software_integration_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "published_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("changes", postgresql.JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_table("software_integration_publications")
    op.drop_table("software_integration_documents")
    op.drop_table("software_integrations")
