"""Полная карточка тендера для интерфейса (раздел 5.6 ТЗ) и заполнение пробелов в данных.

Карточка забирается с сайта источника при первом открытии тендера и сохраняется: страница
ЕИС отвечает секунды, а открывают тендер многократно. Обновляется по явному запросу
пользователя — извещение живёт своей жизнью (редакции, протоколы, договоры), и кнопка
«обновить» нужна, но дёргать сайт при каждом открытии карточки незачем.

Попутно карточка закрывает пробелы в самом тендере. Главный из них — регион: он есть на
странице ЕИС всегда (в адресе заказчика и в его ИНН), но до сих пор заполнялся только
ИИ-анализом документации, которого на большинстве закупок никто не запускал. Заодно из
карточки берутся заказчик, способ закупки и ОКПД2, если их не было в строке реестра.

Автозаполнение пишется в историю тендера с указанием источника значения: подставленное
системой значение должно быть отличимо от введённого человеком и проверяемо.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone

import httpx
from loguru import logger
from sqlalchemy.orm import Session

from app.adapters.eis_card import BASE_URL as EIS_BASE_URL
from app.adapters.eis_card import fetch_card
from app.adapters.eis_documents import normalize_eis_number
from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify
from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_history import HistoryKind, TenderHistoryEntry
from app.models.user import User
from app.services import region_resolver
from app.services.audit import log_action

# Названия полей карточки, из которых берутся значения для самого тендера. Список нарочно
# небольшой: карточка показывается пользователю целиком, а в поля тендера попадает только то,
# по чему потом фильтруют и считают.
_CUSTOMER_NAME_LABELS = ("наименование организации", "наименование заказчика")
_ADDRESS_LABELS = ("почтовый адрес", "место нахождения", "адрес")
_INN_LABELS = ("инн",)
_OKPD2_LABELS = ("окпд2", "код по окпд2", "классификация по окпд2")
_METHOD_LABELS = ("способ осуществления закупки", "способ определения поставщика", "способ закупки")

# Код ОКПД2 в значении столбца: «71.12.40.120 Услуги в области метрологии».
_OKPD2_VALUE_RE = re.compile(r"\b\d{2}(?:\.\d{1,2}){1,3}(?:\.\d{3})?\b")


def _okpd2_from_lots(payload: dict) -> str | None:
    """Код ОКПД2 из таблицы лотов карточки ЕИС.

    Это авторитетный источник: площадка публикует классификацию закупки отдельным столбцом
    («Классификация по ОКПД2»), а не прячет её в тексте документации. Раньше сюда не
    заглядывали, и код приходилось выуживать регулярным выражением из документов — где он
    путался с датами (у тендера на метрологические услуги «Дата подведения итогов:
    23.12.2026» превращалась в ОКПД2 23.12, то есть «стекло обработанное»).

    Берётся первый лот: у закупки с несколькими лотами коды могут отличаться, и выбор одного
    для карточки всё равно условен — детали лотов видны в карточке закупки целиком.
    """

    lots = (payload.get("tables") or {}).get("lots") or {}
    headers = [str(header).lower() for header in lots.get("headers") or []]
    index = next((i for i, header in enumerate(headers) if "окпд2" in header), None)
    if index is None:
        return None
    for row in lots.get("rows") or []:
        if len(row) <= index:
            continue
        match = _OKPD2_VALUE_RE.search(str(row[index]))
        if match:
            return match.group(0)
    return None


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=EIS_BASE_URL,
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=40.0,
        verify=resolve_verify(EIS_BASE_URL),
        follow_redirects=True,
    )


def _card_url(tender: Tender) -> str | None:
    """Адрес карточки закупки в ЕИС.

    Для тендеров самой ЕИС это их `source_url`. Для закупок с коммерческих площадок —
    собранный по реестровому номеру адрес: страница ЕИС есть у любой закупки 44-ФЗ и 223-ФЗ
    независимо от площадки проведения, и данных на ней больше, чем в карточке площадки.
    """

    if tender.source_url and "zakupki.gov.ru" in tender.source_url:
        return tender.source_url

    number = normalize_eis_number(tender.external_id)
    if number is None:
        return None

    # 44-ФЗ — 19 цифр, 223-ФЗ — 11: у них разные разделы сайта.
    if len(number) == 19:
        return f"{EIS_BASE_URL}/epz/order/notice/ea20/view/common-info.html?regNumber={number}"
    return (
        f"{EIS_BASE_URL}/223/purchase/public/purchase/info/common-info.html?regNumber={number}"
    )


def _find_field(payload: dict, section_markers: tuple[str, ...], field_markers: tuple[str, ...]) -> str | None:
    """Значение поля по нечёткому совпадению названий раздела и поля.

    Метки перебираются в заданном порядке, а не в порядке полей на странице: у заказчика есть
    и «Место нахождения», и «Почтовый адрес», и первый по странице — не всегда лучший. В
    почтовом адресе регион пишется словами («Саратовская обл»), в месте нахождения его может
    не быть вовсе — только город.
    """

    for marker in field_markers:
        for section in payload.get("sections", []):
            title = (section.get("title") or "").lower()
            if section_markers and not any(m in title for m in section_markers):
                continue
            for name, value in section.get("fields", []):
                if marker in name.lower():
                    return value
    return None


def _all_text(payload: dict) -> str:
    parts: list[str] = []
    for section in payload.get("sections", []):
        for name, value in section.get("fields", []):
            parts.append(f"{name}: {value}")
    for table in payload.get("tables", {}).values():
        for row in table.get("rows", []):
            parts.append(" ".join(row))
    return "\n".join(parts)


def fill_gaps_from_card(db: Session, tender: Tender, payload: dict, *, actor: User | None = None) -> list[str]:
    """Заполняет пустые поля тендера данными карточки. Возвращает описания того, что заполнено.

    Заполняются только пустые поля: значение, введённое или исправленное человеком, всегда
    имеет приоритет над автоматикой (тот же принцип, что в справочнике продукции и в анализе
    требований).
    """

    filled: list[str] = []

    if not tender.customer_name:
        customer = _find_field(payload, ("заказчик",), _CUSTOMER_NAME_LABELS)
        if customer:
            tender.customer_name = customer[:500]
            filled.append(f"заказчик — {customer[:80]}")

    if not tender.procurement_method:
        method = _find_field(payload, (), _METHOD_LABELS)
        if method:
            tender.procurement_method = method[:255]
            filled.append(f"способ закупки — {method[:60]}")

    # ОКПД2 из карточки перезаписывает значение, найденное в тексте документации, а не
    # уступает ему: площадка публикует классификацию закупки явным полем, а разбор текста —
    # догадка по регулярному выражению. Пока приоритет был обратным («заполняем, только если
    # пусто»), достоверный код проигрывал случайному совпадению из документа.
    okpd2 = _okpd2_from_lots(payload)
    if okpd2 is None:
        field_value = _find_field(payload, (), _OKPD2_LABELS)
        match = _OKPD2_VALUE_RE.search(field_value) if field_value else None
        okpd2 = match.group(0) if match else None
    if okpd2 and okpd2 != tender.okpd2_code:
        was = tender.okpd2_code
        tender.okpd2_code = okpd2
        filled.append(f"ОКПД2 — {okpd2}" + (f" (было {was})" if was else ""))

    if not tender.region_organizer_code:
        address = _find_field(payload, ("заказчик",), _ADDRESS_LABELS)
        inn = _find_field(payload, ("заказчик",), _INN_LABELS)
        region, explanation = region_resolver.resolve_region(
            db, address=address, inn=inn, extra_text=_all_text(payload)[:4000]
        )
        if region is not None:
            tender.region_organizer_code = region.code
            tender.federal_district_code = region.federal_district_code
            filled.append(f"регион заказчика — {region.name} ({explanation})")

    for description in filled:
        db.add(
            TenderHistoryEntry(
                tender_id=tender.id,
                kind=HistoryKind.FIELD_CHANGE.value,
                field_name="card_autofill",
                new_value=description,
                comment="Заполнено из карточки закупки на сайте источника",
                user_id=actor.id if actor else None,
            )
        )

    return filled


def get_card(db: Session, tender: Tender) -> TenderCard | None:
    return db.get(TenderCard, tender.id)


def sync_card(
    db: Session, tender: Tender, *, actor: User | None = None, force: bool = False
) -> TenderCard | None:
    """Забирает карточку с сайта источника и сохраняет её.

    Повторный вызов без `force` возвращает сохранённую: страница ЕИС отвечает секунды, а
    карточку тендера открывают многократно.
    """

    existing = get_card(db, tender)
    if existing is not None and not force:
        return existing

    url = _card_url(tender)
    if url is None:
        return existing

    try:
        with _client() as client:
            card = fetch_card(client, url)
    except Exception as exc:  # noqa: BLE001 - недоступность источника не должна ронять карточку
        logger.warning(f"Карточка закупки {tender.external_id} не получена: {exc}")
        log_action(
            db,
            component="tender_card",
            action=f"sync_card:{tender.external_id}",
            result="error",
            level=LogLevel.WARNING,
            details=str(exc),
            user_id=actor.id if actor else None,
        )
        db.commit()
        return existing

    payload = {
        "sections": [asdict(section) for section in card.sections],
        "tables": {key: asdict(table) for key, table in card.tables.items()},
        "tab_urls": card.tab_urls,
    }

    record = existing or TenderCard(tender_id=tender.id)
    record.payload = payload
    record.fetched_at = datetime.now(timezone.utc)
    db.add(record)

    filled = fill_gaps_from_card(db, tender, payload, actor=actor)

    log_action(
        db,
        component="tender_card",
        action=f"sync_card:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Разделов: {len(card.sections)}, вкладок: {len(card.tables)}"
            + (f"; заполнено полей: {'; '.join(filled)}" if filled else "")
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    db.refresh(record)
    return record
