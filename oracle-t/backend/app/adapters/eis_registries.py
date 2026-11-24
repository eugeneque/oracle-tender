"""Реестры ЕИС для перечня Bitrix24: жалобы, договоры, закупки заказчика (19.11.2026, раздел 115).

* **Реестр жалоб** (`/epz/complaint/...`): поиск по номеру закупки; в выдаче — статус
  («Рассмотрена»), итог («Признана обоснованной»), предписание, орган контроля, заявитель, даты
  поступления и размещения, решение; во вкладке «Сведения жалобы» — дата, время и место
  рассмотрения, резолютивная часть и полный текст решения как разные публикации (пп. 41–47, 89).
* **Реестр договоров 223-ФЗ и контрактов 44-ФЗ**: поиск по номеру закупки; карточка — цена,
  даты заключения и исполнения, обеспечение, причина изменения («Дополнительное соглашение»),
  номер и дата документа; у 223-ФЗ — журнал версий с редакциями (пп. 90, 94).
* **Закупки заказчика**: расширенный поиск ЕИС по ИНН заказчика за период (п. 7). ОКПД2 в выдаче
  нет — отбор по коду делает сервис по карточке закупки.

Только чтение открытых страниц, без учётной записи. TLS — `Russian Trusted Sub CA`, как у
`eis.py` (`resolve_verify`). Разбор — по парам «section__title / section__info» карточек ЕИС и
по тексту блока выдачи: структура выдачи у всех реестров одна (`search-registry-entry-block`).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.services.eis_card_dates import parse_card_datetime as parse_any_datetime
from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

BASE_URL = "https://zakupki.gov.ru"
COMPLAINT_SEARCH = "/epz/complaint/search/search_eis.html"
COMPLAINT_INFO = "/epz/complaint/card/complaint-information.html"
CONTRACT_223_SEARCH = "/epz/contractfz223/search/results.html"
CONTRACT_223_CARD = "/epz/contractfz223/card/contract-info.html"
CONTRACT_223_VERSIONS = "/epz/contractfz223/card/journal-version.html"
CONTRACT_44_SEARCH = "/epz/contract/search/results.html"
CONTRACT_44_CARD = "/epz/contract/contractCard/common-info.html"
ORDER_SEARCH = "/epz/order/extendedsearch/results.html"

MAX_COMPLAINTS = 10
MAX_CONTRACTS = 5
MAX_VERSIONS = 8
HISTORY_PAGES = 3


class EisRegistryError(RuntimeError):
    """Реестр не ответил. Текст показывается в журнале как есть."""


def client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(BASE_URL),
        follow_redirects=True,
    )


def _text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def _get(http: httpx.Client, path: str, params: dict[str, str]) -> BeautifulSoup:
    response = fetch_with_retry(http, "GET", path, params=params)
    if response.status_code >= 400:
        raise EisRegistryError(f"ЕИС ответил HTTP {response.status_code} на {path}")
    return BeautifulSoup(response.text, "lxml")


def sections(soup: BeautifulSoup) -> dict[str, str]:
    """Пары «подпись → значение» карточки ЕИС; повтор подписи — первое значение."""

    out: dict[str, str] = {}
    for section in soup.select("section"):
        title, info = section.select_one(".section__title"), section.select_one(".section__info")
        if title is None or info is None:
            continue
        key = _text(title)
        if key and key not in out:
            out[key] = _text(info)
    # Карточка жалобы размечена иначе: «common-text__title / common-text__value».
    for title in soup.select(".common-text__title"):
        value = title.find_next_sibling(class_="common-text__value")
        key = _text(title)
        if value is not None and key and key not in out:
            out[key] = _text(value)
    return out


def pairs(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """Все пары карточки по порядку — для полей, которые повторяются (у заказчика и у
    поставщика одинаковые подписи «Полное наименование», «ИНН»)."""

    out = []
    for section in soup.select("section"):
        title, info = section.select_one(".section__title"), section.select_one(".section__info")
        if title is not None and info is not None:
            out.append((_text(title), _text(info)))
    return out


def supplier_of(items: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    """Поставщик — последнее «Полное наименование» не заказчика и ИНН сразу за ним."""

    customer = next((v for k, v in items if k.startswith("Полное наименование заказчика")), None)
    for index in range(len(items) - 1, -1, -1):
        key, value = items[index]
        if key == "Полное наименование" and value and value != customer:
            inn = next((v for k, v in items[index + 1:index + 6] if k == "ИНН"), None)
            return value, inn
    return None, None


def _pick(fields: dict[str, str], *markers: str) -> str | None:
    for marker in markers:
        for key, value in fields.items():
            if marker.lower() in key.lower() and value:
                return value
    return None


def _date(text: str | None) -> str | None:
    match = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{1,2}:\d{2})?(?:\s*\(МСК[^)]*\))?", text or "")
    if not match:
        return None
    parsed = parse_any_datetime(match.group(0))
    if parsed is None:
        return None
    # Дата без времени — датой: полночь по Москве выглядела бы как точный момент.
    return parsed.date().isoformat() if ":" not in match.group(0) else parsed.isoformat(timespec="minutes")


_MONEY_RE = re.compile(r"(\d[\d\s ]*(?:[.,]\d{1,2})?)\s*(?:₽|руб)")


def _money(text: str | None) -> float | None:
    match = _MONEY_RE.search(text or "") or re.fullmatch(r"\s*(\d[\d\s ]*(?:[.,]\d{1,2})?)\s*", text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


# --- жалобы ---------------------------------------------------------------------------------------

_OUTCOME_RE = re.compile(r"Признана\s+(?:не)?обоснованной(?:\s+частично)?|Жалоба\s+отозвана|Возвращена|Рассмотрение\s+прекращено", re.I)


def parse_complaint_entry(block: Any) -> dict[str, Any] | None:
    text = _text(block)
    number = re.search(r"№\s*([0-9A-ZА-Я]{10,30})", text)
    link = next((a["href"] for a in block.select("a[href]") if "complaint/card" in a["href"]), None)
    if number is None:
        return None
    status = re.search(r"№\s*[0-9A-ZА-Я]{10,30}\s+(Размещена|Рассмотрена|На рассмотрении|Отозвана|Возвращена|Рассмотрение приостановлено|Прекращено рассмотрение)", text)
    prescription = re.search(r"Предписание\s+(не\s+)?выдано", text)
    decision = re.search(r"Решение\s+№\s*([^ ]+)\s+от\s+(\d{2}\.\d{2}\.\d{4})", text)
    return {
        "number": number.group(1),
        "status": status.group(1) if status else None,
        "outcome": (_OUTCOME_RE.search(text).group(0) if _OUTCOME_RE.search(text) else None),
        "prescription": (None if prescription is None else not prescription.group(1)),
        "control_body": (m.group(1) if (m := re.search(r"Орган контроля\s+(.+?)\s+Субъект контроля", text)) else None),
        "subject": (m.group(1) if (m := re.search(r"Субъект контроля\s+(.+?)\s+Лицо, подавшее жалобу", text)) else None),
        "applicant": (m.group(1) if (m := re.search(r"Лицо, подавшее жалобу\s+(.+?)\s+Жалоба", text)) else None),
        "received": _date(m.group(1)) if (m := re.search(r"Поступление жалобы\s+(\d{2}\.\d{2}\.\d{4})", text)) else None,
        "placed": _date(m.group(1)) if (m := re.search(r"Размещено\s+(\d{2}\.\d{2}\.\d{4})", text)) else None,
        "decision_number": decision.group(1) if decision else None,
        "decision_date": _date(decision.group(2)) if decision else None,
        "url": urljoin(BASE_URL, link) if link else None,
        "text": text[:1500],
    }


def _decision_documents(soup: BeautifulSoup, fields: dict[str, str]) -> dict[str, Any]:
    """Резолютивная часть и полное решение — разные публикации карточки жалобы (пп. 42–43)."""

    out: dict[str, Any] = {
        "resolutive_text": _pick(fields, "Резолютивная часть"),
        "full_text": _pick(fields, "Полный текст решения"),
    }
    for link in soup.select("a[href]"):
        own = _text(link)
        context = own if "решени" in own.lower() else (_text(link.parent) or own)
        lowered = context.lower()
        url = urljoin(BASE_URL, link.get("href", ""))
        # Иногда ссылки стоят в одном блоке: после найденной резолютивной части вторая —
        # полный текст. Собственный текст ссылки важнее общего текста родителя.
        if "полный текст решения" in lowered and ("резолютив" not in lowered or out.get("resolutive_url")):
            out.setdefault("full_url", url)
            out.setdefault("decision_document_name", _text(link) or "Полный текст решения ФАС")
        elif "резолютив" in lowered and "resolutive_url" not in out:
            out["resolutive_url"] = url
        elif "текст решения" in lowered and "резолютив" not in lowered:
            out.setdefault("full_url", url)
            out.setdefault("decision_document_name", _text(link) or "Полный текст решения ФАС")
    out["decision_url"] = out.get("full_url") or out.get("resolutive_url")
    out["decision_form"] = "FULL" if out.get("full_url") or out.get("full_text") else (
        "RESOLUTIVE" if out.get("resolutive_url") or out.get("resolutive_text") else None
    )
    out["decision_access"] = "AVAILABLE" if out["decision_form"] else "NOT_PUBLISHED"
    return out


def _read_decision_text(http: httpx.Client, url: str, file_name: str) -> str:
    """Текст опубликованного файла решения; ничего не сохраняем в документы закупки."""

    from app.services import document_service
    from app.services.document_extraction import extract_text, sniff_extension

    response = fetch_with_retry(http, "GET", url)
    if response.status_code >= 400:
        raise EisRegistryError(f"ЕИС ответил HTTP {response.status_code} на файл решения")
    extension = document_service._guess_extension(file_name, url) or sniff_extension(response.content)  # noqa: SLF001
    return extract_text(extension, response.content)[:20000]


def fetch_complaints(purchase_number: str) -> list[dict[str, Any]]:
    """Жалобы по закупке: выдача реестра плюс дата, место рассмотрения и части решения."""

    with client() as http:
        soup = _get(http, COMPLAINT_SEARCH, {"searchString": purchase_number, "recordsPerPage": "_50"})
        complaints = []
        for block in soup.select(".search-registry-entry-block"):
            entry = parse_complaint_entry(block)
            # Поиск полнотекстовый: жалоба другой закупки с тем же числом в тексте — не наша.
            if entry is None or purchase_number not in entry["text"]:
                continue
            complaints.append(entry)
        for entry in complaints[:MAX_COMPLAINTS]:
            try:
                soup = _get(http, COMPLAINT_INFO, {"complaintNumber": entry["number"]})
                card = sections(soup)
            except (EisRegistryError, httpx.HTTPError):
                continue
            entry["hearing_at"] = _date(_pick(card, "Дата и время рассмотрения жалобы"))
            entry["hearing_place"] = _pick(card, "Место рассмотрения жалобы")
            entry["accepted_at"] = _date(_pick(card, "Дата уведомления о принятии жалобы"))
            result = _pick(card, "Результат рассмотрения жалобы") or ""
            entry["outcome"] = (_OUTCOME_RE.search(result).group(0) if _OUTCOME_RE.search(result) else None) or entry["outcome"]
            record = re.search(r"Размещены результаты контроля\s+№\s*(\S+)\s+от\s+(\d{2}\.\d{2}\.\d{4})", result)
            if record:
                entry["result_record"] = record.group(1)
                entry["result_placed"] = _date(record.group(2))
            entry["essence"] = (_pick(card, "Содержание жалобы") or "")[:1000] or None
            entry.update(_decision_documents(soup, card))
            for form, url_key, text_key in (("FULL", "full_url", "full_text"), ("RESOLUTIVE", "resolutive_url", "resolutive_text")):
                if entry.get(url_key) and not entry.get(text_key):
                    try:
                        entry[text_key] = _read_decision_text(
                            http, entry[url_key], entry.get("decision_document_name") or f"{form}.pdf"
                        )
                    except Exception as exc:  # noqa: BLE001 - ссылка и факт публикации остаются
                        entry["decision_access"] = "UNAVAILABLE"
                        entry["decision_note"] = f"Файл решения опубликован, но не прочитан: {exc}"
    return complaints[:MAX_COMPLAINTS]


# --- договоры ------------------------------------------------------------------------------------


def _contract_entries(soup: BeautifulSoup, card_marker: str) -> list[dict[str, Any]]:
    out = []
    for block in soup.select(".search-registry-entry-block"):
        text = _text(block)
        number = re.search(r"№\s*(\d{15,25})", text)
        link = next((a["href"] for a in block.select("a[href]") if card_marker in a["href"]), None)
        if number is None:
            continue
        out.append(
            {
                "number": number.group(1),
                "status": (m.group(1) if (m := re.search(r"№\s*\d{15,25}\s+(\S+(?:\s+\S+)?)\s+(?:Номер|Заказчик)", text)) else None),
                "price": _money(m.group(1)) if (m := re.search(r"Цена (?:договора|контракта)\s+([\d\s ,]+₽)", text)) else None,
                "concluded": _date(m.group(1)) if (m := re.search(r"Заключение (?:договора|контракта)\s+(\d{2}\.\d{2}\.\d{4})", text)) else None,
                "url": urljoin(BASE_URL, link) if link else None,
                "text": text[:1000],
            }
        )
    return out


def _contract_card(soup: BeautifulSoup, header: str) -> dict[str, Any]:
    supplier, inn = supplier_of(pairs(soup))
    return {**_contract_fields(sections(soup), header), "supplier": supplier, "supplier_inn": inn}


def _contract_fields(fields: dict[str, str], header: str) -> dict[str, Any]:
    term = re.search(r"Срок исполнения\s+(\d{2}\.\d{2}\.\d{4})\s*—\s*(\d{2}\.\d{2}\.\d{4})", header)
    return {
        "price": _money(_pick(fields, "Цена договора", "Цена контракта", "Максимальное значение цены")) or
        (_money(m.group(1)) if (m := re.search(r"Цена (?:договора|контракта)\s+([\d\s ,]+₽)", header)) else None),
        "concluded": _date(_pick(fields, "Дата заключения договора", "Дата заключения контракта")),
        "execution_start": _date(_pick(fields, "Дата начала исполнения")) or (_date(term.group(1)) if term else None),
        "execution_end": _date(_pick(fields, "Дата окончания исполнения")) or (_date(term.group(2)) if term else None),
        "subject": _pick(fields, "Предмет договора", "Предмет контракта"),
        "security": _money(_pick(fields, "Размер обеспечения исполнения")),
        "change_reason": _pick(fields, "Причина изменения информации о договоре", "Основание изменения"),
        "change_date": _date(_pick(fields, "Дата утверждения изменений")),
        "change_terms": _pick(fields, "Изменился объем, цена"),
    }


def fetch_contracts(purchase_number: str, law: str | None, known: list[str] | None = None) -> list[dict[str, Any]]:
    """Договоры по закупке с редакциями (223-ФЗ — журнал версий; 44-ФЗ — текущая редакция).

    Реестр договоров 223-ФЗ по номеру закупки не ищет (проверено 19.11.2026: выдача пуста) —
    номера договоров берутся из вкладки «Сведения о договорах» карточки закупки (`known`)."""

    out: list[dict[str, Any]] = []
    with client() as http:
        if law != "44":
            entries = []
            for number in (known or [])[:MAX_CONTRACTS]:
                soup = _get(http, CONTRACT_223_SEARCH, {"searchString": number})
                entries.extend(e for e in _contract_entries(soup, "contractfz223/card") if e["number"] == number)
            for entry in entries[:MAX_CONTRACTS]:
                entry["law"] = "223"
                card = _get(http, CONTRACT_223_CARD, {"reestrNumber": entry["number"]})
                header = _text(card.select_one(".cardMainInfo")) or _text(card)[:3000]
                entry.update({k: v for k, v in _contract_card(card, header).items() if v is not None})
                versions = []
                journal = _get(http, CONTRACT_223_VERSIONS, {"reestrNumber": entry["number"]})
                for match in re.finditer(
                    r"Сведения о договоре № \d+\s+(\d+)\s+(Действующая|Недействующая)\s+(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2}\s*\(МСК[^)]*\))",
                    _text(journal),
                ):
                    versions.append({"version": int(match.group(1)), "status": match.group(2), "placed": _date(match.group(3))})
                versions.sort(key=lambda v: v["version"])
                for version in versions[-MAX_VERSIONS:]:
                    try:
                        page = _get(http, CONTRACT_223_CARD, {"reestrNumber": entry["number"], "version": str(version["version"])})
                    except (EisRegistryError, httpx.HTTPError):
                        continue
                    head = _text(page.select_one(".cardMainInfo")) or _text(page)[:3000]
                    version.update({k: v for k, v in _contract_card(page, head).items() if v is not None})
                entry["versions"] = versions
                entry.pop("text", None)
                out.append(entry)
        if law != "223":
            soup = _get(http, CONTRACT_44_SEARCH, {"searchString": purchase_number, "fz44": "on"})
            for entry in _contract_entries(soup, "contractCard")[:MAX_CONTRACTS]:
                entry["law"] = "44"
                card = _get(http, CONTRACT_44_CARD, {"reestrNumber": entry["number"]})
                entry.update({k: v for k, v in _contract_card(card, _text(card)[:3000]).items() if v is not None})
                entry["versions"] = []
                entry.pop("text", None)
                out.append(entry)
    return out


# --- закупки заказчика -----------------------------------------------------------------------------


def fetch_customer_purchases(
    customer_inn: str, *, days: int = 365, as_of: date | None = None
) -> list[dict[str, Any]]:
    """Закупки заказчика за период по его ИНН (поиск ЕИС находит ИНН в карточке заказчика)."""

    since = ((as_of or date.today()) - timedelta(days=days)).strftime("%d.%m.%Y")
    until = (as_of or date.today()).strftime("%d.%m.%Y")
    out: list[dict[str, Any]] = []
    with client() as http:
        for page in range(1, HISTORY_PAGES + 1):
            soup = _get(
                http,
                ORDER_SEARCH,
                {"searchString": customer_inn, "morphology": "on", "fz44": "on", "fz223": "on",
                 "recordsPerPage": "_50", "pageNumber": str(page), "publishDateFrom": since,
                 "publishDateTo": until, "sortBy": "PUBLISH_DATE", "sortDirection": "false"},
            )
            blocks = soup.select(".search-registry-entry-block")
            for block in blocks:
                text = _text(block)
                number = re.search(r"№\s*(\d{11,19})", text)
                link = next((a["href"] for a in block.select("a[href]") if "common-info" in a["href"]), None)
                if number is None:
                    continue
                title = re.search(r"Объект закупки\s+(.+?)\s+(?:Заказчик|Организация)", text)
                out.append(
                    {
                        "number": number.group(1),
                        "law": "44" if len(number.group(1)) == 19 else "223",
                        "title": title.group(1) if title else None,
                        "status": (m.group(1) if (m := re.search(r"№\s*\d{11,19}\s+(.+?)\s+Объект закупки", text)) else None),
                        "price": _money(m.group(1)) if (m := re.search(r"Начальная цена\s+([\d\s ,]+₽)", text)) else None,
                        "published": _date(m.group(1)) if (m := re.search(r"Размещено\s+(\d{2}\.\d{2}\.\d{4})", text)) else None,
                        "url": urljoin(BASE_URL, link) if link else None,
                    }
                )
            if len(blocks) < 50:
                break
    return out
