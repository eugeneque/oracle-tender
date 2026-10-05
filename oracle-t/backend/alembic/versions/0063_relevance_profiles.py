"""Профили релевантности, которые заводят сами специалисты (05.10.2026).

Две таблицы: сами профили (слова, исключения, коды ОКПД2, автор) и их совпадения с
закупками — чтобы фильтр на странице тендеров был подзапросом, а не разбором текста всех
закупок на каждое открытие списка.

Revision ID: 0063_relevance_profiles
Revises: 0062_seldon_tenderplan_sources
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0063_relevance_profiles"
down_revision: Union[str, None] = "0062_seldon_tenderplan_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "relevance_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("keywords", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("exclusion_keywords", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "okpd2_codes",
            postgresql.ARRAY(sa.String(20)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("match_mode", sa.String(10), nullable=False, server_default="any"),
        sa.Column("rules_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("matched_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "relevance_profile_matches",
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relevance_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_relevance_profile_matches_tender_id", "relevance_profile_matches", ["tender_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_relevance_profile_matches_tender_id", table_name="relevance_profile_matches")
    op.drop_table("relevance_profile_matches")
    op.drop_table("relevance_profiles")
