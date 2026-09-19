"""Опрос источников тендеров: вызов адаптера, дедупликация и сохранение, журналирование
(раздел 5.1, 5.9 ТЗ).

Дедупликация двухступенчатая, и обе ступени нужны:

1. **Внутри источника** — по паре (source_id, external_id). Повторный опрос той же площадки
   обновляет запись, а не плодит копии.
2. **Между источниками** — по реестровому номеру закупки (`registry_number`). Одна и та же
   закупка публикуется и в ЕИС, и на ЭТП, а идентификаторы у них разные: без второй ступени
   тендерный отдел видел бы её дважды и дважды же считал по ней AI-оценку.

Победителем при межисточниковом совпадении остаётся запись, которая появилась раньше: у неё
уже есть история, комментарии и, возможно, посчитанная оценка, а перенос всего этого на
новую строку ради «более правильного» источника — риск потерять работу человека. Поля,
которых у неё не было, из второго источника при этом дозаполняются.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Callable

from loguru import logger
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.adapters.base import PollError, TenderSummary
from app.adapters.registry import get_adapter
from app.models.log import LogLevel
from app.models.source import POLL_EXCLUDED_SOURCE_TYPES, Source, SourceStatus
from app.models.tender import Tender, TenderStatus
from app.services import notification_service
from app.services import ai_relevance_service, relevance_service
from app.services.audit import log_action

_UPDATABLE_FIELDS = (
    "title",
    "customer_name",
    "organizer_name",
    "procurement_method",
    "status",
    "price",
    "currency",
    "application_start",
    "application_end",
    "publish_date",
    "source_url",
)


@dataclass
class SourcePollResult:
    source_key: str
    found: int
    created: int
    updated: int
    errors: int


def get_source_by_key(db: Session, key: str) -> Source | None:
    return db.scalar(select(Source).where(Source.key == key))


def list_sources(db: Session) -> list[Source]:
    return list(db.scalars(select(Source).order_by(Source.name)))


# Реестровый номер закупки в ЕИС — 19 цифр. Часть площадок публикует закупку под этим же
# номером, не выделяя его отдельным полем: тогда номер выводится из `external_id`, а не
# требуется от каждого адаптера отдельно.
_REGISTRY_NUMBER_RE = re.compile(r"^\d{19}$")


def _registry_number(summary: TenderSummary) -> str | None:
    if summary.registry_number:
        return summary.registry_number.strip() or None
    external_id = (summary.external_id or "").strip()
    return external_id if _REGISTRY_NUMBER_RE.match(external_id) else None


def _fill_missing(existing: Tender, summary: TenderSummary) -> bool:
    """Дозаполняет пустые поля записи данными другого источника.

    Именно пустые: перезаписывать заполненное значением с другой площадки нельзя — площадки
    расходятся в формулировках статуса и наименования, и «обновление» превратилось бы в
    перетягивание карточки туда-сюда при каждом опросе.
    """

    changed = False
    for field_name in _UPDATABLE_FIELDS:
        new_value = getattr(summary, field_name)
        if new_value is not None and getattr(existing, field_name) is None:
            setattr(existing, field_name, new_value)
            changed = True
    return changed


def _upsert_tender(
    db: Session,
    source: Source,
    summary: TenderSummary,
    *,
    groups: list | None = None,
) -> bool:
    """Возвращает True, если создана новая запись, False — если запись уже была.

    «Уже была» покрывает два случая: повторная выдача той же площадки (обновляем поля) и
    та же закупка, пришедшая с другой площадки под своим идентификатором (дубль не
    создаётся, у существующей записи дозаполняются пустые поля).

    Сессия работает с `autoflush=False` (см. app/db/session.py), поэтому запись, добавленная
    чуть раньше в этом же вызове `poll_source`, ещё не видна обычному SELECT, пока её явно не
    сбросить (`flush`) — без этого дубль external_id в одной пачке (например, из-за пересечения
    страниц поиска источника) не был бы найден и привёл бы к падению на уникальном ограничении
    `uq_tenders_source_external_id` при коммите."""

    existing = db.scalar(
        select(Tender).where(
            Tender.source_id == source.id, Tender.external_id == summary.external_id
        )
    )
    registry_number = _registry_number(summary)

    if existing is None:
        # Вторая ступень дедупликации: та же закупка могла прийти с другой площадки под
        # своим идентификатором. Пустой `registry_number` под условие не подставляем — иначе
        # все записи без номера схлопнулись бы в одну.
        if registry_number is not None:
            twin = db.scalar(
                select(Tender).where(
                    Tender.registry_number == registry_number, Tender.source_id != source.id
                )
            )
            if twin is not None:
                if _fill_missing(twin, summary):
                    db.add(twin)
                logger.info(
                    f"Закупка {registry_number} уже есть из источника {twin.source_id} — "
                    f"дубль от {source.key} не создаётся"
                )
                return False

        tender = Tender(
            source_id=source.id,
            external_id=summary.external_id,
            registry_number=registry_number,
            **{field: getattr(summary, field) for field in _UPDATABLE_FIELDS},
        )
        db.add(tender)
        # Тип конкурса по наименованию — чтобы фильтр по типу работал с момента сбора;
        # ИИ-анализ, когда его запустят, поставит свой вердикт по ТЗ.
        from app.services.tender_gaps_service import fill_type_from_title

        fill_type_from_title(tender)
        # Профиль релевантности применяется сразу при сборе — до скачивания документации и
        # до вызова модели (раздел 5.1.1 ТЗ). Не прошедший тендер не удаляется: он помечен
        # и виден в списке при снятии фильтра, потому что профиль настраивают люди и он
        # вполне может оказаться слишком узким.
        relevance_service.apply_to_tender(db, tender, groups)
        db.flush()
        return True

    changed = False
    if registry_number is not None and existing.registry_number != registry_number:
        existing.registry_number = registry_number
        changed = True
    for field_name in _UPDATABLE_FIELDS:
        new_value = getattr(summary, field_name)
        if new_value is not None and getattr(existing, field_name) != new_value:
            setattr(existing, field_name, new_value)
            changed = True
    if changed:
        db.add(existing)
    return False


# Сколько новых закупок проверяем моделью прямо в момент сбора. Остальное — кнопкой
# «Проверить моделью» в настройках: опрос источника не должен превращаться в получасовую
# операцию из-за одной площадки, вывалившей сотню записей.
AI_CHECK_ON_POLL_LIMIT = 25
# Сколько накопившихся непроверенных закупок модель разбирает после опроса всех площадок.
# Поштучный лимит выше защищает от лавины с одной площадки; этот — добирает хвост, чтобы
# «не смотрели» не копилось неделями: пока модель не ответила, тендер висит в списке.
AI_CHECK_AFTER_POLL_LIMIT = 200


def check_pending_ai_relevance(db: Session, *, limit: int = AI_CHECK_AFTER_POLL_LIMIT) -> None:
    """Добирает ИИ-отбором хвост прошедших профиль, но ещё не проверенных закупок."""

    try:
        result = ai_relevance_service.check_batch(db, limit=limit)
    except Exception as exc:  # noqa: BLE001 - сбой модели не отменяет успешный сбор
        logger.warning(f"ИИ-отбор после опроса не выполнен: {exc}")
        return
    if result.checked or result.failed:
        logger.info(
            f"ИИ-отбор после опроса: проверено {result.checked}, подобрано {result.relevant}, "
            f"отклонено {result.rejected}, сбоев {result.failed}"
        )


def poll_source(
    db: Session,
    source: Source,
    *,
    actor_id: uuid.UUID | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> SourcePollResult:
    """Опрашивает один источник. Ошибка этого источника не должна прерывать опрос
    остальных (раздел 5.1, 5.9 ТЗ) — вызывающий код (планировщик/CLI) может смело звать
    эту функцию в цикле по всем источникам без try/except снаружи.

    Выдача сохраняется порциями по `POLL_BATCH_SIZE` записей по мере того, как адаптер их
    вычитывает (правка 17.09.2026): каждая порция коммитится сразу, и новые закупки видны в
    списке ещё до конца опроса, а не после всех 11 000 строк ЭТП ГПБ. `on_progress`
    получает (обработано, создано, обновлено) после каждой порции — для строки прогресса.
    """

    # Ключевые слова берутся из профиля релевантности (раздел 5.1.1 ТЗ), а не из констант
    # адаптера: охват — настройка компании, а не свойство кода площадки. Профиль заводится
    # здесь же, если его ещё нет (первый опрос из CLI на свежей базе): без него адаптер ушёл
    # бы на площадку с двумя фразами из констант, а собранное осталось бы без отметок.
    relevance_service.get_or_create_profile(db)
    adapter = get_adapter(
        source.adapter_key, search_keywords=relevance_service.search_queries(db)
    )
    if adapter is None:
        log_action(
            db,
            component="scheduler",
            action=f"poll_source:{source.key}",
            result="skipped",
            level=LogLevel.WARNING,
            details="Адаптер для источника не реализован",
            user_id=actor_id,
        )
        db.commit()
        return SourcePollResult(source.key, 0, 0, 0, 0)

    created = 0
    updated = 0
    processed = 0
    # Группы читаются один раз на весь опрос, а не на каждый тендер: их девять, а записей в
    # выдаче — сотни.
    groups = relevance_service.active_groups(db)
    errors: list[PollError] = []
    # Что уже сохранено из порций: адаптер после возврата отдаёт полную выдачу, и её
    # остаток (меньше порции) и обновлённые по ходу записи досохраняются без повтора.
    saved: dict[str, TenderSummary] = {}

    def save_batch(summaries: list[TenderSummary]) -> None:
        nonlocal created, updated, processed
        for summary in summaries:
            try:
                # Точка сохранения на каждую запись: ошибка базы на одной строке откатывает
                # только её, а не всю порцию, и не оставляет сессию в сломанном состоянии.
                with db.begin_nested():
                    is_new = _upsert_tender(db, source, summary, groups=groups)
                if is_new:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:  # noqa: BLE001 - ошибка одной записи не должна прервать остальные
                errors.append(PollError(summary.external_id, str(exc)))
            saved[summary.external_id] = summary
            processed += 1
        db.commit()
        if on_progress is not None:
            on_progress(processed, created, updated)

    adapter.on_batch = save_batch
    try:
        outcome = adapter.list_new_tenders(since=source.last_polled_at)
    except Exception as exc:  # noqa: BLE001 - изоляция сбоя одного источника (раздел 5.1, 5.9 ТЗ)
        logger.exception(f"Опрос источника {source.key} завершился ошибкой")
        log_action(
            db,
            component="scheduler",
            action=f"poll_source:{source.key}",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor_id,
        )
        db.commit()
        # Полностью сорвавшийся опрос площадки — событие для администратора (раздел 5.9 ТЗ).
        # Отдельные ошибки внутри выдачи (одна битая строка) сюда не попадают: они и так
        # изолированы и лежат в журнале.
        notification_service.notify_critical_error(
            db,
            subject=f"Опрос источника «{source.name}» завершился ошибкой",
            details=str(exc),
        )
        return SourcePollResult(source.key, processed, created, updated, len(errors) + 1)
    finally:
        adapter.on_batch = None

    errors.extend(outcome.errors)
    # Остаток выдачи: последняя неполная порция и записи, которые адаптер обновил уже после
    # того, как отдал их (та же закупка встретилась по другому слову с новыми полями).
    save_batch(
        [
            summary
            for summary in outcome.tenders
            if saved.get(summary.external_id) is not summary
        ]
    )

    source.last_polled_at = datetime.now(timezone.utc)
    db.add(source)
    db.flush()

    # ИИ-отбор новых закупок сразу после сбора (раздел 5.4 ТЗ): человек должен открыть список
    # и увидеть помеченное «Подобрано ИИ», а не запускать проверку руками. Ограничение по
    # числу — защита от лавины: если источник вывалил сотни новых записей, разбираем первую
    # партию сейчас, остальное дочистит кнопка в настройках.
    if created:
        try:
            ai_relevance_service.check_batch(db, limit=min(created, AI_CHECK_ON_POLL_LIMIT))
        except Exception as exc:  # noqa: BLE001 - сбой модели не отменяет успешный сбор
            logger.warning(f"ИИ-отбор после опроса {source.key} не выполнен: {exc}")

    details = f"Найдено {processed}, создано {created}, обновлено {updated}, ошибок {len(errors)}"
    if errors:
        details += "; " + "; ".join(f"{e.external_id or '?'}: {e.message}" for e in errors[:10])

    log_action(
        db,
        component="scheduler",
        action=f"poll_source:{source.key}",
        result="success" if not errors else "partial_error",
        level=LogLevel.INFO if not errors else LogLevel.WARNING,
        details=details,
        user_id=actor_id,
    )
    db.commit()
    return SourcePollResult(source.key, processed, created, updated, len(errors))


def poll_all_active_sources(
    db: Session, *, actor_id: uuid.UUID | None = None
) -> list[SourcePollResult]:
    sources = db.scalars(
        select(Source).where(
            Source.status == SourceStatus.ACTIVE.value,
            Source.adapter_key.is_not(None),
            # Источники справочника продукции (ФГИС, сайт производителя) живут в той же
            # таблице ради общего управления, но тендеров не отдают — у них своё расписание
            # и свой сервис синхронизации (`app/services/catalog_sync_service.py`). Ручные
            # заявки — тоже не площадка: их заводит человек, опрашивать там нечего.
            Source.type.notin_(POLL_EXCLUDED_SOURCE_TYPES),
        )
    ).all()
    return [poll_source(db, source, actor_id=actor_id) for source in sources]


def poll_sources(
    db: Session, source_keys: list[str], *, actor_id: uuid.UUID | None = None
) -> list[SourcePollResult]:
    """Опрашивает конкретный набор источников по их `key` — используется кнопкой
    «Синхронизировать» / модалкой «Ресурсы» на странице тендеров, в отличие от
    `poll_all_active_sources` (плановый прогон по расписанию, все активные источники сразу).
    Неизвестный ключ молча пропускается — вызывающая сторона (эндпоинт) сама решает, считать
    ли это ошибкой."""

    results = []
    for key in source_keys:
        source = get_source_by_key(db, key)
        if source is not None and source.type not in POLL_EXCLUDED_SOURCE_TYPES:
            results.append(poll_source(db, source, actor_id=actor_id))
    return results


def get_tender_by_id(db: Session, tender_id: uuid.UUID) -> Tender | None:
    return db.get(Tender, tender_id)


@dataclass
class TenderFilters:
    """Фильтры списка тендеров (раздел 5.6 ТЗ): дата, регион, тип конкурса, сумма, статус,
    ОКПД2, процент победителя МИРТЕК. Даты — только `date` (без времени): выбор границы
    день-в-день интуитивнее для пользователя, чем точное время; преобразование в границы
    суток — в `list_tenders`."""

    search: str | None = None
    source_keys: list[str] = field(default_factory=list)
    publish_date_from: date | None = None
    publish_date_to: date | None = None
    deadline_from: date | None = None
    deadline_to: date | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    hide_expired: bool = False
    # Профиль релевантности (раздел 5.1.1 ТЗ). `True` — показывать только прошедшие;
    # не прошедшие при этом не удалены и видны при снятии галочки, потому что профиль
    # настраивают люди и он вполне может оказаться слишком узким.
    #
    # Имя с `profile_` не случайно: рядом живёт `relevance_statuses` — решение ЧЕЛОВЕКА
    # («подтвердил» / «отметил неактуальным»), и в выгрузке Bitrix есть свой `only_relevant`
    # ровно про него. Два разных смысла под одним именем однажды сложатся в неверный отбор.
    only_profile_relevant: bool = False
    # Только то, что модель признала нашим (раздел 5.4 ТЗ). Отдельно от
    # `only_profile_relevant`: профиль ключевых слов — грубое сито, модель — точное.
    only_ai_selected: bool = False
    # Поля, которые заполняет ИИ-анализ (Этапы 5-6). Пока анализ не выполнен, они пустые —
    # тендер просто не попадёт в выдачу с таким фильтром, и это верно: пользователь ищет
    # разобранные закупки.
    region_codes: list[str] = field(default_factory=list)
    federal_district_codes: list[int] = field(default_factory=list)
    tender_types: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    relevance_statuses: list[str] = field(default_factory=list)
    # Этап внутреннего пайплайна (раздел 5.6 ТЗ). Отдельно от `statuses`: состояние закупки
    # на площадке и стадия нашей работы с ней — разные вопросы, и фильтруют по ним порознь.
    stages: list[str] = field(default_factory=list)
    assignee_ids: list[uuid.UUID] = field(default_factory=list)
    okpd2_prefix: str | None = None
    win_percentage_min: Decimal | None = None
    win_percentage_max: Decimal | None = None
    # Диапазон итоговой AI-оценки по профилю (раздел 5.5.1 ТЗ) — главный фильтр списка
    # с 03.09.2026.
    ai_score_min: Decimal | None = None
    ai_score_max: Decimal | None = None
    # Только избранное этого пользователя (замечание тестировщика 16.09.2026). Идентификатор
    # подставляет эндпоинт из текущего пользователя, а не query-параметр: чужое избранное
    # через API читать нельзя.
    bookmarked_by_user_id: uuid.UUID | None = None
    # Теги (замечание 17.09.2026): закупка проходит, если у неё есть хотя бы один из них.
    tag_ids: list[uuid.UUID] = field(default_factory=list)


# По каким столбцам разрешена сортировка (раздел 5.6 ТЗ — «по всем столбцам»). Явный
# перечень, а не `getattr(Tender, name)`: имя столбца приходит из query-параметра, и
# сортировка по произвольному атрибуту модели была бы дырой в API.
_SORTABLE_COLUMNS: dict[str, object] = {
    "publish_date": Tender.publish_date,
    "application_end": Tender.application_end,
    "price": Tender.price,
    "title": Tender.title,
    "customer_name": Tender.customer_name,
    "status": Tender.status,
    "okpd2_code": Tender.okpd2_code,
    "tender_type": Tender.tender_type,
    "relevance_status": Tender.relevance_status,
    "stage": Tender.stage,
    "created_at": Tender.created_at,
}

DEFAULT_SORT = "created_at"

# Ключ, под которым и счётчики, и колонки собирают тендеры с пустым статусом. Тем же
# словом интерфейс называет колонку «Без статуса».
UNCLASSIFIED = "unclassified"


def mirtek_win_percentage_subquery():
    """Коррелированный подзапрос «процент победителя МИРТЕК по этому тендеру».

    Нужен и для фильтра, и для сортировки по проценту. Именно подзапрос, а не JOIN:
    join с `win_percentages` размножил бы строки тендеров по числу производителей, и
    пришлось бы городить distinct поверх сортировки.
    """

    # Импорт внутри функции: `analysis` ссылается на тендеры, и импорт на уровне модуля
    # замкнул бы кольцо через `models.tender`.
    from app.models.analysis import WinPercentage
    from app.models.manufacturer import Manufacturer

    return (
        select(WinPercentage.percentage)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            WinPercentage.tender_id == Tender.id,
            Manufacturer.is_mirtek.is_(True),
            WinPercentage.is_current.is_(True),
        )
        .correlate(Tender)
        .limit(1)
        .scalar_subquery()
    )


def ai_score_subquery():
    """Коррелированный подзапрос «итоговая AI-оценка по профилю у этого тендера».

    Ровно та же роль, что у `mirtek_win_percentage_subquery`, но по другой метрике: с
    03.09.2026 в списке, фильтрах и уведомлениях главной стала именно она. Берётся текущая
    версия оценки — пересчёты хранятся историей (раздел 7 ТЗ).
    """

    from app.models.ai_profile import AiProfileScore

    return (
        select(AiProfileScore.overall_score)
        .where(
            AiProfileScore.tender_id == Tender.id,
            AiProfileScore.is_current.is_(True),
        )
        .correlate(Tender)
        .limit(1)
        .scalar_subquery()
    )


def _build_conditions(filters: TenderFilters) -> list:
    conditions = []

    if filters.search:
        pattern = f"%{filters.search}%"
        conditions.append(
            Tender.title.ilike(pattern) | Tender.customer_name.ilike(pattern)
        )
    if filters.source_keys:
        conditions.append(
            Tender.source_id.in_(select(Source.id).where(Source.key.in_(filters.source_keys)))
        )
    if filters.publish_date_from:
        conditions.append(Tender.publish_date >= filters.publish_date_from)
    if filters.publish_date_to:
        conditions.append(Tender.publish_date <= filters.publish_date_to)
    if filters.deadline_from:
        conditions.append(
            Tender.application_end >= datetime.combine(filters.deadline_from, time.min, tzinfo=timezone.utc)
        )
    if filters.deadline_to:
        conditions.append(
            Tender.application_end <= datetime.combine(filters.deadline_to, time.max, tzinfo=timezone.utc)
        )
    if filters.price_min is not None:
        conditions.append(Tender.price >= filters.price_min)
    if filters.price_max is not None:
        conditions.append(Tender.price <= filters.price_max)
    if filters.only_profile_relevant:
        # `None` («фильтр ещё не применялся») пропускаем наравне с прошедшими: тендеры,
        # собранные до появления профиля, не проверялись, и скрывать их как
        # нерелевантные значило бы соврать.
        conditions.append(
            Tender.passed_relevance_filter.is_(None)
            | Tender.passed_relevance_filter.is_(True)
        )
        # Второй слой того же отбора — модель (раздел 5.4 ТЗ). Ключевые слова слепы:
        # «счетчик*» пропускает счётчики банкнот и посетителей, и без этого условия всё,
        # что модель уже признала чужим, продолжало висеть в списке «по профилю». Прячется
        # только явное «нет»; непроверенное (NULL) остаётся — это «не смотрели», а не
        # «не подходит».
        conditions.append(Tender.ai_relevant.is_not(False))
    if filters.only_ai_selected:
        conditions.append(Tender.ai_relevant.is_(True))
    if filters.hide_expired:
        # «Истёкший» — это не только просроченная дата. Площадки массово отдают закупки без
        # срока подачи вообще (у ЕИС таких больше половины), и почти все они уже завершены:
        # проверка одной только даты пропускала их в выдачу, и при включённой галочке в
        # списке всё равно висели «Завершено». Поэтому скрываем и по дате, и по статусу —
        # подать заявку нельзя ни туда, ни туда. Тендеры без даты и без финального статуса
        # остаются: это неизвестность, а не заведомо закрытая закупка.
        conditions.append(
            Tender.application_end.is_(None) | (Tender.application_end >= func.now())
        )
        conditions.append(
            Tender.status.is_(None)
            | Tender.status.notin_([TenderStatus.COMPLETED.value, TenderStatus.CANCELLED.value])
        )
    if filters.region_codes:
        # Регион заказчика ИЛИ регион поставки: пользователь ищет «закупки, связанные с
        # регионом», а не конкретную роль региона в карточке (раздел 5.6 ТЗ).
        conditions.append(
            Tender.region_organizer_code.in_(filters.region_codes)
            | Tender.region_delivery_code.in_(filters.region_codes)
        )
    if filters.federal_district_codes:
        conditions.append(Tender.federal_district_code.in_(filters.federal_district_codes))
    if filters.tender_types:
        conditions.append(Tender.tender_type.in_(filters.tender_types))
    if filters.statuses:
        conditions.append(Tender.status.in_(filters.statuses))
    if filters.relevance_statuses:
        conditions.append(Tender.relevance_status.in_(filters.relevance_statuses))
    if filters.stages:
        conditions.append(Tender.stage.in_(filters.stages))
    if filters.assignee_ids:
        conditions.append(Tender.assignee_id.in_(filters.assignee_ids))
    if filters.bookmarked_by_user_id is not None:
        from app.models.tender_bookmark import TenderBookmark

        conditions.append(
            Tender.id.in_(
                select(TenderBookmark.tender_id).where(
                    TenderBookmark.user_id == filters.bookmarked_by_user_id
                )
            )
        )
    if filters.tag_ids:
        from app.models.tender_tag import TenderTagLink

        conditions.append(
            Tender.id.in_(
                select(TenderTagLink.tender_id).where(TenderTagLink.tag_id.in_(filters.tag_ids))
            )
        )
    if filters.okpd2_prefix:
        # Именно префикс, а не точное совпадение: ОКПД2 иерархичен, и «26.51» должно
        # находить в том числе 26.51.63.130 (Приложение F ТЗ).
        conditions.append(Tender.okpd2_code.ilike(f"{filters.okpd2_prefix}%"))
    if filters.win_percentage_min is not None or filters.win_percentage_max is not None:
        percentage = mirtek_win_percentage_subquery()
        if filters.win_percentage_min is not None:
            conditions.append(percentage >= filters.win_percentage_min)
        if filters.win_percentage_max is not None:
            conditions.append(percentage <= filters.win_percentage_max)
    if filters.ai_score_min is not None or filters.ai_score_max is not None:
        ai_score = ai_score_subquery()
        if filters.ai_score_min is not None:
            conditions.append(ai_score >= filters.ai_score_min)
        if filters.ai_score_max is not None:
            conditions.append(ai_score <= filters.ai_score_max)

    return conditions


def apply_tender_filters(query, filters: TenderFilters | None):
    """Накладывает фильтры раздела 5.6 ТЗ на произвольный запрос по `Tender`.

    Вынесено из `list_tenders`, чтобы аналитика и выгрузка в Excel считались ровно по тому
    же набору записей, что видит пользователь в списке: иначе цифры на дашборде и строки в
    отчёте расходились бы с выдачей, и объяснить это расхождение было бы нечем.
    """

    conditions = _build_conditions(filters or TenderFilters())
    return query.where(and_(*conditions)) if conditions else query


def count_tenders(db: Session, *, filters: TenderFilters | None = None) -> int:
    """Сколько записей отвечает фильтрам — для пагинации (раздел 5.6 ТЗ)."""

    query = select(func.count()).select_from(Tender)
    conditions = _build_conditions(filters or TenderFilters())
    if conditions:
        query = query.where(and_(*conditions))
    return db.scalar(query) or 0


def count_tenders_by_stage(db: Session, *, filters: TenderFilters | None = None) -> dict[str, int]:
    """Разбивка по этапам пайплайна — счётчики колонок Kanban (раздел 5.6 ТЗ, 03.09.2026).

    Отдельно от `count_tenders_by_status`: доска перешла на `stage`, а разбивка по `status`
    осталась нужна фильтрам и бейджам («идёт приём заявок» / «завершена»).
    """

    query = select(Tender.stage, func.count()).select_from(Tender).group_by(Tender.stage)
    conditions = _build_conditions(filters or TenderFilters())
    if conditions:
        query = query.where(and_(*conditions))
    return {(stage or UNCLASSIFIED): count for stage, count in db.execute(query).all()}


def count_tenders_by_status(db: Session, *, filters: TenderFilters | None = None) -> dict[str, int]:
    """Разбивка отобранных тендеров по статусам — по ВСЕЙ выборке, а не по текущей странице.

    Колонки Kanban раньше считали то, что пришло в ответе, то есть максимум `limit` записей.
    При выдаче в сотни тендеров это давало заведомо ложные цифры: сумма по колонкам всегда
    равнялась размеру страницы, и любое изменение сортировки перекидывало десятки закупок
    из колонки в колонку, хотя выборка не менялась. Считаем отдельным агрегатом с теми же
    условиями, что и `count_tenders`.

    Тендеры без статуса собираются под ключом `unclassified` — тем же, которым интерфейс
    называет колонку «Без статуса».
    """

    query = select(Tender.status, func.count()).select_from(Tender).group_by(Tender.status)
    conditions = _build_conditions(filters or TenderFilters())
    if conditions:
        query = query.where(and_(*conditions))
    return {(status or UNCLASSIFIED): count for status, count in db.execute(query).all()}


def _ordering(sort_by: str, descending: bool):
    """Выражение сортировки списка. Вынесено из `list_tenders`, чтобы колонки Kanban
    упорядочивались ровно так же, как строки таблицы, — иначе один и тот же тендер стоял бы
    в списке и на доске на разных местах."""

    if sort_by == "win_percentage":
        sort_column = mirtek_win_percentage_subquery()
    elif sort_by == "ai_score":
        sort_column = ai_score_subquery()
    else:
        sort_column = _SORTABLE_COLUMNS.get(sort_by, _SORTABLE_COLUMNS[DEFAULT_SORT])

    # Пустые значения — всегда в конце, в обоих направлениях: тендеры без даты окончания
    # или без рассчитанного процента не должны занимать первые экраны выдачи.
    ordering = sort_column.desc() if descending else sort_column.asc()
    return ordering.nullslast()


def _ordering_keys(sort_by: str, descending: bool):
    """Ключи сортировки списка.

    При сортировке по умолчанию подобранные моделью закупки идут первыми: список читают
    сверху, и то, что ИИ признал нашим, должно попадаться на глаза раньше остального
    (`NULLS LAST` — непроверенные ниже отклонённых не опускаем, они просто после
    подобранных). Как только человек выбрал столбец сам, это правило снимается: он попросил
    конкретный порядок, и подмешивать в него свой — значит спорить с пользователем.
    """

    keys = []
    if sort_by == DEFAULT_SORT:
        keys.append(Tender.ai_relevant.desc().nullslast())
    keys.append(_ordering(sort_by, descending))
    keys.append(Tender.created_at.desc())
    return keys


def list_tenders(
    db: Session,
    *,
    limit: int = 200,
    offset: int = 0,
    sort_by: str = DEFAULT_SORT,
    descending: bool = True,
    filters: TenderFilters | None = None,
) -> list[Tender]:
    query = select(Tender)
    conditions = _build_conditions(filters or TenderFilters())
    if conditions:
        query = query.where(and_(*conditions))

    query = query.order_by(*_ordering_keys(sort_by, descending))
    query = query.limit(limit).offset(offset)
    return list(db.scalars(query))



def list_board_columns(
    db: Session,
    *,
    per_column: int = 20,
    sort_by: str = DEFAULT_SORT,
    descending: bool = True,
    filters: TenderFilters | None = None,
) -> dict[str, list[Tender]]:
    """Верхние `per_column` тендеров ОТДЕЛЬНО в каждой колонке Kanban.

    Доска не может набираться обычной страницей списка: страница режет выдачу сквозным
    срезом по одной сортировке, и колонки достаются ей как придётся. На реальных данных это
    выглядело как пропажа тендеров — при сортировке по сроку подачи по возрастанию первые
    полсотни записей оказывались закупками 2010-2015 годов со статусом «Завершено», доска
    показывала одну заполненную колонку из пяти, а «Сбор заявок» уезжал на шестидесятую
    страницу. Причём включение фильтра «скрывать закрытые» тендеры в колонке возвращало —
    ровно наоборот тому, чего ждёшь от фильтра.

    Поэтому здесь оконная функция: нумеруем строки внутри каждой колонки и берём начало
    каждой группы. Один запрос вместо пяти, порядок внутри колонки — тот же, что в списке.

    **С 03.09.2026 колонки — это этапы (`stage`), а не статусы закупки на площадке**
    (раздел 5.6 ТЗ). Доска показывает наш пайплайн: «новая → на проверке → заявка
    подана → выиграли/проиграли/отклонили». `status` при этом никуда не делся — он остался
    фильтром и бейджем карточки, потому что отвечает на другой вопрос.
    """

    ordering = _ordering(sort_by, descending)
    row_number = (
        func.row_number()
        .over(partition_by=Tender.stage, order_by=(ordering, Tender.created_at.desc()))
        .label("row_number")
    )

    numbered = select(Tender.id.label("id"), row_number)
    conditions = _build_conditions(filters or TenderFilters())
    if conditions:
        numbered = numbered.where(and_(*conditions))
    numbered = numbered.subquery()

    query = (
        select(Tender)
        .join(numbered, Tender.id == numbered.c.id)
        .where(numbered.c.row_number <= per_column)
        .order_by(ordering, Tender.created_at.desc())
    )

    columns: dict[str, list[Tender]] = {}
    for tender in db.scalars(query):
        columns.setdefault(tender.stage or UNCLASSIFIED, []).append(tender)
    return columns


def get_tender_stats(db: Session) -> dict:
    total = db.scalar(select(func.count()).select_from(Tender)) or 0
    rows = db.execute(select(Tender.status, func.count()).group_by(Tender.status)).all()
    by_status = {(status or "unknown"): count for status, count in rows}
    return {"total": total, "by_status": by_status}
