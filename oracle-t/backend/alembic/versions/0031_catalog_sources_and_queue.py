"""ФГИС и сайт производителя как полноценные источники справочника продукции

Три группы изменений, все — под задачи «зарегистрировать ФГИС-адаптер в планировщике» и
«автонаполнение справочника с сайта МИРТЕК»:

1. **Очередь `catalog_lookup_queue`** — общая точка входа для обоих триггеров ФГИС:
   ревалидации по расписанию и запроса по событию из модуля сопоставления. Одна очередь на
   оба — чтобы логика обращения к реестру не разъехалась на две копии.

2. **Пометка «требует ручной проверки»** на `si_types` и `products`. Появилась потому, что
   реестр ФГИС по одной торговой марке отдаёт приборы разных видов измерений (случай
   «Пульсар»: под этой маркой в Госреестре и электросчётчики, и пожарные извещатели, и
   расходомеры воды), а автосохранение первого попавшегося кандидата подкладывало в
   справочник «Описание типа» чужого прибора. Теперь такие записи помечаются, а не
   сохраняются молча. Второй потребитель пометки — модели, пропавшие со страницы категории
   сайта производителя: их нельзя удалять автоматически.

3. **Поля записи каталога, приходящие с сайта производителя**: заводское исполнение, URL
   карточки как ключ идемпотентности, источник данных и резервное JSON-поле под
   характеристики, которым не нашлось места в Приложении C.

Плюс два новых источника в `sources` (ФГИС и сайт МИРТЕК) — чтобы они были видны и
управляемы в общем разделе «Источники», как остальные адаптеры, — и заполненный сайт
у самого МИРТЕК (в `manufacturers` он был `NULL`).

Все новые колонки nullable либо со значением по умолчанию: существующие записи остаются
валидными, backfill не требуется.

Revision ID: 0031_catalog_sources_and_queue
Revises: 0030_ai_relevance
Create Date: 2026-09-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.sources_data import CATALOG_SOURCES

revision: str = "0031_catalog_sources_and_queue"
down_revision: Union[str, None] = "0030_ai_relevance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIRTEK_WEBSITE = "https://mirtekgroup.com"


def upgrade() -> None:
    # --- 1. Очередь запросов к источникам справочника ---
    op.create_table(
        "catalog_lookup_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("adapter_key", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "si_type_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("si_types.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            # `clock_timestamp()`, а не `now()`: `now()` внутри одной транзакции возвращает
            # одно и то же время, и записи, поставленные в очередь пачкой, получали бы
            # одинаковый `created_at` — порядок обработки становился бы неопределённым
            # (та же причина, что и в `background_jobs`, миграция 0022).
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_catalog_lookup_queue_adapter_key", "catalog_lookup_queue", ["adapter_key"])
    op.create_index("ix_catalog_lookup_queue_status", "catalog_lookup_queue", ["status"])
    op.create_index("ix_catalog_lookup_queue_created_at", "catalog_lookup_queue", ["created_at"])

    # --- 2. Пометка «требует ручной проверки» ---
    for table in ("si_types", "products"):
        op.add_column(
            table,
            sa.Column("review_status", sa.String(length=20), nullable=False, server_default="ok"),
        )
        op.add_column(table, sa.Column("review_reason", sa.Text(), nullable=True))
    op.add_column("si_types", sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))

    # --- 3. Поля записи каталога с сайта производителя ---
    op.add_column("products", sa.Column("execution", sa.String(length=100), nullable=True))
    op.add_column("products", sa.Column("source_url", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("data_source", sa.String(length=30), nullable=True))
    op.add_column(
        "products",
        sa.Column("extra_specifications", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.add_column("products", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    # Уникальность URL карточки — то, чем обеспечивается идемпотентность повторного обхода
    # (п.2.4 задания). Именно ограничение в БД, а не проверка в коде: два параллельных
    # запуска синхронизации иначе создали бы дубли.
    op.create_unique_constraint("uq_products_source_url", "products", ["source_url"])

    # --- Источники и сайт МИРТЕК ---
    sources = sa.table(
        "sources",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("url", sa.String),
        sa.column("type", sa.String),
        sa.column("status", sa.String),
        sa.column("polling_schedule", sa.String),
        sa.column("adapter_key", sa.String),
        sa.column("adapter_status", sa.String),
        sa.column("note", sa.Text),
    )
    op.bulk_insert(
        sources,
        [
            {
                "key": key,
                "name": name,
                "url": url,
                "type": type_,
                "status": "active",
                "polling_schedule": schedule,
                "adapter_key": adapter_key,
                "adapter_status": adapter_status,
                "note": note,
            }
            for key, name, url, type_, adapter_key, adapter_status, schedule, note in CATALOG_SOURCES
        ],
    )

    op.execute(
        sa.text(
            "UPDATE manufacturers SET website = :site WHERE is_mirtek = true AND website IS NULL"
        ).bindparams(site=MIRTEK_WEBSITE)
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE manufacturers SET website = NULL WHERE is_mirtek = true AND website = :site").bindparams(
            site=MIRTEK_WEBSITE
        )
    )
    op.execute(
        sa.text("DELETE FROM sources WHERE key IN ('fgis', 'mirtek_site')")
    )

    op.drop_constraint("uq_products_source_url", "products", type_="unique")
    for column in ("last_seen_at", "extra_specifications", "data_source", "source_url", "execution"):
        op.drop_column("products", column)
    op.drop_column("si_types", "last_checked_at")
    for table in ("products", "si_types"):
        op.drop_column(table, "review_reason")
        op.drop_column(table, "review_status")

    op.drop_index("ix_catalog_lookup_queue_created_at", table_name="catalog_lookup_queue")
    op.drop_index("ix_catalog_lookup_queue_status", table_name="catalog_lookup_queue")
    op.drop_index("ix_catalog_lookup_queue_adapter_key", table_name="catalog_lookup_queue")
    op.drop_table("catalog_lookup_queue")
