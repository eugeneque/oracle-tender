"""Позиции закупки — таблица спецификации заказчика (23.10.2026, раздел 74 ARCHITECTURE.md).

Тендерный отдел просил видеть в Bitrix24 «какие нужны приборы и количество — ту таблицу,
которую публикует заказчик». У требования была только подпись позиции без количества;
`tender_positions` хранит строки спецификации (номер, наименование, единица, количество,
источник), извлечённые моделью из документации. Портал принимает их строками
`procurement_position` (`docs/AI_API.md` 1.4.1).

Revision ID: 0084_tender_positions
Revises: 0083_ai_feedback_precedents
Create Date: 2026-10-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084_tender_positions"
down_revision: Union[str, None] = "0083_ai_feedback_precedents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_no", sa.String(30), nullable=True),
        sa.Column("lot_number", sa.String(30), nullable=False, server_default="1"),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=True),
        sa.Column("quantity_text", sa.String(100), nullable=True),
        sa.Column("document_name", sa.String(500), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tender_positions_tender_id", "tender_positions", ["tender_id"])


def downgrade() -> None:
    op.drop_index("ix_tender_positions_tender_id", table_name="tender_positions")
    op.drop_table("tender_positions")
