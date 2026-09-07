"""update source notes after investigating "Недоступно" sources

АСТ ГОЗ и ЭТП РФ были недоступны из-за проблем с TLS-цепочкой (не бизнес-блокировка) —
решено на уровне `app/adapters/http_utils.resolve_verify` (Russian Trusted CA для astgoz.ru,
недостающий промежуточный сертификат GlobalSign для etprf.ru), доступность подтверждена пингом.
РТС-тендер, наоборот, целенаправленно блокирует автоматизированный доступ (страница
"Anti-DDoS защита") — проверено, что даже реальный headless-браузер (Playwright) её не проходит
после ожидания; дальнейшие попытки обхода (подмена fingerprint и т.п.) сознательно не
предпринимались. Статус адаптера для него меняется на `blocked`.

Revision ID: 0005_source_notes_update
Revises: 0004_source_availability
Create Date: 2026-08-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_source_notes_update"
down_revision: Union[str, None] = "0004_source_availability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sources = sa.table(
    "sources",
    sa.column("key", sa.String),
    sa.column("adapter_status", sa.String),
    sa.column("note", sa.String),
)


def upgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "astgoz")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ). TLS-проблема решена (Russian Trusted CA,"
            " как для ЕИС) — площадка доступна."
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "etprf")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ). TLS-проблема решена (площадка не досылала"
            " промежуточный сертификат GlobalSign — подставлен вручную) — доступна."
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "rts_tender")
        .values(
            adapter_status="blocked",
            note="Площадка отдаёт страницу антибот-защиты ("
            "\"Anti-DDoS защита\") даже реальному headless-браузеру (Playwright, с ожиданием) —"
            " не только httpx. Дальнейший обход (подмена fingerprint и т.п.) не предпринимался."
            " Подключение возможно только через официальную аккредитацию/API площадки.",
        )
    )


def downgrade() -> None:
    op.execute(
        sources.update()
        .where(sources.c.key == "astgoz")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ)."
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "etprf")
        .values(
            note="В очереди на реализацию (Этап 12 ТЗ)."
        )
    )
    op.execute(
        sources.update()
        .where(sources.c.key == "rts_tender")
        .values(
            adapter_status="not_implemented",
            note="В очереди на реализацию (Этап 12 ТЗ). При предварительной проверке площадка отвечала"
            " HTTP 503 — похоже на защиту от ботов; потребуется отдельное исследование обхода.",
        )
    )
