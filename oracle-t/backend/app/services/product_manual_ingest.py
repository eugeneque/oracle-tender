"""Руководства по эксплуатации с сайтов производителей → характеристики справочника
(раздел 5.3 ТЗ, п.3 алгоритма заполнения каталога).

Что здесь решается. Обход сайта сохраняет в справочник **ссылку** на руководство, но не его
содержимое, а руководство — самый подробный источник о приборе: интерфейсы, протоколы,
функции, условия эксплуатации. Пока текст не извлечён, третий шаг сопоставления
(«fallback на руководство», раздел 5.5 ТЗ, п.3) опирается на пустоту: характеристик с
источником `user_manual` в справочнике были единицы на сотни моделей.

Почему отдельным шагом, а не прямо в обходе каталога. Руководство — это PDF на 5-20 МБ, а
его разбор моделью стоит нескольких запросов к YandexGPT на одну модель. Делать это на
каждом еженедельном обходе всех четырёхсот моделей и дорого, и незачем: документ меняется
куда реже карточки. Поэтому загрузка идёт отдельной задачей и только для тех моделей, у
которых текста руководства ещё нет.

Чужие правила соблюдаются: перед скачиванием проверяется `robots.txt` сайта, и закрытый
раздел не трогается — у Энергомеры, например, `/documentations/` запрещён к обходу, и
руководства оттуда мы не берём, хотя ссылки на них в справочнике храним.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify
from app.models.log import LogLevel
from app.models.manufacturer import (
    CharacteristicSource,
    Manufacturer,
    Product,
    ProductCharacteristic,
)
from app.models.user import User
from app.services.ai_client import AiQuotaExceededError
from app.services.audit import log_action
from app.services.document_extraction import extract_text, sniff_extension

COMPONENT = "product_catalog"

# Поле справочника, где обход сайта оставляет ссылку на руководство.
MANUAL_FIELD = ("Документация", "Ссылка на руководство")

# Предел на один файл. Руководство на счётчик — это десятки страниц; всё, что заметно
# больше, почти наверняка не руководство, а комплект документации целиком.
MAX_MANUAL_BYTES = 40 * 1024 * 1024

REQUEST_TIMEOUT_SECONDS = 60.0

# Сколько знаков руководства отдавать в разбор. Характеристики прибора стоят в начале —
# в разделах «Назначение» и «Технические характеристики»; дальше идут монтаж, поверка и
# приложения, разбор которых стоит запросов к модели, а полей справочника не добавляет.
MAX_MANUAL_CHARS = 40_000


@dataclass
class IngestOutcome:
    processed: int = 0
    # Модели, получившие уже разобранный документ семейства: они не стоили ни скачивания,
    # ни обращения к модели.
    reused_documents: int = 0
    skipped_have_data: int = 0
    skipped_no_link: int = 0
    skipped_by_robots: int = 0
    failed: int = 0
    # Документ скачан, но модель ИИ не ответила ни на один кусок текста (ключ, лимит, сеть).
    # Отдельно от `failed`: иначе человек видит «сохранено 0» и решает, что в руководстве
    # ничего нет (найдено тестом 14.10.2026).
    ai_failed: int = 0
    characteristics_saved: int = 0
    messages: list[str] = field(default_factory=list)


class RobotsGate:
    """Разрешает ли `robots.txt` сайта брать файл. Ответ на хост запрашивается один раз."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allows(self, url: str) -> bool:
        parts = urlparse(url)
        if not parts.scheme.startswith("http"):
            return False
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)
        parser = self._parsers[origin]
        # Недоступный robots.txt — не запрет: сайты, у которых его нет вовсе, отвечают 404,
        # и считать это запретом значило бы не скачать ничего ни с одного такого сайта.
        return True if parser is None else parser.can_fetch(self._user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        """Пауза между запросами к сайту, если `robots.txt` её требует (Инкотекс просит
        десять секунд). `None` — сайт ничего не требует."""

        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)
        parser = self._parsers[origin]
        if parser is None:
            return None
        delay = parser.crawl_delay(self._user_agent)
        return float(delay) if delay else None

    def _load(self, origin: str) -> RobotFileParser | None:
        parser = RobotFileParser()
        parser.set_url(f"{origin}/robots.txt")
        try:
            response = httpx.get(
                f"{origin}/robots.txt",
                headers={"User-Agent": self._user_agent},
                timeout=20.0,
                follow_redirects=True,
                verify=resolve_verify(origin),
            )
        except Exception as exc:  # noqa: BLE001 - сайт без robots.txt не должен ронять загрузку
            logger.warning(f"robots.txt сайта {origin} не получен: {exc}")
            return None
        if response.status_code >= 400:
            return None
        parser.parse(response.text.splitlines())
        return parser


