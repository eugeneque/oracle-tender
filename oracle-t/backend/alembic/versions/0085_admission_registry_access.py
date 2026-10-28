"""Доступ к реестрам допуска настроен (замечание 28.10.2026): источники ГИСП и перечня ПАО
«Россети» больше не «не реализованы».

ГИСП опрашивается JSON-запросом грида реестра с фильтром по ОКПД2, Россети — PDF «Раздел
II. Вторичное оборудование…» со страницы «Проверка качества оборудования» (прежний адрес
ezak.rosseti.ru работает с перебоями, а данные там не всегда актуальны). Строки `sources`
получают адрес, готовность адаптера и заметку, которые видит администратор (тексты — из
`app/seed/sources_data.py` на дату миграции).

`product_registry_records.score` — баллы записи ГИСП по ПП 719. Тендерный отдел
(совещание 28.10.2026): одна и та же модель бывает в реестре с 80 баллами и со 113, а для
закупок годится только запись со 113 баллами и выше. Без баллов «запись есть» вводила бы
сопоставление в заблуждение.

Revision ID: 0085_admission_registry_access
Revises: 0084_tender_positions
Create Date: 2026-10-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0085_admission_registry_access"
down_revision: Union[str, None] = "0084_tender_positions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GISP_NOTE = (
    "Реестр промышленной продукции, произведённой на территории РФ (постановление"
    " Правительства № 719). Опрашивается раз в неделю ночью (днём ГИСП отвечает минутами)"
    " запросом таблицы реестра с фильтром «ОКПД2» по кодам общих профилей отбора. Если"
    " реестр серверу недоступен — загрузите выгрузку XLSX («Скачать только действующие»)"
    " в карточке модели каталога."
)
ROSSETI_NOTE = (
    "Раздел II «Вторичное оборудование, материалы и системы, допущенные к применению на"
    " объектах ПАО «Россети»» — PDF на странице «Проверка качества оборудования», номер и"
    " срок ЗАК по каждой записи. Сайт закрыт проверкой браузера: если сервер не скачал"
    " перечень, загрузите PDF из браузера в карточке модели каталога."
)

_UPDATE = sa.text(
    "UPDATE sources SET name = :name, url = :url, adapter_status = :status, note = :note, "
    "updated_at = now() WHERE key = :key"
)


def upgrade() -> None:
    op.add_column("product_registry_records", sa.Column("score", sa.Float(), nullable=True))
    bind = op.get_bind()
    bind.execute(
        _UPDATE,
        {
            "key": "reg_gisp",
            "name": "ГИСП — реестр российской промышленной продукции (ПП РФ № 719)",
            "url": "https://gisp.gov.ru/pp719v2/pub/prod/",
            "status": "implemented",
            "note": GISP_NOTE,
        },
    )
    bind.execute(
        _UPDATE,
        {
            "key": "reg_rosseti_zak",
            "name": "Перечень оборудования, допущенного на объекты ПАО «Россети» (ЗАК)",
            "url": "https://www.rosseti.ru/suppliers/technical-policy/equipment-quality-control/",
            "status": "implemented",
            "note": ROSSETI_NOTE,
        },
    )


def downgrade() -> None:
    op.drop_column("product_registry_records", "score")
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE sources SET adapter_status = 'not_implemented' WHERE key IN ('reg_gisp', 'reg_rosseti_zak')")
    )
    bind.execute(
        sa.text("UPDATE sources SET url = 'https://ezak.rosseti.ru/' WHERE key = 'reg_rosseti_zak'")
    )
