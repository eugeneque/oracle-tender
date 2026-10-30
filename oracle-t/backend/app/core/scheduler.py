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


from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    EVENT_JOB_SUBMITTED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger as _CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.core import activity
from app.core.config import get_settings
from app.core.timezones import MOSCOW_TZ, now_msk
from app.db.session import SessionLocal
from app.models.log import LogLevel
from app.services.audit import log_action
from app.services.availability_service import ping_all_sources
from app.services.log_service import purge_old_entries
from app.services.notification_service import notify_deadlines_soon
from app.services.tender_service import (
    check_pending_ai_relevance,
    list_pollable_sources,
    poll_all_active_sources,
)

_scheduler: BackgroundScheduler | None = None

# Допуск на опоздание запуска (14.10.2026). По умолчанию в APScheduler 3.x он равен ОДНОЙ
# секунде: запуск, не стартовавший ровно вовремя (занятый пул, машина проснулась после сна,
# долгая сборка мусора), молча пропускался — след оставался только в stdout uvicorn, а в
# «Журнале» опроса просто не было. Час — с запасом на любую задержку, но не настолько много,
# чтобы утренний опрос, сорвавшийся на ночь, внезапно стартовал днём поверх ручного.
MISFIRE_GRACE_SECONDS = 3600
JOB_DEFAULTS = {
    "misfire_grace_time": MISFIRE_GRACE_SECONDS,
    # Несколько пропущенных запусков одного задания — один догоняющий, а не очередь подряд.
    "coalesce": True,
    # Опрос площадок идёт десятки минут: второй экземпляр поверх первого лишь удвоил бы
    # нагрузку на площадки и гонялся бы за теми же записями.
    "max_instances": 1,
}

# Человеческие названия заданий для журнала: «poll_sources_morning» пользователю ничего не
# говорит. Ключ — префикс id задания (у автозаполнения их несколько: catalog_autofill_1…).
_JOB_LABELS = {
    "poll_sources": "Плановый опрос площадок",
    "catalog_autofill": "Автозаполнение каталога",
    "catalog_sites_sync": "Обход каталогов сайтов производителей",
    "catalog_learning": "Обучение справочника",
    "upper_software_sync": "Чтение списков ПО верхнего уровня",
    "fgis_revalidation": "Ревалидация карточек ФГИС",
    "document_registry_check": "Сверка документов по СИ",
    "registry_poll": "Сверка с реестрами допуска",
    "meter_market_load": "Загрузка поверок ФГИС «Аршин»",
    "notify_deadlines": "Уведомления о сроках подачи",
    "purge_logs": "Уборка журнала",
    "tender_gap_repair": "Достройка матриц соответствия",
    "catalog_lookup_queue": "Подбор задач очереди справочника",
    "ai_auto_review": "Автоматический разбор закупок",
}
# Ежеминутный пинг доступности в журнал о пропусках не пишется: следующий придёт через
# минуту, а запись на каждый пропуск при подвисшем пуле засыпала бы журнал.
_QUIET_JOBS = {"ping_sources", "ai_auto_review"}


def _job_label(job_id: str) -> str:
    for prefix, label in _JOB_LABELS.items():
        if job_id.startswith(prefix):
            return label
    return job_id


def _journal(action: str, result: str, details: str, level: LogLevel = LogLevel.INFO) -> None:
    """Запись планировщика в журнал операций — тем же путём, что и `poll_source:<key>`.
    Своя сессия: слушатель событий и задания работают в потоках APScheduler, вне запроса.
    Сбой записи не должен ронять ни задание, ни поток планировщика."""

    db = SessionLocal()
    try:
        log_action(db, component="scheduler", action=action, result=result, level=level, details=details)
        db.commit()
    except Exception as exc:  # noqa: BLE001 - журнал не должен ронять планировщик
        logger.warning(f"Не удалось записать в журнал «{action}»: {exc}")
    finally:
        db.close()


