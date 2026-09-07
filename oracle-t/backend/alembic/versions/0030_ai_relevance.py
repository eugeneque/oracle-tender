"""Отметка ИИ-отбора на тендере: «Подобрано ИИ» (разделы 5.1.1, 5.4 ТЗ).

Профиль ключевых слов отвечает «есть ли нужные слова», а эти поля — «это правда наша
закупка»: модель отличает поставку счётчиков от аренды помещения, где счётчики упомянуты в
составе имущества.

`ai_relevant` nullable: `NULL` — «модель не смотрела», и это не то же самое, что `false`
(«посмотрела и отклонила»). Сбой сети или выключенная интеграция не должны прятать закупку.

Revision ID: 0030_ai_relevance
Revises: 0029_search_profile
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_ai_relevance"
down_revision: Union[str, None] = "0029_search_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("ai_relevant", sa.Boolean(), nullable=True))
    op.add_column("tenders", sa.Column("ai_relevance_reason", sa.Text(), nullable=True))
    op.add_column(
        "tenders", sa.Column("ai_relevance_confidence", sa.Integer(), nullable=True)
    )
    op.add_column(
        "tenders",
        sa.Column("ai_relevance_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Индекс по паре: список по умолчанию сортирует подобранные ИИ первыми и фильтрует по
    # этому же признаку — одиночный индекс по `ai_relevant` пришлось бы дополнять сортировкой.
    op.create_index(
        "ix_tenders_ai_relevant_checked",
        "tenders",
        ["ai_relevant", "ai_relevance_checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tenders_ai_relevant_checked", table_name="tenders")
    op.drop_column("tenders", "ai_relevance_checked_at")
    op.drop_column("tenders", "ai_relevance_confidence")
    op.drop_column("tenders", "ai_relevance_reason")
    op.drop_column("tenders", "ai_relevant")
