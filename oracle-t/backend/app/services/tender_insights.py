"""Извлечённые условия закупки — девять разделов вкладки «Дополнительно» (раздел 5.6 ТЗ).

Это не повторение анализа требований (`tender_analysis.py`) — там модель читает документацию
и извлекает технические требования. Здесь она собирает условия участия и исполнения:
лицензии и допуски, требования к участнику и заявке, условия контракта, место работ.
Это закрывает недочёт «система не погружается вовнутрь тендера» с созвона 02.09.2026.

Источник данных — карточка закупки с сайта источника плюс разобранная документация.
Девять разделов запрашиваются двумя вызовами, а не одним: длинный JSON рвётся по лимиту
токенов на выходе (раздел 5.5.1 ТЗ). Результат сохраняется в карточке тендера: извлечение
платное, а карточку открывают многократно; обновляется вместе с расчётом AI-оценки.

Отдельный «Разбор ИИ» карточки (риски, пробелы в данных, чек-лист) убран 17.09.2026: он
дублировал AI-оценку по профилю, которая с этого дня считается автоматически при открытии
карточки, — два блока с суждениями модели об одной закупке только путали.
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
from app.services.ai_client import run_structured

# Сколько текста документации уходит в разбор разделов «Дополнительно». Больше — рвётся
# ответ; меньше — не доходит до технических приложений, где и лежат условия исполнения.
MAX_DOCUMENT_CHARS = 14000


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


# Все поля схем ниже — обязательные, без значений по умолчанию. Это требование Yandex AI
# Studio: на схему с необязательным полем сервис отвечает
# «Invalid JSON Schema: all fields must be required». Пустой список модель возвращает явно —
# и это к лучшему: «сказать нечего» и «модель забыла раздел» перестают выглядеть одинаково.


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
    """Кладёт разделы в карточку тендера: они относятся к тому же снимку и вместе с ним
    устаревают.

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
