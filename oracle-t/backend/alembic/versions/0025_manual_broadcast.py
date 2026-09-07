"""Произвольные письма администратора: оформленное тело и автор рассылки.

Revision ID: 0025_manual_broadcast
Revises: 0024_tender_cards
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_manual_broadcast"
down_revision: Union[str, None] = "0024_tender_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Тело письма остаётся в `body` текстом — журнал и plain-часть письма читают его, как и
    # раньше. Оформленная версия кладётся рядом отдельной колонкой: она есть только у
    # ручных рассылок, и держать в одной колонке то текст, то HTML значило бы гадать при
    # каждом чтении, что там сейчас.
    op.add_column("notifications", sa.Column("body_html", sa.Text(), nullable=True))
    op.add_column(
        "notifications",
        sa.Column(
            "sent_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("notifications", "sent_by_id")
    op.drop_column("notifications", "body_html")
