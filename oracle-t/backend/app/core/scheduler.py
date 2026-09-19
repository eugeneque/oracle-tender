"""Планировщик опроса источников — 2 раза в день, утро и обед (раздел 4.1, 5.1, 6.2 ТЗ).
Время запусков настраивается через `.env` (`SCHEDULER_MORNING_TIME`/`SCHEDULER_AFTERNOON_TIME`,
раздел 5.1 ТЗ: "время конкретных запусков настраивается в конфигурации").

Помимо тендерных площадок здесь же живут **источники справочника продукции** — реестр ФГИС
и каталог на сайте производителя (раздел 4.2, 4.3, 5.3 ТЗ). Они заведены как обычные
источники и управляются отсюда же, но по своему расписанию и с другой моделью работы:
у тендерной площадки один режим («забрать всё новое»), у ФГИС их два — ревалидация
сохранённых карточек по расписанию и поиск по событию из модуля сопоставления, который
идёт мимо планировщика через очередь (`app/services/catalog_queue_service.py`).
Обработчик очереди тоже стоит в расписании — он подбирает то, что не успело выполниться
в фоне или сорвалось."""

from __future__ import annotations


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.core.config import get_settings
from app.core.timezones import now_msk
from app.db.session import SessionLocal
from app.services.availability_service import ping_all_sources
from app.services.log_service import purge_old_entries
from app.services.notification_service import notify_deadlines_soon
from app.services.tender_service import check_pending_ai_relevance, poll_all_active_sources

_scheduler: BackgroundScheduler | None = None


def _run_poll_job() -> None:
    db = SessionLocal()
    try:
        results = poll_all_active_sources(db)
        logger.info(f"Плановый опрос источников завершён: {results}")
        check_pending_ai_relevance(db)
    finally:
        db.close()
    _run_gaps_job()


def _run_gaps_job() -> None:
    """Дозаполнение ОКПД2, региона и типа конкурса — того, по чему фильтруют список
    (замечание тестировщика 16.09.2026). Сразу после опроса: новые закупки должны попадать
    под фильтры в тот же день, а не после того, как кто-то откроет их карточку."""

    from app.services import tender_gaps_service

    settings = get_settings()
    if not settings.tender_gaps_fill_enabled:
        return
    db = SessionLocal()
    try:
        outcome = tender_gaps_service.run(db, fetch_limit=settings.tender_gaps_fetch_limit)
        logger.info(f"Дозаполнение полей тендеров: {outcome.summary()}")
    except Exception as exc:  # noqa: BLE001 - сбой дозаполнения не должен ронять планировщик
        logger.warning(f"Дозаполнение полей тендеров завершилось ошибкой: {exc}")
    finally:
        db.close()


def _run_ping_job() -> None:
    db = SessionLocal()
    try:
        ping_all_sources(db)
    finally:
        db.close()


def _run_deadline_notifications_job() -> None:
    """Триггер «до окончания приёма заявок меньше N дней» (раздел 5.8 ТЗ).

    Это единственный триггер без внешнего события: срок наступает сам, поэтому его нужно
    проверять по расписанию. Раз в сутки утром — письмо про «осталось 4 дня» одинаково
    полезно в любой час, а чаще означало бы дубликаты."""

    db = SessionLocal()
    try:
        sent = notify_deadlines_soon(db)
        if sent:
            logger.info(f"Уведомлений о приближающихся сроках подачи: {len(sent)}")
    except Exception as exc:  # noqa: BLE001 - сбой рассылки не должен ронять планировщик
        logger.warning(f"Проверка сроков подачи завершилась ошибкой: {exc}")
    finally:
        db.close()


def _run_log_retention_job() -> None:
    """Уборка журнала старше шести месяцев (раздел 5.9 ТЗ). Ночью — операция затрагивает
    десятки тысяч строк и незачем делать это в рабочие часы."""

    db = SessionLocal()
    try:
        removed = purge_old_entries(db)
        if removed:
            logger.info(f"Из журнала удалено устаревших записей: {removed}")
    except Exception as exc:  # noqa: BLE001 - сбой уборки не должен ронять планировщик
        logger.warning(f"Уборка журнала завершилась ошибкой: {exc}")
    finally:
        db.close()


def _run_fgis_revalidation_job() -> None:
    """Ревалидация сохранённых карточек типов СИ (п.1.1 задания, триггер 1).

    Ставит записи в общую очередь и тут же её обрабатывает — не «сама ходит в ФГИС»:
    так у обоих триггеров (расписание и событие) один и тот же путь выполнения, и логика
    обращения к реестру не раздваивается."""

    from app.services import catalog_queue_service, fgis_catalog_sync

    settings = get_settings()
    db = SessionLocal()
    try:
        queued = fgis_catalog_sync.enqueue_revalidation(
            db, limit=settings.fgis_revalidation_batch
        )
        counters = catalog_queue_service.process_queue(
            db, adapter_key=fgis_catalog_sync.ADAPTER_KEY
        )
        logger.info(f"Ревалидация ФГИС: поставлено {queued}, обработано {counters}")
    except Exception as exc:  # noqa: BLE001 - сбой ревалидации не должен ронять планировщик
        logger.warning(f"Ревалидация карточек ФГИС завершилась ошибкой: {exc}")
    finally:
        db.close()


