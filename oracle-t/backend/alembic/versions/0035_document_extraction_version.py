"""Версия правил извлечения текста у документа тендера

Нужна, чтобы уже скачанные документы переразобрались по новым правилам (вложенные архивы) —
без неё техническое задание, лежащее внутри вложенного архива, так и осталось бы невидимым
для анализа у всех тендеров, собранных до этого изменения.

Существующие строки помечаются версией 1 — то есть «разобраны старыми правилами»: NULL значил
бы то же самое, но явное число читается однозначнее в SQL-запросах вручную.

Revision ID: 0035_document_extraction_version
Revises: 0034_wave2_catalog_sources
Create Date: 2026-09-05

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_document_extraction_version"
down_revision = "0034_wave2_catalog_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tender_documents",
        sa.Column("extraction_version", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE tender_documents SET extraction_version = 1 WHERE extracted_text IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("tender_documents", "extraction_version")
