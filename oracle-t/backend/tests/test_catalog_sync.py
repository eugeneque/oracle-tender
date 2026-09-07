"""Тесты пополнения справочника продукции из внешних источников (задачи 1 и 2 задания).

Три предмета проверки:

* **очередь** — оба триггера ФГИС (расписание и событие) идут через один и тот же путь,
  дубликаты не плодятся, ошибка одной задачи не уносит остальные;
* **дисамбигуация в связке с БД** — неоднозначный результат реестра НЕ сохраняется в
  справочник, а уходит в «требует проверки» (критерий приёмки 2);
* **синхронизация каталога МИРТЕК** — идемпотентность повторного обхода, статус «Снят с
  производства», ссылки на документы, изоляция сбоя одной карточки.

Сеть не задействована: адаптеры подменяются заглушками, отдающими ту же структуру, что и
живые (см. `tests/test_mirtek_catalog_adapter.py`, где разметка проверена дословно).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.adapters.fgis import SiSearchResult
from app.adapters.mirtek_catalog import CatalogItem, CatalogOutcome, CatalogProductDetails, DocumentLink
from app.models.catalog_queue import CatalogQueueReason, CatalogQueueStatus
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
    ProductStatus,
    ReviewStatus,
    SiType,
    SiTypeSource,
)
from app.services import catalog_queue_service, catalog_site_sync, fgis_catalog_sync


@pytest.fixture(autouse=True)
def _handlers_registered():
    """Обработчики регистрируются в `app/main.py` при старте приложения; в сервисных тестах
    приложение не поднимается, поэтому регистрируем их явно."""

    fgis_catalog_sync.register()
    catalog_site_sync.register()


@pytest.fixture()
def manufacturer(db_session) -> Manufacturer:
    item = Manufacturer(
        legal_name=f'ООО «Тест {uuid.uuid4().hex[:8]}»', brand_name="Тест", is_mirtek=False
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# --- Заглушки адаптеров ---


class _StubFgis:
    """Отдаёт заранее заданную выдачу реестра. `enrich_from_card` дозаполняет карточку —
    как живой адаптер, отдельным запросом."""

    def __init__(self, results: list[SiSearchResult], *, card_extras: dict | None = None) -> None:
        self.results = results
        self.card_extras = card_extras or {}
        self.enriched: list[str] = []

    def search_by_manufacturer(self, legal_name, *, brand_name=None, fetch_cards=True):
        return list(self.results)

    def enrich_from_card(self, result: SiSearchResult) -> SiSearchResult:
        self.enriched.append(result.si_code)
        for name, value in self.card_extras.items():
            setattr(result, name, value)
        return result


def _meter(si_code: str, notation: str, *, title: str | None = None) -> SiSearchResult:
    return SiSearchResult(
        si_code=si_code,
        type_name=title or "Счетчики электрической энергии статические однофазные",
        notation=notation,
        manufacturer_name="ООО «Тест»",
        mit_uuid=f"uuid-{si_code}",
    )


class _StubMirtekSite:
    def __init__(self, items: list[CatalogItem], details: dict[str, CatalogProductDetails], *, fail: set[str] | None = None):
        self.items = items
        self.details = details
        self.fail = fail or set()
        self.requested: list[str] = []

    def list_catalog(self) -> CatalogOutcome:
        return CatalogOutcome(items=list(self.items), errors=[])

    def get_product_details(self, url: str) -> CatalogProductDetails:
        self.requested.append(url)
        if url in self.fail:
            raise RuntimeError("HTTP 500 на карточке")
        return self.details[url]


def _item(article: str, model: str, *, discontinued: bool = False, execution: str = "Таганрог") -> CatalogItem:
    return CatalogItem(
        model_name=model,
        article=article,
        execution=execution,
        url=f"https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/{article}",
        category="https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki",
        device_type="Однофазный счётчик электроэнергии",
        discontinued=discontinued,
        mounting_badge="DIN-рейка",
    )


def _details(item: CatalogItem, *, specs: dict[str, str] | None = None) -> CatalogProductDetails:
    return CatalogProductDetails(
        url=item.url,
        model_name=item.model_name,
        description="Интеллектуальный прибор учёта электроэнергии однофазный.",
        features_text="Основные интерфейсы связи:\nОптопорт;\nRS485.",
        specifications=specs
        if specs is not None
        else {
            "Класс точности по активной/реактивной энергии": "1/1",
            "Номинальное напряжение": "220 В или 230 В",
            "Базовый ток": "5 А",
            "Максимальный ток": "80 А",
            "Срок службы счётчика, не менее": "48 лет",
            "Средняя наработка на отказ, не менее": "480 000 ч",
            "Полная мощность, потребляемая каждой цепью тока при базовом токе, не превышает": "0,3 В·А",
        },
        documents=[
            DocumentLink(
                group="Разрешительные документы",
                title="Сертификат об утверждении и описание типа средств измерений МИРТЕК-12-РУ",
                url="https://mirtekgroup.com/download/2724",
            ),
            DocumentLink(
                group="Разрешительные документы",
                title="Декларация соответствия МИРТЕК-12-РУ",
                url="https://mirtekgroup.com/download/2728",
            ),
            DocumentLink(
                group="Руководства",
                title="Руководство по эксплуатации МИРТЕК-12-РУ (D17, SP17)",
                url="https://mirtekgroup.com/download/4739",
            ),
            DocumentLink(
                group="Руководства",
                title="Руководство по эксплуатации универсального сменного модуля связи МИРТЕК-МС",
                url="https://mirtekgroup.com/download/5434",
            ),
        ],
        symbol_legend="Тип счётчика\nТип корпуса",
    )


# --- Очередь ---


class TestQueue:
    def test_event_trigger_enqueues_and_is_visible(self, db_session, manufacturer):
        """Триггер по событию: запрос ставится немедленно, вне суточного расписания."""

        task = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="МИРТЕК-12-РУ-D17",
            manufacturer_id=manufacturer.id,
            run_now=False,
        )

        assert task is not None
        assert task.status == CatalogQueueStatus.QUEUED.value
        assert task.reason == CatalogQueueReason.MISSING_CATALOG_DATA.value
        assert task in catalog_queue_service.pending_tasks(db_session)

    def test_duplicate_request_does_not_create_second_task(self, db_session, manufacturer):
        """Один тендер с полусотней требований не должен ставить полсотни одинаковых
        запросов к ФГИС по одной и той же модели."""

        first = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="МИРТЕК-12-РУ-D17",
            manufacturer_id=manufacturer.id,
            run_now=False,
        )
        second = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="МИРТЕК-12-РУ-D17",
            manufacturer_id=manufacturer.id,
            run_now=False,
        )

        assert first.id == second.id

    def test_scheduled_trigger_enqueues_stale_si_types(self, db_session, manufacturer):
        """Триггер по расписанию: в очередь идут давно не проверявшиеся карточки."""

        fresh = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"11111-{uuid.uuid4().hex[:2]}",
            source=SiTypeSource.AUTO_SEARCH.value,
            last_checked_at=datetime.now(timezone.utc),
        )
        stale = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"22222-{uuid.uuid4().hex[:2]}",
            source=SiTypeSource.AUTO_SEARCH.value,
            last_checked_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        db_session.add_all([fresh, stale])
        db_session.commit()

        # Предел заведомо больше содержимого тестовой БД: `enqueue_revalidation` берёт
        # самые давно не проверявшиеся, а тестовая Postgres персистентна и хранит записи
        # соседних тестов — с маленьким пределом наши две в выборку могли бы не попасть.
        fgis_catalog_sync.enqueue_revalidation(db_session, limit=10_000)

        queued_si_types = {
            task.si_type_id
            for task in catalog_queue_service.pending_tasks(db_session, limit=10_000)
        }
        assert stale.id in queued_si_types
        assert fresh.id not in queued_si_types

    def test_manual_entries_are_not_revalidated(self, db_session, manufacturer):
        """Ручной ввод имеет приоритет перед автопоиском (раздел 5.3 ТЗ) — перезапрашивать
        его из реестра, чтобы потом не иметь права перезаписать, бессмысленно."""

        manual = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"33333-{uuid.uuid4().hex[:2]}",
            source=SiTypeSource.MANUAL.value,
        )
        db_session.add(manual)
        db_session.commit()

        fgis_catalog_sync.enqueue_revalidation(db_session, limit=10_000)

        queued = {
            task.si_type_id
            for task in catalog_queue_service.pending_tasks(db_session, limit=10_000)
        }
        assert manual.id not in queued

    def test_failing_task_does_not_stop_the_rest(self, db_session, manufacturer, monkeypatch):
        """Изоляция ошибок (раздел 5.9 ТЗ): сбой одной задачи не уносит очередь."""

        calls = {"n": 0}

        def flaky_handler(db, task):
            calls["n"] += 1
            if task.model_name == "битая":
                raise RuntimeError("источник недоступен")
            return catalog_queue_service.TaskOutcome(message="ок")

        catalog_queue_service.register_handler("test_flaky", flaky_handler)
        monkeypatch.setattr(catalog_queue_service, "RETRY_DELAY_SECONDS", 0)
        monkeypatch.setattr(catalog_queue_service, "MAX_ATTEMPTS", 1)

        for name in ("битая", "нормальная"):
            catalog_queue_service.enqueue(
                db_session,
                adapter_key="test_flaky",
                reason=CatalogQueueReason.MANUAL,
                model_name=name,
                manufacturer_id=manufacturer.id,
                run_now=False,
            )

        counters = catalog_queue_service.process_queue(db_session, adapter_key="test_flaky")

        assert counters == {"success": 1, "needs_review": 0, "error": 1}
        assert calls["n"] == 2


# --- Дисамбигуация в связке со справочником (критерий приёмки 2) ---


class TestFgisDisambiguation:
    def test_pulsar_collision_is_not_saved_automatically(self, db_session, manufacturer):
        """Кейс «Пульсар»: реестр вернул электросчётчик и пожарный извещатель одной марки.
        В справочник не должно попасть ничего — запись уходит в «требует проверки»."""

        adapter = _StubFgis(
            [
                _meter("55555-13", "Пульсар", title="Извещатели пожарные дымовые оптико-электронные"),
                _meter("66666-17", "Пульсар", title="Счетчики воды крыльчатые"),
            ]
        )
        task = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="Пульсар",
            manufacturer_id=manufacturer.id,
            run_now=False,
        )

        outcome = fgis_catalog_sync._lookup_model(db_session, task, adapter=adapter)

        assert outcome.needs_review is True
        assert "извещатель пожарный" in outcome.message
        # Ничего не сохранено — главное требование п.1.2 задания.
        saved = db_session.scalars(
            select(SiType).where(SiType.manufacturer_id == manufacturer.id)
        ).all()
        assert saved == []
        # И карточка типа для отвергнутых кандидатов не запрашивалась: незачем нагружать
        # нестабильный сервис ради того, что всё равно не сохранится.
        assert adapter.enriched == []

    def test_needs_review_marks_the_product_too(self, db_session, manufacturer):
        """Пометка должна дойти до карточки товара, иначе человек увидит просто пустые
        характеристики без объяснения."""

        product = Product(
            manufacturer_id=manufacturer.id, model_name="Пульсар-1", article="pulsar-1"
        )
        db_session.add(product)
        db_session.commit()

        task = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="Пульсар-1",
            manufacturer_id=manufacturer.id,
            product_id=product.id,
            run_now=False,
        )
        adapter = _StubFgis(
            [_meter("55555-13", "Пульсар", title="Извещатели пожарные дымовые оптико-электронные")]
        )

        fgis_catalog_sync._lookup_model(db_session, task, adapter=adapter)
        db_session.refresh(product)

        assert product.review_status == ReviewStatus.NEEDS_REVIEW.value
        assert "извещатель пожарный" in (product.review_reason or "")

    def test_single_meter_is_saved_and_linked(self, db_session, manufacturer):
        """Однозначный результат сохраняется автоматически и привязывается к модели."""

        product = Product(
            manufacturer_id=manufacturer.id, model_name="ТЕСТ-12-РУ-D17", article="test-12-ru-d17"
        )
        db_session.add(product)
        db_session.commit()

        task = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="ТЕСТ-12-РУ-D17",
            manufacturer_id=manufacturer.id,
            product_id=product.id,
            run_now=False,
        )
        si_code = f"61891-{uuid.uuid4().hex[:2]}"
        adapter = _StubFgis(
            [_meter(si_code, "ТЕСТ-12-РУ")],
            card_extras={"mpi_months": 192, "description_type_version": "4"},
        )

        outcome = fgis_catalog_sync._lookup_model(db_session, task, adapter=adapter)
        db_session.refresh(product)

        assert outcome.needs_review is False
        si_type = db_session.scalar(
            select(SiType).where(
                SiType.manufacturer_id == manufacturer.id, SiType.si_code == si_code
            )
        )
        assert si_type is not None
        assert si_type.mpi_months == 192
        # Проверка человеком по-прежнему обязательна (раздел 5.3 ТЗ) — автопоиск лишь
        # предлагает, а не подтверждает.
        assert si_type.verified_by_user is False
        assert product.si_type_id == si_type.id

    def test_existing_manual_link_is_not_overwritten(self, db_session, manufacturer):
        """Код СИ, выставленный человеком, автопоиск не перебивает."""

        manual_si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"99999-{uuid.uuid4().hex[:2]}",
            source=SiTypeSource.MANUAL.value,
            verified_by_user=True,
        )
        db_session.add(manual_si_type)
        db_session.commit()
        product = Product(
            manufacturer_id=manufacturer.id,
            model_name="ТЕСТ-12",
            article="test-12",
            si_type_id=manual_si_type.id,
        )
        db_session.add(product)
        db_session.commit()

        task = catalog_queue_service.enqueue(
            db_session,
            adapter_key=fgis_catalog_sync.ADAPTER_KEY,
            reason=CatalogQueueReason.MISSING_CATALOG_DATA,
            model_name="ТЕСТ-12",
            manufacturer_id=manufacturer.id,
            product_id=product.id,
            run_now=False,
        )
        fgis_catalog_sync._lookup_model(
            db_session, task, adapter=_StubFgis([_meter(f"12345-{uuid.uuid4().hex[:2]}", "ТЕСТ-12")])
        )
        db_session.refresh(product)

        assert product.si_type_id == manual_si_type.id


class TestRevalidation:
    def test_expired_certificate_raises_review_flag(self, db_session, manufacturer):
        """Истёкшее свидетельство об утверждении типа — повод остановить человека до подачи
        заявки, а не после."""

        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"61891-{uuid.uuid4().hex[:2]}",
            mit_uuid="uuid-1",
            source=SiTypeSource.AUTO_SEARCH.value,
            valid_to=date.today() - timedelta(days=1),
        )
        db_session.add(si_type)
        db_session.commit()

        outcome = fgis_catalog_sync._revalidate(db_session, si_type, adapter=_StubFgis([]))
        db_session.refresh(si_type)

        assert outcome.needs_review is True
        assert si_type.review_status == ReviewStatus.NEEDS_REVIEW.value
        assert "истекло" in (si_type.review_reason or "")

    def test_new_description_version_drops_stale_text(self, db_session, manufacturer):
        """Новая редакция «Описания типа» означает, что ранее извлечённый текст устарел:
        иначе сопоставление пойдёт по отменённой редакции."""

        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"61891-{uuid.uuid4().hex[:2]}",
            mit_uuid="uuid-2",
            source=SiTypeSource.AUTO_SEARCH.value,
            description_type_version="3",
            description_type_text="старый текст описания типа",
        )
        db_session.add(si_type)
        db_session.commit()

        adapter = _StubFgis([], card_extras={"description_type_version": "4"})
        outcome = fgis_catalog_sync._revalidate(db_session, si_type, adapter=adapter)
        db_session.refresh(si_type)

        assert si_type.description_type_version == "4"
        assert si_type.description_type_text is None
        assert "новая редакция" in outcome.message

    def test_revalidation_stamps_check_time(self, db_session, manufacturer):
        """`last_checked_at` — то, по чему очередь выбирает следующих кандидатов; без
        отметки одна и та же карточка проверялась бы каждый прогон."""

        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"61891-{uuid.uuid4().hex[:2]}",
            source=SiTypeSource.AUTO_SEARCH.value,
        )
        db_session.add(si_type)
        db_session.commit()
        assert si_type.last_checked_at is None

        fgis_catalog_sync._revalidate(db_session, si_type, adapter=_StubFgis([]))
        db_session.refresh(si_type)

        assert si_type.last_checked_at is not None


# --- Каталог МИРТЕК ---


class TestMirtekCatalogSync:
    @pytest.fixture()
    def mirtek(self, db_session) -> Manufacturer:
        item = db_session.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
        assert item is not None, "МИРТЕК должен быть в справочнике из сида"
        return item

    def _run(self, db_session, items, *, fail=None, details_map=None):
        details_map = details_map or {item.url: _details(item) for item in items}
        adapter = _StubMirtekSite(items, details_map, fail=fail)
        outcome = catalog_site_sync.sync_catalog(db_session, adapter=adapter, use_ai=False)
        return outcome, adapter

    def test_both_executions_saved_as_separate_records(self, db_session, mirtek):
        """Таганрог и Владивосток — отдельные записи справочника (критерий приёмки 3)."""

        suffix = uuid.uuid4().hex[:6]
        items = [
            _item(f"mirtek-12-ru-{suffix}", "МИРТЕК-12-РУ-D17", execution="Таганрог"),
            _item(f"mirtek-212-ru-{suffix}", "МИРТЕК-12-РУ-D17", execution="Владивосток"),
        ]
        outcome, _ = self._run(db_session, items)

        assert outcome.products_created == 2
        saved = db_session.scalars(
            select(Product).where(Product.source_url.in_([item.url for item in items]))
        ).all()
        assert {p.execution for p in saved} == {"Таганрог", "Владивосток"}
        assert {p.model_name for p in saved} == {"МИРТЕК-12-РУ-D17"}
        assert all(p.data_source == ProductDataSource.MANUFACTURER_SITE.value for p in saved)

    def test_repeat_run_updates_instead_of_duplicating(self, db_session, mirtek):
        """Критерий приёмки 7: повторный запуск не создаёт дублей.

        Второй прогон карточку не перезагружает (запись свежая, см. `FRESH_CARD_MAX_AGE`),
        поэтому она числится пропущенной; с `full_refresh` — обновлённой. В обоих случаях
        запись остаётся одна."""

        items = [_item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")]
        first, _ = self._run(db_session, items)
        second, _ = self._run(db_session, items)
        third = catalog_site_sync.sync_catalog(
            db_session,
            adapter=_StubMirtekSite(items, {i.url: _details(i) for i in items}),
            use_ai=False,
            full_refresh=True,
        )

        assert first.products_created == 1
        assert second.products_created == 0 and second.products_skipped == 1
        assert third.products_created == 0 and third.products_updated == 1
        assert (
            len(db_session.scalars(select(Product).where(Product.source_url == items[0].url)).all())
            == 1
        )

    def test_discontinued_mark_lands_in_status(self, db_session, mirtek):
        """Критерий приёмки 4."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-W2", discontinued=True)
        self._run(db_session, [item])

        product = db_session.scalar(select(Product).where(Product.source_url == item.url))
        assert product.status == ProductStatus.DISCONTINUED.value
        status_value = self._characteristic(db_session, product, "Основные характеристики", "Статус")
        assert status_value == "Снят с производства"

    def test_specifications_are_mapped_to_catalog_fields(self, db_session, mirtek):
        """Критерий приёмки 5: ключи сайта раскладываются по полям Приложения C через
        словарь синонимов («Базовый ток» → «Номинальный ток» и т.д.)."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._run(db_session, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._characteristic(db_session, product, "Электрические характеристики", "Номинальный ток") == "5 А"
        assert self._characteristic(db_session, product, "Электрические характеристики", "Класс точности") == "1/1"
        assert self._characteristic(db_session, product, "Конструктивные характеристики", "Срок службы") == "48 лет"
        # Количество фаз в таблице характеристик отсутствует — оно выводится из категории.
        assert self._characteristic(db_session, product, "Электрические характеристики", "Количество фаз") == "1"
        # Тип монтажа есть только на бейдже страницы категории.
        assert self._characteristic(db_session, product, "Конструктивные характеристики", "Тип монтажа") == "DIN-рейка"

    def test_older_card_generation_is_mapped_too(self, db_session, mirtek):
        """У моделей, выпущенных раньше (W2, W3, W6, SP2, D33), те же величины записаны
        другими словами — «Базовый (номинальный) ток» вместо «Базовый ток», «Максимальная
        сила тока» вместо «Максимальный ток», класс точности разнесён по двум ГОСТам.
        Выяснилось на живом обходе; без обоих наборов синонимов половина каталога осталась
        бы без электрических характеристик."""

        item = _item(f"mirtek-12-ru-w2-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-W2", discontinued=True)
        old_specs = {
            "Класс точности по ГОСТ 31819.21-2012": "1",
            "Класс точности по ГОСТ 31819.22-2012": "1",
            "Номинальное напряжение": "220 В или 230 В",
            "Базовый (номинальный) ток": "5 А, или 10 А",
            "Максимальная сила тока": "60 А, 80 А или 100 А",
            "Диапазон значений постоянной счётчика по активной электрической энергии": "от 800 до 3200 имп/(кВт·ч)",
            "Диапазон значений постоянной счётчика по реактивной электрической энергии": "от 800 до 3200 имп/(кВт·ч)",
            "Полная (активная) мощность, потребляемая цепью напряжения счётчика при номинальном "
            "напряжении, нормальной температуре, номинальной частоте, не превышает": "10 В·А (2 Вт)",
        }
        self._run(
            db_session,
            [item],
            details_map={item.url: _details(item, specs=old_specs)},
        )
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._characteristic(db_session, product, "Электрические характеристики", "Номинальный ток") == "5 А, или 10 А"
        assert self._characteristic(db_session, product, "Электрические характеристики", "Максимальный ток") == "60 А, 80 А или 100 А"
        assert self._characteristic(db_session, product, "Электрические характеристики", "Класс точности") == "1"
        assert self._characteristic(db_session, product, "Электрические характеристики", "Полная потребляемая мощность") == "10 В·А (2 Вт)"

    def test_two_site_keys_for_one_catalog_field_do_not_overwrite_each_other(self, db_session, mirtek):
        """Класс точности по ГОСТ 31819.21 (активная) и 31819.22 (реактивная) легли бы в одно
        поле Приложения C. Второй не затирает первый молча — он уходит в резервное поле под
        своим настоящим именем, иначе значение зависело бы от порядка строк в таблице сайта."""

        item = _item(f"mirtek-12-ru-w2-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-W2")
        self._run(
            db_session,
            [item],
            details_map={
                item.url: _details(
                    item,
                    specs={
                        "Класс точности по ГОСТ 31819.21-2012": "1",
                        "Класс точности по ГОСТ 31819.22-2012": "2",
                    },
                )
            },
        )
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._characteristic(db_session, product, "Электрические характеристики", "Класс точности") == "1"
        assert product.extra_specifications["Класс точности по ГОСТ 31819.22-2012"] == "2"

    def test_unmapped_keys_go_to_reserve_field_not_to_trash(self, db_session, mirtek):
        """Набор полей на сайте шире Приложения C и меняется без предупреждения — потеря
        значения обнаружилась бы только когда по нему придёт требование тендера."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._run(db_session, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert (
            "Полная мощность, потребляемая каждой цепью тока при базовом токе, не превышает"
            in product.extra_specifications
        )

    def test_document_links_are_saved(self, db_session, mirtek):
        """Критерий приёмки 6. «Сертификат об утверждении и описание типа» — один
        комбинированный PDF, он же заполняет оба поля справочника."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._run(db_session, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        certificate = self._characteristic(db_session, product, "Документация", "Ссылка на сертификат")
        description = self._characteristic(db_session, product, "Документация", "Ссылка на описание типа")
        assert certificate == description == "https://mirtekgroup.com/download/2724"
        assert self._characteristic(db_session, product, "Документация", "Ссылка на декларацию") == "https://mirtekgroup.com/download/2728"
        # Руководств на карточке два (на прибор и на модуль связи) — берётся первое.
        assert self._characteristic(db_session, product, "Документация", "Ссылка на руководство") == "https://mirtekgroup.com/download/4739"
        assert self._characteristic(db_session, product, "Документация", "Ссылка на каталог") == item.url

    def test_broken_card_does_not_stop_the_rest(self, db_session, mirtek):
        """Критерий приёмки 8."""

        suffix = uuid.uuid4().hex[:6]
        broken = _item(f"mirtek-12-ru-bad-{suffix}", "МИРТЕК-12-РУ-BAD")
        good = _item(f"mirtek-12-ru-ok-{suffix}", "МИРТЕК-12-РУ-OK")
        details_map = {good.url: _details(good)}
        adapter = _StubMirtekSite([broken, good], details_map, fail={broken.url})

        outcome = catalog_site_sync.sync_catalog(db_session, adapter=adapter, use_ai=False)

        assert outcome.cards_failed == 1
        assert outcome.products_created == 1
        assert db_session.scalar(select(Product).where(Product.source_url == good.url)) is not None
        assert db_session.scalar(select(Product).where(Product.source_url == broken.url)) is None

    def test_disappeared_model_is_flagged_not_deleted(self, db_session, mirtek):
        """п.2.4 задания: модель, пропавшая со страницы категории, не удаляется — решение
        за пользователем."""

        suffix = uuid.uuid4().hex[:6]
        first = _item(f"mirtek-12-ru-a-{suffix}", "МИРТЕК-12-РУ-A")
        second = _item(f"mirtek-12-ru-b-{suffix}", "МИРТЕК-12-РУ-B")
        self._run(db_session, [first, second])

        # Второй обход: модель B пропала с сайта.
        outcome, _ = self._run(db_session, [first])

        gone = db_session.scalar(select(Product).where(Product.source_url == second.url))
        assert gone is not None, "запись не должна удаляться автоматически"
        assert gone.review_status == ReviewStatus.NEEDS_REVIEW.value
        assert "пропала" in (gone.review_reason or "")
        assert outcome.marked_for_review >= 1

    def test_returning_model_clears_the_flag(self, db_session, mirtek):
        """Модель вернулась на сайт — пометка снимается, иначе она осталась бы навсегда."""

        suffix = uuid.uuid4().hex[:6]
        first = _item(f"mirtek-12-ru-a-{suffix}", "МИРТЕК-12-РУ-A")
        second = _item(f"mirtek-12-ru-b-{suffix}", "МИРТЕК-12-РУ-B")
        self._run(db_session, [first, second])
        self._run(db_session, [first])
        self._run(db_session, [first, second])

        returned = db_session.scalar(select(Product).where(Product.source_url == second.url))
        assert returned.review_status == ReviewStatus.OK.value
        assert returned.review_reason is None

    def test_manual_values_are_not_overwritten(self, db_session, mirtek):
        """Раздел 5.3 ТЗ: ручной ввод имеет приоритет и не перетирается автозаполнением."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._run(db_session, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        characteristic = db_session.scalar(
            select(ProductCharacteristic).where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.field_name == "Номинальный ток",
            )
        )
        characteristic.value = "выправлено человеком"
        characteristic.source = CharacteristicSource.MANUAL_ENTRY.value
        characteristic.verified_by_user = True
        db_session.commit()

        self._run(db_session, [item])
        db_session.refresh(characteristic)

        assert characteristic.value == "выправлено человеком"

    @staticmethod
    def _characteristic(db_session, product: Product, group: str, field: str) -> str | None:
        row = db_session.scalar(
            select(ProductCharacteristic).where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.group_name == group,
                ProductCharacteristic.field_name == field,
            )
        )
        return row.value if row is not None else None