def _run_catalog_sites_job() -> None:
    """Еженедельный обход каталогов на сайтах производителей — своего и конкурентов.

    Сайты обходятся последовательно и независимо: недоступность одного не отменяет
    остальные (раздел 5.9 ТЗ). Собственный каталог идёт первым — он полнее всех и нужнее
    для расчёта соответствия."""

    from app.adapters.manufacturer_catalog import PROFILES
    from app.services import catalog_site_sync

    db = SessionLocal()
    try:
        for adapter_key in (catalog_site_sync.MIRTEK_ADAPTER_KEY, *PROFILES):
            site = catalog_site_sync.resolve_site(db, adapter_key)
            if site is None:
                logger.warning(
                    f"Каталог {adapter_key}: производитель не найден в справочнике — обход пропущен"
                )
                continue
            manufacturer, adapter = site
            try:
                outcome = catalog_site_sync.sync_catalog(
                    db, manufacturer=manufacturer, adapter=adapter
                )
                logger.info(
                    f"Каталог {adapter_key}: создано {outcome.products_created}, обновлено "
                    f"{outcome.products_updated}, характеристик {outcome.characteristics_saved}, "
                    f"привязано к кодам СИ {outcome.si_types_linked}, "
                    f"на проверку {outcome.marked_for_review}, ошибок {outcome.cards_failed}"
                )
            except Exception as exc:  # noqa: BLE001 - сбой одного сайта не отменяет остальные
                logger.warning(f"Обход каталога {adapter_key} завершился ошибкой: {exc}")
    finally:
        db.close()


def _run_document_registry_job() -> None:
    """Еженедельная сверка документов по СИ и руководств по эксплуатации с источниками
    (правка по итогам показа 15.09.2026): справочник актуальных дат обновляется, и обо
    всём, что изменилось, появилось или пропало, уходит отдельный отчёт на почту."""

    from app.services import document_registry_service

    db = SessionLocal()
    try:
        outcome = document_registry_service.run_check(db)
        logger.info(f"Сверка документов по СИ и руководств: {outcome.summary()}")
    except Exception as exc:  # noqa: BLE001 - сбой сверки не должен ронять планировщик
        logger.warning(f"Сверка документов по СИ и руководств завершилась ошибкой: {exc}")
    finally:
        db.close()


def _run_catalog_learning_job() -> None:
    """Еженедельное обучение справочника по Аршину и документации (замечание заказчика
    15.09.2026). Ставит проход по каждому производителю в общую очередь и обрабатывает её:
    один проход — минуты (документы, обращения к модели), и делать это в HTTP-запросе или
    прямо здесь без очереди значило бы потерять ход работы при перезапуске."""

    from app.services import catalog_learning, catalog_queue_service

    db = SessionLocal()
    try:
        queued = catalog_learning.enqueue_all(db)
        counters = catalog_queue_service.process_queue(
            db, adapter_key=catalog_learning.ADAPTER_KEY, limit=max(queued, 1)
        )
        logger.info(f"Обучение справочника: поставлено {queued}, обработано {counters}")
    except Exception as exc:  # noqa: BLE001 - сбой обучения не должен ронять планировщик
        logger.warning(f"Обучение справочника завершилось ошибкой: {exc}")
    finally:
        db.close()


def _run_upper_software_job() -> None:
    """Еженедельное чтение списков поддерживаемого оборудования ПО верхнего уровня
    (замечание тестировщика 16.09.2026). Площадки читаются подряд и независимо; после
    обхода каталогов и обучения справочника — связь записей с моделями строится по уже
    обновлённому каталогу."""

    from app.services import upper_software_service

    db = SessionLocal()
    try:
        outcomes = upper_software_service.sync_all(db)
        for outcome in outcomes:
            logger.info(f"ПО верхнего уровня {outcome.adapter_key}: {outcome.summary()}")
    except Exception as exc:  # noqa: BLE001 - сбой чтения списков не должен ронять планировщик
        logger.warning(f"Чтение списков ПО верхнего уровня завершилось ошибкой: {exc}")
    finally:
        db.close()


def _run_catalog_queue_job() -> None:
    """Подбирает задачи очереди справочника, оставшиеся невыполненными.

    Штатно запрос по событию выполняется сразу в фоновом пуле; сюда попадает то, что не
    успело выполниться до перезапуска сервера или сорвалось на недоступном источнике."""

    from app.services import catalog_queue_service

    catalog_queue_service.process_queue_in_background()


