"""Интеграция приборов в ПО верхнего уровня (замечание тестировщика 16.09.2026).

Цепочка из трёх звеньев, каждое хранится в БД и проверяется отдельно:

1. **Чтение списков** (`sync_platform`) — семь площадок из `app/adapters/upper_software.py`
   читаются раз в неделю и по кнопке; записи кладутся в `upper_software_devices` как на
   сайте плюс результат разбора. Повторное чтение идемпотентно по отпечатку строки;
   пропавшие записи не удаляются, а перестают получать `last_seen_at`.
2. **Связь со справочником** (`link_devices`) — у каждой записи определяется наш
   производитель и модели, которые она покрывает. Три признака по убыванию надёжности:
   номер ГРСИ (совпал с типом СИ справочника — сомнений нет), название производителя на
   сайте, бренд в обозначении прибора (у Энергосферы и ЛЭРС производитель не назван вовсе).
   Модели — префиксным сравнением обозначений в тех же нормализациях, что и привязка к
   типам СИ (`si_type_linking.designation_keys`): «МИРТЕК-12-РУ» в списке покрывает
   «МИРТЕК-12-РУ-D1», «…-W3», «…-SP3» в каталоге. Граница обязательна: «МИРТЕК-1» —
   другой тип и не должен покрывать «МИРТЕК-12-РУ-*».
3. **Факты для сопоставления** (`software_facts`) — когда в требованиях закупки названо ПО
   верхнего уровня, в карточку производителя добавляются строки `[software]`: что из его
   моделей есть в списке этого ПО, либо прямо — что в списке его приборов **нет**.
   Второе принципиально: список официальный и проверенный, и отсутствие в нём — это
   «не соответствует», а не «нет данных». Именно об этом просил тестировщик: система
   должна проверять, интегрирован прибор или нет.

Поддержка «любых счётчиков по протоколу СПОДЭС» (АльфаЦЕНТР, Энергосфера) — отдельный
статус `protocol_only`: производитель в списке не назван, но заявлена поддержка по
протоколу, а у него есть модели с СПОДЭС. В матрице это «частично», не «соответствует».
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.upper_software import PLATFORMS, SupportedDevice, fetch_platform
from app.core.timezones import now_utc, to_msk
from app.models.analysis import Requirement
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, SiType
from app.models.source import Source, SourceType
from app.models.tender import Tender
from app.models.upper_software import UpperSoftwareDevice, UpperSoftwareProductLink
from app.models.user import User
from app.services import catalog_queue_service
from app.services.audit import log_action
from app.services.catalog_queue_service import TaskOutcome
from app.services.si_type_linking import designation_keys

COMPONENT = "upper_software"

# Сколько моделей из списка ПО называть в карточке производителя для модели. Больше не
# нужно: вердикт «есть в списке» подтверждает и одна модель, а ради полноты списка у
# Пирамиды по МИРТЕК двадцать строк.
MAX_DEVICES_IN_FACT = 8

# Бренд справочника (`Manufacturer.brand_name` в нижнем регистре) → как этого
# производителя называют на сайтах ПО и как узнаётся его прибор по обозначению.
# Первый кортеж — шаблоны для названия производителя, второй — для обозначения прибора.
# Раздельно, потому что «МИР» как слово в названии («НПО МИР») и «МИР С-04» в
# обозначении — разные шаблоны, а «Меркурий» встречается только в обозначении.
_MANUFACTURER_PATTERNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "миртек": ((r"миртек", r"mirtek"), (r"^миртек", r"^mirtek")),
    "нартис": ((r"нартис", r"nartis"), (r"^нартис", r"^nartis")),
    "энергомера": (
        (r"энергомера", r"energomera"),
        (r"^(?:энергомера\s+)?(?:ce|се|цэ)\s?-?\d{3,4}\b",),
    ),
    "мир": ((r"нпо\s*[«\"]?\s*мир\b",), (r"^мир\s*[сc]-?\d",)),
    "waviot": ((r"вавиот", r"waviot", r"телематические решения"), (r"^фобос", r"^fobos")),
    "пульсар": ((r"тепловодохран", r"pulsar"), (r"^пульсар", r"^pulsar")),
    "инкотекс": ((r"инкотекс", r"incotex"), (r"^меркурий", r"^mercury")),
    "промэнерго": ((r"промэнерго", r"promenergo"), (r"^i-?prom",)),
    "кпз": ((r"\bкпз\b", r"калужский приборостроительный"), (r"^[mм]2[mм]-?\d",)),
    "тайпит": ((r"тайпит", r"taipit"), (r"^нева\b", r"^heba\b", r"^neva\b")),
    "рим": ((r"радио и микроэлектроника", r"^(?:ао|зао)?\s*[«\"]?рим[»\"]?$"), (r"^рим\b", r"^рим-?\d", r"^rim\b")),
    "милур": ((r"милур", r"milur"), (r"^милур", r"^milur")),
    "ротек": ((r"ротек", r"rotek"), (r"^(?:ротек\s+)?ртм-?\d", r"^rotek")),
}

# Названия производителей на сайтах ПО, которые похожи на наши, но наши не являются:
# Фанипольский завод — белорусская площадка Энергомеры со своими моделями (CE208BY),
# которых в каталоге energomera.ru нет.
_MANUFACTURER_EXCLUSIONS = (r"фанипол",)

# Слово-бренд перед обозначением («Энергомера CE301-R33», «РОТЕК РТМ-03»): в каталоге
# модель записана без него, и для сравнения строится второй ключ без первого слова.
_BRAND_WORDS = frozenset(
    {"энергомера", "energomera", "ротек", "rotek", "миртек", "нартис", "милур", "тайпит",
     "инкотекс", "промэнерго", "пульсар", "счетчик", "счётчик"}
)

# Обозначение короче трёх символов после нормализации («A1», «A3» у АльфаЦЕНТР) —
# слишком общее для префиксного сравнения.
MIN_KEY_LENGTH = 3

# Признаки требования об интеграции без названия конкретного ПО: тогда в карточку
# кладутся факты по всем площадкам, чтобы модель видела, где прибор поддержан.
# «Интегрированы в …», «интеграция с …», «ПО верхнего уровня» — достаточно сами по себе
# (предлог обязателен: «интегрированный модем» — про модем, а не про ПО); аббревиатуры
# систем (АСКУЭ, ИСУЭ, ИВК) — только рядом со словом о совместимости: «шкаф УСПД АИИС КУЭ»
# или «заменить блоки питания УСПД» — не требование об интеграции прибора.
_INTEGRATION_RE = re.compile(
    r"верхнего уровня|интегрирован\w*\s+(?:в|с|со)\s|интеграци\w*\s+(?:в|с|со)\s", re.IGNORECASE
)
_SYSTEM_RE = re.compile(r"\bаскуэ\b|\bаиис\b|\bисуэ?\b|\bивкэ?\b", re.IGNORECASE)
_COMPATIBILITY_RE = re.compile(r"совмест|поддерж|подключ|передач|обмен|опрос|интегр", re.IGNORECASE)

# Разделы списков, не имеющие отношения к электросчётчикам: у Энергосферы и ЛЭРС в одном
# списке стоят и тепловычислители, и расходомеры, и счётчики газа. Запись «Пульсар-2М»
# из раздела расходомеров не подтверждает интеграцию электросчётчика того же
# производителя, и в сводку не идёт.
_FOREIGN_SECTION_RE = re.compile(r"расходомер|тепло|газ|вод[ао]?счет|пар[ао]?\b", re.IGNORECASE)
_ELECTRIC_RE = re.compile(r"электр", re.IGNORECASE)

_ALIAS_RES: dict[str, list[re.Pattern[str]]] = {
    key: [re.compile(alias, re.IGNORECASE) for alias in profile.aliases]
    for key, profile in PLATFORMS.items()
}


@dataclass
class SyncOutcome:
    adapter_key: str
    devices_read: int = 0
    created: int = 0
    updated: int = 0
    disappeared: int = 0
    with_manufacturer: int = 0
    products_linked: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"прочитано {self.devices_read}, новых {self.created}, обновлено {self.updated}, "
            f"пропало с сайта {self.disappeared}, с производителем из справочника "
            f"{self.with_manufacturer}, связей с моделями {self.products_linked}"
        )


@dataclass
class PlatformSupport:
    """Как одна площадка относится к одному производителю."""

    key: str
    name: str
    vendor: str
    url: str
    # `supported` — модели производителя есть в списке; `not_listed` — список прочитан,
    # производителя в нём нет; `protocol_only` — в списке нет, но заявлена поддержка
    # любых счётчиков по протоколу, а у производителя такие модели есть; `not_synced` —
    # список ещё не читали, судить не о чем.
    status: str
    last_synced_at: datetime | None = None
    devices: list[dict] = field(default_factory=list)
    note: str | None = None


# ------------------------------------------------------------------------------ источники


def resolve_source(db: Session, adapter_key: str) -> Source | None:
    if adapter_key not in PLATFORMS:
        return None
    return db.scalar(
        select(Source).where(
            Source.type == SourceType.UPPER_SOFTWARE.value, Source.adapter_key == adapter_key
        )
    )


def list_platforms(db: Session) -> list[dict]:
    """Площадки с состоянием списка: сколько записей, сколько из них — по нашим
    производителям, когда читали в последний раз, доступен ли сайт."""

    rows: list[dict] = []
    for key, profile in PLATFORMS.items():
        source = resolve_source(db, key)
        total = matched = 0
        if source is not None:
            total = db.scalar(
                select(func.count(UpperSoftwareDevice.id)).where(
                    UpperSoftwareDevice.source_id == source.id
                )
            ) or 0
            matched = db.scalar(
                select(func.count(UpperSoftwareDevice.id)).where(
                    UpperSoftwareDevice.source_id == source.id,
                    UpperSoftwareDevice.manufacturer_id.is_not(None),
                )
            ) or 0
        rows.append(
            {
                "adapter_key": key,
                "name": profile.name,
                "vendor": profile.vendor,
                "url": profile.url,
                "source_id": source.id if source else None,
                "devices_total": total,
                "devices_matched": matched,
                "last_synced_at": source.last_polled_at if source else None,
                "availability_status": source.availability_status if source else None,
            }
        )
    return rows


# ------------------------------------------------------------------------------- чтение


def _fingerprint(device: SupportedDevice) -> str:
    return hashlib.sha1(f"{device.section}|{device.device_raw}".encode()).hexdigest()


def sync_platform(
    db: Session,
    adapter_key: str,
    *,
    actor: User | None = None,
    devices: list[SupportedDevice] | None = None,
) -> SyncOutcome:
    """Читает список одной площадки и связывает его со справочником.

    `devices` — уже прочитанные записи (тесты и отладка разбора); без них список
    скачивается адаптером. Сетевая ошибка не глотается: очередь повторит задачу, а
    плановый прогон по всем площадкам изолирует её сам."""

    outcome = SyncOutcome(adapter_key=adapter_key)
    profile = PLATFORMS.get(adapter_key)
    source = resolve_source(db, adapter_key)
    if profile is None or source is None:
        outcome.errors.append(f"Площадка «{adapter_key}» не заведена в источниках")
        return outcome

    if devices is None:
        devices = fetch_platform(profile)
    outcome.devices_read = len(devices)
    now = now_utc()

    existing = {
        row.fingerprint: row
        for row in db.scalars(
            select(UpperSoftwareDevice).where(UpperSoftwareDevice.source_id == source.id)
        )
    }
    seen: set[str] = set()
    for device in devices:
        fingerprint = _fingerprint(device)
        if fingerprint in seen:
            continue  # сайт повторяет строку (у Энфорса «Милур 307» дважды)
        seen.add(fingerprint)
        row = existing.get(fingerprint)
        if row is None:
            row = UpperSoftwareDevice(source_id=source.id, fingerprint=fingerprint)
            db.add(row)
            outcome.created += 1
        else:
            outcome.updated += 1
        row.section = device.section
        row.device_raw = device.device_raw
        row.device_names = list(device.device_names)
        row.manufacturer_raw = device.manufacturer_raw
        row.si_codes = list(device.si_codes)
        row.device_type = device.device_type
        row.is_generic = device.is_generic
        row.details = dict(device.details)
        row.last_seen_at = now

    outcome.disappeared = sum(1 for fingerprint in existing if fingerprint not in seen)
    source.last_polled_at = now
    db.flush()

    linked = link_devices(db, source=source)
    outcome.with_manufacturer = linked["with_manufacturer"]
    outcome.products_linked = linked["products_linked"]

    log_action(
        db,
        component=COMPONENT,
        action=f"sync:{adapter_key}",
        result="success" if outcome.devices_read else "empty",
        level=LogLevel.INFO if outcome.devices_read else LogLevel.WARNING,
        details=outcome.summary(),
        user_id=actor.id if actor else None,
    )
    db.commit()
    logger.info(f"ПО верхнего уровня {adapter_key}: {outcome.summary()}")
    return outcome


def sync_all(db: Session, *, actor: User | None = None) -> list[SyncOutcome]:
    """Все площадки подряд, независимо: недоступность одной не отменяет остальные."""

    outcomes: list[SyncOutcome] = []
    for key in PLATFORMS:
        try:
            outcomes.append(sync_platform(db, key, actor=actor))
        except Exception as exc:  # noqa: BLE001 - сбой одной площадки не отменяет остальные
            db.rollback()
            logger.warning(f"ПО верхнего уровня {key}: чтение списка не удалось: {exc}")
            outcome = SyncOutcome(adapter_key=key)
            outcome.errors.append(str(exc))
            outcomes.append(outcome)
            log_action(
                db,
                component=COMPONENT,
                action=f"sync:{key}",
                result="error",
                level=LogLevel.ERROR,
                details=str(exc)[:500],
                user_id=actor.id if actor else None,
            )
            db.commit()
    return outcomes


# ------------------------------------------------------------------------------- связь


def _brand_of(manufacturer: Manufacturer) -> str:
    return (manufacturer.brand_name or manufacturer.legal_name).strip().lower()


def _patterns_for(manufacturer: Manufacturer) -> tuple[list[re.Pattern[str]], list[re.Pattern[str]]]:
    brand = _brand_of(manufacturer)
    by_name, by_device = _MANUFACTURER_PATTERNS.get(brand, ((), ()))
    name_patterns = [re.compile(p, re.IGNORECASE) for p in by_name]
    device_patterns = [re.compile(p, re.IGNORECASE) for p in by_device]
    # Производитель, заведённый администратором без шаблонов, узнаётся по бренду: как
    # слову в названии на сайте и как началу обозначения прибора — лучше, чем не
    # узнаваться вовсе.
    if not name_patterns and len(brand) >= 3:
        name_patterns.append(re.compile(re.escape(brand), re.IGNORECASE))
        device_patterns.append(re.compile("^" + re.escape(brand), re.IGNORECASE))
    return name_patterns, device_patterns


def _excluded(manufacturer_raw: str) -> bool:
    lowered = manufacturer_raw.lower()
    return any(re.search(p, lowered) for p in _MANUFACTURER_EXCLUSIONS)


@dataclass
class _KnownManufacturer:
    """Производитель с заранее скомпилированными шаблонами: связь идёт по тысячам записей,
    и компилировать регулярные выражения на каждую было бы заметно медленно."""

    manufacturer: Manufacturer
    name_patterns: list[re.Pattern[str]]
    device_patterns: list[re.Pattern[str]]


def _known_manufacturers(manufacturers: list[Manufacturer]) -> list[_KnownManufacturer]:
    return [_KnownManufacturer(m, *_patterns_for(m)) for m in manufacturers]


def resolve_manufacturer(
    device: UpperSoftwareDevice,
    known: list[_KnownManufacturer],
    si_types_by_code: dict[str, SiType],
) -> tuple[Manufacturer | None, str | None]:
    """Кто из наших производителей стоит за записью списка и по какому признаку."""

    by_id = {item.manufacturer.id: item.manufacturer for item in known}
    for code in device.si_codes or []:
        si_type = si_types_by_code.get(code)
        if si_type is not None and si_type.manufacturer_id in by_id:
            return by_id[si_type.manufacturer_id], "si_code"

    raw = (device.manufacturer_raw or "").strip()
    if raw and not _excluded(raw):
        for item in known:
            if any(p.search(raw) for p in item.name_patterns):
                return item.manufacturer, "manufacturer_name"

    for name in device.device_names or []:
        stripped = name.strip()
        for item in known:
            if any(p.search(stripped) for p in item.device_patterns):
                return item.manufacturer, "device_brand"
    return None, None


def _device_keys(name: str) -> set[str]:
    """Ключи обозначения из списка ПО: как есть и без слова-бренда впереди."""

    keys = {k for k in designation_keys(name) if len(k) >= MIN_KEY_LENGTH}
    words = name.split()
    if len(words) >= 2 and words[0].lower().strip("«»\"") in _BRAND_WORDS:
        rest = " ".join(words[1:])
        keys |= {k for k in designation_keys(rest) if len(k) >= MIN_KEY_LENGTH}
    return keys


def _product_keys(product: Product) -> set[str]:
    keys: set[str] = set()
    for value in (product.model_code, product.model_name):
        keys |= designation_keys(value)
    return {k for k in keys if k}


def _covers(device_key: str, product_key: str) -> bool:
    """Обозначение из списка покрывает модель каталога.

    Совпадение точное либо префиксное с границей: если ключ списка кончается цифрой, за
    ним в ключе модели не может идти цифра — иначе «МИРТЕК-1» покрыл бы «МИРТЕК-12-РУ»."""

    if device_key == product_key:
        return True
    if not product_key.startswith(device_key):
        return False
    next_char = product_key[len(device_key)]
    return not (device_key[-1].isdigit() and next_char.isdigit())


def match_products(
    device: UpperSoftwareDevice,
    products: list[tuple[Product, set[str]]],
    si_types_by_code: dict[str, SiType],
) -> dict[uuid.UUID, str]:
    """Модели производителя, которые покрывает запись, → признак совпадения."""

    matched: dict[uuid.UUID, str] = {}
    si_type_ids = {
        si_types_by_code[code].id for code in (device.si_codes or []) if code in si_types_by_code
    }
    if si_type_ids:
        for product, _ in products:
            if product.si_type_id in si_type_ids:
                matched[product.id] = "si_code"

    device_keys: set[str] = set()
    for name in device.device_names or []:
        device_keys |= _device_keys(name)
    if device_keys:
        for product, product_keys in products:
            if product.id in matched:
                continue
            if any(_covers(dk, pk) for dk in device_keys for pk in product_keys):
                matched[product.id] = "designation"
    return matched


def link_devices(db: Session, *, source: Source | None = None) -> dict[str, int]:
    """Определяет производителя и модели для записей списков — одной площадки или всех.

    Вызывается после чтения списка и после обхода каталогов: появилась новая модель в
    справочнике — записи списков должны её увидеть, не дожидаясь следующего чтения."""

    known = _known_manufacturers(list(db.scalars(select(Manufacturer))))
    si_types_by_code = {
        si_type.si_code: si_type for si_type in db.scalars(select(SiType)) if si_type.si_code
    }
    products_by_manufacturer: dict[uuid.UUID, list[tuple[Product, set[str]]]] = {}
    for product in db.scalars(select(Product)):
        products_by_manufacturer.setdefault(product.manufacturer_id, []).append(
            (product, _product_keys(product))
        )

    query = select(UpperSoftwareDevice)
    if source is not None:
        query = query.where(UpperSoftwareDevice.source_id == source.id)
    devices = list(db.scalars(query))
    device_ids = [device.id for device in devices]
    existing_links: dict[uuid.UUID, dict[uuid.UUID, UpperSoftwareProductLink]] = {}
    if device_ids:
        for link in db.scalars(
            select(UpperSoftwareProductLink).where(
                UpperSoftwareProductLink.device_id.in_(device_ids)
            )
        ):
            existing_links.setdefault(link.device_id, {})[link.product_id] = link

    with_manufacturer = 0
    products_linked = 0
    for device in devices:
        manufacturer, matched_by = resolve_manufacturer(device, known, si_types_by_code)
        device.manufacturer_id = manufacturer.id if manufacturer else None
        device.manufacturer_matched_by = matched_by
        wanted: dict[uuid.UUID, str] = {}
        if manufacturer is not None and not device.is_generic:
            with_manufacturer += 1
            wanted = match_products(
                device, products_by_manufacturer.get(manufacturer.id, []), si_types_by_code
            )
        current = existing_links.get(device.id, {})
        for product_id, how in wanted.items():
            link = current.get(product_id)
            if link is None:
                db.add(
                    UpperSoftwareProductLink(
                        device_id=device.id, product_id=product_id, matched_by=how
                    )
                )
            elif link.matched_by != how:
                link.matched_by = how
            products_linked += 1
        for product_id, link in current.items():
            if product_id not in wanted:
                db.delete(link)
    db.flush()
    return {"with_manufacturer": with_manufacturer, "products_linked": products_linked}


# ------------------------------------------------------------------------ сводки для UI


def _has_spodes_models(db: Session, manufacturer_id: uuid.UUID) -> bool:
    value = db.scalar(
        select(ProductCharacteristic.id)
        .join(Product, Product.id == ProductCharacteristic.product_id)
        .where(
            Product.manufacturer_id == manufacturer_id,
            ProductCharacteristic.field_name == "СПОДЭС",
            ProductCharacteristic.value.is_not(None),
            func.lower(ProductCharacteristic.value).notin_(["нет", "-", "—", "не поддерживается"]),
        )
        .limit(1)
    )
    return value is not None


def is_electricity_relevant(device: UpperSoftwareDevice) -> bool:
    """Запись относится к электросчётчикам или УСПД, а не к тепло-, водо- или газоучёту."""

    scope = f"{device.section} {device.device_type or ''}"
    return not (_FOREIGN_SECTION_RE.search(scope) and not _ELECTRIC_RE.search(scope))


def _device_brief(db: Session, device: UpperSoftwareDevice, *, with_products: bool = True) -> dict:
    products: list[str] = []
    if with_products:
        # Два заводских исполнения одной модели называются одинаково — в сводке имя одно.
        products = list(
            dict.fromkeys(
                db.scalars(
                    select(Product.model_name)
                    .join(UpperSoftwareProductLink, UpperSoftwareProductLink.product_id == Product.id)
                    .where(UpperSoftwareProductLink.device_id == device.id)
                    .order_by(Product.model_name)
                )
            )
        )
    return {
        "id": device.id,
        "section": device.section,
        "device_raw": device.device_raw,
        "device_names": list(device.device_names or []),
        "manufacturer_raw": device.manufacturer_raw,
        "si_codes": list(device.si_codes or []),
        "is_generic": device.is_generic,
        "details": device.details or {},
        "manufacturer_matched_by": device.manufacturer_matched_by,
        "products": products,
    }


def manufacturer_support(db: Session, manufacturer: Manufacturer) -> list[PlatformSupport]:
    """Статус производителя на каждой площадке — для карточки в каталоге и матрицы."""

    result: list[PlatformSupport] = []
    spodes_checked: bool | None = None
    for key, profile in PLATFORMS.items():
        source = resolve_source(db, key)
        if source is None or source.last_polled_at is None:
            result.append(
                PlatformSupport(
                    key=key, name=profile.name, vendor=profile.vendor, url=profile.url,
                    status="not_synced", note="Список поддерживаемого оборудования ещё не прочитан",
                )
            )
            continue
        devices = [
            device
            for device in db.scalars(
                select(UpperSoftwareDevice)
                .where(
                    UpperSoftwareDevice.source_id == source.id,
                    UpperSoftwareDevice.manufacturer_id == manufacturer.id,
                    UpperSoftwareDevice.is_generic.is_(False),
                )
                .order_by(UpperSoftwareDevice.section, UpperSoftwareDevice.device_raw)
            )
            if is_electricity_relevant(device)
        ]
        if devices:
            briefs = [_device_brief(db, device) for device in devices]
            # Сначала записи, за которыми стоят модели каталога, УСПД — в конец: в факте
            # для сопоставления первыми должны идти счётчики, о которых спрашивает закупка.
            briefs.sort(
                key=lambda d: (not d["products"], "УСПД" in d["device_raw"].upper(), d["device_raw"])
            )
            result.append(
                PlatformSupport(
                    key=key, name=profile.name, vendor=profile.vendor, url=profile.url,
                    status="supported", last_synced_at=source.last_polled_at, devices=briefs,
                )
            )
            continue
        generic = db.scalar(
            select(UpperSoftwareDevice).where(
                UpperSoftwareDevice.source_id == source.id,
                UpperSoftwareDevice.is_generic.is_(True),
            )
        )
        if generic is not None:
            if spodes_checked is None:
                spodes_checked = _has_spodes_models(db, manufacturer.id)
            if spodes_checked:
                result.append(
                    PlatformSupport(
                        key=key, name=profile.name, vendor=profile.vendor, url=profile.url,
                        status="protocol_only", last_synced_at=source.last_polled_at,
                        devices=[_device_brief(db, generic, with_products=False)],
                        note=(
                            "Моделей производителя в списке нет, но заявлена поддержка любых "
                            "счётчиков по протоколу СПОДЭС; у производителя есть модели с СПОДЭС"
                        ),
                    )
                )
                continue
        result.append(
            PlatformSupport(
                key=key, name=profile.name, vendor=profile.vendor, url=profile.url,
                status="not_listed", last_synced_at=source.last_polled_at,
                note="Приборов производителя в списке поддерживаемого оборудования нет",
            )
        )
    return result


def product_support(db: Session, product: Product) -> list[dict]:
    """Площадки, в списках которых есть эта модель, — для карточки модели."""

    rows = db.execute(
        select(UpperSoftwareDevice, Source.adapter_key)
        .join(UpperSoftwareProductLink, UpperSoftwareProductLink.device_id == UpperSoftwareDevice.id)
        .join(Source, Source.id == UpperSoftwareDevice.source_id)
        .where(UpperSoftwareProductLink.product_id == product.id)
        .order_by(Source.adapter_key, UpperSoftwareDevice.section)
    ).all()
    result: list[dict] = []
    for device, adapter_key in rows:
        profile = PLATFORMS.get(adapter_key)
        result.append(
            {
                "adapter_key": adapter_key,
                "name": profile.name if profile else adapter_key,
                "url": profile.url if profile else None,
                **_device_brief(db, device, with_products=False),
            }
        )
    return result


# -------------------------------------------------------------- требования и сопоставление


def detect_platforms(text: str | None) -> list[str]:
    """Какие площадки названы в тексте требования (ключи профилей, в порядке профилей)."""

    if not text:
        return []
    return [key for key, patterns in _ALIAS_RES.items() if any(p.search(text) for p in patterns)]


def mentions_integration(text: str | None) -> bool:
    if not text:
        return False
    if _INTEGRATION_RE.search(text):
        return True
    return _SYSTEM_RE.search(text) is not None and _COMPATIBILITY_RE.search(text) is not None


def platforms_for_requirements(requirements: list[Requirement]) -> tuple[list[str], bool]:
    """Площадки, названные в требованиях закупки, и есть ли требование об интеграции без
    названия ПО. Во втором случае в карточку идут факты по всем площадкам."""

    named: list[str] = []
    generic = False
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        found = detect_platforms(text)
        for key in found:
            if key not in named:
                named.append(key)
        # «Поддержка ИВК верхнего уровня „Пирамида 2.0“» — требование о конкретном ПО, а
        # не общее: факты по остальным шести площадкам ему не нужны.
        if not found and mentions_integration(text):
            generic = True
    return named, generic


def software_facts(
    db: Session, manufacturer: Manufacturer, requirements: list[Requirement]
) -> list[str]:
    """Строки `[software]` для карточки производителя в модуле сопоставления.

    Пусто, если требования об интеграции нет: незачем раздувать контекст фактами, по
    которым нет вопроса. Площадки, чей список ещё не читали, пропускаются — модель
    честно поставит `no_data`."""

    named, generic = platforms_for_requirements(requirements)
    if not named and not generic:
        return []
    # Общее требование («интегрированы в АСКУЭ») — факты по всем площадкам; только
    # названное ПО — по нему одному, остальные шесть строк были бы шумом.
    wanted = set(PLATFORMS) if generic else set(named)

    facts: list[str] = []
    for support in manufacturer_support(db, manufacturer):
        if support.key not in wanted or support.status == "not_synced":
            continue
        checked = to_msk(support.last_synced_at)
        checked_text = f"список проверен {checked:%d.%m.%Y}" if checked else "список проверен"
        if support.status == "supported":
            items: list[str] = []
            for device in support.devices[:MAX_DEVICES_IN_FACT]:
                extra: list[str] = []
                if device["si_codes"]:
                    extra.append("ГРСИ " + ", ".join(device["si_codes"]))
                details = device["details"] or {}
                support_flags = [name for name, ok in (details.get("support") or {}).items() if ok]
                if support_flags:
                    extra.append("; ".join(support_flags[:5]))
                if details.get("functions"):
                    extra.append(", ".join(details["functions"][:4]))
                if details.get("via_protocol"):
                    extra.append(f"по протоколу {details['via_protocol']}")
                items.append(
                    device["device_raw"] + (f" ({'; '.join(extra)})" if extra else "")
                )
            more = len(support.devices) - len(items)
            tail = f" и ещё {more}" if more > 0 else ""
            facts.append(
                f"[software] {support.name} ({support.vendor}, {checked_text}): в списке "
                f"поддерживаемого оборудования есть приборы производителя — {'; '.join(items)}{tail}"
            )
        elif support.status == "protocol_only":
            facts.append(
                f"[software] {support.name} ({support.vendor}, {checked_text}): моделей "
                f"производителя в списке НЕТ, но заявлена поддержка любых счётчиков по "
                f"протоколу СПОДЭС, а у производителя есть модели с СПОДЭС — поддержка "
                f"только по протоколу, без подтверждения конкретной модели"
            )
        else:
            facts.append(
                f"[software] {support.name} ({support.vendor}, {checked_text}): приборов "
                f"производителя «{manufacturer.brand_name or manufacturer.legal_name}» в списке "
                f"поддерживаемого оборудования НЕТ"
            )
    return facts


def tender_overview(db: Session, tender: Tender) -> dict:
    """Сводка для карточки закупки: какие требования об интеграции, какое ПО названо и
    как каждый производитель представлен на каждой площадке."""

    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.created_at)
        )
    )
    integration_requirements: list[dict] = []
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        platforms = detect_platforms(text)
        generic = mentions_integration(text)
        if platforms or generic:
            integration_requirements.append(
                {
                    "id": requirement.id,
                    "text": requirement.text,
                    "criticality": requirement.criticality,
                    "platforms": platforms,
                    "generic": generic and not platforms,
                }
            )
    named, generic = platforms_for_requirements(requirements)

    platforms = []
    for row in list_platforms(db):
        row["mentioned"] = row["adapter_key"] in named
        platforms.append(row)

    manufacturers = []
    for manufacturer in db.scalars(
        select(Manufacturer).order_by(Manufacturer.is_mirtek.desc(), Manufacturer.legal_name)
    ):
        support = {
            item.key: {
                "status": item.status,
                "devices": [
                    {"device_raw": d["device_raw"], "si_codes": d["si_codes"], "products": d["products"]}
                    for d in item.devices[:MAX_DEVICES_IN_FACT]
                ],
                "devices_total": len(item.devices),
                "note": item.note,
            }
            for item in manufacturer_support(db, manufacturer)
        }
        manufacturers.append(
            {
                "manufacturer_id": manufacturer.id,
                "name": manufacturer.brand_name or manufacturer.legal_name,
                "is_mirtek": manufacturer.is_mirtek,
                "support": support,
            }
        )
    return {
        "requirements": integration_requirements,
        "named_platforms": named,
        "generic_requirement": generic,
        "platforms": platforms,
        "manufacturers": manufacturers,
    }


# ------------------------------------------------------------------------------- очередь


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    outcome = sync_platform(db, task.adapter_key)
    return TaskOutcome(
        message=outcome.summary(),
        details={"errors": outcome.errors[:20]} if outcome.errors else None,
    )


def enqueue_sync(
    db: Session, *, adapter_key: str, actor_id: uuid.UUID | None = None
) -> CatalogLookupTask | None:
    return catalog_queue_service.enqueue(
        db,
        adapter_key=adapter_key,
        reason=CatalogQueueReason.MANUAL,
        model_name=None,
        actor_id=actor_id,
    )


def register() -> None:
    for key in PLATFORMS:
        catalog_queue_service.register_handler(key, handle_task)