def ingest_manuals(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor: User,
    limit: int = 20,
    use_ai: bool = True,
    refresh: bool = False,
    products: list[Product] | None = None,
) -> IngestOutcome:
    """Скачивает руководства моделей производителя и извлекает из них характеристики.

    `products` — ограничить проход перечисленными моделями (одна модель после поиска её
    документации в интернете); по умолчанию берутся все модели производителя.

    Берутся только модели, у которых ссылка есть, а характеристик из руководства ещё нет:
    повторный запуск не переплачивает за уже разобранные документы. `limit` ограничивает
    один прогон — руководств у производителя могут быть сотни.

    `refresh=True` перечитывает и уже разобранные руководства. Нужен после расширения
    справочника характеристик: поля, которых в нём не было, при прошлом разборе отбрасывались,
    и вернуть их можно только повторным чтением документа.
    """

    outcome = IngestOutcome()
    gate = RobotsGate()
    # Разобранные документы по адресу: одно руководство описывает всё семейство — у 478
    # моделей справочника всего 134 разных руководства. Без этого кеша каждое семейство
    # оплачивалось бы по числу исполнений, а не по числу документов.
    parsed: dict[str, list] = {}

    if products is None:
        products = list(
            db.scalars(
                select(Product)
                .where(Product.manufacturer_id == manufacturer.id)
                .order_by(Product.model_name)
            )
        )

    with httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
        verify=resolve_verify(manufacturer.website or "https://example.com"),
    ) as client:
        for product in products:
            if not refresh and _has_manual_characteristics(db, product):
                outcome.skipped_have_data += 1
                continue
            url = _manual_url(db, product)
            if not url:
                outcome.skipped_no_link += 1
                continue
            if not gate.allows(url):
                outcome.skipped_by_robots += 1
                continue

            # Это руководство уже прочитано для модели, и ничего не извлеклось — повторный
            # разбор того же файла даст то же, но стоит токенов (14.10.2026).
            if not refresh and product.manual_read_url == url:
                outcome.skipped_have_data += 1
                continue

            if url in parsed:
                # Тот же документ уже разобран в этом прогоне — записываем результат модели
                # без повторного скачивания и без повторного обращения к YandexGPT.
                outcome.reused_documents += 1
                outcome.characteristics_saved += _apply(db, product, parsed[url], actor=actor)
                product.manual_read_url = url
                continue

            # Предел считается по числу разобранных документов, и проверяется он здесь, а
            # не в начале цикла: раздача уже разобранного документа остальным моделям
            # семейства ничего не стоит и обрываться на пределе не должна.
            if outcome.processed >= limit:
                continue

            text = _download_text(client, url)
            if not text:
                outcome.failed += 1
                continue

            outcome.processed += 1
            if not use_ai:
                continue
            try:
                characteristics, saved = _extract(db, product, text, actor=actor)
            except AiQuotaExceededError as exc:
                # Лимит, отклонённый ключ, нет связи — остальные руководства получат тот же
                # отказ, перебирать их незачем.
                outcome.ai_failed += 1
                outcome.messages.append(f"Разбор руководств остановлен: {exc}")
                break
            if characteristics is None:
                outcome.ai_failed += 1
                outcome.messages.append(f"{product.model_name}: модель ИИ не ответила, руководство не прочитано")
                continue
            parsed[url] = characteristics
            outcome.characteristics_saved += saved
            product.manual_read_url = url

    log_action(
        db,
        component=COMPONENT,
        action=f"ingest_manuals:{manufacturer.id}",
        result="success" if outcome.characteristics_saved else "empty",
        level=LogLevel.INFO,
        details=(
            f"Руководств разобрано {outcome.processed} (ещё {outcome.reused_documents} "
            f"моделей получили уже разобранный документ семейства), характеристик сохранено "
            f"{outcome.characteristics_saved}; пропущено: уже разобраны "
            f"{outcome.skipped_have_data}, без ссылки {outcome.skipped_no_link}, "
            f"запрещено robots.txt {outcome.skipped_by_robots}; не загрузилось "
            f"{outcome.failed}"
        ),
        user_id=actor.id,
    )
    db.commit()
    return outcome


