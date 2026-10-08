"""GigaChat (Сбер) — четвёртый провайдер ИИ-модуля, задел под подключение (08.10.2026).

Заказчик попросил заложить GigaChat заранее, пока оформляется доступ к API: модель должна
появиться в переключателях и на странице «Интеграции», чтобы после получения ключа оставалось
вписать его и проверить подключение, а не дописывать интеграцию.

У GigaChat своя схема доступа, не как у RouterAI: «ключ авторизации» (Base64 от
Client ID:Client Secret) обменивается на токен доступа на 30 минут, а версия API (scope)
зависит от договора — физлицо, юрлицо по предоплате или по постоплате. Поэтому отдельные
колонки, а не общий ключ RouterAI. Ключ хранится зашифрованным (`app/core/crypto.py`), как
вебхук Bitrix24: им можно тратить деньги компании.

Revision ID: 0071_gigachat_provider
Revises: 0070_bitrix24_deals
Create Date: 2026-10-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071_gigachat_provider"
down_revision: Union[str, None] = "0070_bitrix24_deals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_settings", sa.Column("gigachat_auth_key_encrypted", sa.Text(), nullable=True)
    )
    op.add_column("ai_provider_settings", sa.Column("gigachat_scope", sa.String(40), nullable=True))
    op.add_column("ai_provider_settings", sa.Column("gigachat_model", sa.String(100), nullable=True))
    op.add_column(
        "ai_provider_settings", sa.Column("gigachat_base_url", sa.String(200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ai_provider_settings", "gigachat_base_url")
    op.drop_column("ai_provider_settings", "gigachat_model")
    op.drop_column("ai_provider_settings", "gigachat_scope")
    op.drop_column("ai_provider_settings", "gigachat_auth_key_encrypted")
