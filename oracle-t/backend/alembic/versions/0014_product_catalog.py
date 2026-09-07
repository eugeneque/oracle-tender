"""product catalog: manufacturers, si_types, products, product_characteristics
(раздел 4.3, 5.3, 7 ТЗ — Этап 4)

Revision ID: 0014_product_catalog
Revises: 0013_yandex_ai_studio_settings
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.seed.manufacturers_data import MANUFACTURERS

revision: str = "0014_product_catalog"
down_revision: Union[str, None] = "0013_yandex_ai_studio_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    manufacturers = op.create_table(
        "manufacturers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("legal_name", sa.String(length=500), nullable=False),
        sa.Column("brand_name", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("is_mirtek", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "si_types",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "manufacturer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manufacturers.id"), nullable=False
        ),
        sa.Column("si_code", sa.String(length=100), nullable=False),
        sa.Column("description_type_url", sa.Text(), nullable=True),
        sa.Column("description_type_text", sa.Text(), nullable=True),
        sa.Column("allowed_modifications", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="auto_search"),
        sa.Column("verified_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Дедупликация при повторном автопоиске (раздел 5.3 ТЗ) — та же идея, что и
        # `uq_tender_documents_tender_source_url` в Этапе 3: повторный запуск поиска по тому же
        # производителю обновляет уже найденную запись, а не плодит дубли.
        sa.UniqueConstraint("manufacturer_id", "si_code", name="uq_si_types_manufacturer_si_code"),
    )
    op.create_index("ix_si_types_manufacturer_id", "si_types", ["manufacturer_id"])
    op.create_index("ix_si_types_si_code", "si_types", ["si_code"])

    op.create_table(
        "products",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "manufacturer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("manufacturers.id"), nullable=False
        ),
        sa.Column("si_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("si_types.id"), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("article", sa.String(length=100), nullable=True),
        sa.Column("device_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_products_manufacturer_id", "products", ["manufacturer_id"])
    op.create_index("ix_products_si_type_id", "products", ["si_type_id"])

    op.create_table(
        "product_characteristics",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("group_name", sa.String(length=100), nullable=False),
        sa.Column("field_name", sa.String(length=150), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("verified_by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("product_id", "group_name", "field_name", name="uq_product_characteristics_field"),
    )
    op.create_index("ix_product_characteristics_product_id", "product_characteristics", ["product_id"])

    op.bulk_insert(
        manufacturers,
        [
            {
                "legal_name": legal_name,
                "brand_name": brand_name,
                "website": website,
                "is_mirtek": is_mirtek,
            }
            for legal_name, brand_name, website, is_mirtek in MANUFACTURERS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_product_characteristics_product_id", table_name="product_characteristics")
    op.drop_table("product_characteristics")
    op.drop_index("ix_products_si_type_id", table_name="products")
    op.drop_index("ix_products_manufacturer_id", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_si_types_si_code", table_name="si_types")
    op.drop_index("ix_si_types_manufacturer_id", table_name="si_types")
    op.drop_table("si_types")
    op.drop_table("manufacturers")
