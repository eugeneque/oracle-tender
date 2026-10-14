"""Карточка модели: признак «извлечено ИИ», журнал опроса реестров допуска, без доли рынка
(замечания тендерного отдела 13.10.2026).

* `product_characteristics.ai_extracted` — значение вычитано моделью (из «Описания типа»,
  руководства или текста «Ключевых особенностей»), а не взято дословно из таблицы сайта или
  карточки реестра. Пометка «требует проверки» нужна только таким значениям: на номер в
  Госреестре или габариты из таблицы на сайте она вызывала вопрос «зачем проверять то, что
  записано по ссылке». По источнику признак не восстановить: под `fgis_description_type`
  лежат и прямые факты карточки типа (номер, МПИ), и разбор документа моделью, а под
  `manufacturer_site` — и таблица сайта, и разбор «Ключевых особенностей». Заполнение
  существующих строк — по тому же правилу, что используют сервисы: прямые факты карточки
  типа перечислены, у таблицы сайта `confidence` пуст, у модели — заполнен.
* `admission_registry_polls` — когда и с каким итогом опрашивался реестр допуска (ГИСП,
  перечень Россетей): «не проверялось» у модели должно отличаться от «реестр опрошен
  вчера, модели в нём не нашлось».
* `product_registry_records.auto_found_at` — запись заведена автоматическим опросом, а не
  человеком; ручная правка её сбрасывает, и следующий опрос такую запись не трогает.
* `manufacturers.market_share_*` — удалены: оценка Onside была только у пяти производителей
  и тендерному отделу не нужна (замечание 13.10.2026, п. 11).

Revision ID: 0073_catalog_data_quality
Revises: 0072_remove_seldon
Create Date: 2026-10-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073_catalog_data_quality"
down_revision: Union[str, None] = "0072_remove_seldon"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Факты карточки типа, которые `fgis_description_ingest` проставляет без модели.
_REGISTRY_FACTS = (
    ("Метрологические характеристики", "Номер в Госреестре"),
    ("Метрологические характеристики", "Утверждение типа СИ"),
    ("Метрологические характеристики", "Межповерочный интервал"),
    ("Метрологические характеристики", "Дата утверждения типа"),
    ("Документация", "Ссылка на описание типа"),
)


def upgrade() -> None:
    op.add_column(
        "product_characteristics",
        sa.Column("ai_extracted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    facts = " OR ".join(
        f"(group_name = '{group}' AND field_name = '{field}')" for group, field in _REGISTRY_FACTS
    )
    op.execute(
        "UPDATE product_characteristics SET ai_extracted = true WHERE "
        "source = 'user_manual' "
        f"OR (source = 'fgis_description_type' AND NOT ({facts})) "
        "OR (source = 'manufacturer_site' AND confidence IS NOT NULL)"
    )

    op.add_column(
        "product_registry_records",
        sa.Column("auto_found_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "admission_registry_polls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("registry", sa.String(40), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_relevant", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_saved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_admission_registry_polls_registry_started",
        "admission_registry_polls",
        ["registry", "started_at"],
    )

    op.drop_column("manufacturers", "market_share_source")
    op.drop_column("manufacturers", "market_share_pct")


def downgrade() -> None:
    op.add_column("manufacturers", sa.Column("market_share_pct", sa.Numeric(5, 2), nullable=True))
    op.add_column("manufacturers", sa.Column("market_share_source", sa.Text(), nullable=True))
    op.drop_index("ix_admission_registry_polls_registry_started", table_name="admission_registry_polls")
    op.drop_table("admission_registry_polls")
    op.drop_column("product_registry_records", "auto_found_at")
    op.drop_column("product_characteristics", "ai_extracted")
