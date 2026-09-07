"""ИИ-разбор карточки тендера: пробелы в данных, риски и ошибки (раздел 5.4 ТЗ).

Это не повторение анализа требований (`tender_analysis.py`) — там модель читает документацию
и извлекает технические требования. Здесь она смотрит на карточку целиком, глазами человека,
который решает, стоит ли участвовать: что в закупке настораживает, каких сведений не хватает,
где условия выглядят так, будто их писали под конкретного поставщика.

**Что модель делает, а что нет.** Регион, ОКПД2, заказчик и способ закупки к этому моменту
уже определены формально — по справочникам и реквизитам (`tender_card_service`). Модели
достаются только те поля, где формального признака не нашлось, и вопросы, на которые
справочником не ответишь: риски, странности в сроках, противоречия между разделами. Так
дешевле, быстрее и, главное, надёжнее: справочное соответствие модель может «додумать», а
суждение о рисках — ровно та работа, где она полезна.

Результат сохраняется: разбор платный, а карточку открывают многократно. Обновляется по
кнопке.

**Вкладка «Дополнительно» (раздел 5.6 ТЗ, решение 03.09.2026).** Здесь же собираются девять
разделов извлечённых условий закупки — то, что закрывает недочёт «система не погружается
вовнутрь тендера» с созвона 02.09.2026. Это расширение этого модуля, а не новый сервис:
источник данных тот же (карточка плюс документация), и обновляться они должны вместе.
Девять разделов запрашиваются двумя вызовами, а не одним: длинный JSON рвётся по лимиту
токенов на выходе (раздел 5.5.1 ТЗ).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.user import User
from app.services.audit import log_action
from app.services.yandex_ai_client import chunk_text, run_structured

MAX_CONTEXT_CHARS = 12000
# Сколько текста документации уходит в разбор разделов «Дополнительно». Больше — рвётся
# ответ; меньше — не доходит до технических приложений, где и лежат условия исполнения.
MAX_DOCUMENT_CHARS = 14000


# Все поля схем ниже — обязательные, без значений по умолчанию. Это требование Yandex AI
# Studio: на схему с необязательным полем сервис отвечает
# «Invalid JSON Schema: all fields must be required». Пустой список модель возвращает явно —
# и это к лучшему: «рисков нет» и «модель забыла про риски» перестают выглядеть одинаково.


class InsightItem(pydantic.BaseModel):
    """Одно наблюдение. `severity` управляет цветом в интерфейсе, `evidence` — цитата или
    поле карточки, на котором наблюдение основано: вывод без опоры проверить нельзя, а
    решение об участии принимается по нему."""

    title: str
    detail: str
    severity: str
    evidence: str


class FilledField(pydantic.BaseModel):
    """Поле, которое модель восстановила из текста карточки, с указанием источника."""

    field: str
    value: str
    source: str
    confidence: float


class InsightsResult(pydantic.BaseModel):
    summary: str
    risks: list[InsightItem]
    data_gaps: list[InsightItem]
    filled_fields: list[FilledField]
    checklist: list[str]


_SYSTEM_PROMPT = """Ты — аналитик тендерного отдела производителя приборов учёта электроэнергии.
Тебе дана карточка закупки с сайта источника. Твоя задача — помочь специалисту быстро понять,
что это за закупка и на что смотреть.

Верни JSON:
- summary: 2-3 предложения о сути закупки простым языком.
- risks: риски и странности. Каждый — {title, detail, severity, evidence}. severity:
  "high" — то, что может лишить участия или денег (короткий срок подачи, обеспечение,
  требования под конкретного производителя, закупка у единственного поставщика);
  "medium" — то, что требует внимания; "low" — мелочи. evidence — конкретное поле или
  формулировка из карточки, на которой основан вывод.
- data_gaps: чего в карточке не хватает для принятия решения (те же поля объекта).
- filled_fields: значения, которые ты смог восстановить из текста карточки:
  {field, value, source, confidence}. field — одно из: region, okpd2, delivery_region,
  contact_person, contact_email, contact_phone, deadline, price. source — откуда взял.
  Если значение в карточке не написано — не выдумывай, не включай поле вовсе.
