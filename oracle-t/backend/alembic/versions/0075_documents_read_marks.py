"""Отметки «документ уже прочитан моделью» у модели прибора (14.10.2026).

Зачем. Второй проход опроса каталога (`catalog_learning`) пропускал модель, только если у
неё уже есть характеристики из «Описания типа» или руководства. Модель, из документа
которой ИИ ничего не извлёк (47 по «Описаниям типа», 349 по руководствам на 14.10.2026),
отправлялась в модель заново при каждом опросе — а опрос идёт дважды в день и при каждом
перезапуске сервера. За один день так ушло ~7,5 млн токенов.

- `fgis_description_read_version` — редакция «Описания типа», которую модель уже читала для
  этого прибора; повторно читается только новая редакция.
- `manual_read_url` — ссылка на руководство, которое уже прочитано; повторно — только если
  ссылка сменилась.

Без заполнения: уже прочитанные с результатом и так пропускаются по наличию характеристик,
а пустые прочитаются ещё один раз и получат отметку.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075_documents_read_marks"
down_revision: Union[str, None] = "0074_ai_usage_and_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("fgis_description_read_version", sa.String(20), nullable=True))
    op.add_column("products", sa.Column("manual_read_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "manual_read_url")
    op.drop_column("products", "fgis_description_read_version")
