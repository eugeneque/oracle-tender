"""История участий МИРТЕК и юридические данные профиля компании.

Уточнение ТЗ от 03.09.2026 (разделы 5.5.1, 5.6, 7): измерение History перестаёт ждать
`tender_outcomes` и считается из реальной истории участия компании по ИНН. Отсюда два
изменения одной миграцией — по отдельности они бессмысленны:

* `company_participations` — сама история;
* юридические поля `company_profile` — ИНН, по которому история и запрашивается.

Источник наполнения — реестр контрактов ЕИС по ИНН поставщика (решение 04.09.2026). Он
открыт и не требует ни учётной записи, ни ключа, поэтому таблицы для хранения секретов
доступа здесь нет: изначально задуманная синхронизация с OPTI отменена — у сервиса нет ни
публичного API, ни выдаваемых нам токенов.

Revision ID: 0027_company_participations
Revises: 0026_ai_profile_and_stage
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_company_participations"
down_revision: Union[str, None] = "0026_ai_profile_and_stage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- company_profile: юридические данные (раздел 7 ТЗ) -----------------------------
    op.add_column("company_profile", sa.Column("legal_name", sa.String(length=500), nullable=True))
    op.add_column("company_profile", sa.Column("inn", sa.String(length=20), nullable=True))
    op.add_column("company_profile", sa.Column("ogrn", sa.String(length=20), nullable=True))
    op.add_column("company_profile", sa.Column("registration_date", sa.Date(), nullable=True))
    op.add_column("company_profile", sa.Column("legal_address", sa.Text(), nullable=True))
    op.add_column(
        "company_profile",
        sa.Column(
            "field_sources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_company_profile_inn", "company_profile", ["inn"])

    # --- company_participations --------------------------------------------------------
    op.create_table(
        "company_participations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_tender_id", sa.String(length=100), nullable=True),
        sa.Column("tender_title", sa.Text(), nullable=True),
        sa.Column("customer_name", sa.String(length=500), nullable=True),
        sa.Column("customer_org_id", sa.String(length=100), nullable=True),
        sa.Column("our_inn", sa.String(length=20), nullable=True),
        sa.Column("our_bid", sa.Numeric(18, 2), nullable=True),
        sa.Column("price_drop_pct", sa.Numeric(6, 2), nullable=True),
        sa.Column("competitors_count", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("final_contract_value", sa.Numeric(18, 2), nullable=True),
        sa.Column("executed_at", sa.Date(), nullable=True),
        sa.Column("lessons_learned_md", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "manufacturer_id", "external_tender_id", name="uq_company_participations_external"
        ),
    )
    op.create_index(
        "ix_company_participations_external_tender_id",
        "company_participations",
        ["external_tender_id"],
    )
    op.create_index("ix_company_participations_our_inn", "company_participations", ["our_inn"])
    op.create_index("ix_company_participations_outcome", "company_participations", ["outcome"])
    op.create_index(
        "ix_company_participations_executed_at", "company_participations", ["executed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_company_participations_executed_at", table_name="company_participations")
    op.drop_index("ix_company_participations_outcome", table_name="company_participations")
    op.drop_index("ix_company_participations_our_inn", table_name="company_participations")
    op.drop_index(
        "ix_company_participations_external_tender_id", table_name="company_participations"
    )
    op.drop_table("company_participations")
    op.drop_index("ix_company_profile_inn", table_name="company_profile")
    op.drop_column("company_profile", "field_sources")
    op.drop_column("company_profile", "legal_address")
    op.drop_column("company_profile", "registration_date")
    op.drop_column("company_profile", "ogrn")
    op.drop_column("company_profile", "inn")
    op.drop_column("company_profile", "legal_name")