- checklist: 3-6 коротких пунктов «что проверить перед подачей».

Пиши по-русски, кратко и по делу. Не повторяй одно и то же в разных разделах.
Не выдумывай фактов: если чего-то в карточке нет, это относится к data_gaps, а не к
filled_fields.

Возвращай ВСЕ перечисленные поля, даже когда сказать нечего: пустой список вместо
пропущенного поля. У каждого наблюдения обязательно заполняй evidence — поле карточки или
формулировку, на которой основан вывод; если опоры нет, так и напиши: "прямого указания в
карточке нет"."""


def _card_text(tender: Tender, card: TenderCard | None) -> str:
    parts: list[str] = [
        f"Наименование: {tender.title}",
        f"Номер закупки: {tender.external_id}",
        f"Площадка: {tender.source.name if tender.source else '—'}",
        f"Сумма: {tender.price if tender.price is not None else '—'} {tender.currency}",
        f"Срок подачи: {tender.application_end or '—'}",
        f"Способ закупки: {tender.procurement_method or '—'}",
        f"ОКПД2: {tender.okpd2_code or '—'}",
    ]

    if card is not None:
        payload = card.payload or {}
        for section in payload.get("sections", []):
            parts.append(f"\n== {section.get('title')} ==")
            for name, value in section.get("fields", []):
                parts.append(f"{name}: {value}")
        for key, table in (payload.get("tables") or {}).items():
            rows = table.get("rows") or []
            if not rows:
                continue
            parts.append(f"\n== {table.get('title') or key} ==")
            if table.get("headers"):
                parts.append(" | ".join(table["headers"]))
            for row in rows[:20]:
                parts.append(" | ".join(row))

    return "\n".join(parts)


