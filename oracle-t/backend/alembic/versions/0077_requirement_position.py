"""Позиция спецификации у требования (15.10.2026).

Зачем. Закупка бывает из нескольких позиций — 32616469643 «Россети Тюмень»: ВШУ с
однофазным ПУ и ВШУ с трёхфазным ПУ полукосвенного включения в одном лоте. Извлечение
сливало требования обеих позиций в один плоский список, и матрица сравнивала
производителя целиком со смесью требований разных приборов: «Номинальное напряжение:
400 В» сверялось с однофазной моделью из карточки — «не соответствует» у всех, включая
эталон ТЗ. Заплатки на выходе (`apply_lot_variants`, раздел 60 ARCHITECTURE.md) гасили
такие отказы эвристикой, но не знали, к какому прибору относится требование.

`position` — номер и название позиции, как её назвала модель извлечения («1. ВШУ с
однофазным ПУ»); NULL — требование общее для всех позиций или закупка из одной позиции.
По нему матрица подбирает модели и сверяет требования отдельно для каждой позиции
(раздел 61 ARCHITECTURE.md).

Без заполнения: у старых закупок позиции нет, для них остаётся эвристика
`apply_lot_variants`; повторный анализ документации проставит позиции.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0077_requirement_position"
down_revision: Union[str, None] = "0076_catalog_queue_author"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirements", sa.Column("position", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("requirements", "position")