class TestCompetitorCatalogs:
    """Каталоги конкурентов сохраняются тем же сервисом, что и МИРТЕК: это и есть смысл
    общей логики — по неполному каталогу конкурента процент соответствия занижался бы
    не из-за свойств прибора, а из-за пустых строк справочника."""

    @pytest.fixture()
    def energomera(self, db_session) -> Manufacturer:
        item = db_session.scalar(
            select(Manufacturer).where(Manufacturer.legal_name == "АО «Энергомера»")
        )
        assert item is not None, "Энергомера должна быть в справочнике из сида"
        return item

    def _competitor_item(self, article: str, model: str, *, discontinued: bool = False) -> CatalogItem:
        return CatalogItem(
            model_name=model,
            article=article,
            execution=None,
            url=f"https://www.energomera.ru/ru/products/meters/{article}",
            category="https://www.energomera.ru/ru/products/meters/single-phase",
            device_type="Однофазный счётчик электроэнергии",
            discontinued=discontinued,
            mounting_badge=None,
        )

    def _competitor_details(self, item: CatalogItem) -> CatalogProductDetails:
        # Ключи — дословно как на карточке Энергомеры (разведка 04.09.2026).
        return CatalogProductDetails(
            url=item.url,
            model_name=item.model_name,
            description="Однофазный электросчетчик серии «СЕ». Устанавливается на din-рейку.",
            features_text=None,
            specifications={
                "Фазность": "Однофазный",
                "Класс точности": "1",
                "Номинальное напряжение": "230 В",
                "Базовый (максимальный) ток": "5 (60) А",
                "Стартовый ток (чувствительность)": "10 мА",
                "Способ крепления": "DIN-рейка",
                "Количество датчиков тока": "1 шт",
            },
            documents=[
                DocumentLink(group="", title="Описание типа", url="https://www.energomera.ru/documentations/product/ce101_ot.pdf"),
                DocumentLink(group="", title="Декларация о соответствии ЕАЭС", url="https://www.energomera.ru/documentations/product/ce101_ds.pdf"),
                DocumentLink(group="", title="Руководство по эксплуатации", url="https://www.energomera.ru/documentations/product/ce101_re.pdf"),
            ],
            symbol_legend=None,
        )

    def _run(self, db_session, manufacturer, items, *, fail=None):
        details = {i.url: self._competitor_details(i) for i in items}
        adapter = _StubMirtekSite(items, details, fail=fail)
        return catalog_site_sync.sync_catalog(
            db_session, manufacturer=manufacturer, adapter=adapter, use_ai=False
        )

    def test_competitor_products_are_saved_with_their_manufacturer(self, db_session, energomera):
        item = self._competitor_item(f"ce101-r5-{uuid.uuid4().hex[:6]}", "CE101 R5 145 M6")
        outcome = self._run(db_session, energomera, [item])

        assert outcome.products_created == 1
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))
        assert product.manufacturer_id == energomera.id
        assert product.data_source == ProductDataSource.MANUFACTURER_SITE.value

    def test_manufacturer_and_brand_come_from_the_directory(self, db_session, energomera):
        """Раньше в эти поля писалась константа «МИРТЕК» — для конкурента это была бы
        прямая ложь в справочнике."""

        item = self._competitor_item(f"ce101-r5-{uuid.uuid4().hex[:6]}", "CE101 R5 145 M6")
        self._run(db_session, energomera, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._char(db_session, product, "Основные характеристики", "Производитель") == "АО «Энергомера»"
        assert self._char(db_session, product, "Основные характеристики", "Бренд") == "Энергомера"

    def test_competitor_specification_keys_are_mapped(self, db_session, energomera):
        """У Энергомеры своя манера формулировок — «Базовый (максимальный) ток»,
        «Стартовый ток (чувствительность)», «Способ крепления»."""

        item = self._competitor_item(f"ce101-r5-{uuid.uuid4().hex[:6]}", "CE101 R5 145 M6")
        self._run(db_session, energomera, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._char(db_session, product, "Электрические характеристики", "Номинальный ток") == "5 (60) А"
        assert self._char(db_session, product, "Электрические характеристики", "Стартовый ток") == "10 мА"
        # «Фазность: Однофазный» приводится к числу: у МИРТЕК то же свойство пишется как
        # «1», и два написания одного свойства несравнимы при сверке с требованием тендера.
        assert self._char(db_session, product, "Электрические характеристики", "Количество фаз") == "1"
        assert self._char(db_session, product, "Конструктивные характеристики", "Тип монтажа") == "DIN-рейка"
        # Неизвестный ключ не теряется.
        assert "Количество датчиков тока" in product.extra_specifications

    def test_documents_are_mapped_by_title(self, db_session, energomera):
        """Групп документов, как у МИРТЕК, на этих сайтах нет — назначение определяется
        по названию документа."""

        item = self._competitor_item(f"ce101-r5-{uuid.uuid4().hex[:6]}", "CE101 R5 145 M6")
        self._run(db_session, energomera, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert self._char(db_session, product, "Документация", "Ссылка на описание типа").endswith("ce101_ot.pdf")
        assert self._char(db_session, product, "Документация", "Ссылка на декларацию").endswith("ce101_ds.pdf")
        assert self._char(db_session, product, "Документация", "Ссылка на руководство").endswith("ce101_re.pdf")

    def test_archive_category_sets_discontinued_status(self, db_session, energomera):
        """У Энергомеры «снятые с серийного производства» — отдельный раздел каталога."""

        item = self._competitor_item(f"ce200-r5-{uuid.uuid4().hex[:6]}", "CE200 R5.1 145", discontinued=True)
        self._run(db_session, energomera, [item])
        product = db_session.scalar(select(Product).where(Product.source_url == item.url))

        assert product.status == ProductStatus.DISCONTINUED.value
        assert self._char(db_session, product, "Основные характеристики", "Статус") == "Снят с производства"

    def test_repeat_run_is_idempotent_for_competitors(self, db_session, energomera):
        item = self._competitor_item(f"ce101-r5-{uuid.uuid4().hex[:6]}", "CE101 R5 145 M6")
        first = self._run(db_session, energomera, [item])
        second = self._run(db_session, energomera, [item])

        assert first.products_created == 1
        # Второй прогон карточку не перезагружает — запись свежая; дубля при этом нет.
        assert second.products_created == 0 and second.products_skipped == 1

    def test_mirtek_products_are_untouched_by_competitor_crawl(self, db_session, energomera):
        """Обход одного производителя не должен помечать «пропавшими» модели другого —
        они просто не встречаются на чужом сайте."""

        mirtek = db_session.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
        mirtek_product = Product(
            manufacturer_id=mirtek.id,
            model_name="МИРТЕК-12-РУ-D17",
            article=f"mirtek-12-ru-{uuid.uuid4().hex[:6]}",
            source_url=f"https://mirtekgroup.com/produkciya/odnofaznye-schyotchiki/x{uuid.uuid4().hex[:6]}",
        )
        db_session.add(mirtek_product)
        db_session.commit()

        self._run(db_session, energomera, [self._competitor_item(f"ce101-{uuid.uuid4().hex[:6]}", "CE101")])
        db_session.refresh(mirtek_product)

        assert mirtek_product.review_status == ReviewStatus.OK.value

    @staticmethod
    def _char(db_session, product: Product, group: str, field: str) -> str | None:
        row = db_session.scalar(
            select(ProductCharacteristic).where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.group_name == group,
                ProductCharacteristic.field_name == field,
            )
        )
        return row.value if row is not None else None


class TestIncrementalCrawl:
    """Повторный обход не перезагружает карточки, которые не менялись.

    Сайты производителей отвечают по 2-6 секунд на страницу, и каталог в двести позиций
    обходится минутами — при том что между еженедельными прогонами он почти не меняется."""

    @pytest.fixture()
    def mirtek(self, db_session) -> Manufacturer:
        item = db_session.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
        assert item is not None
        return item

    def _sync(self, db_session, items, **kwargs):
        details = {i.url: _details(i) for i in items}
        adapter = _StubMirtekSite(items, details)
        outcome = catalog_site_sync.sync_catalog(
            db_session, adapter=adapter, use_ai=False, **kwargs
        )
        return outcome, adapter

    def test_second_run_does_not_refetch_fresh_cards(self, db_session, mirtek):
        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")

        first, first_adapter = self._sync(db_session, [item])
        second, second_adapter = self._sync(db_session, [item])

        assert first.products_created == 1 and first_adapter.requested == [item.url]
        assert second.products_skipped == 1
        assert second_adapter.requested == [], "карточка не должна перезапрашиваться"

    def test_full_refresh_reloads_everything(self, db_session, mirtek):
        """После правки профиля сайта или словаря синонимов старые записи надо перечитать."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._sync(db_session, [item])

        outcome, adapter = self._sync(db_session, [item], full_refresh=True)

        assert outcome.products_skipped == 0
        assert adapter.requested == [item.url]

    def test_status_still_updates_for_skipped_cards(self, db_session, mirtek):
        """Пропуск касается только карточки товара: снятие с производства видно на странице
        категории и стоит ноль запросов, поэтому статус обновляется всё равно."""

        article = f"mirtek-12-ru-{uuid.uuid4().hex[:6]}"
        self._sync(db_session, [_item(article, "МИРТЕК-12-РУ-D17")])

        discontinued = _item(article, "МИРТЕК-12-РУ-D17", discontinued=True)
        outcome, adapter = self._sync(db_session, [discontinued])

        assert outcome.products_skipped == 1
        assert adapter.requested == []
        product = db_session.scalar(select(Product).where(Product.source_url == discontinued.url))
        assert product.status == ProductStatus.DISCONTINUED.value

    def test_record_without_characteristics_is_reloaded(self, db_session, mirtek):
        """Позиция, у которой прошлый обход сорвался на загрузке карточки, «свежая» по
        времени, но пустая. Пропустить её значило бы законсервировать пустую строку."""

        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._sync(db_session, [item])

        product = db_session.scalar(select(Product).where(Product.source_url == item.url))
        db_session.execute(
            ProductCharacteristic.__table__.delete().where(
                ProductCharacteristic.product_id == product.id
            )
        )
        db_session.commit()

        outcome, adapter = self._sync(db_session, [item])

        assert outcome.products_skipped == 0
        assert adapter.requested == [item.url]

    def test_stale_record_is_reloaded(self, db_session, mirtek):
        item = _item(f"mirtek-12-ru-{uuid.uuid4().hex[:6]}", "МИРТЕК-12-РУ-D17")
        self._sync(db_session, [item])

        product = db_session.scalar(select(Product).where(Product.source_url == item.url))
        product.last_seen_at = datetime.now(timezone.utc) - (
            catalog_site_sync.FRESH_CARD_MAX_AGE + timedelta(hours=1)
        )
        db_session.commit()

        outcome, adapter = self._sync(db_session, [item])

        assert outcome.products_skipped == 0
        assert adapter.requested == [item.url]
