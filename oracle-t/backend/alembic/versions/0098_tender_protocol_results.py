"""Итоги процедуры из протоколов для Bitrix24 (11.11.2026).

Перечень «Что еще нужно от ИИ» (пп. 69–87): участники с ИНН, цены, допуск и причины
отклонения, победитель, итоговость протокола, результат нашей заявки. Таблица
`tender_outcomes` (победитель и цена) логикой так и не заполнялась, и участников в ней не
уместить; разбор протоколов хранится одним JSONB-документом на закупку и заменяется целиком.

Revision ID: 0098_tender_protocol_results
Revises: 0097_tender_contract_conditions
Create Date: 2026-11-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0098_tender_protocol_results"
down_revision: Union[str, None] = "0097_tender_contract_conditions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_protocol_results",
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tender_id"),
    )


def downgrade() -> None:
    op.drop_table("tender_protocol_results")
