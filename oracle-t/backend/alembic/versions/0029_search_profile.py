"""Профиль релевантности: группы ключевых слов и отметки на тендерах (раздел 5.1.1 ТЗ).

До этой миграции охват сбора был захардкожен двумя фразами в каждом адаптере
(«счетчик электрической энергии», «прибор учета электрической энергии»), из-за чего система
не видела ни поверку, ни монтаж, ни обслуживание приборов учёта — целые типы закупок просто
не попадали в базу. Профиль выносит охват в данные: девять групп потребностей, у каждой свои
ключи, исключения и коды ОКПД2.

Заодно на `tenders` появляются поля результата фильтра (раздел 7 ТЗ): прошёл или нет и какая
группа сработала. Второе нужно не меньше первого — без него нельзя понять, почему тендер
попал в выборку, и настроить профиль.

Revision ID: 0029_search_profile
Revises: 0028_company_profile_kpp
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_search_profile"
down_revision: Union[str, None] = "0028_company_profile_kpp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("min_nmck", sa.Numeric(18, 2), nullable=True),
        sa.Column("ai_score_threshold", sa.Numeric(5, 2), nullable=True),
        sa.Column("regions_scope", postgresql.ARRAY(sa.String(length=2)), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "search_keyword_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "search_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_profiles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "keywords",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "exclusion_keywords",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("okpd2_codes", postgresql.ARRAY(sa.String(length=20)), nullable=True),
        sa.Column("match_mode", sa.String(length=10), nullable=False, server_default="any"),
        sa.Column(
            "search_queries",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- результат фильтра на тендере (раздел 7 ТЗ) -------------------------------------
    # `passed_relevance_filter` намеренно nullable: `null` — «фильтр ещё не применялся», и
    # это не то же самое, что «проверен и не прошёл». Уже собранные 7000+ тендеров получают
    # именно null, а не false — объявлять их нерелевантными задним числом система не вправе.
    op.add_column(
        "tenders", sa.Column("passed_relevance_filter", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "tenders",
        sa.Column(
            "matched_keyword_group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("search_keyword_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tenders_passed_relevance_filter", "tenders", ["passed_relevance_filter"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenders_passed_relevance_filter", table_name="tenders")
    op.drop_column("tenders", "matched_keyword_group_id")
    op.drop_column("tenders", "passed_relevance_filter")
    op.drop_table("search_keyword_groups")
    op.drop_table("search_profiles")
