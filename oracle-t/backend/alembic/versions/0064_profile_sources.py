"""Привязка личных профилей релевантности к площадкам (05.10.2026).

`source_keys` — ключи источников, к которым применяется профиль; пусто — ко всем. Профиль
можно привязать к нескольким площадкам, а на одну площадку — привязать несколько профилей.

Revision ID: 0064_profile_sources
Revises: 0063_relevance_profiles
Create Date: 2026-10-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_profile_sources"
down_revision: Union[str, None] = "0063_relevance_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "relevance_profiles",
        sa.Column(
            "source_keys",
            postgresql.ARRAY(sa.String(50)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("relevance_profiles", "source_keys")