# Ответы robots.txt для карточки модели: она открывается часто, а правила сайта меняются
# редко — спрашивать сайт при каждом открытии незачем.
_ROBOTS_TTL_SECONDS = 6 * 3600
_robots_cache: dict[str, tuple[float, bool]] = {}


def robots_allows(url: str) -> bool:
    """Разрешает ли сайт роботу скачать документ. Для карточки модели: «руководство не
    прочитано, потому что сайт запрещает» — причина, которую человек должен видеть."""

    now = time.monotonic()
    cached = _robots_cache.get(url)
    if cached and now - cached[0] < _ROBOTS_TTL_SECONDS:
        return cached[1]
    allowed = RobotsGate().allows(url)
    _robots_cache[url] = (now, allowed)
    return allowed


def ingest_uploaded_manual(
    db: Session, product: Product, *, content: bytes, file_name: str, actor: User
) -> IngestOutcome:
    """Руководство, загруженное человеком (замечание 13.10.2026): когда сайт производителя
    запрещает роботам раздел документации — как Энергомера `/documentations/` — обход его не
    скачивает, и единственный честный путь — скачать PDF в браузере и отдать сюда.

    Документ описывает семейство, поэтому прочитанное раздаётся и остальным моделям
    производителя с той же ссылкой на руководство — как при обычном разборе."""

    outcome = IngestOutcome()
    extension = _extension_of(file_name) or sniff_extension(content)
    try:
        text = extract_text(extension, content)
    except Exception as exc:  # noqa: BLE001 - битый файл → понятная ошибка
        raise ValueError(f"Файл не удалось прочитать: {exc}") from exc
    text = (text or "")[:MAX_MANUAL_CHARS]
    if not text.strip():
        raise ValueError("В файле нет текста (скан без текстового слоя?) — характеристики не извлечь")

    try:
        characteristics, saved = _extract(db, product, text, actor=actor)
    except AiQuotaExceededError as exc:
        raise ValueError(str(exc)) from exc
    if characteristics is None:
        raise ValueError(AI_DID_NOT_ANSWER)
    outcome.processed = 1
    outcome.characteristics_saved += saved

    url = _manual_url(db, product)
    if url:
        siblings = db.scalars(
            select(Product)
            .join(ProductCharacteristic, ProductCharacteristic.product_id == Product.id)
            .where(
                Product.manufacturer_id == product.manufacturer_id,
                Product.id != product.id,
                ProductCharacteristic.group_name == MANUAL_FIELD[0],
                ProductCharacteristic.field_name == MANUAL_FIELD[1],
                ProductCharacteristic.value == url,
            )
        )
        for sibling in siblings:
            outcome.reused_documents += 1
            outcome.characteristics_saved += _apply(db, sibling, characteristics, actor=actor)

    log_action(
        db,
        component=COMPONENT,
        action=f"upload_manual:{product.id}",
        result="success" if outcome.characteristics_saved else "empty",
        level=LogLevel.INFO,
        details=(
            f"Руководство «{file_name}» загружено вручную для {product.model_name}: характеристик "
            f"сохранено {outcome.characteristics_saved}, моделей семейства с той же ссылкой "
            f"{outcome.reused_documents}"
        ),
        user_id=actor.id,
    )
    db.commit()
    return outcome