def build_insights(
    db: Session, tender: Tender, card: TenderCard | None, *, actor: User | None = None
) -> InsightsResult:
    """Спрашивает модель о карточке. Исключение поднимается наружу — вызывающий эндпоинт
    показывает причину пользователю (чаще всего это ненастроенное подключение)."""

    context = _card_text(tender, card)
    # Карточка крупной закупки с журналом событий не помещается в контекст целиком; берём
    # начало — реквизиты, условия и сроки идут в самом верху, а хвост журнала для суждения
    # о рисках не нужен.
    chunks = chunk_text(context, max_chars=MAX_CONTEXT_CHARS)
    user_text = chunks[0] if chunks else context

    result = run_structured(
        db,
        system_prompt=_SYSTEM_PROMPT,
        user_text=user_text,
        response_model=InsightsResult,
        temperature=0.2,
    )

    log_action(
        db,
        component="tender_insights",
        action=f"build_insights:{tender.external_id}",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Рисков: {len(result.risks)}, пробелов: {len(result.data_gaps)}, "
            f"восстановлено полей: {len(result.filled_fields)}"
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return result


def store_insights(db: Session, tender: Tender, result: InsightsResult) -> None:
    """Сохраняет разбор в карточке тендера.

    В `tender_cards.payload`, а не отдельной таблицей: разбор относится к конкретному снимку
    карточки и вместе с ним же устаревает — при обновлении карточки он должен пересчитываться,
    а не оставаться от прошлой редакции извещения.
    """

    card = db.get(TenderCard, tender.id)
    if card is None:
        card = TenderCard(tender_id=tender.id, payload={})
        db.add(card)

    payload = dict(card.payload or {})
    payload["insights"] = {
        **result.model_dump(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    card.payload = payload
    db.commit()


def get_stored_insights(db: Session, tender: Tender) -> dict | None:
    card = db.get(TenderCard, tender.id)
    if card is None:
        return None
    return (card.payload or {}).get("insights")


def apply_filled_fields(db: Session, tender: Tender, result: InsightsResult) -> list[str]:
    """Применяет к тендеру только те поля, которые пусты и которые можно проверить.

    Регион и ОКПД2 берутся не «как сказала модель», а сверяются со справочником и форматом:
    именно здесь модель ошибается чаще всего — уверенно называет соседний регион или
    выдумывает несуществующий код.
    """

    import re

    from app.services.region_resolver import region_from_address

    applied: list[str] = []
    for item in result.filled_fields:
        if item.confidence < 0.5:
            continue

        if item.field == "region" and not tender.region_organizer_code:
            region = region_from_address(db, item.value)
            if region is not None:
                tender.region_organizer_code = region.code
                tender.federal_district_code = region.federal_district_code
                applied.append(f"регион заказчика — {region.name}")
        elif item.field == "okpd2" and not tender.okpd2_code:
            match = re.fullmatch(r"\d{2}(?:\.\d{1,2}){1,4}", item.value.strip())
            if match:
                tender.okpd2_code = item.value.strip()
                applied.append(f"ОКПД2 — {item.value.strip()}")

    if applied:
        db.commit()
        logger.info(f"Из ИИ-разбора заполнено полей тендера {tender.external_id}: {applied}")
    return applied


# --- вкладка «Дополнительно»: девять разделов извлечённых условий -----------------------
# Разделы и их порядок заданы разделом 5.6 ТЗ. Ключ — стабильный (по нему интерфейс хранит
# состояние «развёрнуто/свёрнуто»), заголовок — то, что видит пользователь.

EXTRA_SECTIONS: dict[str, str] = {
    "main_requirements": "Основные требования",
    "scope_and_context": "Объём и контекст",
    "licenses_and_admissions": "Лицензии, СРО и допуски",
    "participant_requirements": "Требования к участнику",
    "contract_terms": "Условия контракта",
    "special_conditions": "Особые условия исполнения",
    "work_location": "Место выполнения работ",
    "application_requirements": "Требования к заявке",
    "additional_facts": "Доп. факты",
}


class SectionsBatchOne(pydantic.BaseModel):
    """Первая пятёрка разделов. Каждый — список коротких пунктов; пустой список означает
    «в документации об этом не сказано» и так и показывается в карточке."""

    main_requirements: list[str]
    scope_and_context: list[str]
    licenses_and_admissions: list[str]
    participant_requirements: list[str]
    contract_terms: list[str]


class SectionsBatchTwo(pydantic.BaseModel):
    special_conditions: list[str]
    work_location: list[str]
    application_requirements: list[str]
    additional_facts: list[str]


_SECTIONS_SYSTEM_PROMPT = """Ты — специалист тендерного отдела производителя приборов учёта
электроэнергии. Тебе дана карточка закупки и текст документации. Извлеки из них условия
закупки и разложи по разделам.

Правила:
- Каждый раздел — список коротких пунктов (одно предложение), дословно по смыслу документа.
- Ничего не выдумывай. Если в документах о разделе не сказано — верни пустой список.
- Не повторяй один и тот же факт в разных разделах.
- Читай текст целиком, включая технические приложения и протоколы обмена: важные требования
  часто спрятаны там, а не в перечне требований.
- Числа, сроки, единицы измерения переноси точно, как в документе.

Возвращай ВСЕ перечисленные поля, даже когда сказать нечего — пустым списком.
Отвечай по-русски."""

_BATCH_ONE_HINT = """Разделы этого запроса:
- main_requirements: основные требования к предмету закупки (что именно нужно поставить/
  сделать, ключевые технические условия).
- scope_and_context: объём и контекст (количество, этапность, для какого объекта, зачем).
- licenses_and_admissions: обязательные лицензии, СРО, допуски, сертификаты, членства.
- participant_requirements: требования к участнику (опыт, стаж, ресурсы, отсутствие в РНП,
  требования к квалификации персонала).
- contract_terms: условия контракта (сроки, оплата, обеспечение заявки и контракта,
  гарантии, штрафы)."""

_BATCH_TWO_HINT = """Разделы этого запроса:
- special_conditions: особые условия исполнения (шефмонтаж, обучение, ввод в эксплуатацию,
  совместимость с существующими системами, интеграции).
- work_location: место выполнения работ/поставки (адреса, объекты, регион).
- application_requirements: требования к составу и оформлению заявки (какие документы,
  форма, способ подачи).
- additional_facts: прочие существенные факты, не попавшие в другие разделы."""


def _documents_text(db: Session, tender: Tender) -> str:
    """Текст документации для разбора — приоритетные файлы первыми.

    Порядок не случайный: звёздочку («приоритетный источник») ставит человек, а
    автоклассификация выделяет ТЗ. Если резать текст без этого порядка, под обрезку попадёт
    именно техническое задание, а в разбор уйдут извещение и проект контракта.
    """

    documents = list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id)
            .order_by(TenderDocument.created_at)
        )
    )
    priority = {DocumentClass.TZ_DESCRIPTION.value: 1, DocumentClass.SSR.value: 2}
    documents.sort(
        key=lambda document: (
            not document.is_priority_source,
            priority.get(document.document_class or "", 9),
        )
    )

    parts: list[str] = []
    length = 0
    for document in documents:
        if not document.has_text:
            continue
        header = f"\n=== {document.file_name} ===\n"
        body = document.extracted_text[: MAX_DOCUMENT_CHARS - length]
        parts.append(header + body)
        length += len(body)
        if length >= MAX_DOCUMENT_CHARS:
            break
    return "".join(parts)