def _on_job_skipped(event: JobExecutionEvent | JobSubmissionEvent) -> None:
    """Пропущенный запуск — WARNING в журнале (14.10.2026): коллеги по выгрузке журнала за
    13–14.10 не могли отличить «опроса не было по расписанию» от «опрос сорвался».
    Пропуск по сроку приходит `JobExecutionEvent` с одним временем, отказ из-за ещё идущего
    экземпляра — `JobSubmissionEvent` со списком."""

    if event.job_id in _QUIET_JOBS:
        return
    when = getattr(event, "scheduled_run_time", None) or next(
        iter(getattr(event, "scheduled_run_times", None) or []), None
    )
    when_text = when.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M") if when else "?"
    if event.code == EVENT_JOB_MAX_INSTANCES:
        reason = "предыдущий запуск ещё не закончился"
    else:
        reason = f"не стартовал в пределах {MISFIRE_GRACE_SECONDS // 60} мин после срока"
    details = f"{_job_label(event.job_id)} на {when_text} МСК пропущен: {reason}"
    logger.warning(details)
    _journal(f"job_missed:{event.job_id}", "skipped", details, level=LogLevel.WARNING)


# Задания, ход которых виден всем в центре уведомлений шапки (14.10.2026): долгие и
# заметные по результату. Автозаполнение и обучение справочника сюда не входят — они только
# ставят задачи в очередь справочника, а её ход центр уведомлений берёт из БД.
_TRACKED_JOBS = (
    "poll_sources",
    "catalog_sites_sync",
    "upper_software_sync",
    "fgis_revalidation",
    "document_registry_check",
    "registry_poll",
    "meter_market_load",
)


def _tracked_key(job_id: str) -> str | None:
    return next((prefix for prefix in _TRACKED_JOBS if job_id.startswith(prefix)), None)


def _on_job_activity(event: JobExecutionEvent | JobSubmissionEvent) -> None:
    """Старт и окончание заметных заданий — в реестр идущих процессов (`app.core.activity`).
    Слушатель, а не обёртка каждой функции: заданий много, а правило для всех одно."""

    key = _tracked_key(event.job_id)
    if key is None:
        return
    if event.code == EVENT_JOB_SUBMITTED:
        activity.start(key, _job_label(event.job_id))
    else:
        exception = getattr(event, "exception", None)
        activity.finish(key, error=f"Завершилось ошибкой: {exception}" if exception else None)


def CronTrigger(**fields) -> _CronTrigger:  # noqa: N802 - заменяет класс APScheduler по месту
    """Cron-триггер по Москве. Без явного `timezone` APScheduler 3.x берёт пояс МАШИНЫ, а не
    пояс планировщика: на Mac разработчика это Москва, а на сервере заказчика — UTC, и опрос
    «в 09:00 и 14:00» шёл в 12:00 и 17:00 по Москве, уведомления о сроках — в 12:30
    (журнал сервера 01–08.10.2026). Все расписания в настройках задаются по Москве."""

    return _CronTrigger(timezone=MOSCOW_TZ, **fields)


def _run_poll_job() -> None:
    # Старт и окончание — в журнал (14.10.2026): по одним записям `poll_source:<key>` не
    # видно, был ли плановый прогон вообще, а при нуле площадок их и вовсе нет.
    db = SessionLocal()
    try:
        _journal(
            "scheduled_poll",
            "started",
            f"Плановый опрос запущен: площадок {len(list_pollable_sources(db))}",
        )
        try:
            results = poll_all_active_sources(db)
        except Exception as exc:
            db.rollback()
            _journal("scheduled_poll", "error", f"Плановый опрос прерван ошибкой: {exc}", LogLevel.ERROR)
            raise
        created = sum(result.created for result in results)
        failed = sum(1 for result in results if result.errors)
        _journal(
            "scheduled_poll",
            "success" if not failed else "partial_error",
            f"Плановый опрос завершён: площадок {len(results)}, создано {created}, "
            f"обновлено {sum(result.updated for result in results)}, площадок с ошибками {failed}",
            LogLevel.INFO if not failed else LogLevel.WARNING,
        )
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
        adapter_keys = (catalog_site_sync.MIRTEK_ADAPTER_KEY, *PROFILES)
        for index, adapter_key in enumerate(adapter_keys):
            activity.progress(
                "catalog_sites_sync", done=index, total=len(adapter_keys), message=f"Сайт: {adapter_key}"
            )
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

    from app.services import catalog_autofill, catalog_learning, catalog_queue_service

    db = SessionLocal()
    try:
        queued = catalog_learning.enqueue_all(db)
        # Выполняет поток очереди справочника, а не планировщик: при включённом
        # автозаполнении тот же поток ведёт быстрый проход, и два потока опрашивали бы
        # производителей параллельно (с 14.10.2026 этот проход идёт и при автозаполнении).
        catalog_queue_service.submit(
            lambda: catalog_autofill._process_all(with_upper_software=False)
        )
        logger.info(f"Обучение справочника: поставлено в очередь {queued}")
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


