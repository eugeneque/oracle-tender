"""НМЦК с НДС и без НДС из документации (раздел 106 ARCHITECTURE.md).

Площадка отдаёт одну «Сумму», и не понять, с НДС она или без, — а в документации это написано
в пункте о начальной (максимальной) цене («…в т.ч. НДС 22% 394 555,13 руб.», «…руб. без
НДС»). Разбор — кодом (`price_vat_service`), результат — один JSONB на закупку: ставка, сумма
налога, обе цены, какая из них досчитана, документ и цитата. Заполнение старых закупок —
`python -m app.services.price_vat_service`.

Revision ID: 0104_tender_price_vat
Revises: 0103_drop_auto_review
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0104_tender_price_vat"
down_revision: Union[str, None] = "0103_drop_auto_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("price_vat", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "price_vat")
