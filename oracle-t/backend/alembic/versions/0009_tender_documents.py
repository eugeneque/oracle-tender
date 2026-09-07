"""tender_documents (раздел 5.2, 7 ТЗ — Этап 3, подтянуто раньше срока по запросу заказчика)

Revision ID: 0009_tender_documents
Revises: 0008_sberbank_ast_implemented
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_tender_documents"
down_revision: Union[str, None] = "0008_sberbank_ast_implemented"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenders.id"), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_type", sa.String(length=20), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.String(length=600), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tender_id", "source_url", name="uq_tender_documents_tender_source_url"),
    )
    op.create_index("ix_tender_documents_tender_id", "tender_documents", ["tender_id"])


def downgrade() -> None:
    op.drop_index("ix_tender_documents_tender_id", table_name="tender_documents")
    op.drop_table("tender_documents")
