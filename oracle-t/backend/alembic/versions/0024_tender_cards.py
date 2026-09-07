"""Полная карточка закупки с сайта источника (раздел 5.6 ТЗ).

Revision ID: 0024_tender_cards
Revises: 0023_api_clients
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_tender_cards"
down_revision: Union[str, None] = "0023_api_clients"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_cards",
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # JSONB, а не набор колонок: состав разделов карточки различается у 44-ФЗ и 223-ФЗ,
        # между способами закупки и редакциями формы ЕИС. Жёсткая схема означала бы миграцию
        # на каждое изменение формы и молчаливую потерю непредусмотренных полей.
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("tender_cards")
