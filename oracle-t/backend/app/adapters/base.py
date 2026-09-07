"""Единый интерфейс адаптера источника тендеров (раздел 5.1, 6.1, 6.3 ТЗ).

Каждый источник (раздел 4.1 ТЗ) реализуется отдельным классом-наследником `SourceAdapter`.
Ядро системы (планировщик, CLI, сервис опроса) работает только через этот интерфейс и не
знает о технических деталях конкретной площадки — добавление нового источника не требует
переработки ядра (принцип адаптерной архитектуры, раздел 6.3 ТЗ).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class TenderSummary:
    """Одна запись из списка тендеров источника — минимум, достаточный для дедупликации
    и сохранения карточки (раздел 5.1 ТЗ: `list_new_tenders`)."""

    external_id: str
    title: str
    source_url: str
    # Реестровый номер закупки в ЕИС — единственный идентификатор, общий для всех площадок.
    # По нему тендер дедуплицируется МЕЖДУ источниками (раздел 5.1 ТЗ): одна и та же закупка
    # публикуется и в ЕИС, и на ЭТП, а `external_id` у них разный. Адаптер заполняет поле,
    # когда площадка отдаёт номер явно; когда `external_id` сам является реестровым номером,
    # сервис опроса выводит его самостоятельно.
    registry_number: str | None = None
    customer_name: str | None = None
    organizer_name: str | None = None
    procurement_method: str | None = None
    status: str | None = None
    price: Decimal | None = None
    currency: str = "RUB"
    application_start: datetime | None = None
    application_end: datetime | None = None
    publish_date: date | None = None


@dataclass
class TenderDetails(TenderSummary):
    """Полная карточка тендера (раздел 5.1 ТЗ: `get_tender_details`). На Этапе 2 совпадает
    по составу полей с `TenderSummary` — расширяется по мере того, как более поздние этапы
    (3, 5) начинают использовать дополнительные данные карточки."""

    raw_html: str | None = None


@dataclass
class DocumentRef:
    """Ссылка на документ тендера (раздел 5.1 ТЗ: `download_documents`). Собственно скачивание
    в файловое хранилище и запись в `tender_documents` — задача Этапа 3; на Этапе 2 адаптер
    только возвращает список доступных документов с прямыми ссылками."""

    file_name: str
    url: str
    file_type: str | None = None


@dataclass
class PollError:
    """Нефатальная ошибка, собранная в процессе опроса источника (раздел 5.1, 5.9 ТЗ:
    "ошибки парсинга отдельных полей логируются, а не прерывают весь цикл сбора")."""

    external_id: str | None
    message: str


@dataclass
class PollOutcome:
    tenders: list[TenderSummary] = field(default_factory=list)
    errors: list[PollError] = field(default_factory=list)


class SourceAdapter(ABC):
    """Базовый интерфейс адаптера источника (раздел 5.1 ТЗ)."""

    source_key: str

    @abstractmethod
    def list_new_tenders(self, since: datetime | None) -> PollOutcome:
        """Список новых/изменённых тендеров с момента последнего опроса (`since`).
        `since=None` — первый опрос источника, адаптер сам решает разумную глубину выборки."""

    @abstractmethod
    def get_tender_details(self, external_id: str) -> TenderDetails:
        """Полная карточка тендера по его номеру в источнике."""

    @abstractmethod
    def download_documents(
        self, external_id: str, source_url: str | None = None
    ) -> list[DocumentRef]:
        """Список документов тендера (без скачивания — см. `DocumentRef`).

        `source_url` — адрес карточки, уже сохранённый при сборе. Он передаётся, чтобы
        адаптеру не приходилось заново искать закупку в выдаче площадки: на большинстве
        источников поиск по номеру — это обход десятков страниц, а тендер, у которого истёк
        срок подачи, из выдачи вообще пропадает, и документы по нему стали бы недоступны.
        Адаптер вправе его проигнорировать, если карточка ему не нужна.
        """
