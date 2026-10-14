"""Автор задачи очереди справочника (14.10.2026).

Зачем. Центр уведомлений в шапке показывает пользователю его запуски и итоги по ним: «опрос
каталога закончен», «обход сайта производителя — ошибка». У разборов закупок автор есть
(`background_jobs.created_by_id`), а у очереди справочника — нет: кто нажал «Опросить
каталог», знал только журнал операций. Без автора нельзя ни показать запуск тому, кто его
сделал, ни отличить его от планового прогона.

Без заполнения: старые задачи остаются «без автора», то есть плановыми.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0076_catalog_queue_author"
down_revision: Union[str, None] = "0075_documents_read_marks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "catalog_lookup_queue",
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_catalog_lookup_queue_created_by_id", "catalog_lookup_queue", ["created_by_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_lookup_queue_created_by_id", table_name="catalog_lookup_queue")
    op.drop_column("catalog_lookup_queue", "created_by_id")
