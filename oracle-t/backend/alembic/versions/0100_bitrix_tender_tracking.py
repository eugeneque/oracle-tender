"""Отслеживание закупок, выгруженных в Bitrix24: автоматически, с отметкой просмотра и письмом
(12.11.2026, раздел 100 ARCHITECTURE.md).

Коллеги из Bitrix24: «Журнал событий» на портале не пополняется, хотя в Сове данные свежие.
Причина — наблюдение (раздел 84) включалось только вручную, и выгрузка в Bitrix24 была
единственным рукопожатием. Теперь каждая выгруженная закупка встаёт под наблюдение сама.

* `tender_monitors.auto` — наблюдение поставлено системой (выгрузка в Bitrix24), а не
  человеком; такое система сама и снимает (отмена процедуры, давно прошедший срок). Снятое
  человеком (`enabled = false`) повторно не включается.
* `tender_event_views` — когда пользователь последний раз смотрел «Журнал событий» закупки:
  события после этой отметки подсвечиваются зелёной точкой.
* `notification_settings.trigger_tender_changed` — письмо об изменениях по закупке.

Revision ID: 0100_bitrix_tender_tracking
Revises: 0099_meter_market_remap_letters
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0100_bitrix_tender_tracking"
down_revision: Union[str, None] = "0099_meter_market_remap_letters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tender_monitors",
        sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "notification_settings",
        sa.Column("trigger_tender_changed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "tender_event_views",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "tender_id"),
    )
    op.create_index("ix_tender_events_created_at", "tender_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_tender_events_created_at", table_name="tender_events")
    op.drop_table("tender_event_views")
    op.drop_column("notification_settings", "trigger_tender_changed")
    op.drop_column("tender_monitors", "auto")
