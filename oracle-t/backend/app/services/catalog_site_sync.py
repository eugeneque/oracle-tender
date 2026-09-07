"""Автонаполнение справочника продукции с сайтов производителей (раздел 5.3 ТЗ, источник 2).

Один сервис на всех: МИРТЕК и конкурентов (Энергомера, КПЗ, Промэнерго, …). Отличается
только адаптер, который отдаёт позиции и карточки, — логика сохранения общая, и это
принципиально: маппинг на Приложение C, идемпотентность, статус «снят с производства»,
привязка к типам СИ и обработка пропавших моделей должны работать одинаково для всех,
иначе каталог конкурентов будет наполняться по другим правилам, чем собственный, а
процент соответствия считается по ним вместе.

**Почему для МИРТЕК сайт — основной источник, а не запасной.** В «Описании типа» ФГИС лежат
только сертификационные данные: класс точности, токи, МПИ. Всё, чем закупки реально отличают
приборы друг от друга — интерфейсы, протоколы, глубина архива и профиля, журналы событий,
тарифные функции — есть только на сайте. Для конкурентов картина та же, поэтому у записи
каталога есть поле `data_source`: модуль сопоставления должен знать происхождение данных,
а не догадываться о нём.

**Три способа получить характеристику из карточки, по убыванию надёжности:**

1. **Таблица характеристик** — пары ключ-значение, раскладываются по полям Приложения C
   через словарь синонимов ниже. Ключи с сайтов не совпадают с полями справочника дословно
   («Базовый ток» против «Номинальный ток», «Частота измерительной сети» против «Частота
   сети»), причём у каждого производителя своя манера, поэтому словарь неизбежен.
2. **Метаданные карточки** — модель, артикул, исполнение, категория, статус, ссылки на
   документы. Здесь ничего извлекать не нужно, значение известно точно.
3. **Блок «Ключевые особенности»** — свободный текст, уходит в тот же YandexGPT-модуль, что
   разбирает требования тендеров. Регулярками его не берут сознательно: формулировки не
   унифицированы, и любой шаблон ломается на соседней карточке.

**Идемпотентность.** Ключ записи — URL карточки (`products.source_url`, уникальный индекс).
Не тройка «производитель + модель + артикул»: артикул на сайте может смениться, адрес
карточки — нет. Повторный обход обновляет запись, а модели, пропавшие со страницы категории,
не удаляются, а помечаются «требует проверки» — исчезновение с сайта не обязательно означает
снятие с производства, решать должен человек.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.manufacturer_catalog import normalise_phases, phases_for_device_type
from app.adapters.mirtek_catalog import (
    CatalogItem,
    CatalogProductDetails,
    MirtekCatalogAdapter,
)
from app.models.catalog_queue import CatalogLookupTask, CatalogQueueReason
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
    ProductDataSource,
    ProductStatus,
    ReviewStatus,
)
from app.models.user import User
from app.seed.characteristics_data import is_known_field
from app.services import catalog_queue_service, si_type_linking
from app.services.audit import log_action
from app.services.catalog_queue_service import TaskOutcome

COMPONENT = "catalog_sync"

# Ключ источника каталога МИРТЕК. Он один разбирается отдельным адаптером: только у МИРТЕК
# есть заводские исполнения (Таганрог/Владивосток) со своими характеристиками и своими
# типами СИ. Остальные сайты обслуживает общий движок с профилем
# (`app/adapters/manufacturer_catalog.py`).
MIRTEK_ADAPTER_KEY = "mirtek_site"

# Насколько свежей должна быть запись, чтобы её карточку можно было не перезагружать.
# Сайты производителей отвечают по 2-6 секунд на страницу, и обход в двести позиций занимает
# минуты — при этом каталог между еженедельными прогонами почти не меняется. Три дня, а не
# «неделя минус час»: плановый обход может сдвинуться, а два прогона подряд за один день
# (например, после ручного запуска) не должны заново тянуть весь каталог.
FRESH_CARD_MAX_AGE = timedelta(days=3)


# --- Словарь синонимов: ключ вкладки «Характеристики» → (группа, поле) Приложения C ---
#
# Сопоставление по нормализованному ключу (нижний регистр, без пунктуации и лишних пробелов),
# а не по точной строке: на карточках встречаются и «Базовый ток», и «Базовый&nbsp;ток», и
# «Диапазон напряжений питания» с «U ном» через пробел.
#
# Ключи здесь длинные и дословные там, где на сайте они длинные: обрезать «Полная (активная)
# мощность, потребляемая цепью напряжения счётчика при номинальном напряжении, не превышает»
# до «полная мощность» нельзя — рядом в той же таблице стоит вторая «Полная мощность,
# потребляемая каждой цепью тока…», и обрезанные ключи схлопнулись бы в один.
#
# Вторая из них (по цепи тока) в словарь сознательно НЕ внесена: в Приложении C есть
# «Полная потребляемая мощность» и «Активная потребляемая мощность», и обе про цепь
# напряжения. Подставить туда мощность по цепи тока значило бы записать в каталог
# заведомо неверное значение — она уходит в резервное JSON-поле как есть.
#
# Словарь покрывает ДВА поколения карточек, и это выяснилось на живом обходе, а не в
# теории: у новых моделей (D17, D37, SP31) ключи «Класс точности по активной/реактивной
# энергии» и «Базовый ток», а у выпущенных раньше (W2, W3, W6, SP2, D33) — «Класс точности
# по ГОСТ 31819.21-2012», «Базовый (номинальный) ток», «Максимальная сила тока» и мощности
# с добавкой «нормальной температуре, номинальной частоте». Это одни и те же величины,
# записанные по-разному, и без обоих наборов половина каталога осталась бы без
# электрических характеристик.
SPECIFICATION_SYNONYMS: dict[str, tuple[str, str]] = {
    "класс точности по активной реактивной энергии": (
        "Электрические характеристики",
        "Класс точности",
    ),
    "класс точности": ("Электрические характеристики", "Класс точности"),
    # Старые карточки разносят класс точности по двум ГОСТам: 31819.21 — активная энергия,
    # 31819.22 — реактивная. В Приложении C поле одно, поэтому в него идёт первое (активная,
    # и в таблице сайта оно стоит первым), а второе остаётся в резервном JSON — см.
    # разрешение коллизий в `_save_card_characteristics`.
    "класс точности по гост 31819 21 2012": ("Электрические характеристики", "Класс точности"),
    "класс точности по гост 31819 22 2012": ("Электрические характеристики", "Класс точности"),
    "номинальное напряжение": ("Электрические характеристики", "Номинальное напряжение"),
    "диапазон напряжений питания": (
        "Электрические характеристики",
        "Диапазон рабочих напряжений",
    ),
    "диапазон рабочих напряжений": (
        "Электрические характеристики",
        "Диапазон рабочих напряжений",
    ),
    "частота измерительной сети": ("Электрические характеристики", "Частота сети"),
    "частота сети": ("Электрические характеристики", "Частота сети"),
    # «Базовый ток» — терминология ГОСТ для того же, что в Приложении C названо номинальным.
    "базовый ток": ("Электрические характеристики", "Номинальный ток"),
    "базовый номинальный ток": ("Электрические характеристики", "Номинальный ток"),
    "номинальный ток": ("Электрические характеристики", "Номинальный ток"),
    "максимальный ток": ("Электрические характеристики", "Максимальный ток"),
    "максимальная сила тока": ("Электрические характеристики", "Максимальный ток"),
    "диапазон значений постоянной счётчика по активной электрической энергии": (
        "Электрические характеристики",
        "Постоянная счётчика",
    ),
    "диапазон значений постоянной счётчика по реактивной электрической энергии": (
        "Электрические характеристики",
        "Постоянная счётчика",
    ),
    "стартовый ток": ("Электрические характеристики", "Стартовый ток"),
    "полная активная мощность потребляемая цепью напряжения счётчика при номинальном напряжении не превышает": (
        "Электрические характеристики",
        "Полная потребляемая мощность",
    ),
    "полная активная мощность потребляемая цепью напряжения счётчика при номинальном напряжении нормальной температуре номинальной частоте не превышает": (
        "Электрические характеристики",
        "Полная потребляемая мощность",
    ),
    "диапазон рабочих температур": (
        "Конструктивные характеристики",
        "Диапазон рабочих температур",
    ),
    "межповерочный интервал": ("Метрологические характеристики", "Межповерочный интервал"),
    "срок службы счётчика не менее": ("Конструктивные характеристики", "Срок службы"),
    "срок службы": ("Конструктивные характеристики", "Срок службы"),
    "средняя наработка на отказ не менее": (
        "Конструктивные характеристики",
        "Средняя наработка на отказ",
    ),
    "средняя наработка на отказ": (
        "Конструктивные характеристики",
        "Средняя наработка на отказ",
    ),
    "степень защиты": ("Конструктивные характеристики", "Степень защиты"),
    "степень защиты по гост 14254 хх": ("Конструктивные характеристики", "Степень защиты"),
    "степень защиты от проникновения твердых предметов и воды по гост 14254 2015": (
        "Конструктивные характеристики",
        "Степень защиты",
    ),
    # --- Формулировки конкурентов (разведка 04.09.2026) ---
    # Энергомера
    "фазность": ("Электрические характеристики", "Количество фаз"),
    # «Тарифность» на той же карточке — словесная («Однотарифный»), а поле в Приложении C
    # одно; в него идёт число, словесная форма остаётся в резервном JSON.
    "число тарифов": ("Электрические характеристики", "Количество тарифов"),
    "тип отсчетного устройства": ("Электрические характеристики", "Тип отсчётного устройства"),
    "базовый максимальный ток": ("Электрические характеристики", "Номинальный ток"),
    "стартовый ток чувствительность": ("Электрические характеристики", "Стартовый ток"),
    "способ крепления": ("Конструктивные характеристики", "Тип монтажа"),
    "тип крепления": ("Конструктивные характеристики", "Тип монтажа"),
    "установка": ("Конструктивные характеристики", "Тип монтажа"),
    # КПЗ
    "рабочий диапазон напряжений": (
        "Электрические характеристики",
        "Диапазон рабочих напряжений",
    ),
    "рабочий диапазон частоты сети": ("Электрические характеристики", "Частота сети"),
    "базовый максимальный ток а": ("Электрические характеристики", "Номинальный ток"),
    "постоянная счетчика по активной энергии": (
        "Электрические характеристики",
        "Постоянная счётчика",
    ),
    "количество тарифов тарифных зон": (
        "Электрические характеристики",
        "Количество тарифов",
    ),
    "размеры д х ш х г": ("Конструктивные характеристики", "Габаритные размеры"),
    "размеры": ("Конструктивные характеристики", "Габаритные размеры"),
    "масса исполнения": ("Конструктивные характеристики", "Масса"),
    "максимальная площадь сечения проводов": (
        "Конструктивные характеристики",
        "Сечение проводов",
    ),
    "температура окружающей среды": (
        "Конструктивные характеристики",
        "Диапазон рабочих температур",
    ),
    "атмосферное давление": ("Конструктивные характеристики", "Атмосферное давление"),
    # Промэнерго
    "номинальное фазное напряжение": (
        "Электрические характеристики",
        "Номинальное напряжение",
    ),
    "номинальное значение частоты сети": ("Электрические характеристики", "Частота сети"),
    "предельный рабочий диапазон температур": (
        "Конструктивные характеристики",
        "Диапазон рабочих температур",
    ),
    "средний срок службы": ("Конструктивные характеристики", "Срок службы"),
    "встроенное реле управления нагрузкой": (
        "Интерфейсы и связь",
        "Реле управления нагрузкой",
    ),
    "поддерживаемый протокол передачи данных": ("Протоколы обмена", "Прочие протоколы"),
    "интеграция с ивк": ("Совместимость", "Наименования совместимых ИВК"),
    "масса": ("Конструктивные характеристики", "Масса"),
    "габаритные размеры": ("Конструктивные характеристики", "Габаритные размеры"),
    "относительная влажность": ("Конструктивные характеристики", "Относительная влажность"),
}

# Заголовок группы документов на карточке → поле «Документация» Приложения C, куда идёт
# ссылка. Разбор по группе, а не по тексту ссылки: тексты на сайте разные («Сертификат об
# утверждении и описание типа средств измерений МИРТЕК-12-РУ», «Декларация соответствия
# МИРТЕК-12-РУ», «Декларация о соответствии МИРТЕК-212-РУ»), группа — стабильна.
_CERTIFICATE_PATTERN = re.compile(r"сертификат|описание\s+типа", re.IGNORECASE)
_DECLARATION_PATTERN = re.compile(r"деклараци", re.IGNORECASE)
# Паспорт прибора — тот же материал, что и руководство: характеристики, режимы, интерфейсы.
# У Инкотекса руководства на карточке нет вовсе, а паспорт есть, и без него семейство
# «Меркурий» осталось бы вообще без разобранной документации.
_MANUAL_PATTERN = re.compile(r"руководств|паспорт", re.IGNORECASE)

# Тип монтажа с бейджа карточки категории («DIN-рейка», «Сплит», «Щиток») — на самой карточке
# товара этого поля нет вовсе, а в справочнике оно есть.
_MOUNTING_FIELD = ("Конструктивные характеристики", "Тип монтажа")

_AI_SYSTEM_PROMPT_SOURCE = "manufacturer"

# Начало текста пометки «модель пропала со страницы категории». Вынесено в константу, потому
# что по нему же пометка и снимается, когда модель возвращается на сайт: пометки на записи
# бывают разной природы (вторая приходит от дисамбигуации ФГИС), и снимать нужно только свою.
DISAPPEARED_REASON_PREFIX = "Модель пропала со страницы категории"


@dataclass
class SyncOutcome:
    """Итог обхода каталога — то, что показывается администратору после ручного запуска."""

    products_created: int = 0
    products_updated: int = 0
    # Карточки, которые не перезагружались: запись свежая, а листинг про неё ничего нового
    # не сообщил. Считается отдельно от «обновлено» — иначе по отчёту не отличить «обошли
    # и ничего не изменилось» от «даже не ходили».
    products_skipped: int = 0
    characteristics_saved: int = 0
    ai_extracted: int = 0
    # Сколько моделей привязано к утверждённым типам СИ. Без привязки данные с сайта и
    # «Описание типа» из ФГИС остаются двумя несвязанными наборами фактов, и модуль
    # сопоставления не знает, какой тип относится к какой модели.
    si_types_linked: int = 0
    marked_for_review: int = 0
    cards_failed: int = 0
    errors: list[str] = field(default_factory=list)


# --- Точка входа очереди ---


def handle_task(db: Session, task: CatalogLookupTask) -> TaskOutcome:
    """Исполнитель очереди. Какой сайт обходить, определяет `adapter_key` задачи — по нему же
    находится и производитель, чей каталог за этим сайтом стоит."""

    site = resolve_site(db, task.adapter_key)
    if site is None:
        return TaskOutcome(
            message=f"Источник «{task.adapter_key}» не сопоставлен ни с одним производителем"
        )
    manufacturer, adapter = site

    outcome = sync_catalog(db, manufacturer=manufacturer, adapter=adapter, use_ai=True)
    return TaskOutcome(
        message=(
            f"Создано моделей: {outcome.products_created}, обновлено: {outcome.products_updated}, "
            f"пропущено как свежие: {outcome.products_skipped}, "
            f"характеристик сохранено: {outcome.characteristics_saved}, "
            f"привязано к кодам СИ: {outcome.si_types_linked}, "
            f"помечено на проверку: {outcome.marked_for_review}, "
            f"карточек с ошибкой: {outcome.cards_failed}"
        ),
        needs_review=outcome.marked_for_review > 0,
        details={"errors": outcome.errors[:20]},
    )


def resolve_site(db: Session, adapter_key: str):
    """Ключ источника → (производитель, адаптер его сайта).

    МИРТЕК стоит особняком не по прихоти: его каталог разобран отдельным адаптером
    (`mirtek_catalog`), потому что там есть заводские исполнения, которых нет ни у кого
    другого. Остальные сайты обслуживает общий движок с профилем."""

    from app.adapters.manufacturer_catalog import PROFILES, ManufacturerCatalogAdapter

    if adapter_key == MIRTEK_ADAPTER_KEY:
        manufacturer = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
        return (manufacturer, MirtekCatalogAdapter()) if manufacturer is not None else None

    profile = PROFILES.get(adapter_key)
    if profile is None:
        return None
    manufacturer = db.scalar(
        select(Manufacturer).where(Manufacturer.legal_name == profile.manufacturer_legal_name)
    )
    if manufacturer is None:
        return None
    return manufacturer, ManufacturerCatalogAdapter(profile)


def list_sites(db: Session) -> list[dict]:
    """Перечень сайтов-источников каталога с признаком «производитель заведён»."""

    from app.adapters.manufacturer_catalog import PROFILES

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    sites: list[dict] = [
        {
            "adapter_key": MIRTEK_ADAPTER_KEY,
            "manufacturer_legal_name": mirtek.legal_name if mirtek else "ООО «МИРТЕК»",
            "base_url": "https://mirtekgroup.com",
            "categories": 2,
            "manufacturer_id": mirtek.id if mirtek else None,
        }
    ]
    for key, profile in PROFILES.items():
        manufacturer = db.scalar(
            select(Manufacturer).where(Manufacturer.legal_name == profile.manufacturer_legal_name)
        )
        sites.append(
            {
                "adapter_key": key,
                "manufacturer_legal_name": profile.manufacturer_legal_name,
                "base_url": profile.base_url,
                "categories": len(profile.categories),
                "manufacturer_id": manufacturer.id if manufacturer else None,
            }
        )
    return sites


def enqueue_sync(
    db: Session, *, adapter_key: str = MIRTEK_ADAPTER_KEY, actor_id: uuid.UUID | None = None
) -> CatalogLookupTask | None:
    """Ручной запуск обхода из админки. Идёт через ту же очередь, что и плановый: обход
    занимает минуты, и держать на нём открытый HTTP-запрос из браузера нельзя (та же
    причина, что и у фоновых задач анализа)."""

    return catalog_queue_service.enqueue(
        db,
        adapter_key=adapter_key,
        reason=CatalogQueueReason.MANUAL,
        model_name=None,
        actor_id=actor_id,
    )


def register() -> None:
    """Регистрирует исполнителя для каждого сайта-источника: у них один обработчик, но
    разные ключи в очереди — так в журнале и в очереди видно, какой именно сайт обходится."""

    from app.adapters.manufacturer_catalog import PROFILES

    for key in (MIRTEK_ADAPTER_KEY, *PROFILES):
        catalog_queue_service.register_handler(key, handle_task)


def sync_catalog(
    db: Session,
    *,
    manufacturer: Manufacturer | None = None,
    adapter=None,
    actor: User | None = None,
    use_ai: bool = True,
    full_refresh: bool = False,
) -> SyncOutcome:
    """Полный обход каталога одного производителя с сохранением в справочник.

    `manufacturer`/`adapter` не заданы — обходится каталог МИРТЕК: так вызывает плановая
    задача и старый код, которому знать про профили сайтов незачем.

    `use_ai=False` отключает разбор «Ключевых особенностей» моделью — нужен там, где
    YandexGPT недоступен или не настроен: остальные данные карточки при этом собираются
    полностью, а не теряются вместе с недоступной интеграцией.

    `full_refresh=True` перезагружает каждую карточку, даже недавно обойдённую. По умолчанию
    свежие пропускаются (см. `FRESH_CARD_MAX_AGE`): страница категории и так сообщает, жива
    ли позиция и не снята ли она с производства, а характеристики между еженедельными
    прогонами не меняются. Полный обход нужен, когда правили профиль сайта или словарь
    синонимов — тогда старые записи надо перечитать заново.
    """

    outcome = SyncOutcome()
    actor_id = actor.id if actor is not None else None

    if manufacturer is None:
        manufacturer = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    if adapter is None:
        adapter = MirtekCatalogAdapter()
    if manufacturer is None:
        message = "Производитель не найден в справочнике — обход каталога невозможен"
        outcome.errors.append(message)
        log_action(
            db,
            component=COMPONENT,
            action="catalog_site_sync",
            result="error",
            level=LogLevel.ERROR,
            details=message,
            user_id=actor_id,
        )
        db.commit()
        return outcome

    catalog = adapter.list_catalog()
    for error in catalog.errors:
        outcome.errors.append(f"{error.external_id}: {error.message}")

    seen_urls: set[str] = set()
    started_at = datetime.now(timezone.utc)

    for item in catalog.items:
        fresh = _fresh_product(db, item, since=started_at, full_refresh=full_refresh)
        if fresh is not None:
            # Карточку не перезагружаем: запись обновлялась недавно, а на странице категории
            # ничего про неё не изменилось. Данные при этом трогаем — статус и «последний раз
            # виден» приходят из листинга и стоят ноль запросов.
            fresh.status = (
                ProductStatus.DISCONTINUED.value if item.discontinued else ProductStatus.ACTIVE.value
            )
            fresh.last_seen_at = datetime.now(timezone.utc)
            # Пометка «пропала со страницы категории» снимается и здесь: позиция снова в
            # листинге, и это видно без единого запроса к карточке. Иначе запись, вернувшаяся
            # на сайт, оставалась бы помеченной до первого полного обхода.
            _clear_disappeared_flag(fresh)
            seen_urls.add(item.url)
            outcome.products_skipped += 1
            db.commit()
            continue

        try:
            details = adapter.get_product_details(item.url)
        except Exception as exc:  # noqa: BLE001 - сбой одной карточки не останавливает обход
            outcome.cards_failed += 1
            outcome.errors.append(f"{item.url}: карточка не загружена ({exc})")
            logger.warning(f"Каталог {manufacturer.legal_name}: карточка {item.url} не обработана: {exc}")
            log_action(
                db,
                component=COMPONENT,
                action=f"catalog_site_card:{item.article}",
                result="error",
                level=LogLevel.WARNING,
                details=f"{item.url}: {exc}",
                user_id=actor_id,
            )
            db.commit()
            continue

        try:
            product, created = _upsert_product(db, manufacturer, item, details=details)
            seen_urls.add(item.url)
            saved = _save_card_characteristics(
                db, product, item, details, manufacturer=manufacturer
            )
            outcome.characteristics_saved += saved
            if created:
                outcome.products_created += 1
            else:
                outcome.products_updated += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001 - см. выше: изоляция ошибки одной позиции
            db.rollback()
            outcome.cards_failed += 1
            outcome.errors.append(f"{item.url}: не сохранено ({exc})")
            logger.warning(f"Каталог {manufacturer.legal_name}: позиция {item.url} не сохранена: {exc}")
            continue

        if use_ai and details.features_text:
            outcome.ai_extracted += _extract_features_with_ai(db, product, details, actor=actor)

    # Соединение с сайтом больше не нужно: дальше идёт только работа с базой. Закрываем
    # явно, а не полагаемся на сборщик мусора, — оно живёт на весь обход (см.
    # `_session_client`), и держать его открытым после обхода незачем.
    close = getattr(adapter, "close", None)
    if callable(close):
        close()

    outcome.marked_for_review = _mark_disappeared(
        db, manufacturer, seen_urls=seen_urls, since=started_at, actor_id=actor_id
    )

    # Привязка к типам СИ — сразу после обхода, а не отдельной кнопкой: свежесозданные модели
    # без кода СИ бесполезны модулю сопоставления, а нужные типы к этому моменту, как правило,
    # уже найдены автопоиском по производителю. Если их ещё нет — шаг честно ничего не делает
    # и повторится при следующем обходе.
    link_outcome = si_type_linking.link_products_to_si_types(db, manufacturer, actor_id=actor_id)
    outcome.si_types_linked = link_outcome.linked
    outcome.marked_for_review += link_outcome.needs_review

    log_action(
        db,
        component=COMPONENT,
        action=f"catalog_site_sync:{manufacturer.legal_name}",
        result="success" if not outcome.cards_failed else "partial",
        level=LogLevel.INFO if not outcome.cards_failed else LogLevel.WARNING,
        details=(
            f"Создано моделей {outcome.products_created}, обновлено {outcome.products_updated}, "
            f"пропущено как свежие {outcome.products_skipped}, "
            f"характеристик сохранено {outcome.characteristics_saved}, "
            f"извлечено моделью {outcome.ai_extracted}, "
            f"привязано к кодам СИ {outcome.si_types_linked}, "
            f"помечено на проверку {outcome.marked_for_review}, "
            f"карточек с ошибкой {outcome.cards_failed}"
        ),
        user_id=actor_id,
    )
    db.commit()
    return outcome


def _upsert_product(
    db: Session,
    manufacturer: Manufacturer,
    item: CatalogItem,
    *,
    details: CatalogProductDetails | None = None,
) -> tuple[Product, bool]:
    """Запись каталога по URL карточки. Возвращает (модель, создана ли она заново)."""

    model_name = _preferred_model_name(item, details)
    product = db.scalar(select(Product).where(Product.source_url == item.url))
    created = product is None
    if product is None:
        product = Product(manufacturer_id=manufacturer.id, source_url=item.url)
        db.add(product)

    # Наименование берётся с карточки товара, когда профиль сайта говорит, что там оно
    # полнее (КПЗ: в листинге «М2М-1С», на карточке «Счетчик электроэнергии однофазный
    # компактный M2M-1С»). Иначе — из листинга: у Нартиса, наоборот, полное имя в листинге,
    # а H1 карточки короткий.
    product.model_name = model_name
    product.model_code = item.model_code or product.model_code
    product.article = item.article
    product.execution = item.execution or None
    product.device_type = item.device_type
    product.data_source = ProductDataSource.MANUFACTURER_SITE.value
    product.status = (
        ProductStatus.DISCONTINUED.value if item.discontinued else ProductStatus.ACTIVE.value
    )
    product.last_seen_at = datetime.now(timezone.utc)
    # Модель снова на сайте — снимаем пометку «пропала», если она была.
    _clear_disappeared_flag(product)

    db.flush()
    return product, created


def _fresh_product(
    db: Session, item: CatalogItem, *, since: datetime, full_refresh: bool
) -> Product | None:
    """Запись, карточку которой можно не перезагружать.

    Три условия сразу, и каждое нужно: запись существует, обновлялась недавно и у неё уже
    есть характеристики. Последнее важно — позиция, у которой прошлый обход сорвался на
    загрузке карточки, тоже «свежая» по времени, но данных у неё нет, и пропускать её
    значило бы законсервировать пустую строку в справочнике."""

    if full_refresh:
        return None

    product = db.scalar(select(Product).where(Product.source_url == item.url))
    if product is None or product.last_seen_at is None:
        return None
    if since - product.last_seen_at > FRESH_CARD_MAX_AGE:
        return None
    has_characteristics = db.scalar(
        select(ProductCharacteristic.id).where(ProductCharacteristic.product_id == product.id).limit(1)
    )
    return product if has_characteristics is not None else None


def _clear_disappeared_flag(product: Product) -> None:
    """Снимает пометку «модель пропала со страницы категории».

    Пометку, поставленную дисамбигуацией ФГИС или неудачной привязкой к типу СИ, не трогает:
    у них другая причина и своя жизнь."""

    if (product.review_reason or "").startswith(DISAPPEARED_REASON_PREFIX):
        product.review_status = ReviewStatus.OK.value
        product.review_reason = None


def _preferred_model_name(item: CatalogItem, details: CatalogProductDetails | None) -> str:
    """Какое из двух наименований — из листинга или с карточки — писать в справочник.

    Берётся более полное. Правило именно такое, а не «всегда карточка» или «всегда листинг»,
    потому что сайты расходятся: у КПЗ в листинге короткое «М2М-1С», а на карточке полное
    «Счетчик электроэнергии однофазный компактный M2M-1С»; у Нартиса ровно наоборот — в
    листинге «Счётчик однофазный интеллектуальный НАРТИС-И100», а H1 карточки просто
    «НАРТИС-И100». Требование одно: в справочнике должно стоять наименование как у
    производителя."""

    from_listing = item.model_name
    from_card = details.model_name if details is not None else None
    if from_card and _shouts(from_card) and not _shouts(from_listing):
        # Заголовок карточки набран прописными («СЧЕТЧИК ЭЛЕКТРОЭНЕРГИИ ТРЕХФАЗНЫЙ МИР С-04»
        # у НПО МИР) — это оформление, а не то, как прибор называют в документации.
        return from_listing[:255]
    if from_card and len(from_card) > len(from_listing):
        return from_card[:255]
    return from_listing[:255]


def _shouts(name: str) -> bool:
    """Наименование набрано прописными целиком. Одиночные аббревиатуры («НЕВА МТ 113») сюда
    не попадают — нужно, чтобы прописных букв было заметно больше, чем слов из аббревиатур."""

    letters = [char for char in name if char.isalpha()]
    if len(letters) < 12:
        return False
    return all(char.isupper() for char in letters)


def _save_card_characteristics(
    db: Session,
    product: Product,
    item: CatalogItem,
    details: CatalogProductDetails,
    *,
    manufacturer: Manufacturer,
) -> int:
    """Раскладывает содержимое карточки по полям Приложения C.

    Значения, введённые или подтверждённые человеком, не перезаписываются (раздел 5.3 ТЗ) —
    та же защита, что и в AI-экстракции.
    """

    saved = 0
    unmapped: dict[str, str] = {}

    values: list[tuple[str, str, str]] = [
        ("Основные характеристики", "Производитель", manufacturer.legal_name),
        ("Основные характеристики", "Бренд", manufacturer.brand_name or manufacturer.legal_name),
        ("Основные характеристики", "Модель", product.model_code or product.model_name),
        ("Основные характеристики", "Артикул", item.article),
        ("Основные характеристики", "Тип прибора", item.device_type),
        (
            "Основные характеристики",
            "Статус",
            "Снят с производства" if item.discontinued else "Выпускается",
        ),
    ]

    phases = phases_for_device_type(item.device_type)
    if phases:
        # Количества фаз в таблице характеристик нет ни у однофазных, ни у трёхфазных
        # карточек — оно задаётся категорией каталога, и терять его нельзя: «трёхфазный»
        # стоит требованием едва ли не в каждой закупке.
        values.append(("Электрические характеристики", "Количество фаз", phases))
    if item.mounting_badge:
        values.append((*_MOUNTING_FIELD, item.mounting_badge))
    # «Наименование полное» — название позиции целиком, если оно длиннее кода модели
    # (у Промэнерго «Однофазный прибор учета электроэнергии i-PROM.1» против «i-PROM.1»);
    # если полного названия нет, годится описание с карточки.
    # «Наименование полное» — то же, что в `model_name`: наименование производителя целиком.
    # Описание карточки сюда не идёт, это другой смысл (оно длинное и про назначение прибора).
    values.append(("Основные характеристики", "Наименование полное", product.model_name[:500]))
    if details.description:
        values.append(("Основные характеристики", "Наименование краткое", (product.model_code or product.model_name)[:255]))

    taken_fields: set[tuple[str, str]] = set()
    for raw_key, raw_value in details.specifications.items():
        mapped = _synonym_index().get(_normalise_key(raw_key))
        if mapped is None:
            # Ключ с сайта, которому нет места в Приложении C. Не выбрасывается: набор полей
            # на сайте шире справочника и меняется без предупреждения (п.2.3 задания).
            unmapped[raw_key] = raw_value
            continue
        if mapped == ("Электрические характеристики", "Количество фаз"):
            raw_value = normalise_phases(raw_value) or raw_value
        if mapped in taken_fields:
            # Два ключа сайта легли бы в одно поле справочника (класс точности по двум
            # ГОСТам, постоянная счётчика по активной и реактивной энергии). Второй не
            # затирает первый молча — он сохраняется в резервном поле под своим настоящим
            # именем, иначе значение поля зависело бы от порядка строк в таблице сайта.
            unmapped[raw_key] = raw_value
            continue
        taken_fields.add(mapped)
        values.append((mapped[0], mapped[1], raw_value))

    values.extend(_document_values(details))

    for group_name, field_name, value in values:
        if not value:
            continue
        if _upsert_characteristic(db, product, group_name, field_name, str(value)):
            saved += 1

    product.extra_specifications = unmapped
    if unmapped:
        logger.debug(
            f"Каталог: {item.article}: характеристик вне Приложения C — {len(unmapped)}: "
            f"{', '.join(sorted(unmapped))}"
        )
    db.flush()
    return saved


def _document_values(details: CatalogProductDetails) -> list[tuple[str, str, str]]:
    """Ссылки на документы → группа «Документация» Приложения C.

    «Сертификат об утверждении и описание типа» на сайте — один комбинированный PDF, поэтому
    он проставляется сразу в два поля справочника: и «Ссылка на сертификат», и «Ссылка на
    описание типа». Это не дублирование по недосмотру — оба поля существуют в Приложении C
    и оба должны быть заполнены, иначе поиск по любому из них ничего не найдёт."""

    values: list[tuple[str, str, str]] = [
        ("Документация", "Ссылка на каталог", details.url),
        ("Документация", "Дата обновления документации", date.today().isoformat()),
    ]

    for document in details.documents:
        if _CERTIFICATE_PATTERN.search(document.title):
            values.append(("Документация", "Ссылка на сертификат", document.url))
            values.append(("Документация", "Ссылка на описание типа", document.url))
        elif _DECLARATION_PATTERN.search(document.title):
            values.append(("Документация", "Ссылка на декларацию", document.url))
        elif _MANUAL_PATTERN.search(document.title) and "Ссылка на руководство" not in [
            v[1] for v in values
        ]:
            # Руководств на карточке бывает несколько (на сам счётчик и на сменный модуль
            # связи) — в справочник идёт первое, оно же руководство на прибор: документы
            # перечислены на сайте в этом порядке.
            values.append(("Документация", "Ссылка на руководство", document.url))

    return values


def _upsert_characteristic(
    db: Session, product: Product, group_name: str, field_name: str, value: str
) -> bool:
    """Сохраняет одно значение. `False` — значение защищено от перезаписи либо поля нет в
    справочнике."""

    if not is_known_field(group_name, field_name):
        logger.warning(
            f"Каталог: поле «{group_name} → {field_name}» отсутствует в Приложении C — "
            "значение не сохранено"
        )
        return False

    existing = db.scalar(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )
    if existing is not None:
        if (
            existing.verified_by_user
            or existing.source == CharacteristicSource.MANUAL_ENTRY.value
        ):
            return False
        if existing.value == value:
            return False
        existing.value = value
        existing.source = CharacteristicSource.MANUFACTURER_SITE.value
        # Значение взято из таблицы на сайте дословно, а не выведено моделью, — здесь нет
        # вероятностной оценки, и `confidence` остаётся пустым осознанно.
        existing.confidence = None
        db.flush()
        return True

    db.add(
        ProductCharacteristic(
            product_id=product.id,
            group_name=group_name,
            field_name=field_name,
            value=value,
            source=CharacteristicSource.MANUFACTURER_SITE.value,
            confidence=None,
            verified_by_user=False,  # раздел 5.3 ТЗ — обязательная проверка человеком
        )
    )
    db.flush()
    return True


def _extract_features_with_ai(
    db: Session, product: Product, details: CatalogProductDetails, *, actor: User | None
) -> int:
    """«Ключевые особенности» → характеристики через YandexGPT-модуль.

    Сбой модели не отменяет уже сохранённые данные карточки: интеграция может быть не
    настроена или недоступна, а таблица характеристик и документы к этому моменту уже в
    справочнике (раздел 5.9 ТЗ)."""

    from app.services import characteristic_extraction

    if actor is None:
        # `extract_characteristics_for_product` пишет в журнал от имени пользователя;
        # у планового прогона автора нет, берём первого администратора.
        actor = db.scalar(select(User).where(User.is_active.is_(True)).order_by(User.created_at))
    if actor is None:
        logger.warning("Каталог: нет активного пользователя для записи в журнал — AI-разбор пропущен")
        return 0

    try:
        outcome = characteristic_extraction.extract_characteristics_for_product(
            db,
            product,
            text=details.features_text or "",
            document_source=_AI_SYSTEM_PROMPT_SOURCE,
            characteristic_source=CharacteristicSource.MANUFACTURER_SITE,
            actor=actor,
        )
    except Exception as exc:  # noqa: BLE001 - недоступная модель не должна ронять обход каталога
        db.rollback()
        logger.warning(
            f"Каталог: разбор «Ключевых особенностей» {product.model_name} не выполнен: {exc}"
        )
        log_action(
            db,
            component=COMPONENT,
            action=f"catalog_site_features:{product.id}",
            result="error",
            level=LogLevel.WARNING,
            details=str(exc),
        )
        db.commit()
        return 0

    return outcome.saved


def _mark_disappeared(
    db: Session,
    manufacturer: Manufacturer,
    *,
    seen_urls: set[str],
    since: datetime,
    actor_id: uuid.UUID | None,
) -> int:
    """Модели, которых не оказалось на страницах категорий при этом обходе.

    Не удаляются и не архивируются автоматически (п.2.4 задания): позиция могла временно
    пропасть из-за правки сайта, а автоматическое снятие с производства в справочнике
    развернуло бы это в «прибор нельзя предлагать» на следующем же тендере. Ставится пометка
    и запись в журнал — решение за человеком.

    Обход при этом мог быть частичным (упала одна из категорий): чтобы не пометить половину
    каталога из-за одной недоступной страницы, помечаются только записи МИРТЕК, чей
    `source_url` относится к успешно обойдённым категориям.
    """

    if not seen_urls:
        # Ни одной карточки не собрано — обход провалился целиком, помечать нечего.
        return 0

    seen_categories = {url.rsplit("/", 1)[0] for url in seen_urls}
    candidates = list(
        db.scalars(
            select(Product).where(
                Product.manufacturer_id == manufacturer.id,
                Product.source_url.is_not(None),
                Product.review_status == ReviewStatus.OK.value,
            )
        )
    )

    marked = 0
    for product in candidates:
        url = product.source_url or ""
        if url in seen_urls or url.rsplit("/", 1)[0] not in seen_categories:
            continue
        product.review_status = ReviewStatus.NEEDS_REVIEW.value
        product.review_reason = (
            f"{DISAPPEARED_REASON_PREFIX} сайта производителя при обходе "
            f"{since.date().isoformat()}. Возможные причины: снята с производства, "
            "переименована или изменилась структура сайта. Автоматически не удалена — "
            "требуется решение пользователя."
        )
        marked += 1

    if marked:
        log_action(
            db,
            component=COMPONENT,
            action="catalog_site_disappeared",
            result="needs_review",
            level=LogLevel.WARNING,
            details=f"Помечено моделей, пропавших с сайта: {marked}",
            user_id=actor_id,
        )
    db.commit()
    return marked


# Хвост ключа, который несёт единицу измерения или уточнение, а не смысл: «Номинальное
# напряжение, В», «Срок службы, лет», «Габаритные размеры (ВхШхГ), мм, не более». Без его
# отсечения словарь синонимов пришлось бы вести по каждому написанию каждого сайта отдельно —
# три производителя дают три разных хвоста у одной и той же величины.
_UNIT_TAIL_RE = re.compile(
    r",\s*(?:в|а|гц|мм2?|м2|кг|г|вт|в·а|ва|лет|год\w*|час\w*|ч|сут\w*|мес\w*|°?\s*с|%|кпа|"
    r"имп/[^,]*|дб|мка|ма)\b.*$",
    re.IGNORECASE,
)
_QUALIFIER_TAIL_RE = re.compile(r",?\s*не\s+(?:более|менее)\s*:?\s*$", re.IGNORECASE)
_DIMENSION_NOTE_RE = re.compile(r"\((?:вхшхг|дхшхг|в\s*х\s*ш\s*х\s*г)\)", re.IGNORECASE)


@lru_cache(maxsize=1)
def _synonym_index() -> dict[str, tuple[str, str]]:
    """Словарь синонимов, приведённый к той же нормализации, что и ключи с сайта.

    Ключи в `SPECIFICATION_SYNONYMS` записаны так, как они выглядят на сайтах, — с единицами
    измерения и оговорками («срок службы счётчика не менее»). Прогонять их через
    `_normalise_key` на старте, а не держать в словаре уже нормализованными, важно ровно по
    одной причине: словарь читают и правят люди, и он должен оставаться похожим на то, что
    видно на странице, иначе следующий вариант формулировки добавят не туда."""

    return {_normalise_key(key): value for key, value in SPECIFICATION_SYNONYMS.items()}


def _normalise_key(value: str) -> str:
    """Ключ таблицы характеристик → форма для поиска в словаре синонимов.

    Нижний регистр, без пунктуации и неразрывных пробелов («Базовый&nbsp;ток» и «Базовый ток»
    дают один ключ), плюс отсечение единиц измерения и оговорок «не более»/«не менее»:
    у трёх производителей одна и та же величина записана как «Базовый ток», «Базовый ток, А»
    и «Базовый (номинальный) ток», и вести словарь по каждому написанию отдельно значило бы
    ловить каждый новый вариант по факту потери данных."""

    lowered = value.lower().replace("\xa0", " ").strip()
    lowered = _DIMENSION_NOTE_RE.sub(" ", lowered)
    lowered = _QUALIFIER_TAIL_RE.sub("", lowered)
    lowered = _UNIT_TAIL_RE.sub("", lowered)
    lowered = _QUALIFIER_TAIL_RE.sub("", lowered)
    cleaned = re.sub(r"[^0-9a-zа-яё ]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()
