"""ИИ-отбор тендеров: «Подобрано ИИ» (разделы 5.1.1, 5.4 ТЗ).

Второй слой отбора поверх профиля ключевых слов. Разделение задач между ними такое:

* **профиль релевантности** (`relevance_service`) — дешёвый и слепой. Отвечает «есть ли в
  тексте нужные слова», работает без сети и без денег, применяется ко всему потоку;
* **этот модуль** — дорогой и понимающий. Отвечает «это правда наша закупка», отличая
  поставку счётчиков от аренды помещения, где счётчики упомянуты в составе имущества.

Порядок обязателен: сначала слова, потом модель. Иначе каждый вызов уходил бы на весь поток
со всех источников, включая заведомо чужие закупки, — ровно то, от чего предостерегает
раздел 5.1.1 ТЗ.

**Почему это не AI-оценка по профилю (5.5.1).** Та отвечает «стоит ли идти и с какими
рисками», требует заполненного профиля компании и трёх вызовов модели. Здесь один короткий
вызов и один вопрос: наше это вообще или нет. Отбор не может ждать, пока заполнят профиль
компании, — иначе список остаётся не разобранным.

**Отказ модели — не «нерелевантен».** Сбой сети, кончившаяся квота, выключенная интеграция
оставляют `ai_relevant = None`: «не проверяли». Записать `False` значило бы спрятать закупку
из-за нашей же аварии.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.log import LogLevel
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action
from app.services.ai_client import run_structured

# Что именно ищет компания. Вынесено в константу, а не зашито в промпт по кускам: это
# предметное описание бизнеса, и менять его придётся вместе с профилем релевантности.
COMPANY_CONTEXT = """\
Компания МИРТЕК — российский производитель приборов учёта электрической энергии
(однофазные и трёхфазные электросчётчики, системы АСКУЭ/АИИС КУЭ, УСПД).
Компания участвует в закупках на:
— поставку электросчётчиков и оборудования учёта электроэнергии;
— замену, монтаж и подключение приборов учёта электроэнергии;
— поверку, техническое обслуживание и ремонт приборов учёта электроэнергии;
— создание и модернизацию систем коммерческого учёта электроэнергии.
Компания НЕ занимается: приборами учёта воды, газа и тепла; общестроительными работами;
поставкой кабеля, трансформаторов и прочего электрооборудования, если приборы учёта в
закупке не главное; закупками, где счётчики лишь упомянуты в составе имущества."""

SYSTEM_PROMPT = f"""\
Ты — аналитик тендерного отдела. Твоя задача — решить, относится ли закупка к профилю
компании.

{COMPANY_CONTEXT}

Правила:
1. Отвечай «да» только если предмет закупки — приборы учёта электроэнергии или работы и
   услуги с ними (поставка, замена, монтаж, поверка, обслуживание, ремонт, АСКУЭ).
2. Отвечай «нет», если приборы учёта электроэнергии не являются предметом закупки, даже
   когда они упомянуты в тексте.
3. Приборы учёта воды, газа и тепла — это «нет».
4. Не додумывай то, чего нет в тексте. Если данных не хватает, чтобы решить, ставь
   уверенность ниже 50.
5. Причина — одно предложение на русском языке, по существу, без вводных слов."""


class RelevanceAnswer(pydantic.BaseModel):
    """Ответ модели. Все поля обязательны и без `default`.

    Значения по умолчанию здесь уже приводили к тому, что модель просто не заполняла часть
    полей и мы получали пустые причины при непустом вердикте (раздел 5.5.1 ТЗ, требования к
    структурированному выводу).
    """

    is_relevant: bool
    confidence: int
    reason: str


@dataclass
class BatchResult:
    checked: int = 0
    relevant: int = 0
    rejected: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)


def _tender_text(tender: Tender) -> str:
    """Что показываем модели.

    Только заголовок, заказчик, способ закупки и ОКПД2 — то, что известно сразу после сбора.
    Документацию сюда не подмешиваем: она весит сотни тысяч символов, а для вопроса «наше или
    нет» ничего не добавляет к предмету закупки.
    """

    parts = [
        f"Наименование закупки: {tender.title or '—'}",
        f"Заказчик: {tender.customer_name or '—'}",
        f"Способ закупки: {tender.procurement_method or '—'}",
        f"Код ОКПД2: {tender.okpd2_code or '—'}",
    ]
    return "\n".join(parts)


def check_tender(db: Session, tender: Tender) -> RelevanceAnswer | None:
    """Спрашивает модель об одном тендере и сохраняет ответ. `None` — проверить не удалось."""

    try:
        answer = run_structured(
            db,
            system_prompt=SYSTEM_PROMPT,
            user_text=_tender_text(tender),
            response_model=RelevanceAnswer,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - сбой модели не должен ронять разбор пачки
        logger.warning(f"ИИ-отбор тендера {tender.external_id} не удался: {exc}")
        return None

    tender.ai_relevant = answer.is_relevant
    tender.ai_relevance_reason = answer.reason.strip()[:1000] or None
    # Уверенность приводим к диапазону: модель изредка отдаёт долю (0.85) вместо процентов.
    confidence = answer.confidence
    if 0 <= confidence <= 1 and isinstance(confidence, int) is False:
        confidence = int(confidence * 100)
    tender.ai_relevance_confidence = max(0, min(100, int(confidence)))
    tender.ai_relevance_checked_at = datetime.now(timezone.utc)
    return answer


def check_batch(
    db: Session,
    *,
    limit: int = 50,
    actor: User | None = None,
    tender_ids: list[uuid.UUID] | None = None,
) -> BatchResult:
    """Проверяет пачку тендеров, ещё не проверенных моделью.

    Отбираются только прошедшие профиль ключевых слов: тратить вызовы на заведомо чужие
    закупки незачем. Порядок — свежие первыми: они и нужны в работе, а исторический архив
    можно разобрать потом.
    """

    query = select(Tender)
    if tender_ids:
        query = query.where(Tender.id.in_(tender_ids))
    else:
        query = query.where(
            Tender.ai_relevance_checked_at.is_(None),
            Tender.passed_relevance_filter.is_(True),
        ).order_by(Tender.created_at.desc())
    tenders = list(db.scalars(query.limit(limit)))

    result = BatchResult()
    for tender in tenders:
        answer = check_tender(db, tender)
        if answer is None:
            result.failed += 1
            # Первый же сбой обычно означает недоступную интеграцию, а не проблему тендера:
            # продолжать пачку смысла нет, только жечь время.
            if result.failed >= 3 and result.checked == 0:
                result.messages.append(
                    "Модель недоступна — проверка остановлена. Проверьте настройки "
                    "интеграции с Yandex AI Studio."
                )
                break
            continue
        result.checked += 1
        if answer.is_relevant:
            result.relevant += 1
        else:
            result.rejected += 1

    if result.checked or result.failed:
        log_action(
            db,
            component="ai_relevance",
            action="check_batch",
            result="ok" if not result.failed else "partial_error",
            level=LogLevel.INFO if not result.failed else LogLevel.WARNING,
            details=(
                f"Проверено {result.checked}: подобрано {result.relevant}, "
                f"отклонено {result.rejected}, сбоев {result.failed}"
            ),
            user_id=actor.id if actor else None,
        )
    db.commit()
    return result


def pending_count(db: Session) -> int:
    """Сколько прошедших профиль тендеров ещё не смотрела модель."""

    from sqlalchemy import func

    return db.scalar(
        select(func.count())
        .select_from(Tender)
        .where(
            Tender.ai_relevance_checked_at.is_(None),
            Tender.passed_relevance_filter.is_(True),
        )
    ) or 0
