"""Qwen3.8 27B вместо Claude и GigaChat (20.10.2026).

Зачем. Заказчик попросил оставить в ИИ-модуле YandexGPT и DeepSeek, добавить Qwen3.8 27B
(через тот же ключ RouterAI, https://routerai.ru/models/qwen/qwen3.8-27b) и убрать Claude
и GigaChat. Код этих двух провайдеров удалён, поэтому:

- `qwen_model` — путь до модели Qwen в RouterAI (NULL — `DEFAULT_QWEN_MODEL`);
- у пользователей, выбравших Claude или GigaChat, личный выбор сбрасывается — они работают
  с моделью по умолчанию;
- если моделью по умолчанию был Claude или GigaChat, ею становится DeepSeek (ключ RouterAI
  тот же), а без ключа RouterAI — YandexGPT;
- из выключенных моделей и окон контекста убираются записи удалённых моделей;
- столбцы настроек Claude (`routerai_model`) и GigaChat удаляются.

История (`ai_usage_records.provider`, `ai_profile_scores.ai_provider`, журнал) не меняется:
старые записи подписываются по `HISTORICAL_PROVIDER_LABELS`.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0079_qwen_provider"
down_revision: Union[str, None] = "0078_bitrix_ai_receiver"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ai_provider_settings", sa.Column("qwen_model", sa.String(100), nullable=True))
    op.execute("UPDATE users SET ai_provider = NULL WHERE ai_provider IN ('claude', 'gigachat')")
    op.execute(
        """
        UPDATE ai_provider_settings
        SET active_provider = CASE WHEN routerai_api_key IS NOT NULL AND routerai_api_key <> ''
                                   THEN 'deepseek' ELSE 'yandex' END
        WHERE active_provider IN ('claude', 'gigachat')
        """
    )
    op.execute(
        """
        UPDATE ai_provider_settings
        SET disabled_providers = disabled_providers - 'claude' - 'gigachat',
            model_limits = model_limits - 'claude' - 'gigachat'
        """
    )
    op.drop_column("ai_provider_settings", "routerai_model")
    op.drop_column("ai_provider_settings", "gigachat_auth_key_encrypted")
    op.drop_column("ai_provider_settings", "gigachat_scope")
    op.drop_column("ai_provider_settings", "gigachat_model")
    op.drop_column("ai_provider_settings", "gigachat_base_url")


def downgrade() -> None:
    op.add_column("ai_provider_settings", sa.Column("gigachat_base_url", sa.String(200), nullable=True))
    op.add_column("ai_provider_settings", sa.Column("gigachat_model", sa.String(100), nullable=True))
    op.add_column("ai_provider_settings", sa.Column("gigachat_scope", sa.String(40), nullable=True))
    op.add_column("ai_provider_settings", sa.Column("gigachat_auth_key_encrypted", sa.Text(), nullable=True))
    op.add_column("ai_provider_settings", sa.Column("routerai_model", sa.String(100), nullable=True))
    op.execute("UPDATE users SET ai_provider = NULL WHERE ai_provider = 'qwen'")
    op.execute("UPDATE ai_provider_settings SET active_provider = 'deepseek' WHERE active_provider = 'qwen'")
    op.drop_column("ai_provider_settings", "qwen_model")
