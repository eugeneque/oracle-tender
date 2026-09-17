"""Обучение справочника по Аршину: исполнения из карточки типа и след разбора «Описания типа»

Замечание заказчика (15.09.2026): система должна учиться техническим характеристикам
приборов всех производителей по Аршину (ФГИС) и замечать изменения в «Описании типа». Живой
пример — НАРТИС-И100: в редакции 2 «Описания типа» (приказ № 1037 от 01.06.2026) появился
корпус W115, которого нет в каталоге на сайте производителя, а руководство на него находится
только поиском в Яндексе.

Что меняется в схеме:

* `si_types.tested_modifications` — исполнения, представленные на испытания, из карточки
  типа (`j_factorynums`, «На испытания представлены: …»). Именно по ним система узнаёт о
  новом исполнении раньше, чем оно появится на сайте;
* `si_types.description_type_extracted_version` — редакция «Описания типа», из которой
  характеристики уже разнесены по моделям. Расходится с `description_type_version` —
  значит, вышла новая редакция и разбор надо повторить;
* `si_types.description_type_changed_at` — когда ревалидация заметила новую редакцию: в
  интерфейсе это «описание типа изменилось», и человеку важно видеть дату;
* `products.registry_modification` — полное условное обозначение исполнения из реестра
  («НАРТИС-И100-W115-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D») у записей, заведённых из
  Аршина. По нему и легенде структуры обозначения расшифровываются характеристики.

Revision ID: 0040_registry_learning
Revises: 0039_catalog_documents
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_registry_learning"
down_revision: Union[str, None] = "0039_catalog_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "si_types",
        sa.Column(
            "tested_modifications",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "si_types",
        sa.Column("description_type_extracted_version", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "si_types",
        sa.Column("description_type_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("products", sa.Column("registry_modification", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "registry_modification")
    op.drop_column("si_types", "description_type_changed_at")
    op.drop_column("si_types", "description_type_extracted_version")
    op.drop_column("si_types", "tested_modifications")
