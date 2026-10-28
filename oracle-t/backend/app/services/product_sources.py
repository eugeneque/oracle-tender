"""Источники данных модели: что опрошено, что нет и почему (замечание 13.10.2026).

Тендерный отдел видел пустые П18–П28 и «СПОДЭС: —» у CE210 и спрашивал, почему ИИ «не
заполняет». Причина была не в модели ИИ: руководство Энергомеры лежит в разделе сайта,
закрытом для роботов (`robots.txt`), и обход его не скачивает. Карточка должна говорить это
прямо — по каждому источнику:

* что с ним сейчас (`ok` — прочитан; `pending` — есть, но не прочитан; `blocked` — сайт
  запрещает скачивание; `missing` — источника нет; `error` — последний опрос не удался);
* что сделать (`actions`) и **участвует ли в этом ИИ** (`ai`): по кнопке с моделью ИИ
  пользователь должен знать, что результат нужно проверить и что это стоит запросов.

Ничего не хранится — собирается из справочника на каждый запрос.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.manufacturer import (
    CharacteristicSource,
    Product,
    ProductCharacteristic,
    ProductDataSource,
    SiType,
)
from app.services import product_manual_ingest, registry_polling, registry_records_service

STATE_OK = "ok"
STATE_PENDING = "pending"
STATE_BLOCKED = "blocked"
STATE_MISSING = "missing"
STATE_ERROR = "error"
STATE_RUNNING = "running"

# Действия — ключи, по которым интерфейс выбирает кнопку.
ACTION_AUTOFILL = "autofill"
ACTION_EXTRACT_FGIS = "extract_fgis"
ACTION_READ_MANUAL = "read_manual"
ACTION_FIND_DOCS = "find_docs"
ACTION_UPLOAD_MANUAL = "upload_manual"
ACTION_POLL_REGISTRY = "poll_registry"
ACTION_UPLOAD_REGISTRY = "upload_registry"


@dataclass
class SourceAction:
    key: str
    label: str
    ai: bool = False


@dataclass
class DataSource:
    key: str
    label: str
    state: str
    detail: str
    url: str | None = None
    actions: list[SourceAction] = field(default_factory=list)
    registry: str | None = None


def _values(db: Session, product: Product) -> dict[tuple[str, str], ProductCharacteristic]:
    return {
        (item.group_name, item.field_name): item
        for item in db.scalars(select(ProductCharacteristic).where(ProductCharacteristic.product_id == product.id))
    }


def _count_by_source(db: Session, product: Product, source: str, *, ai_only: bool = False) -> int:
    query = select(func.count(ProductCharacteristic.id)).where(
        ProductCharacteristic.product_id == product.id, ProductCharacteristic.source == source
    )
    if ai_only:
        query = query.where(ProductCharacteristic.ai_extracted.is_(True))
    return db.scalar(query) or 0


def _autofill() -> SourceAction:
    # Обход сайта сам по себе без ИИ, но сквозной опрос производителя вторым проходом
    # читает «Описания типа» и руководства моделью — кнопку честнее пометить.
    return SourceAction(ACTION_AUTOFILL, "Опросить производителя", ai=True)


def _site(product: Product, values: dict) -> DataSource:
    label = "Сайт производителя"
    if product.source_url:
        refreshed = values.get(("Документация", "Дата обновления документации"))
        detail = "Карточка модели на сайте производителя обойдена"
        if refreshed and refreshed.value:
            detail += f" ({refreshed.value})"
        return DataSource("site", label, STATE_OK, detail + ". Значения взяты из таблицы сайта, без ИИ.", product.source_url)
    if product.data_source == ProductDataSource.FGIS.value:
        return DataSource(
            "site",
            label,
            STATE_MISSING,
            "Исполнения нет в каталоге на сайте — модель заведена из реестра ФГИС. Повторный опрос "
            "производителя найдёт её, когда она появится на сайте.",
            actions=[_autofill()],
        )
    return DataSource(
        "site",
        label,
        STATE_PENDING,
        "Карточка модели на сайте ещё не обойдена.",
        actions=[_autofill()],
    )


def _fgis(db: Session, product: Product) -> DataSource:
    label = "«Описание типа» (ФГИС)"
    if product.si_type_id is None:
        return DataSource(
            "fgis",
            label,
            STATE_MISSING,
            "Код СИ не привязан — «Описание типа» неизвестно. Привяжите код на шаге «Коды СИ».",
        )
    si_type = db.get(SiType, product.si_type_id)
    url = (si_type.description_type_mirror_url or si_type.description_type_url) if si_type else None
    read = _count_by_source(db, product, CharacteristicSource.FGIS_DESCRIPTION_TYPE.value, ai_only=True)
    if read:
        version = (
            f", редакция {si_type.description_type_extracted_version}"
            if si_type and si_type.description_type_extracted_version
            else ""
        )
        return DataSource(
            "fgis",
            label,
            STATE_OK,
            f"Прочитано ИИ{version}: {read} значений. По рангу ниже сайта и руководства — "
            "заполняет только то, чего в них нет.",
            url,
            [SourceAction(ACTION_EXTRACT_FGIS, "Перечитать", ai=True)],
        )
    if not url:
        return DataSource("fgis", label, STATE_MISSING, "У типа в реестре нет ссылки на документ.")
    return DataSource(
        "fgis",
        label,
        STATE_PENDING,
        "Документ есть, но ещё не прочитан.",
        url,
        [SourceAction(ACTION_EXTRACT_FGIS, "Прочитать", ai=True)],
    )


def _manual(db: Session, product: Product, values: dict) -> DataSource:
    label = "Руководство по эксплуатации"
    link = values.get(("Документация", "Ссылка на руководство"))
    url = link.value if link and (link.value or "").startswith("http") else None
    read = _count_by_source(db, product, CharacteristicSource.USER_MANUAL.value)
    upload = SourceAction(ACTION_UPLOAD_MANUAL, "Загрузить PDF", ai=True)
    if read:
        return DataSource(
            "manual",
            label,
            STATE_OK,
            f"Прочитано ИИ: {read} значений.",
            url,
            [SourceAction(ACTION_READ_MANUAL, "Перечитать", ai=True), upload] if url else [upload],
        )
    if url is None:
        return DataSource(
            "manual",
            label,
            STATE_MISSING,
            "Ссылки на руководство нет: на сайте производителя документ не найден. Найдите его "
            "поиском или загрузите PDF — из руководства берутся П13–П34 (винты, крышки, "
            "батарейка, протоколы).",
            actions=[SourceAction(ACTION_FIND_DOCS, "Найти в интернете", ai=True), upload],
        )
    if not product_manual_ingest.robots_allows(url):
        return DataSource(
            "manual",
            label,
            STATE_BLOCKED,
            "Сайт производителя запрещает роботам скачивать документацию (robots.txt), поэтому "
            "автоматически руководство не читается. Откройте ссылку, сохраните PDF и загрузите "
            "его сюда — из него заполнятся П13–П34.",
            url,
            [upload],
        )
    return DataSource(
        "manual",
        label,
        STATE_PENDING,
        "Руководство найдено, но ещё не прочитано — оно в очереди еженедельного обучения "
        "каталога. Можно прочитать сейчас.",
        url,
        [SourceAction(ACTION_READ_MANUAL, "Прочитать", ai=True), upload],
    )


def _registries(db: Session, product: Product) -> list[DataSource]:
    sources: list[DataSource] = []
    for row in registry_records_service.product_overview(db, product):
        if not row["pollable"]:
            continue
        key = "gisp" if row["registry"] == "industrial_products" else "zak"
        record = row["record"]
        last = row["last_poll"]
        last_ok = row["last_successful_poll"]
        # Если реестр серверу недоступен, файл скачивается из браузера: у ГИСП — XLSX
        # «Скачать только действующие», у Россетей — PDF «Раздел II» (28.10.2026).
        upload_label = "Загрузить XLSX из ГИСП" if key == "gisp" else "Загрузить PDF перечня"
        actions = [
            SourceAction(ACTION_POLL_REGISTRY, "Опросить реестр"),
            SourceAction(ACTION_UPLOAD_REGISTRY, upload_label),
        ]
        if last and last["status"] == STATE_RUNNING:
            state, detail = STATE_RUNNING, "Идёт опрос реестра…"
        elif record:
            how = "найдено автоматически" if record.get("auto_found_at") else "внесено вручную"
            state, detail = STATE_OK, f"{record['summary']} — {how}."
        elif last_ok:
            when = last_ok["finished_at"] or last_ok["started_at"]
            state = STATE_PENDING
            detail = (
                f"Реестр опрошен {when:%d.%m.%Y}: по обозначению модель не найдена. Это не "
                "доказывает, что записи нет, — сверьте вручную по ссылке."
            )
        elif last and last["status"] == STATE_ERROR:
            state, detail = STATE_ERROR, f"Опрос не удался: {last['message']}"
        else:
            state, detail = STATE_PENDING, "Реестр ещё не опрашивался."
        if key == "gisp":
            detail = f"{detail} {registry_polling.gisp_timing_hint()}"
        sources.append(DataSource(key, row["label"], state, detail, row["url"], actions, row["registry"]))
    return sources


def product_sources(db: Session, product: Product) -> list[DataSource]:
    values = _values(db, product)
    return [
        _site(product, values),
        _fgis(db, product),
        _manual(db, product, values),
        *_registries(db, product),
    ]