def _run_registry_poll_job() -> None:
    """Еженедельная сверка каталога с реестрами допуска (замечание 13.10.2026): выгрузки
    ГИСП и перечня Россетей раскладываются по моделям. Сбой одного реестра — в журнал
    опросов, второй всё равно опрашивается."""

    from app.services import registry_polling

    try:
        registry_polling.poll_all_scheduled()
    except Exception as exc:  # noqa: BLE001 - сбой сверки не должен ронять планировщик
        logger.warning(f"Сверка с реестрами допуска завершилась ошибкой: {exc}")


def _run_meter_market_job() -> None:
    """Ночная догрузка поверок ФГИС «Аршин» для отчёта «Рынок ПУ» (раздел 79 ARCHITECTURE.md):
    цепочка файлов изменений от последнего применённого до `currentId` манифеста. Раньше
    `nextUpdate` обращаться к сервису незачем — задание просто ничего не найдёт. Итог и
    ошибки — в журнал (компонент `meter_market`), файлы после применения удаляются."""

    from app.services import meter_market_loader

    try:
        applied = meter_market_loader.catch_up()
        if applied:
            logger.info(f"Поверки ФГИС: применены пакеты {', '.join(applied)}")
    except Exception as exc:  # noqa: BLE001 - сбой загрузки не должен ронять планировщик
        _journal("meter_market_load", "error", f"Загрузка поверок ФГИС «Аршин»: {exc}", level=LogLevel.ERROR)


def _run_catalog_queue_job() -> None:
    """Подбирает задачи очереди справочника, оставшиеся невыполненными.

    Штатно запрос по событию выполняется сразу в фоновом пуле; сюда попадает то, что не
    успело выполниться до перезапуска сервера или сорвалось на недоступном источнике."""

    from app.services import catalog_autofill, catalog_queue_service

    # Опрос производителя целиком — десятки минут; он идёт только в потоке очереди, строго
    # по одному. Взять его здесь значило бы опрашивать двух производителей параллельно.
    catalog_queue_service.process_pending(exclude_keys=catalog_autofill.LONG_RUNNING_KEYS)


def _run_catalog_autofill_job() -> None:
    """Суточное автозаполнение каталога: все производители по очереди, затем списки ПО
    верхнего уровня. Выполняется в потоке очереди справочника, планировщик только ставит."""

    from app.services import catalog_autofill

    try:
        queued = catalog_autofill.run_all_in_background()
        logger.info(f"Автозаполнение каталога: в очереди производителей {queued}")
    except Exception as exc:  # noqa: BLE001 - сбой постановки не должен ронять планировщик
        logger.warning(f"Автозаполнение каталога не запустилось: {exc}")


def _run_gap_repair_job() -> None:
    """Достройка матриц соответствия у открытых закупок с заключением ИИ (28.09.2026):
    оборванный разбор или переанализ без пересчёта не должен оставлять в карточке
    «не проверен по ТЗ» до того, как кто-то заметит."""

    from app.services.job_runner import enqueue_gap_repairs

    db = SessionLocal()
    try:
        queued = enqueue_gap_repairs(db)
        logger.info(f"Достройка матриц соответствия: в очереди закупок {queued}")
    except Exception as exc:  # noqa: BLE001 - сбой обхода не должен ронять планировщик
        logger.warning(f"Достройка матриц соответствия не запустилась: {exc}")
    finally:
        db.close()


def _run_auto_review_job() -> None:
    """Авторазбор через YandexGPT (раздел 81 ARCHITECTURE.md): следующая закупка встаёт на
    полный разбор, когда предыдущая закончилась. Выключенный авторазбор — пустой проход."""

    from app.services import auto_review_service

    db = SessionLocal()
    try:
        job = auto_review_service.feed(db)
        if job is not None:
            logger.info(f"Авторазбор: на разбор поставлена закупка {job.tender_id}")
    except Exception as exc:  # noqa: BLE001 - сбой постановки не должен ронять планировщик
        logger.warning(f"Авторазбор не продвинулся: {exc}")
    finally:
        db.close()