def build_extra_sections(
    db: Session, tender: Tender, card: TenderCard | None, *, actor: User | None = None
) -> dict[str, list[str]]:
    """Извлекает девять разделов вкладки «Дополнительно» двумя вызовами модели.

    Неудача одной пачки не отменяет вторую: пять разделов лучше, чем ноль, а карточка честно
    покажет, что остальные не разобраны.
    """

    context = (
        f"КАРТОЧКА ЗАКУПКИ:\n{_card_text(tender, card)}\n\n"
        f"ДОКУМЕНТАЦИЯ:\n{_documents_text(db, tender) or '(документы не разобраны)'}"
    )

    sections: dict[str, list[str]] = {}
    failures: list[str] = []
    for hint, model in ((_BATCH_ONE_HINT, SectionsBatchOne), (_BATCH_TWO_HINT, SectionsBatchTwo)):
        try:
            result = run_structured(
                db,
                system_prompt=f"{_SECTIONS_SYSTEM_PROMPT}\n\n{hint}",
                user_text=context,
                response_model=model,
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001 - пачки независимы
            logger.warning(
                f"Разделы «Дополнительно» ({model.__name__}) для {tender.external_id} "
                f"не извлечены: {exc}"
            )
            failures.append(str(exc))
            continue
        sections.update(result.model_dump())

    log_action(
        db,
        component="tender_insights",
        action=f"build_extra_sections:{tender.external_id}",
        result="success" if sections else "error",
        level=LogLevel.INFO if sections else LogLevel.WARNING,
        details=(
            f"Разделов заполнено: {sum(1 for value in sections.values() if value)} из "
            f"{len(EXTRA_SECTIONS)}" + ("; " + "; ".join(failures) if failures else "")
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return sections


def store_extra_sections(db: Session, tender: Tender, sections: dict[str, list[str]]) -> None:
    """Кладёт разделы в карточку рядом с разбором: они относятся к тому же снимку и вместе
    с ним устаревают.

    В `extra_sections` — списки пунктов по ключам `EXTRA_SECTIONS`; заголовки хранить рядом
    незачем, они заданы кодом и переводятся на лету."""

    card = db.get(TenderCard, tender.id)
    if card is None:
        card = TenderCard(tender_id=tender.id, payload={})
        db.add(card)

    payload = dict(card.payload or {})
    payload["extra_sections"] = {
        "sections": sections,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    card.payload = payload
    db.commit()


def get_stored_extra_sections(db: Session, tender: Tender) -> dict | None:
    card = db.get(TenderCard, tender.id)
    if card is None:
        return None
    return (card.payload or {}).get("extra_sections")
