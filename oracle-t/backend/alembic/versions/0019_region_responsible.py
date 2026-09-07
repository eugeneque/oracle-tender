"""справочник «регион → ответственный/руководитель» (раздел 5.6 ТЗ, Приложение D)

Два поля Приложения D («Ответственный за регион», «Руководитель ответственный за регион»)
неоткуда взять без этого справочника — до сих пор их просто не существовало ни в схеме, ни
в интерфейсе. Отдельная таблица, а не колонки в `regions`: `regions` сидируется из
Приложения H и перезаписывается при пересиде, а назначения ответственных вводит заказчик и
терять их нельзя.

Ключ — код региона: на один регион один ответственный и один руководитель (раздел 11,
вопрос №4 ТЗ).

Revision ID: 0019_region_responsible
Revises: 0018_source_credentials
Create Date: 2026-08-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_region_responsible"
down_revision: Union[str, None] = "0018_source_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "region_responsibles",
        sa.Column("region_code", sa.String(length=2), sa.ForeignKey("regions.code"), primary_key=True),
        sa.Column("responsible_name", sa.String(length=255), nullable=True),
        sa.Column("manager_name", sa.String(length=255), nullable=True),
        # Кто последним правил назначение — вопрос «почему тендер ушёл этому человеку»
        # возникает регулярно, и история изменений здесь дешевле, чем разбирательство.
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("region_responsibles")
