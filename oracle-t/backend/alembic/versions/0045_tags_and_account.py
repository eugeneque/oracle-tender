"""Теги закупок и учётная запись пользователя: аватар (замечания 17.09.2026).

Две вещи:

* таблицы `tender_tags` и `tender_tag_links` — общие для команды метки на закупках («срочно»,
  «Россети», «переповерка»). Общие, а не личные, как избранное: тег — это язык отдела, и
  метка, которую поставил один, должна быть видна остальным. Имя уникально без учёта
  регистра (частичный индекс по `lower(name)`), цвет хранится ключом палитры, а не HEX:
  палитра живёт в интерфейсе, и смена оттенка не должна требовать миграции данных;
* аватар пользователя — байтами в `users`, а не файлом в хранилище: картинка уменьшается
  до 256 px в браузере до отправки и весит десятки килобайт, а отдельный каталог файлов
  пришлось бы переносить вместе с базой при каждом развёртывании. `avatar_updated_at`
  нужен интерфейсу как признак «аватар есть» и метка для сброса кэша.

Revision ID: 0045_tags_and_account
Revises: 0044_registry_records
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_tags_and_account"
down_revision: Union[str, None] = "0044_registry_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("avatar_content_type", sa.String(length=100), nullable=True))
    op.add_column(
        "users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "tender_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=False, server_default="zinc"),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "uq_tender_tags_name_lower", "tender_tags", [sa.text("lower(name)")], unique=True
    )

    op.create_table(
        "tender_tag_links",
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tender_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_tender_tag_links_tag_id", "tender_tag_links", ["tag_id"])


def downgrade() -> None:
    op.drop_table("tender_tag_links")
    op.drop_index("uq_tender_tags_name_lower", table_name="tender_tags")
    op.drop_table("tender_tags")
    op.drop_column("users", "avatar_updated_at")
    op.drop_column("users", "avatar_content_type")
    op.drop_column("users", "avatar")
