"""AI-оценка по профилю, профиль компании, этапы тендера и рыночная статистика.

Решения 03.09.2026 (ТЗ v2.5, разделы 5.5.1, 5.6, 7):
* `tenders.relevance_status` → `tenders.stage` (внутренний пайплайн МИРТЕК); старое поле
  остаётся во внешнем API как вычисляемый алиас, но не как колонка;
* новые `company_profile` и `ai_profile_scores` — без них Task/Competencies не посчитать;
* `similar_tenders`, `tender_embeddings`, `niche_statistics` — схема заводится сразу,
  наполнение идёт позже (раздел 0.2 ТЗ);
* версионность `win_percentages` — как у `ai_profile_scores`, чтобы пересчёт не стирал
  историю.

Revision ID: 0026_ai_profile_and_stage
Revises: 0025_manual_broadcast
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_ai_profile_and_stage"
down_revision: Union[str, None] = "0025_manual_broadcast"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- tenders: этап пайплайна вместо статуса релевантности ------------------------
    op.add_column(
        "tenders",
        sa.Column("stage", sa.String(length=30), nullable=False, server_default="ai_selected"),
    )
    # Прямое соответствие значений из раздела 7 ТЗ. `confirmed` → `under_review`: тендер,
    # который человек подтвердил, находится в работе, но заявка по нему ещё не подана —
    # более поздний этап здесь означал бы приписать компании действие, которого не было.
    op.execute(
        """
        UPDATE tenders SET stage = CASE relevance_status
            WHEN 'new' THEN 'ai_selected'
            WHEN 'confirmed' THEN 'under_review'
            WHEN 'rejected' THEN 'rejected'
            ELSE 'ai_selected'
        END
        """
    )
    op.create_index("ix_tenders_stage", "tenders", ["stage"])
    op.drop_column("tenders", "relevance_status")

    op.add_column("tenders", sa.Column("registry_number", sa.String(length=50), nullable=True))
    op.create_index("ix_tenders_registry_number", "tenders", ["registry_number"])
    op.add_column(
        "tenders", sa.Column("customer_contact_name", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "tenders", sa.Column("customer_contact_phone", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "tenders", sa.Column("customer_contact_email", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "tenders",
        sa.Column(
            "assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # --- tender_documents: класс документа и приоритет для расчёта --------------------
    op.add_column(
        "tender_documents", sa.Column("document_class", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "tender_documents",
        sa.Column(
            "is_priority_source", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )

    # --- win_percentages: версионность вместо перезаписи ------------------------------
    op.add_column(
        "win_percentages",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.drop_constraint("uq_win_tender_manufacturer", "win_percentages", type_="unique")
    op.create_index(
        "uq_win_tender_manufacturer_current",
        "win_percentages",
        ["tender_id", "manufacturer_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    # --- tender_outcomes: откуда взят исход -------------------------------------------
    op.add_column(
        "tender_outcomes",
        sa.Column("source", sa.String(length=30), nullable=False, server_default="other"),
    )

    # --- company_profile ---------------------------------------------------------------
    op.create_table(
        "company_profile",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "manufacturer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("manufacturers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("years_of_experience", sa.Integer(), nullable=True),
        sa.Column(
            "licenses", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "past_projects",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("bank_requisites", postgresql.JSONB(), nullable=True),
        sa.Column("letterhead_file_path", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("manufacturer_id", name="uq_company_profile_manufacturer"),
    )

    # --- ai_profile_scores -------------------------------------------------------------
    op.create_table(
        "ai_profile_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("history_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("history_comment", sa.Text(), nullable=True),
        sa.Column("history_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("task_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("task_comment", sa.Text(), nullable=True),
        sa.Column(
            "task_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("competencies_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("competencies_comment", sa.Text(), nullable=True),
        sa.Column(
            "competencies_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("overall_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=30), nullable=True),
        sa.Column(
            "weak_points",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("recommended_strategy", postgresql.JSONB(), nullable=True),
        sa.Column("company_profile_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ai_profile_scores_tender_id", "ai_profile_scores", ["tender_id"])
    op.create_index(
        "uq_ai_profile_scores_current",
        "ai_profile_scores",
        ["tender_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    # --- эмбеддинги и похожие тендеры --------------------------------------------------
    # Вектор лежит в JSONB, а не в типе `vector`: расширения `pgvector` нет ни в образе
    # `postgres:15`, ни в локальной установке, и `CREATE EXTENSION` здесь уронил бы
    # миграцию у всех разом. Близость считается в Python (см. similarity_service).
    op.create_table(
        "tender_embeddings",
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding", postgresql.JSONB(), nullable=False),
        sa.Column("model_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "similar_tenders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "similar_tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("similarity_score", sa.Numeric(6, 5), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tender_id", "similar_tender_id", name="uq_similar_tenders_pair"),
    )
    op.create_index("ix_similar_tenders_tender_id", "similar_tenders", ["tender_id"])

    # --- niche_statistics ----------------------------------------------------------------
    op.create_table(
        "niche_statistics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("okpd2_code", sa.String(length=20), nullable=False),
        sa.Column(
            "region_code", sa.String(length=2), sa.ForeignKey("regions.code"), nullable=True
        ),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("avg_participants", sa.Numeric(6, 2), nullable=True),
        sa.Column("single_participant_share", sa.Numeric(5, 2), nullable=True),
        sa.Column("median_price_reduction_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("usual_submission_days", sa.Integer(), nullable=True),
        sa.Column(
            "top_winners",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("okpd2_code", "region_code", name="uq_niche_statistics_key"),
    )
    op.create_index("ix_niche_statistics_okpd2_code", "niche_statistics", ["okpd2_code"])

    # --- уведомления: high_win_percentage → high_ai_score --------------------------------
    # Триггер переименован вслед за метрикой (раздел 7 и 5.8 ТЗ, исправление 03.09.2026):
    # порог берётся от `overall_score` AI-оценки по профилю, а не от процента победителя.
    # Колонки настроек переименовываются, а не заводятся заново: значение порога (обычно
    # 80) заказчик уже выставил, и терять его при смене метрики незачем.
    op.alter_column("notification_settings", "trigger_high_win", new_column_name="trigger_high_ai_score")
    op.alter_column(
        "notification_settings", "win_percentage_threshold", new_column_name="ai_score_threshold"
    )
    op.execute(
        "UPDATE notifications SET trigger = 'high_ai_score' WHERE trigger = 'high_win_percentage'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE notifications SET trigger = 'high_win_percentage' WHERE trigger = 'high_ai_score'"
    )
    op.alter_column(
        "notification_settings", "ai_score_threshold", new_column_name="win_percentage_threshold"
    )
    op.alter_column(
        "notification_settings", "trigger_high_ai_score", new_column_name="trigger_high_win"
    )

    op.drop_table("niche_statistics")
    op.drop_table("similar_tenders")
    op.drop_table("tender_embeddings")
    op.drop_index("uq_ai_profile_scores_current", table_name="ai_profile_scores")
    op.drop_table("ai_profile_scores")
    op.drop_table("company_profile")

    op.drop_column("tender_outcomes", "source")
    op.drop_index("uq_win_tender_manufacturer_current", table_name="win_percentages")
    op.create_unique_constraint(
        "uq_win_tender_manufacturer", "win_percentages", ["tender_id", "manufacturer_id"]
    )
    op.drop_column("win_percentages", "is_current")

    op.drop_column("tender_documents", "is_priority_source")
    op.drop_column("tender_documents", "document_class")

    op.drop_column("tenders", "assignee_id")
    op.drop_column("tenders", "customer_contact_email")
    op.drop_column("tenders", "customer_contact_phone")
    op.drop_column("tenders", "customer_contact_name")
    op.drop_index("ix_tenders_registry_number", table_name="tenders")
    op.drop_column("tenders", "registry_number")

    op.add_column(
        "tenders",
        sa.Column("relevance_status", sa.String(length=20), nullable=False, server_default="new"),
    )
    op.execute(
        """
        UPDATE tenders SET relevance_status = CASE stage
            WHEN 'rejected' THEN 'rejected'
            WHEN 'ai_selected' THEN 'new'
            ELSE 'confirmed'
        END
        """
    )
    op.drop_index("ix_tenders_stage", table_name="tenders")
    op.drop_column("tenders", "stage")
