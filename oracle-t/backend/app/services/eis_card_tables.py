"""Таблицы и этап закупки из карточки ЕИС (`tender_cards.payload`), 17.11.2026.

Перечень Bitrix24 «Что еще нужно от ИИ» (раздел 113 ARCHITECTURE.md). Карточка ЕИС, которую
мониторинг перечитывает при каждой проверке, уже содержит то, чего не было в слепке:

* поле «Этап закупки» — официальная формулировка («Подача заявок», «Работа комиссии»,
  «Определение поставщика приостановлено/отменено/завершено») — пп. 62, 63, 66, 72;
* «Журнал событий» ЕИС — дата, время с поясом и текст: приостановка, возобновление, жалоба,
  предписание, отмена — пп. 41–47, 62, 66, 67;
* «Протоколы» — название, **редакция**, статус («Действующая»/«Недействующая»), время размещения
  с поясом заказчика — пп. 70, 84;
* «Сведения о договорах» — заключённый договор и его редакции (изменение сведений о договоре —
  допсоглашение или исполнение) — пп. 88, 94;
* «Изменения и разъяснения (запросы заказчика)» — название, редакция, время — пп. 14, 52, 60, 65;
* «Список лотов» — номер, наименование, цена и ОКПД2 лота — п. 1.

Всё — кодом, без модели: это структурированные данные площадки, а не текст документа. Модуль
только читает `payload`; сравнение — в `tender_monitor_service`, строки для портала — в
`bitrix_ai_service`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.eis_card_dates import parse_card_datetime

_REVISION_RE = re.compile(r"^\s*(\d{1,3})\s*$")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _table(payload: dict | None, key: str) -> tuple[list[str], list[list[str]]]:
    table = ((payload or {}).get("tables") or {}).get(key) or {}
    if not isinstance(table, dict):
        return [], []
    headers = [_clean(h) for h in table.get("headers") or []]
    rows = [[_clean(cell) for cell in row] for row in table.get("rows") or [] if isinstance(row, list)]
    return headers, rows


def official_stage(payload: dict | None) -> str | None:
    """«Этап закупки» карточки дословно — официальный статус процедуры для журнала."""

    for section in (payload or {}).get("sections", []) or []:
        for field in section.get("fields", []) or []:
            if isinstance(field, (list, tuple)) and len(field) >= 2 and _clean(field[0]).lower() == "этап закупки":
                return _clean(field[1]) or None
    return None


def is_suspended(stage: str | None) -> bool:
    return bool(stage and "приостан" in stage.lower())


def is_cancelled(stage: str | None) -> bool:
    return bool(stage and "отмен" in stage.lower())


@dataclass(frozen=True)
class CardDocument:
    """Строка таблиц «Протоколы», «Сведения о договорах», «Изменения и разъяснения»."""

    name: str
    revision: str | None
    status: str | None
    placed_raw: str | None
    placed_at: datetime | None

    @property
    def active(self) -> bool:
        return not (self.status and "недейств" in self.status.lower())

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "revision": self.revision,
            "status": self.status,
            "placed": self.placed_at.isoformat(timespec="minutes") if self.placed_at else self.placed_raw,
        }


def card_documents(payload: dict | None, key: str) -> list[CardDocument]:
    """Строки таблицы документов карточки. Столбцы находятся по смыслу, а не по номеру: у
    протоколов первый столбец пустой (значок), у договоров его нет."""

    headers, rows = _table(payload, key)
    out: list[CardDocument] = []
    for row in rows:
        cells = [c for c in row]
        if headers and len(cells) == len(headers):
            named = dict(zip((h.lower() for h in headers), cells))
            name = named.get("наименование документа") or ""
            revision = named.get("редакция")
            status = named.get("статус")
            placed = named.get("размещено")
        else:
            # Без заголовков (у части карточек таблица пустая по заголовкам): название —
            # первое непустое, время — ячейка с датой, редакция — число.
            filled = [c for c in cells if c]
            if not filled:
                continue
            name = filled[0]
            placed = next((c for c in filled if re.search(r"\d{2}\.\d{2}\.\d{4}", c)), None)
            revision = next((c for c in filled[1:] if _REVISION_RE.match(c)), None)
            status = next((c for c in filled if "ействующ" in c), None)
        if not name:
            continue
        out.append(
            CardDocument(
                name=name,
                revision=(_REVISION_RE.match(revision).group(1) if revision and _REVISION_RE.match(revision) else None),
                status=status or None,
                placed_raw=placed or None,
                placed_at=parse_card_datetime(placed) if placed else None,
            )
        )
    return out


def documents_map(payload: dict | None, key: str) -> dict[str, dict[str, Any]]:
    """Для слепка мониторинга: название → редакция, статус, время. Несколько строк с одним
    названием (редакции одного протокола) сводятся к последней по редакции."""

    out: dict[str, dict[str, Any]] = {}
    for doc in card_documents(payload, key):
        prev = out.get(doc.name)
        if prev is None or int(doc.revision or 0) >= int(prev.get("revision") or 0):
            out[doc.name] = doc.as_dict()
    return out


@dataclass(frozen=True)
class JournalEntry:
    at: datetime | None
    raw_at: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.raw_at}|{self.text[:200]}"


# Записи журнала ЕИС, которые важны для Bitrix24 сами по себе; рутинные переходы этапов и
# «Размещён документ» журнал мониторинга и так видит по этапу и составу документов.
NOTABLE_RE = re.compile(r"приостан|возобнов|жалоб|предписан|антимонопол|\bФАС\b|отмен", re.IGNORECASE)


def journal(payload: dict | None) -> list[JournalEntry]:
    _, rows = _table(payload, "events")
    out: list[JournalEntry] = []
    for row in rows:
        filled = [c for c in row if c]
        if len(filled) < 2:
            continue
        out.append(JournalEntry(at=parse_card_datetime(filled[0]), raw_at=filled[0], text=" ".join(filled[1:])))
    return out


def notable_journal(payload: dict | None) -> dict[str, dict[str, Any]]:
    return {
        entry.key: {"text": entry.text[:600], "at": entry.at.isoformat(timespec="minutes") if entry.at else entry.raw_at}
        for entry in journal(payload)
        if NOTABLE_RE.search(entry.text)
    }


_LOT_RE = re.compile(r"^\s*(\d{1,4})\s+(.+)$")
_PRICE_RE = re.compile(r"(\d[\d\s]*[.,]?\d*)\s*₽")


def lots(payload: dict | None) -> list[dict[str, Any]]:
    """«Список лотов» карточки: номер, наименование, цена, ОКПД2 (п. 1). Строки критериев
    оценки, которые ЕИС кладёт в ту же таблицу, отбрасываются."""

    _, rows = _table(payload, "lots")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not row or not row[0]:
            continue
        match = _LOT_RE.match(row[0])
        if match is None or row[0].startswith("№"):
            continue
        price_text = next((c for c in row[1:] if "₽" in c or "цен" in c.lower()), None)
        price = None
        if price_text and (m := _PRICE_RE.search(price_text)):
            try:
                price = float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
            except ValueError:
                price = None
        okpd2 = next((c for c in row[1:] if re.match(r"^\d{2}\.\d{2}", c)), None)
        out.append(
            {
                "lot_number": match.group(1),
                "name": match.group(2).strip(),
                "price": price,
                "price_text": price_text,
                "okpd2": okpd2,
            }
        )
    return out