def _has_manual_characteristics(db: Session, product: Product) -> bool:
    return (
        db.scalar(
            select(ProductCharacteristic.id)
            .where(
                ProductCharacteristic.product_id == product.id,
                ProductCharacteristic.source == CharacteristicSource.USER_MANUAL.value,
            )
            .limit(1)
        )
        is not None
    )


def _manual_url(db: Session, product: Product) -> str | None:
    group_name, field_name = MANUAL_FIELD
    value = db.scalar(
        select(ProductCharacteristic.value).where(
            ProductCharacteristic.product_id == product.id,
            ProductCharacteristic.group_name == group_name,
            ProductCharacteristic.field_name == field_name,
        )
    )
    return value if value and value.startswith("http") else None


def _download_text(client: httpx.Client, url: str) -> str | None:
    """Файл руководства → текст. `None` — не скачался, слишком велик или формат не поддержан."""

    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            buffer = io.BytesIO()
            for chunk in response.iter_bytes():
                buffer.write(chunk)
                if buffer.tell() > MAX_MANUAL_BYTES:
                    logger.warning(f"Руководство {url} больше {MAX_MANUAL_BYTES} байт — пропущено")
                    return None
            content = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - один недоступный документ не должен ронять прогон
        logger.warning(f"Руководство {url} не загружено: {exc}")
        return None

    extension = _extension_of(url) or sniff_extension(content)
    try:
        text = extract_text(extension, content)
    except Exception as exc:  # noqa: BLE001 - см. выше
        logger.warning(f"Руководство {url} не разобрано: {exc}")
        return None
    return (text or "")[:MAX_MANUAL_CHARS] or None


def _extension_of(url: str) -> str | None:
    path = urlparse(url).path
    _, _, tail = path.rpartition(".")
    return f".{tail.lower()}" if tail and tail != path else None


AI_DID_NOT_ANSWER = (
    "Модель ИИ не ответила — руководство не прочитано. Проверьте подключение ИИ в разделе "
    "«Модели ИИ» и повторите."
)


def _extract(db: Session, product: Product, text: str, *, actor: User) -> tuple[list | None, int]:
    """Читает руководство моделью и записывает результат первой модели семейства.

    Возвращает вычитанное — чтобы остальные модели с тем же документом получили его без
    повторного обращения к YandexGPT. `None` — модель не ответила ни на один кусок."""

    from app.services import characteristic_extraction

    try:
        reading = characteristic_extraction.read_characteristics(
            db, text=text, document_source="manufacturer"
        )
    except AiQuotaExceededError:
        # Лимит, отклонённый ключ, нет связи — остальные руководства получат тот же отказ.
        raise
    except Exception as exc:  # noqa: BLE001 - недоступность модели не должна ронять прогон
        logger.warning(f"Разбор руководства модели {product.model_name} не удался: {exc}")
        return None, 0
    if reading.chunks_failed and not reading.chunks_processed:
        return None, 0

    return reading.characteristics, _apply(db, product, reading.characteristics, actor=actor)


def _apply(db: Session, product: Product, characteristics: list, *, actor: User) -> int:
    from app.services import characteristic_extraction

    outcome = characteristic_extraction.ExtractionOutcome()
    characteristic_extraction.apply_characteristics(
        db,
        product,
        characteristics,
        characteristic_source=CharacteristicSource.USER_MANUAL,
        outcome=outcome,
    )
    db.commit()
    return outcome.saved
