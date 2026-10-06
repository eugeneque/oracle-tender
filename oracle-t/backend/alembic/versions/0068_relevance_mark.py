"""Кто и когда отметил закупку «Релевантна» / «Неактуально» (06.10.2026).

Отметка сама по себе живёт в этапе (`stage`: «на проверке» и дальше — релевантна,
«отклонена» — неактуальна), но автора и времени у неё не было. Для общих списков
«Релевантные» и «Неактуальные» и для метки в классических списках нужно видеть, кто из
специалистов уже посмотрел закупку, — иначе её проверяет второй человек.

Заполнение — по истории изменений: последняя правка этапа или статуса релевантности с
автором. Ручные заявки (этап «на проверке» с момента создания) получают автора заявки.

Revision ID: 0068_relevance_mark
Revises: 0067_requirement_vendor
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0068_relevance_mark"
down_revision: Union[str, None] = "0067_requirement_vendor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenders",
        sa.Column(
            "relevance_marked_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "tenders", sa.Column("relevance_marked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE tenders t
        SET relevance_marked_by_id = h.user_id, relevance_marked_at = h.created_at
        FROM (
            SELECT DISTINCT ON (tender_id) tender_id, user_id, created_at
            FROM tender_history
            WHERE field_name IN ('stage', 'relevance_status') AND user_id IS NOT NULL
            ORDER BY tender_id, created_at DESC
        ) h
        WHERE h.tender_id = t.id AND t.stage <> 'ai_selected'
        """
    )
    # Ручные заявки: отметки в истории нет, решение «наша» принял автор заявки.
    op.execute(
        """
        UPDATE tenders t
        SET relevance_marked_by_id = t.assignee_id, relevance_marked_at = t.created_at
        FROM sources s
        WHERE s.id = t.source_id AND s.key = 'manual'
          AND t.stage <> 'ai_selected' AND t.relevance_marked_by_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("tenders", "relevance_marked_at")
    op.drop_column("tenders", "relevance_marked_by_id")