def _parse_times(value: str) -> list[str]:
    """«06:00,20:00» → ["06:00", "20:00"]: у задачи может быть несколько запусков в сутки."""

    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_hh_mm(value: str) -> tuple[int, int]:
    hour_str, _, minute_str = value.partition(":")
    return int(hour_str), int(minute_str or "0")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("Планировщик опроса источников отключён (SCHEDULER_ENABLED=false)")
        return None

    # Опрос реестра, оборванный перезапуском, иначе вечно висел бы в карточке «идёт опрос».
    db = SessionLocal()
    try:
        from app.services import registry_polling

        registry_polling.mark_interrupted(db)
    except Exception as exc:  # noqa: BLE001 - уборка не должна мешать запуску
        logger.warning(f"Не удалось закрыть оборванные опросы реестров: {exc}")
    finally:
        db.close()

    scheduler = BackgroundScheduler(timezone="Europe/Moscow", job_defaults=JOB_DEFAULTS)
    scheduler.add_listener(_on_job_skipped, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    scheduler.add_listener(
        _on_job_activity, EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
    )
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
    # Суточное автозаполнение покрывает обход сайтов, обучение и списки ПО — при нём
    # еженедельные запуски тех же шагов не ставятся.
    autofill = settings.catalog_autofill_enabled
    if autofill:
        for number, time_str in enumerate(_parse_times(settings.catalog_autofill_time)):
            hour, minute = _parse_hh_mm(time_str)
            scheduler.add_job(
                _run_catalog_autofill_job,
                CronTrigger(hour=hour, minute=minute),
                id="catalog_autofill" if number == 0 else f"catalog_autofill_{number}",
                replace_existing=True,
            )
    if settings.catalog_sites_sync_enabled and not autofill:
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
    # Второй проход (характеристики из документов моделью ИИ) — раз в неделю и при
    # автозаполнении: суточный опрос с 14.10.2026 идёт без модели (`catalog_autofill.enqueue_all`).
    if settings.catalog_learning_enabled:
        hour, minute = _parse_hh_mm(settings.catalog_learning_time)
        scheduler.add_job(
            _run_catalog_learning_job,
            CronTrigger(day_of_week=settings.catalog_learning_weekday, hour=hour, minute=minute),
            id="catalog_learning",
            replace_existing=True,
        )
    if settings.upper_software_sync_enabled and not autofill:
        hour, minute = _parse_hh_mm(settings.upper_software_sync_time)
        scheduler.add_job(
            _run_upper_software_job,
            CronTrigger(day_of_week=settings.upper_software_sync_weekday, hour=hour, minute=minute),
            id="upper_software_sync",
            replace_existing=True,
        )
    if settings.registry_poll_enabled:
        hour, minute = _parse_hh_mm(settings.registry_poll_time)
        scheduler.add_job(
            _run_registry_poll_job,
            CronTrigger(day_of_week=settings.registry_poll_weekday, hour=hour, minute=minute),
            id="registry_poll",
            replace_existing=True,
        )
    if settings.meter_market_enabled:
        hour, minute = _parse_hh_mm(settings.meter_market_time)
        scheduler.add_job(
            _run_meter_market_job,
            CronTrigger(hour=hour, minute=minute),
            id="meter_market_load",
            replace_existing=True,
        )
    # Ночью, до утреннего опроса: к нему матрицы оборванных разборов уже достроены.
    scheduler.add_job(
        _run_gap_repair_job,
        CronTrigger(hour=5, minute=0),
        id="tender_gap_repair",
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

    # Авторазбор — раз в минуту: полный разбор идёт минуты, и минута простоя между
    # закупками незаметна, а чаще проверять, закончилась ли предыдущая, незачем.
    scheduler.add_job(
        _run_auto_review_job,
        IntervalTrigger(minutes=1),
        id="ai_auto_review",
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
        f"площадки в {settings.scheduler_morning_time} и {settings.scheduler_afternoon_time} МСК, "
        "пинг доступности — площадки раз в 15 мин; справочник продукции: ревалидация ФГИС "
        f"{settings.fgis_revalidation_time if settings.fgis_revalidation_enabled else 'отключена'}, "
        f"сверка документов {settings.document_registry_check_time if settings.document_registry_check_enabled else 'отключена'}, "
        + (
            # Еженедельные обход сайтов и списки ПО при автозаполнении не ставятся (см. выше).
            f"автозаполнение каталога (сайты производителей, ФГИС, Аршин) "
            f"{settings.catalog_autofill_time} ежедневно"
            if autofill
            else "каталоги сайтов производителей "
            f"{settings.catalog_sites_sync_time if settings.catalog_sites_sync_enabled else 'отключены'}, "
            "списки ПО верхнего уровня "
            f"{settings.upper_software_sync_time if settings.upper_software_sync_enabled else 'отключены'}"
        )
    )
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
