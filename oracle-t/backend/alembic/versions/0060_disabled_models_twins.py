"""Отключение моделей администратором и общий разбор у записей одной закупки (29.09.2026).

`ai_provider_settings.disabled_providers` — модели, выключенные намертво: ключ — провайдер,
значение — когда и кто выключил. Время нужно, чтобы остановить задачи, начатые до
выключения, и не трогать начатые после (они уже идут через другую модель).

`tenders.analysis_tender_id` — у записи, чей разбор хранится у другой записи той же
закупки. Одна закупка приходит несколькими строками: из ЕИС, с площадки и отдельной строкой
в канале Госплана (32616398561 — три записи). Пользователь А разбирал одну, пользователь Б
открывал другую и видел её неразобранной. Теперь у всех записей с одним реестровым номером
разбор один — у «основной» записи, остальные ссылаются на неё.

Основная запись группы при заполнении — та, у которой есть текущая AI-оценка (свежее —
первее), затем та, у которой больше требований, затем самая ранняя.

Revision ID: 0060_disabled_models_twins
Revises: 0059_si_type_expiry_not_review
Create Date: 2026-09-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_disabled_models_twins"
down_revision: Union[str, None] = "0059_si_type_expiry_not_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_settings",
        sa.Column(
            "disabled_providers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "tenders",
        sa.Column(
            "analysis_tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tenders_analysis_tender_id", "tenders", ["analysis_tender_id"])

    op.execute(
        """
        WITH ranked AS (
            SELECT t.id,
                   t.registry_number,
                   row_number() OVER (
                       PARTITION BY t.registry_number
                       ORDER BY s.calculated_at DESC NULLS LAST,
                                COALESCE(r.cnt, 0) DESC,
                                t.created_at,
                                t.id
                   ) AS rank
            FROM tenders t
            LEFT JOIN ai_profile_scores s ON s.tender_id = t.id AND s.is_current
            LEFT JOIN (
                SELECT tender_id, count(*) AS cnt FROM requirements GROUP BY tender_id
            ) r ON r.tender_id = t.id
            WHERE t.registry_number IS NOT NULL
        ),
        primary_record AS (
            SELECT registry_number, id FROM ranked WHERE rank = 1
        )
        UPDATE tenders t
        SET analysis_tender_id = p.id
        FROM primary_record p
        WHERE t.registry_number = p.registry_number AND t.id <> p.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tenders_analysis_tender_id", table_name="tenders")
    op.drop_column("tenders", "analysis_tender_id")
    op.drop_column("ai_provider_settings", "disabled_providers")
