"""Поиск документации на прибор через Яндекс (замечание заказчика 15.09.2026).

**Ситуация, ради которой модуль написан.** На сайте производителя нет ни карточки, ни
документов о новом приборе — исполнение известно только из Аршина, — а руководство по
эксплуатации на официальном сайте уже лежит и находится поисковиком. Пример: НАРТИС-И100
с корпусом W115 — в каталоге `nartis.ru` его нет, а
`nartis.ru/upload/iblock/5f6/nmge5xsoiskcr0avk6yrwilnnctko9rp.pdf` описывает его на 121
странице. То же относится к моделям, у которых обход сайта ссылку на руководство не нашёл
(Waviot: документов на карточках нет вовсе).

**Что считается найденным.** Только документ с официального сайта производителя: домен
результата должен совпадать с сайтом из справочника производителя либо с адресом каталога
из профиля обхода (с поддоменами). Форумы, магазины и агрегаторы документации отсекаются —
на них лежат устаревшие редакции и чужие файлы, а справочник потом сверяется с тендером.
Дальше документ должен упоминать прибор: в заголовке или фрагментах выдачи — обозначение
модели либо хотя бы семейство (обозначение типа); PDF предпочтительнее страницы.

Найденная ссылка сохраняется в справочник полем «Ссылка на руководство» с источником
`web_search`, а разбор документа делает тот же `product_manual_ingest`, что и для ссылок
с карточек сайта: способ найти документ — другой, способ прочитать его — тот же.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.yandex_search import SearchHit, search_with_credentials
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    SiType,
)
from app.services import si_type_linking
from app.services.audit import log_action

COMPONENT = "product_catalog"

MANUAL_FIELD = ("Документация", "Ссылка на руководство")

# Слова, по которым документ узнаётся как руководство/паспорт, — и слова, по которым он
# точно не то (прайс, новость, сертификат).
_MANUAL_WORDS_RE = re.compile(r"руководств|паспорт|эксплуатац|инструкци|техническ\w*\s+описани", re.IGNORECASE)
_NOT_MANUAL_RE = re.compile(r"прайс|price|новост|сертификат|деклараци|методик\w*\s+поверк|каталог\s+продукц", re.IGNORECASE)

# Минимальный балл, при котором документ принимается без участия человека.
MIN_SCORE = 3


@dataclass
class DocumentCandidate:
    url: str
    title: str
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class DiscoveryOutcome:
    products_checked: int = 0
    found: int = 0
    not_found: int = 0
    skipped_have_link: int = 0
    queries: int = 0
    messages: list[str] = field(default_factory=list)


def official_domains(db: Session, manufacturer: Manufacturer) -> set[str]:
    """Домены, которые считаются официальным сайтом производителя: сайт из справочника и
    адрес каталога из профиля обхода (у Нартиса в справочнике `nartis-region.ru`, а каталог
    и документы — на `nartis.ru`)."""

    from app.adapters.manufacturer_catalog import PROFILES

    domains: set[str] = set()
    if manufacturer.website:
        domains.add(_bare_host(manufacturer.website))
    for profile in PROFILES.values():
        if profile.manufacturer_legal_name == manufacturer.legal_name:
            domains.add(_bare_host(profile.base_url))
    if manufacturer.is_mirtek:
        domains.add("mirtekgroup.com")
    return {d for d in domains if d}


def _bare_host(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _is_official(hit: SearchHit, domains: set[str]) -> bool:
    host = hit.host
    host = host[4:] if host.startswith("www.") else host
    return any(host == d or host.endswith("." + d) for d in domains)


def build_queries(product: Product, si_type: SiType | None, domains: set[str]) -> list[str]:
    """Запросы от точного к общему. Первый — по коду модели на официальном сайте; второй —
    по семейству (обозначению типа), когда код модели в документе ещё не проиндексирован."""

    code = (product.model_code or product.model_name or "").strip()
    family = (si_type.notation or "").strip() if si_type else ""
    queries: list[str] = []
    for domain in sorted(domains):
        queries.append(f"site:{domain} {code} руководство по эксплуатации")
        if family and family.lower() not in code.lower():
            queries.append(f"site:{domain} {family} {code} руководство по эксплуатации")
        elif family and family.lower() != code.lower():
            queries.append(f"site:{domain} {family} руководство по эксплуатации")
    return queries


def score_hit(hit: SearchHit, *, product: Product, si_type: SiType | None, domains: set[str]) -> DocumentCandidate | None:
    """Оценка одного результата выдачи. `None` — не с официального сайта."""

    if not _is_official(hit, domains):
        return None
    text = hit.text
    text_keys = si_type_linking.designation_keys(text)
    score = 0
    reasons: list[str] = []

    code_keys = si_type_linking.designation_keys(product.model_code or product.model_name)
    if any(ck in tk for ck in code_keys for tk in text_keys if len(ck) >= 3):
        score += 3
        reasons.append("упоминает модель")
    family_keys = si_type_linking.designation_keys(si_type.notation) if si_type else set()
    if any(fk in tk for fk in family_keys for tk in text_keys if len(fk) >= 3):
        score += 1
        reasons.append("упоминает семейство")
    if hit.is_pdf:
        score += 1
        reasons.append("PDF")
    if _MANUAL_WORDS_RE.search(text):
        score += 1
        reasons.append("похоже на руководство")
    if _NOT_MANUAL_RE.search(hit.title):
        score -= 3
        reasons.append("не руководство по заголовку")
    if score <= 0:
        return None
    return DocumentCandidate(url=hit.url, title=hit.title, score=score, reasons=reasons)


def find_manual_for_product(
    db: Session,
    product: Product,
    manufacturer: Manufacturer,
    *,
    outcome: DiscoveryOutcome | None = None,
    search=search_with_credentials,
) -> DocumentCandidate | None:
    """Ищет руководство на модель на официальном сайте. Ничего не сохраняет."""

    outcome = outcome or DiscoveryOutcome()
    domains = official_domains(db, manufacturer)
    if not domains:
        outcome.messages.append(
            f"У производителя «{manufacturer.legal_name}» не указан сайт — искать негде"
        )
        return None

    si_type = db.get(SiType, product.si_type_id) if product.si_type_id else None
    best: DocumentCandidate | None = None
    for query in build_queries(product, si_type, domains):
        outcome.queries += 1
        hits = search(db, query)
        for hit in hits:
            candidate = score_hit(hit, product=product, si_type=si_type, domains=domains)
            if candidate is None:
                continue
            if best is None or candidate.score > best.score:
                best = candidate
        if best is not None and best.score >= MIN_SCORE + 1:
            # Нашёлся документ, упоминающий модель, — второй запрос не нужен.
            break
    if best is None or best.score < MIN_SCORE:
        return None
    return best


def discover_documents(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor_id: uuid.UUID | None = None,
    limit: int = 20,
    products: list[Product] | None = None,
    search=search_with_credentials,
) -> DiscoveryOutcome:
    """Находит ссылки на руководства для моделей производителя, у которых их нет.

    `limit` — сколько моделей проверить за проход: каждая модель — один-два платных запроса
    к поисковику. Модели, заведённые из реестра, идут первыми: у них документов нет заведомо."""

    outcome = DiscoveryOutcome()
    candidates = products if products is not None else list(
        db.scalars(
            select(Product)
            .where(Product.manufacturer_id == manufacturer.id)
            .order_by(Product.registry_modification.is_(None), Product.model_name)
        )
    )

    for product in candidates:
        if _manual_link(db, product):
            outcome.skipped_have_link += 1
            continue
        if outcome.products_checked >= limit:
            break
        outcome.products_checked += 1
        found = find_manual_for_product(db, product, manufacturer, outcome=outcome, search=search)
        if found is None:
            outcome.not_found += 1
            continue
        save_manual_link(db, product, found.url)
        outcome.found += 1
        outcome.messages.append(f"{product.model_code or product.model_name}: {found.url} ({', '.join(found.reasons)})")
        logger.info(
            f"Поиск документации: для «{product.model_code or product.model_name}» найдено "
            f"{found.url} (балл {found.score}: {', '.join(found.reasons)})"
        )

    log_action(
        db,
        component=COMPONENT,
        action=f"discover_documents:{manufacturer.id}",
        result="success" if outcome.found else "empty",
        level=LogLevel.INFO,
        details=(
            f"Поиск документации в интернете: проверено моделей {outcome.products_checked}, "
            f"найдено {outcome.found}, не найдено {outcome.not_found}, уже со ссылкой "
            f"{outcome.skipped_have_link}; запросов {outcome.queries}"
        ),
        user_id=actor_id,
    )
    db.commit()
    return outcome


def _manual_link(db: Session, product: Product) -> str | None:
    group_name, field_name = MANUAL_FIELD
    value = db.scalar(
        select(ProductCharacteristic.value).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )
    return value if value and value.startswith("http") else None


def save_manual_link(db: Session, product: Product, url: str) -> ProductCharacteristic:
    group_name, field_name = MANUAL_FIELD
    existing = db.scalar(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )
    if existing is None:
        existing = ProductCharacteristic(
            product_id=product.id,
            group_name=group_name,
            field_name=field_name,
            value=url,
            source=CharacteristicSource.WEB_SEARCH.value,
            confidence=0.8,
            verified_by_user=False,
        )
        db.add(existing)
    elif not existing.verified_by_user and existing.source != CharacteristicSource.MANUAL_ENTRY.value:
        existing.value = url
        existing.source = CharacteristicSource.WEB_SEARCH.value
        existing.confidence = 0.8
    db.flush()
    db.commit()
    return existing
