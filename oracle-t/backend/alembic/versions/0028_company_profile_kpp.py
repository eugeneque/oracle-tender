"""КПП в профиле компании.

КПП не дублирует ИНН: у организации один ИНН, но свой КПП у каждого обособленного
подразделения, и в реквизитах заявки (раздел 5.6 ТЗ, вкладка «Заявка») требуется именно пара
ИНН/КПП. Хранить его внутри строки адреса или реквизитов нельзя — он нужен отдельным полем
для подстановки в документы.

Revision ID: 0028_company_profile_kpp
Revises: 0027_company_participations
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_company_profile_kpp"
down_revision: Union[str, None] = "0027_company_participations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("company_profile", sa.Column("kpp", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("company_profile", "kpp")
