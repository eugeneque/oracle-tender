"""Обучение справочника продукции: один проход по производителю (замечание заказчика
15.09.2026 — «обучение системы тех. характеристикам приборов всех компаний-конкурентов и
МИРТЕК с использованием Аршин»).

Шаги идут в порядке «от реестра к документации», и порядок не случаен — каждый следующий
шаг опирается на результат предыдущего:

1. **Карточки Аршина** — у типов, загруженных до этой правки, нет списка исполнений;
   карточка перечитывается из ФГИС (`refresh_cards`).
2. **Исполнения** — из карточек в каталог заводятся исполнения, которых на сайте
   производителя нет (`registry_modifications`).
3. **«Описание типа»** — документ вычитывается и характеристики разносятся по моделям,
   включая только что заведённые (`fgis_description_ingest`).
4. **Поиск документации** — для моделей без ссылки на руководство (в первую очередь
   заведённых из реестра) руководство ищется на официальном сайте через Яндекс
   (`document_discovery`).
5. **Руководства** — найденные документы разбираются (`product_manual_ingest`).

Проход идёт через общую очередь справочника (`catalog_lookup_queue`) с ключом
`catalog_learning`: он занимает минуты (документы, обращения к модели), и держать на нём
HTTP-запрос нельзя. Планировщик ставит проход по всем производителям раз в неделю, после
обхода каталогов сайтов; администратор — кнопкой по одному производителю.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis import FgisAdapter
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason
from app.models.manufacturer import Manufacturer, SiType
from app.models.user import User
from app.services import (
    catalog_queue_service,
    document_discovery,
    fgis_description_ingest,
    product_manual_ingest,
    registry_modifications,
    si_type_linking,
)
from app.services.catalog_queue_service import TaskOutcome

ADAPTER_KEY = "catalog_learning"

# Пределы одного прохода. Каждый документ — это запросы к модели, а каждый поиск — платный
# запрос к поисковику; проход по всем производителям должен укладываться в ночь.
DEFAULT_DESCRIPTION_LIMIT = 15
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_MANUAL_LIMIT = 10
DEFAULT_CARD_REFRESH_LIMIT = 60


@dataclass
class LearningOutcome:
    cards_refreshed: int = 0
    modifications: registry_modifications.ModificationsOutcome = field(
        default_factory=registry_modifications.ModificationsOutcome
    )
    descriptions: fgis_description_ingest.DescriptionIngestOutcome = field(
        default_factory=fgis_description_ingest.DescriptionIngestOutcome
    )
    documents: document_discovery.DiscoveryOutcome = field(
        default_factory=document_discovery.DiscoveryOutcome
    )
    manuals: product_manual_ingest.IngestOutcome = field(
        default_factory=product_manual_ingest.IngestOutcome
    )

    def summary(self) -> str:
        return (
            f"карточек Аршина обновлено {self.cards_refreshed}; исполнений из реестра: заведено "
            f"{self.modifications.products_created}, привязано {self.modifications.products_linked}; "
            f"«Описание типа»: вычитано {self.descriptions.documents_read}, моделей обновлено "
            f"{self.descriptions.products_updated}, характеристик {self.descriptions.characteristics_saved}; "
            f"документация в интернете: найдено {self.documents.found} из "
            f"{self.documents.products_checked} проверенных; руководств разобрано "
            f"{self.manuals.processed}, характеристик {self.manuals.characteristics_saved}"
        )


def register() -> None:
    catalog_queue_service.register_handler(ADAPTER_KEY, handle_task)


def enqueue(
    db: Session, manufacturer: Manufacturer, *, actor_id: uuid.UUID | None = None, run_now: bool = True
) -> CatalogLookupTask | None:
    return catalog_queue_service.enqueue(
        db,
        adapter_key=ADAPTER_KEY,
        reason=CatalogQueueReason.MANUAL if actor_id else CatalogQueueReason.SCHEDULED_REVALIDATION,
        manufacturer_id=manufacturer.id,
        # Имя производителя в поле цели — чтобы дубликат прохода по нему не ставился в
        # очередь повторно и чтобы в списке задач было видно, о ком речь.
        model_name=manufacturer.brand_name or manufacturer.legal_name,
        actor_id=actor_id,
        run_now=run_now,
    )


def enqueue_all(db: Session, *, actor_id: uuid.UUID | None = None) -> int:
    queued = 0
    for manufacturer in db.scalars(select(Manufacturer).order_by(Manufacturer.is_mirtek.desc())):
        task = enqueue(db, manufacturer, actor_id=actor_id, run_now=False)
        if task is not None:
            queued += 1
    return queued


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    manufacturer = db.get(Manufacturer, task.manufacturer_id) if task.manufacturer_id else None
    if manufacturer is None:
        return TaskOutcome(message="Производитель не указан либо удалён — обучать нечего")
    actor = _actor(db)
    outcome = learn_manufacturer(db, manufacturer, actor=actor)
    return TaskOutcome(
        message=f"Обучение по «{manufacturer.brand_name or manufacturer.legal_name}»: {outcome.summary()}",
        needs_review=outcome.modifications.products_created > 0,
        details={
            "created_from_registry": outcome.modifications.created_names[:50],
            "documents_found": outcome.documents.messages[:50],
            "problems": (outcome.descriptions.messages + outcome.manuals.messages)[:50],
        },
    )


def learn_manufacturer(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor: User,
    description_limit: int = DEFAULT_DESCRIPTION_LIMIT,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    manual_limit: int = DEFAULT_MANUAL_LIMIT,
    adapter: FgisAdapter | None = None,
) -> LearningOutcome:
    outcome = LearningOutcome()
    adapter = adapter or FgisAdapter()

    outcome.cards_refreshed = refresh_cards(db, manufacturer, adapter=adapter)
    outcome.modifications = registry_modifications.discover_modifications(
        db, manufacturer, actor_id=actor.id
    )
    outcome.descriptions = fgis_description_ingest.ingest_description_types(
        db, manufacturer, actor_id=actor.id, limit=description_limit, adapter=adapter
    )
    outcome.documents = document_discovery.discover_documents(
        db, manufacturer, actor_id=actor.id, limit=search_limit
    )
    outcome.manuals = product_manual_ingest.ingest_manuals(
        db, manufacturer, actor=actor, limit=manual_limit
    )
    logger.info(f"Обучение справочника по «{manufacturer.legal_name}»: {outcome.summary()}")
    return outcome


def refresh_cards(
    db: Session,
    manufacturer: Manufacturer,
    *,
    adapter: FgisAdapter | None = None,
    limit: int = DEFAULT_CARD_REFRESH_LIMIT,
) -> int:
    """Перечитывает карточки типов, у которых ещё нет списка исполнений.

    Нужен один раз для записей, заведённых до этой правки: дальше список поддерживает
    ревалидация ФГИС. Предел — чтобы первый проход по производителю с полутора сотнями
    типов (Энергомера) не превратился в полторы сотни запросов к нестабильному сервису
    за раз; остаток доберут следующие проходы."""

    from app.services.fgis_catalog_sync import _as_search_result
    from app.services.product_catalog_service import _apply_search_result

    adapter = adapter or FgisAdapter()
    refreshed = 0
    for si_type in si_type_linking.electricity_meter_types(db, manufacturer.id):
        if si_type.tested_modifications or not si_type.mit_uuid:
            continue
        if refreshed >= limit:
            break
        result = adapter.enrich_from_card(_as_search_result(si_type))
        if not result.tested_modifications and not result.allowed_modifications:
            continue
        _apply_search_result(si_type, result)
        refreshed += 1
    db.commit()
    return refreshed


def _actor(db: Session) -> User:
    """Кто записан автором фонового прохода. Первый администратор: у фоновой задачи нет
    пользователя, а журнал и характеристики требуют автора."""

    from app.models.user import UserRole

    admin = db.scalar(select(User).where(User.role == UserRole.ADMIN.value).order_by(User.created_at))
    if admin is None:
        admin = db.scalar(select(User).order_by(User.created_at))
    if admin is None:
        raise RuntimeError("В системе нет ни одного пользователя — фоновому обучению некому приписать результат")
    return admin
