"""Подробности события — поле `data` (05.11.2026).

Bitrix24 (документация «События СОВА и Bitrix24», редакция 2026-11-05) принимает у события
необязательный объект `data`: валюта суммы, вид обеспечения (заявки/договора), вид протокола и
признак итогового. Храним его рядом с событием, чтобы повторная отправка несла то же
содержимое: тот же `event_id` с другим содержимым портал отклоняет (409).

Revision ID: 0094_tender_event_data
Revises: 0093_tender_events
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094_tender_event_data"
down_revision: Union[str, None] = "0093_tender_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tender_events", sa.Column("data", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tender_events", "data")
