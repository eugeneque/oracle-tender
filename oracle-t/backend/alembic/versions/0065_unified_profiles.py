"""Одна логика отбора: группы системного профиля становятся общими профилями (05.10.2026).

До этой миграции отбор жил в двух местах: группы `search_keyword_groups` (их вёл
администратор, они проставляли `tenders.passed_relevance_filter` и прятались за галочкой
«Только прошедшие профиль») и личные профили специалистов (`relevance_profiles`). Логика у них
расходилась: в группе код ОКПД2 пропускал закупку сам по себе, в профиле — только вместе со
словами. Пользователь видел два «профиля» и пересекающуюся выдачу.

Теперь всё — профили, с одним движком сопоставления:

* группы переносятся в `relevance_profiles` с теми же id, флагом `is_default` (общий
  профиль, действует по умолчанию) и `okpd2_mode = 'either'` — прежнее «код проходит сам»;
* поисковые фразы групп — в отдельную таблицу `collection_terms`: это охват СБОРА («что мы
  спрашиваем у площадок»), а не отбор, и смешивать их с профилями нельзя;
* `tenders.matched_profile_id` — какой профиль отобрал закупку (вместо
  `matched_keyword_group_id`; id совпадают, поэтому значения переносятся как есть).

Старые таблицы не удаляются: откат возвращает систему к ним без потери данных.

Revision ID: 0065_unified_profiles
Revises: 0064_profile_sources
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065_unified_profiles"
down_revision: Union[str, None] = "0064_profile_sources"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("relevance_profiles", "name", type_=sa.String(200))
    op.add_column(
        "relevance_profiles",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "relevance_profiles",
        sa.Column("okpd2_mode", sa.String(10), nullable=False, server_default="narrow"),
    )
    op.add_column(
        "relevance_profiles",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "collection_terms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("phrase", sa.String(300), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ux_collection_terms_phrase", "collection_terms", [sa.text("lower(phrase)")], unique=True
    )
    op.add_column(
        "tenders",
        sa.Column(
            "matched_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("relevance_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.execute(
        """
        INSERT INTO relevance_profiles
            (id, name, description, owner_id, keywords, exclusion_keywords, okpd2_codes,
             match_mode, source_keys, rules_version, matched_version, is_default, okpd2_mode,
             is_active, created_at, updated_at)
        SELECT id, name, NULL, NULL, keywords, exclusion_keywords, COALESCE(okpd2_codes, '{}'),
               match_mode, '{}', 1, 0, TRUE, 'either', is_active, created_at, updated_at
        FROM search_keyword_groups
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO collection_terms (id, phrase, is_active)
        SELECT gen_random_uuid(), phrase, active
        FROM (
            SELECT DISTINCT ON (lower(btrim(q))) btrim(q) AS phrase, bool_or(g.is_active)
                   OVER (PARTITION BY lower(btrim(q))) AS active
            FROM search_keyword_groups g, jsonb_array_elements_text(g.search_queries) AS q
            WHERE btrim(q) <> ''
            ORDER BY lower(btrim(q))
        ) t
        """
    )
    op.execute(
        """
        UPDATE tenders SET matched_profile_id = matched_keyword_group_id
        WHERE matched_keyword_group_id IS NOT NULL
          AND matched_keyword_group_id IN (SELECT id FROM relevance_profiles)
        """
    )


def downgrade() -> None:
    op.drop_column("tenders", "matched_profile_id")
    op.drop_index("ux_collection_terms_phrase", table_name="collection_terms")
    op.drop_table("collection_terms")
    op.execute("DELETE FROM relevance_profiles WHERE is_default")
    op.drop_column("relevance_profiles", "is_active")
    op.drop_column("relevance_profiles", "okpd2_mode")
    op.drop_column("relevance_profiles", "is_default")
    op.alter_column("relevance_profiles", "name", type_=sa.String(120))
