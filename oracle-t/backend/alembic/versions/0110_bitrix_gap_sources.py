"""Источники для оставшихся пунктов перечня Bitrix24 «Что еще нужно от ИИ» (раздел 115).

19.11.2026. Пункты, которые закрываются без специалиста Bitrix24, упирались в данные, которых у
СОВЫ не было:

* `tender_eis_registry` — сведения реестров ЕИС по закупке: жалобы (реестр жалоб: статус,
  дата и место рассмотрения, итог, предписание — пп. 41–47, 89), договоры (реестр договоров и
  контрактов: цена, срок, редакции, допсоглашения — пп. 90, 94), история закупок заказчика
  по его ИНН (п. 7). Заменяется целиком при обновлении, время обновления — свои поля: жалобы и
  договоры перечитываются чаще истории;
* `tender_guarantees` — выданные банковские гарантии по закупке, вводит тендерный отдел: без
  срока действия гарантии нельзя ответить, нужно ли её продлевать (п. 32);
* `company_profile.resources` — персонал, склады и производственные площади, оборудование
  «Моей компании»: квалификационные требования этих видов было не с чем сверить (пп. 11–12);
* `notification_settings.imap_*` и `tender_mail_messages` — чтение почтового ящика с
  уведомлениями площадок: запрос заказчика о дополнении или уточнении нашей заявки приходит
  только участнику, публично его нет (п. 65). Логин и пароль — те же, что у SMTP.

Revision ID: 0110_bitrix_gap_sources
Revises: 0109_meter_market_analyst_rules
Create Date: 2026-11-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0110_bitrix_gap_sources"
down_revision: Union[str, None] = "0109_meter_market_analyst_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_eis_registry",
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("complaints", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("contracts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("customer_history", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("registry_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_table(
        "tender_guarantees",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("number", sa.String(100), nullable=True),
        sa.Column("bank", sa.String(300), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("company_profile", sa.Column("resources", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("notification_settings", sa.Column("imap_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("notification_settings", sa.Column("imap_host", sa.String(255), nullable=True))
    op.add_column("notification_settings", sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"))
    op.add_column("notification_settings", sa.Column("imap_folder", sa.String(200), nullable=False, server_default="INBOX"))
    op.add_column("notification_settings", sa.Column("imap_last_uid", sa.BigInteger(), nullable=True))
    op.add_column("notification_settings", sa.Column("imap_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("notification_settings", sa.Column("imap_last_error", sa.Text(), nullable=True))
    op.create_table(
        "tender_mail_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("message_id", sa.String(500), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("sender", sa.String(300), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tender_mail_messages")
    for column in ("imap_last_error", "imap_checked_at", "imap_last_uid", "imap_folder", "imap_port", "imap_host", "imap_enabled"):
        op.drop_column("notification_settings", column)
    op.drop_column("company_profile", "resources")
    op.drop_table("tender_guarantees")
    op.drop_table("tender_eis_registry")
