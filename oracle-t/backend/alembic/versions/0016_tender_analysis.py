"""анализ тендера: requirements, compliance_matrix_entries, win_percentages, tender_outcomes
(разделы 5.4, 5.5, 7 ТЗ — Этапы 5 и 6)

Четыре таблицы одной миграцией: первые три — звенья одной цепочки (требование → вердикт →
процент), их бессмысленно заводить порознь. `tender_outcomes` — задел на вторую очередь
методики расчёта (раздел 5.5 ТЗ): логикой не заполняется, но схема должна существовать
с самого начала, чтобы накопление истории потом не потребовало миграции.

Revision ID: 0016_tender_analysis
Revises: 0015_si_type_registry_fields
Create Date: 2026-08-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_tender_analysis"
down_revision: Union[str, None] = "0015_si_type_registry_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "requirements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tender_documents.id"),
            nullable=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("criticality", sa.String(length=20), nullable=False, server_default="important"),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("field_name", sa.String(length=150), nullable=True),
        sa.Column("verified_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_requirements_tender_id", "requirements", ["tender_id"])

    op.create_table(
        "compliance_matrix_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Пересчёт обновляет вердикт, а не добавляет второй по той же паре.
        sa.UniqueConstraint(
            "requirement_id", "manufacturer_id", name="uq_compliance_requirement_manufacturer"
        ),
    )
    op.create_index("ix_compliance_tender_id", "compliance_matrix_entries", ["tender_id"])
    op.create_index("ix_compliance_requirement_id", "compliance_matrix_entries", ["requirement_id"])
    op.create_index("ix_compliance_manufacturer_id", "compliance_matrix_entries", ["manufacturer_id"])

    op.create_table(
        "win_percentages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id"),
            nullable=False,
        ),
        sa.Column("percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=True),
        sa.Column("requirements_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requirements_scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tender_id", "manufacturer_id", name="uq_win_tender_manufacturer"),
    )
    op.create_index("ix_win_percentages_tender_id", "win_percentages", ["tender_id"])
    op.create_index("ix_win_percentages_manufacturer_id", "win_percentages", ["manufacturer_id"])

    op.create_table(
        "tender_outcomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # nullable: протокол итогов может относиться к закупке, которой нет в нашей базе.
        sa.Column(
            "tender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenders.id"), nullable=True
        ),
        sa.Column("external_tender_id", sa.String(length=100), nullable=True),
        sa.Column("winner_manufacturer_name", sa.String(length=500), nullable=True),
        sa.Column("winner_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("protocol_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tender_outcomes_tender_id", "tender_outcomes", ["tender_id"])
    op.create_index("ix_tender_outcomes_external_id", "tender_outcomes", ["external_tender_id"])


def downgrade() -> None:
    op.drop_table("tender_outcomes")
    op.drop_table("win_percentages")
    op.drop_table("compliance_matrix_entries")
    op.drop_table("requirements")