def _parse_hh_mm(value: str) -> tuple[int, int]:
    hour_str, _, minute_str = value.partition(":")
    return int(hour_str), int(minute_str or "0")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Планировщик опроса источников отключён (SCHEDULER_ENABLED=false)")
        return None

    scheduler = BackgroundScheduler(timezone="Europe/Moscow")
    for label, time_str in (
        ("morning", settings.scheduler_morning_time),
        ("afternoon", settings.scheduler_afternoon_time),
    ):
        hour, minute = _parse_hh_mm(time_str)
        scheduler.add_job(
            _run_poll_job,
            CronTrigger(hour=hour, minute=minute),
            id=f"poll_sources_{label}",
            replace_existing=True,
        )
    # Пинг доступности — раз в минуту, для всех источников (не только с реализованным
    # адаптером), см. app/services/availability_service.py. `next_run_time=now` — чтобы
    # «Настройки» не показывали пустое "не проверено" целую минуту после старта приложения.
    # Проверка сроков подачи — раз в сутки, через полчаса после утреннего опроса: к этому
    # моменту свежие тендеры уже в базе и попадут в ту же рассылку.
    morning_hour, morning_minute = _parse_hh_mm(settings.scheduler_morning_time)
    scheduler.add_job(
        _run_deadline_notifications_job,
        CronTrigger(hour=(morning_hour + (morning_minute + 30) // 60) % 24, minute=(morning_minute + 30) % 60),
        id="notify_deadlines",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_log_retention_job,
        CronTrigger(hour=3, minute=15),
        id="purge_logs",
        replace_existing=True,
    )
    # --- Источники справочника продукции (раздел 5.3 ТЗ) ---
    if settings.fgis_revalidation_enabled:
        hour, minute = _parse_hh_mm(settings.fgis_revalidation_time)
        scheduler.add_job(
            _run_fgis_revalidation_job,
            CronTrigger(hour=hour, minute=minute),
            id="fgis_revalidation",
            replace_existing=True,
        )
    if settings.catalog_sites_sync_enabled:
        hour, minute = _parse_hh_mm(settings.catalog_sites_sync_time)
        scheduler.add_job(
            _run_catalog_sites_job,
            CronTrigger(day_of_week=settings.catalog_sites_sync_weekday, hour=hour, minute=minute),
            id="catalog_sites_sync",
            replace_existing=True,
        )
    if settings.document_registry_check_enabled:
        hour, minute = _parse_hh_mm(settings.document_registry_check_time)
        scheduler.add_job(
            _run_document_registry_job,
            CronTrigger(
                day_of_week=settings.document_registry_check_weekday, hour=hour, minute=minute
            ),
            id="document_registry_check",
            replace_existing=True,
        )
    if settings.catalog_learning_enabled:
        hour, minute = _parse_hh_mm(settings.catalog_learning_time)
        scheduler.add_job(
            _run_catalog_learning_job,
            CronTrigger(day_of_week=settings.catalog_learning_weekday, hour=hour, minute=minute),
            id="catalog_learning",
            replace_existing=True,
        )
    if settings.upper_software_sync_enabled:
        hour, minute = _parse_hh_mm(settings.upper_software_sync_time)
        scheduler.add_job(
            _run_upper_software_job,
            CronTrigger(day_of_week=settings.upper_software_sync_weekday, hour=hour, minute=minute),
            id="upper_software_sync",
            replace_existing=True,
        )
    # Подбор «зависших» задач очереди. Раз в четверть часа, а не раз в минуту: штатно
    # задачи выполняются сразу в фоне, и этот проход — страховка, а не основной путь.
    scheduler.add_job(
        _run_catalog_queue_job,
        IntervalTrigger(minutes=15),
        id="catalog_lookup_queue",
        replace_existing=True,
    )

    scheduler.add_job(
        _run_ping_job,
        IntervalTrigger(minutes=1),
        id="ping_sources",
        replace_existing=True,
        # Планировщик живёт в МСК, поэтому и «сейчас» ему нужно в МСК: наивное
        # `datetime.now()` на UTC-сервере он истолковал бы как московское время и
        # получил бы момент на три часа в прошлом (раздел 8 ТЗ).
        next_run_time=now_msk(),
    )

    scheduler.start()
    logger.info(
        "Планировщик опроса источников запущен: "
        f"утро {settings.scheduler_morning_time}, обед {settings.scheduler_afternoon_time}, "
        "пинг доступности — раз в минуту; справочник продукции: ревалидация ФГИС "
        f"{settings.fgis_revalidation_time if settings.fgis_revalidation_enabled else 'отключена'}, "
        f"каталоги сайтов производителей {settings.catalog_sites_sync_time if settings.catalog_sites_sync_enabled else 'отключены'}, "
        f"сверка документов {settings.document_registry_check_time if settings.document_registry_check_enabled else 'отключена'}, "
        f"списки ПО верхнего уровня {settings.upper_software_sync_time if settings.upper_software_sync_enabled else 'отключены'}"
    )
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
