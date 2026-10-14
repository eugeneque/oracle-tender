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

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pydantic
from loguru import logger
from sqlalchemy import func, select
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
    # Сколько из проверенных решено без вызова модели: правилом по названию и по уже
    # проверенной закупке с тем же названием.
    by_rule: int = 0
    from_cache: int = 0
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


# --- Решения без модели (14.10.2026) --------------------------------------------------------
#
# Модель нужна там, где по названию не понять, наше ли это: «строительство ВЛ с установкой
# прибора учёта», «материалы для монтажа электросчётчиков». Но заметная часть потока решается
# по названию однозначно — «Поставка электросчётчиков "Миртек"». Сверка на 933 закупках,
# уже размеченных моделью: правила ниже сработали на 265 (28%) и не разошлись с моделью ни
# разу. Правила нарочно узкие: всё, где есть сомнение, по-прежнему уходит модели.

# Предмет — именно приборы учёта электроэнергии или системы учёта.
_METER_RE = re.compile(
    r"(электро\s*сч[её]тчик|сч[её]тчик\w*\s+(активной\s+|реактивной\s+)?(электр|э/э)"
    r"|прибор\w*\s+(коммерческого\s+|технического\s+)?уч[её]та\s+(электр|э/э)"
    r"|аскуэ|аиис\s*куэ)"
)
# Действие с ними стоит в начале названия: «Поставка…», «Лот №1 Поставка…», «Право
# заключения договора на поставку…». В середине длинного названия то же слово чаще всего
# описывает часть строительного объекта.
_ACTION_RE = re.compile(
    r"^(\S+\s+){0,3}?(на\s+)?(право\s+заключения\s+договора\s+на\s+)?"
    r"(поставк|приобретени|закупк|замен|поверк)"
)
# Признаки того, что счётчики — не предмет, а часть чужого предмета (стройка, материалы,
# сопутствующее оборудование) или речь о другом ресурсе. Любое совпадение — решает модель.
_NOT_SURE_RE = re.compile(
    r"(строительств|смр|пир\b|реконструкц|испытательн|коробк|модул|журнал|расходн|опор"
    r"|снят\w*\s+показ|информац|диагност|вод|газ|тепл|жидкост|аренд"
    r"|ремонт\s+(здани|помещ)|трансформатор|кабел|провод|щит|шкаф|пломб|печат|программн"
    r"|лицензи|материал|сервер|компьютер|обогрев|комплектующ|запасн|зип\b"
    r"|для\s+(монтажа|работы|оборудования|обслуживания)|услуг\w*\s+по\s+(сбор|передач))"
)
# Счётчик или узел учёта другого ресурса: воды, газа, тепла, жидкости, частиц.
_OTHER_RESOURCE_RE = re.compile(
    r"(сч[её]тчик|прибор\w*\s+уч[её]та|узл\w*\s+уч[её]та|расходомер)\w*[^.;]{0,40}"
    r"(вод|газ|тепл|жидкост|частиц|семян|аэрозол)"
)
# Длинные названия — почти всегда сводные строительные объекты, по ним решает модель.
_RULE_MAX_TITLE = 250
RULE_CONFIDENCE = 95


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", (title or "").lower().replace("ё", "е")).strip()


def rule_answer(tender: Tender) -> RelevanceAnswer | None:
    """Решение по названию без модели или `None`, если название неоднозначно."""

    title = normalize_title(tender.title)
    if not title:
        return None
    if "электр" not in title and "э/э" not in title and _OTHER_RESOURCE_RE.search(title):
        return RelevanceAnswer(
            is_relevant=False,
            confidence=RULE_CONFIDENCE,
            reason="Определено без модели: прибор учёта воды, газа, тепла или другого ресурса.",
        )
    if (
        len(title) <= _RULE_MAX_TITLE
        and _METER_RE.search(title)
        and _ACTION_RE.search(title)
        and not _NOT_SURE_RE.search(title)
    ):
        return RelevanceAnswer(
            is_relevant=True,
            confidence=RULE_CONFIDENCE,
            reason="Определено без модели: предмет — поставка, замена или поверка приборов учёта электроэнергии.",
        )
    return None


