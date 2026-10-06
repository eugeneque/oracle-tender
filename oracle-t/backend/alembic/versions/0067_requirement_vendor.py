"""Требования, привязанные к продукции конкретного производителя (06.10.2026).

ТЗ закупки 32616436166 требует работу через «AdminTools» и «CE Net-Connection» — фирменные
программу и сервер Энергомеры. Матрица считала это обычными «важными» требованиями и давала
МИРТЕК «с оговорками 92%», хотя заявку с другим прибором по этим пунктам отклонят.

* `requirements.vendor` — чью продукцию называет требование;
* `requirements.vendor_exclusive` — выполнимо ли оно только этой продукцией;
* `win_percentages.tz_reference` — ТЗ составлено по модели этого производителя.

Revision ID: 0067_requirement_vendor
Revises: 0066_drop_fake_si_type
Create Date: 2026-10-06
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067_requirement_vendor"
down_revision: Union[str, None] = "0066_drop_fake_si_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("vendor", sa.String(100), nullable=True))
    op.add_column(
        "requirements",
        sa.Column("vendor_exclusive", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "win_percentages",
        sa.Column("tz_reference", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("win_percentages", "tz_reference")
    op.drop_column("requirements", "vendor_exclusive")
    op.drop_column("requirements", "vendor")
