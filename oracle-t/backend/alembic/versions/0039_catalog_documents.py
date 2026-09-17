"""Справочник документов по СИ и руководств с датой актуальности (правка по итогам показа
15.09.2026, п.2).

Две вещи:

* таблица `catalog_documents` — по строке на документ каждой модели и каждого кода СИ:
  ссылка, назначение, актуальная дата и её происхождение, отпечаток файла для сверки,
  статус последней проверки, отметки об изменениях. Заполняется из ссылок, которые обход
  сайтов производителей уже кладёт в группу «Документация» справочника характеристик, и из
  «Описания типа» кодов СИ; сверяется с источниками раз в неделю
  (`app/services/document_registry_service.py`);
* флаг `trigger_documents_updated` в настройках уведомлений — ещё один триггер почтового
  канала: отчёт об изменившихся документах. Включён по умолчанию, как и остальные.

Первичное наполнение таблицы миграция не делает: оно — часть еженедельной проверки и
запускается кнопкой из каталога, а на проде удобнее увидеть первый отчёт целиком, чем
получить полупустую таблицу без отпечатков.

Revision ID: 0039_catalog_documents
Revises: 0038_remove_blocked_sources
Create Date: 2026-09-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_catalog_documents"
down_revision: Union[str, None] = "0038_remove_blocked_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "si_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("si_types.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("document_date_source", sa.String(length=20), nullable=True),
        sa.Column("version_label", sa.String(length=50), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("last_modified_header", sa.String(length=100), nullable=True),
        sa.Column("content_length", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "check_status", sa.String(length=30), nullable=False, server_default="not_checked"
        ),
        sa.Column("check_error", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_catalog_documents_manufacturer_id", "catalog_documents", ["manufacturer_id"]
    )
    op.create_index("ix_catalog_documents_product_id", "catalog_documents", ["product_id"])
    op.create_index("ix_catalog_documents_si_type_id", "catalog_documents", ["si_type_id"])
    # Не больше одного документа каждого назначения у модели и у кода СИ (см. модель).
    # Два частичных уникальных индекса вместо одного ограничения: у строки заполнен либо
    # `product_id`, либо `si_type_id`, а NULL в уникальном ограничении не равен NULL.
    op.create_index(
        "uq_catalog_documents_product_kind",
        "catalog_documents",
        ["product_id", "kind"],
        unique=True,
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_index(
        "uq_catalog_documents_si_type_kind",
        "catalog_documents",
        ["si_type_id", "kind"],
        unique=True,
        postgresql_where=sa.text("si_type_id IS NOT NULL"),
    )

    op.add_column(
        "notification_settings",
        sa.Column(
            "trigger_documents_updated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "trigger_documents_updated")
    op.drop_index("uq_catalog_documents_si_type_kind", table_name="catalog_documents")
    op.drop_index("uq_catalog_documents_product_kind", table_name="catalog_documents")
    op.drop_index("ix_catalog_documents_si_type_id", table_name="catalog_documents")
    op.drop_index("ix_catalog_documents_product_id", table_name="catalog_documents")
    op.drop_index("ix_catalog_documents_manufacturer_id", table_name="catalog_documents")
    op.drop_table("catalog_documents")