def _title_key():
    """Выражение SQL для того же ключа, что `normalize_title` (без замены «ё»: в названиях
    закупок её почти не бывает, а повторы приходят с одной площадки побайтно)."""

    return func.btrim(func.regexp_replace(func.lower(Tender.title), r"\s+", " ", "g"))


def _cached_answers(db: Session, tenders: list[Tender]) -> dict[str, RelevanceAnswer]:
    """Ответы, уже полученные для закупок с тем же названием.

    Каждая четвёртая закупка в потоке повторяет название уже проверенной (переторжка, лоты,
    одна закупка на нескольких площадках): спрашивать модель второй раз о том же тексте —
    платить за тот же ответ.
    """

    keys = {normalize_title(item.title) for item in tenders if item.title}
    if not keys:
        return {}
    key = _title_key()
    rows = db.execute(
        select(key, Tender.ai_relevant, Tender.ai_relevance_confidence, Tender.ai_relevance_reason)
        .where(
            key.in_(keys),
            Tender.ai_relevance_checked_at.is_not(None),
            Tender.ai_relevant.is_not(None),
        )
        .order_by(Tender.ai_relevance_checked_at.desc())
    ).all()
    cached: dict[str, RelevanceAnswer] = {}
    for title_key, relevant, confidence, reason in rows:
        cached.setdefault(
            title_key,
            RelevanceAnswer(is_relevant=relevant, confidence=confidence or 0, reason=reason or ""),
        )
    return cached


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

    _store(tender, answer)
    return answer


def _store(tender: Tender, answer: RelevanceAnswer) -> None:
    tender.ai_relevant = answer.is_relevant
    tender.ai_relevance_reason = answer.reason.strip()[:1000] or None
    # Уверенность приводим к диапазону: модель изредка отдаёт долю (0.85) вместо процентов.
    confidence = answer.confidence
    if 0 <= confidence <= 1 and isinstance(confidence, int) is False:
        confidence = int(confidence * 100)
    tender.ai_relevance_confidence = max(0, min(100, int(confidence)))
    tender.ai_relevance_checked_at = datetime.now(timezone.utc)


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
    # Явный перезапуск по выбранным закупкам — просьба спросить именно модель: ни правило, ни
    # повтор старого ответа её бы не выполнили.
    shortcuts = not tender_ids
    cached = _cached_answers(db, tenders) if shortcuts else {}
    for tender in tenders:
        key = normalize_title(tender.title)
        answer = rule_answer(tender) if shortcuts else None
        if answer is not None:
            _store(tender, answer)
            result.by_rule += 1
        elif key in cached:
            answer = cached[key]
            _store(tender, answer)
            result.from_cache += 1
        else:
            answer = check_tender(db, tender)
            if answer is not None and key:
                cached[key] = answer
        if answer is None:
            result.failed += 1
            # Первый же сбой обычно означает недоступную интеграцию, а не проблему тендера:
            # продолжать пачку смысла нет, только жечь время.
            # Решённые без модели не в счёт: они ничего не говорят о её доступности.
            if result.failed >= 3 and result.checked == result.by_rule + result.from_cache:
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

    if result.by_rule or result.from_cache:
        result.messages.append(
            f"Без обращения к модели решено {result.by_rule + result.from_cache}: "
            f"по названию — {result.by_rule}, по уже проверенной закупке с тем же названием — "
            f"{result.from_cache}"
        )
    if result.checked or result.failed:
        log_action(
            db,
            component="ai_relevance",
            action="check_batch",
            result="ok" if not result.failed else "partial_error",
            level=LogLevel.INFO if not result.failed else LogLevel.WARNING,
            details=(
                f"Проверено {result.checked}: подобрано {result.relevant}, "
                f"отклонено {result.rejected}, сбоев {result.failed}; без модели: "
                f"по правилу {result.by_rule}, по такому же названию {result.from_cache}"
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
