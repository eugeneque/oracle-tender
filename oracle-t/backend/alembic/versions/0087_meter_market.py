"""Рынок счётчиков электроэнергии по поверкам ФГИС «Аршин» (ТЗ ARSHIN_ANALYTICS.md,
29.10.2026; раздел 79 ARCHITECTURE.md).

Аналитик раз в квартал собирала отчёт о рынке ПУ вручную: программа Чернова качала поверки в
чужую базу, Excel считал `COUNT(*)` по модификациям, модификации размечались руками, итог
собирался в Power BI. Сова теперь грузит выгрузку поверок ФГИС сама и хранит:

* `meter_verifications` — поверки только по счётчикам ЭЭ (~30 млн строк с 2021 года), без
  внешних ключей и с целыми ключами справочников — ради объёма и скорости загрузки COPY;
* справочник типы ГРСИ → модификации → варианты написания, производители с синонимами и
  группы компаний с периодами, пороги баллов ПП 719 по годам;
* `meter_verification_monthly` — «вариант × месяц → количество», по нему строятся отчёты;
* `meter_market_loads` — пакеты выгрузки (слепок/изменения) с чекпойнтом для докачки и
  продолжения разбора после обрыва.

Пороги баллов на старт — по файлу аналитика за 3 кв. 2026: 94–105 баллов отмечены
требованиями 2024 года, 113–120 — 2025-го. Нижняя граница 2024 года и порог 2026-го не
подтверждены (раздел 11 ТЗ) — поэтому таблица, а не константа.

Правят справочник роли «Аналитик» и «Администратор» — роль хранится строкой (`users.role`),
новая роль миграции не требует.

Revision ID: 0087_meter_market
Revises: 0086_user_delete_set_null
Create Date: 2026-10-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0087_meter_market"
down_revision: Union[str, None] = "0086_user_delete_set_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "meter_market_manufacturers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("synonyms", postgresql.JSONB(), nullable=False, server_default="[]"),
        _created_at(),
    )
    op.create_table(
        "meter_market_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
    )
    op.create_table(
        "meter_market_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("meter_market_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "manufacturer_id",
            sa.Integer(),
            sa.ForeignKey("meter_market_manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year_from", sa.Integer(), nullable=True),
        sa.Column("year_to", sa.Integer(), nullable=True),
    )
    op.create_index("ix_meter_market_group_members_group_id", "meter_market_group_members", ["group_id"])

    op.create_table(
        "meter_market_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mit_number", sa.String(32), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("notation", sa.Text(), nullable=True),
        sa.Column(
            "manufacturer_id",
            sa.Integer(),
            sa.ForeignKey("meter_market_manufacturers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cert_valid_to", sa.Date(), nullable=True),
        sa.Column("mpi_years", sa.Integer(), nullable=True),
        sa.Column("in_report", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="new"),
        _created_at(),
    )

    op.create_table(
        "meter_market_modifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("meter_market_types.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(4), nullable=True),
        sa.Column("design", sa.String(10), nullable=False, server_default="nd"),
        sa.Column("interface", sa.String(255), nullable=True),
        sa.Column("mpi_years", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "manufacturer_override_id",
            sa.Integer(),
            sa.ForeignKey("meter_market_manufacturers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("in_rpp", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rpp_included_at", sa.Date(), nullable=True),
        sa.Column("rpp_record_number", sa.String(100), nullable=True),
        sa.Column("localization_source", sa.String(10), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("type_id", "name", name="uq_meter_market_modifications_type_name"),
    )
    op.create_index("ix_meter_market_modifications_type_id", "meter_market_modifications", ["type_id"])

    op.create_table(
        "meter_market_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("meter_market_types.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("norm_key", sa.Text(), nullable=False),
        sa.Column(
            "modification_id",
            sa.Integer(),
            sa.ForeignKey("meter_market_modifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mapped_by", sa.String(10), nullable=True),
        _created_at(),
        sa.UniqueConstraint("type_id", "raw", name="uq_meter_market_variants_type_raw"),
    )
    op.create_index("ix_meter_market_variants_modification_id", "meter_market_variants", ["modification_id"])
    op.create_index("ix_meter_market_variants_type_norm", "meter_market_variants", ["type_id", "norm_key"])

    op.create_table(
        "meter_market_orgs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False, unique=True),
    )

    op.create_table(
        "meter_verifications",
        sa.Column("global_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column("mi_number", sa.String(64), nullable=True),
        sa.Column("verification_date", sa.Date(), nullable=False),
        sa.Column("valid_date", sa.Date(), nullable=True),
        sa.Column("applicable", sa.Boolean(), nullable=False),
        sa.Column("annulled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Пересчёт агрегата по месяцу и точный подсчёт локализации с даты внесения в РПП.
    op.create_index("ix_meter_verifications_variant_date", "meter_verifications", ["variant_id", "verification_date"])
    op.create_index("ix_meter_verifications_date", "meter_verifications", ["verification_date"])

    op.create_table(
        "meter_verification_monthly",
        sa.Column("variant_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_meter_verification_monthly_month", "meter_verification_monthly", ["month"])

    op.create_table(
        "meter_market_loads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package_id", sa.String(32), nullable=False),
        sa.Column("prev_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("md5", sa.String(32), nullable=True),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("checkpoint", sa.String(255), nullable=True),
        sa.Column("rows_total", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_kept", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_annulled", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_meter_market_loads_package_id", "meter_market_loads", ["package_id"])

    op.create_table(
        "meter_market_score_thresholds",
        sa.Column("year", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("min_score", sa.Float(), nullable=False),
    )
    op.bulk_insert(
        sa.table("meter_market_score_thresholds", sa.column("year", sa.Integer()), sa.column("min_score", sa.Float())),
        [{"year": 2024, "min_score": 94}, {"year": 2025, "min_score": 113}],
    )


def downgrade() -> None:
    for table in (
        "meter_market_score_thresholds",
        "meter_market_loads",
        "meter_verification_monthly",
        "meter_verifications",
        "meter_market_orgs",
        "meter_market_variants",
        "meter_market_modifications",
        "meter_market_types",
        "meter_market_group_members",
        "meter_market_groups",
        "meter_market_manufacturers",
    ):
        op.drop_table(table)
