"""Источник «ФГИС «Аршин»: результаты поверки СИ» в разделе «Источники» (раздел 79
ARCHITECTURE.md).

Поверки для отчёта «Рынок ПУ» грузятся из сервиса выгрузки ФГИС каждую ночь, и администратор
должен видеть этот источник рядом с остальными источниками справочника — с адресом манифеста
для проверки доступности и заметкой, что и когда грузится. Тип `fgis`: опрос тендеров его
пропускает (`CATALOG_SOURCE_TYPES`), адаптер тендерной площадки у него не вызывается.

Revision ID: 0088_meter_market_source
Revises: 0087_meter_market
Create Date: 2026-10-29
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0088_meter_market_source"
down_revision: Union[str, None] = "0087_meter_market"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = "fgis_poverki"
NOTE = (
    "Сведения о результатах поверки СИ (сервис выгрузки ФГИС «Аршин»): для отчёта «Аналитика →"
    " Рынок ПУ» сохраняются поверки только счётчиков электроэнергии. Первая загрузка — слепок"
    " (≈12 ГБ: база на 01.09.2022 и все изменения после неё), дальше каждую ночь в 01:15 МСК —"
    " файлы изменений по цепочке манифеста с проверкой md5. Ход и ошибки — в журнале,"
    " компонент «meter_market»."
)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO sources (id, key, name, url, type, status, polling_schedule, adapter_key, adapter_status, note) "
            "VALUES (:id, :key, :name, :url, 'fgis', 'active', 'daily', 'fgis_poverki', 'implemented', :note) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "key": KEY,
            "name": "ФГИС «Аршин» — результаты поверки СИ (рынок ПУ)",
            "url": "https://fgis.gost.ru/fundmetrology/exporter/poverki/manifest.json",
            "note": NOTE,
        },
    )


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM sources WHERE key = :key"), {"key": KEY})
