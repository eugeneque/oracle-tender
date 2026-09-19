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
from typing import Callable


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


# Сколько записей адаптер накапливает, прежде чем отдать их сервису опроса на сохранение
# (правка 17.09.2026: «пусть передаёт по 100 тендеров, по очереди»). Раньше адаптер
# возвращал всю выдачу разом — у ЭТП ГПБ это 11 000 записей и несколько минут, — и всё это
# время в базе не появлялось ничего, а список в интерфейсе ждал конца опроса.
POLL_BATCH_SIZE = 100


class SourceAdapter(ABC):
    """Базовый интерфейс адаптера источника (раздел 5.1 ТЗ)."""

    source_key: str

    # Получатель порций (`poll_source` ставит его перед опросом). `None` — порции никому не
    # нужны, и адаптер просто возвращает всё в `PollOutcome`, как раньше: так работают CLI,
    # тесты адаптеров и любой код, который зовёт `list_new_tenders` напрямую.
    on_batch: Callable[[list[TenderSummary]], None] | None = None
    batch_size: int = POLL_BATCH_SIZE
    _pending: list[TenderSummary]

    def _collect(self, seen: dict[str, TenderSummary], summary: TenderSummary) -> None:
        """Кладёт запись в `seen` (дедупликация внутри выдачи) и, если подписчик есть, — в
        очередную порцию; полная порция тут же отдаётся ему. Остаток меньше порции адаптер
        не сбрасывает сам: `poll_source` дочитывает его из `outcome.tenders` после
        возврата, сверяясь с тем, что уже получил."""

        seen[summary.external_id] = summary
        if self.on_batch is None:
            return
        pending = getattr(self, "_pending", None)
        if pending is None:
            pending = self._pending = []
        pending.append(summary)
        if len(pending) >= self.batch_size:
            self._pending = []
            self.on_batch(pending)

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
