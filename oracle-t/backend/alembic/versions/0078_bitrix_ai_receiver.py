"""Пакеты ИИ-анализа во вкладку сделки Bitrix24 (20.10.2026).

Зачем. На портале `b24.mirtek.info` установлено приложение «ИИ анализ и решения» с входящим
обработчиком `receive.php` (инструкция заказчика «коннект ИИ и Б24»): один JSON-пакет на
тендер — короткие поля сделки и подробные строки шести разделов вкладки, подпись HMAC
SHA-256 общим секретом. Это второй канал рядом с вебхуком (раздел 45 ARCHITECTURE.md): сделку
по нему находит или создаёт сам портал и возвращает её id.

- `bitrix24_settings`: адрес обработчика, секрет (зашифрован `core/crypto`), выключатель
  отправки — по умолчанию выключен, портал боевой.
- `bitrix_ai_requests`: журнал отправленных пакетов. Тело хранится дословно: повтор технически
  не принятого запроса по инструкции обязан уйти с тем же `request_id` и теми же байтами, а
  HTTP-код и ответ портала администратор передаёт администратору Bitrix24 при разборе ошибок.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078_bitrix_ai_receiver"
down_revision: Union[str, None] = "0077_requirement_position"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bitrix24_settings", sa.Column("ai_receiver_url", sa.Text(), nullable=True))
    op.add_column(
        "bitrix24_settings", sa.Column("ai_secret_encrypted", sa.Text(), nullable=True)
    )
    op.add_column(
        "bitrix24_settings",
        sa.Column("ai_push_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "bitrix_ai_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("request_id", sa.String(191), nullable=False, unique=True),
        sa.Column("analysis_id", sa.String(191), nullable=False),
        sa.Column("deal_id_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("full_snapshot", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("items_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("duplicate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deal_id", sa.Integer(), nullable=True),
        sa.Column("deal_created", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "sent_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("bitrix_ai_requests")
    op.drop_column("bitrix24_settings", "ai_push_enabled")
    op.drop_column("bitrix24_settings", "ai_secret_encrypted")
    op.drop_column("bitrix24_settings", "ai_receiver_url")
