"""Каталог совместимого ПО «Ready for Astra» (astra.ru) — параметр 39 файла «Параметры
для ПУ»: поддержка Astra Linux конфигуратором прибора учёта.

Комментарий тендерного отдела — «попробовать, чтобы нейронка искала соответствующий
сертификат». Искать не пришлось: страница каталога
(`/ready-for-astra/compatible-software/`) рисуется скриптом из одного JSON
(`/local/parsers/results/soft.json`, ~1 МБ, 1132 позиции на 25.09.2026). В нём у каждой
позиции есть название ПО, разработчик и версии ОС, с которыми совместимость
подтверждена. Приборных производителей там немного: МИРТЕК (MeterTools, RadioAccess,
M2MServer), Энергомера (AdminTools), МИЛУР (конфигуратор Милур DLMS, Milur TCP Server),
РОТЕК (конфигуратор счётчиков, разработчик «ЛИС»).

Разработчик ПО не всегда совпадает с производителем прибора (РОТЕК — «ЛИС»), поэтому
производитель ищется и в названии ПО, и в названии разработчика.

Каталог загружается один раз на процесс и живёт `CACHE_SECONDS`: на один расчёт
соответствия он нужен по всем производителям сразу, а меняется раз в недели."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, resolve_verify

CATALOG_URL = "https://astra.ru/local/parsers/results/soft.json"
PAGE_URL = "https://astra.ru/ready-for-astra/compatible-software/"
CACHE_SECONDS = 24 * 3600
REQUEST_TIMEOUT = 60.0


@dataclass(frozen=True)
class AstraSoftware:
    name: str
    vendor: str
    os_lines: tuple[str, ...]

    def describe(self) -> str:
        os_text = ", ".join(self.os_lines) if self.os_lines else "версии ОС не указаны"
        return f"{self.name} (разработчик {self.vendor or '—'}; {os_text})"


@dataclass
class AstraCatalog:
    items: list[AstraSoftware]
    loaded_at: float

    def find(self, brand: str) -> list[AstraSoftware]:
        """ПО, где бренд производителя стоит отдельным словом в названии или у
        разработчика. Граница слова обязательна: «МИР» не должен находить «МИРТЕК»."""

        needle = _brand_pattern(brand)
        if needle is None:
            return []
        return [item for item in self.items if needle.search(f"{item.name} {item.vendor}")]


_cache: AstraCatalog | None = None


def _brand_pattern(brand: str) -> re.Pattern[str] | None:
    # «Метроника (АЛЬФА)» → «Метроника»: в скобках — линейка, а не производитель.
    core = re.sub(r"\(.*?\)", "", brand or "").strip()
    if len(core) < 2:
        return None
    return re.compile(rf"(?<!\w){re.escape(core)}(?!\w)", re.IGNORECASE)


def parse_catalog(payload: dict) -> list[AstraSoftware]:
    items = []
    for raw in payload.get("DATA") or []:
        vendor = raw.get("vendor") or {}
        items.append(
            AstraSoftware(
                name=str(raw.get("NAME") or "").strip(),
                vendor=str(vendor.get("NAME") or "").strip(),
                os_lines=tuple(sorted((raw.get("osLine") or {}).keys())),
            )
        )
    return items


def load_catalog(*, client: httpx.Client | None = None, force: bool = False) -> AstraCatalog | None:
    """Каталог из кэша или с сайта. `None` — сайт недоступен: вызывающий код не выдаёт
    фактов, и требование остаётся «нет данных», а не «не соответствует»."""

    global _cache
    if not force and _cache is not None and time.time() - _cache.loaded_at < CACHE_SECONDS:
        return _cache

    own_client = client is None
    client = client or httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        follow_redirects=True,
        timeout=REQUEST_TIMEOUT,
        verify=resolve_verify(CATALOG_URL),
    )
    try:
        response = client.get(CATALOG_URL)
        response.raise_for_status()
        items = parse_catalog(response.json())
    except Exception as exc:  # noqa: BLE001 - недоступность сайта не должна ронять расчёт
        logger.warning(f"Каталог Ready for Astra не загружен: {exc}")
        return _cache
    finally:
        if own_client:
            client.close()
    if not items:
        return _cache
    _cache = AstraCatalog(items=items, loaded_at=time.time())
    return _cache


def set_cache(catalog: AstraCatalog | None) -> None:
    """Для тестов: подставить каталог без обращения к сайту."""

    global _cache
    _cache = catalog
