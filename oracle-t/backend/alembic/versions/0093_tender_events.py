"""Отслеживание изменений по закупкам и журнал событий Bitrix24 (05.11.2026).

Bitrix24 принимает изменения по тендерам отдельным разделом «Журнал событий»: актуальное
значение — в `deal_fields`, само изменение («было → стало») — в массиве `events`. Для
сравнения нужен слепок закупки на прошлую проверку (`tender_monitors`), а обнаруженные
изменения хранятся в `tender_events` до отправки.

Revision ID: 0093_tender_events
Revises: 0092_software_integrations
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0093_tender_events"
down_revision: Union[str, None] = "0092_software_integrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_monitors",
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("checks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("tender_id"),
    )
    op.create_table(
        "tender_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.String(191), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("field_key", sa.String(300), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("scope", sa.String(12), nullable=False, server_default="PROCEDURE"),
        sa.Column("lot_number", sa.String(30), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_document", sa.String(500), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_version", sa.String(100), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("supersedes_event_id", sa.String(191), nullable=True),
        sa.Column("delivery", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("request_id", sa.String(191), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_tender_events_event_id"),
    )
    op.create_index("ix_tender_events_tender_id", "tender_events", ["tender_id"])
    op.create_index("ix_tender_events_delivery", "tender_events", ["delivery"])


def downgrade() -> None:
    op.drop_index("ix_tender_events_delivery", table_name="tender_events")
    op.drop_index("ix_tender_events_tender_id", table_name="tender_events")
    op.drop_table("tender_events")
    op.drop_table("tender_monitors")
