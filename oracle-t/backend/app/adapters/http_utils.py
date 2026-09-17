"""Общий HTTP-хелпер для адаптеров источников: повторные попытки с экспоненциальной
задержкой при ошибке загрузки (раздел 5.1, 5.9 ТЗ: "по умолчанию 3 попытки с экспоненциальной
задержкой"). Один источник ошибки не должен использовать свою собственную логику ретраев —
иначе поведение будет расходиться между адаптерами без причины."""

from __future__ import annotations

import ssl
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.core.config import get_settings

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; OracleTBot/1.0; +tender collection for ORACLE-T)"
)

_CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"

# Бандл Russian Trusted CA (Минцифры России): сертификаты *.zakupki.gov.ru и *.etp.gpb.ru
# подписаны государственным Sub CA, самоподписанный корень которого нет смысла добавлять в
# системный список — доверяем ему только явно для этих доменов, заменяя весь список CA целиком
# (`verify=<путь к бандлу>`), не отключая проверку.
RUSSIAN_CA_BUNDLE = _CERTS_DIR / "russian_trusted_ca.pem"
# `etp.gpb.ru` — торговая часть ЭТП ГПБ, откуда отдаются файлы документации (сам портал
# `etpgpb.ru` живёт на обычном международном сертификате, а вот файловый хост — на
# государственном). Без этого исключения скачивание документов ЭТП ГПБ, крупнейшего нашего
# источника, падало с "self-signed certificate in certificate chain".
_RUSSIAN_CA_HOSTS = ("zakupki.gov.ru", "etp.gpb.ru")

# etprf.ru — другой случай: сервер не досылает промежуточный сертификат
# "GlobalSign GCC R6 AlphaSSL CA 2025" (частая ошибка конфигурации веб-сервера), из-за чего
# цепочка не строится, хотя корневой GlobalSign R6 давно в любом системном списке. Сам
# промежуточный сертификат — из поля Authority Information Access листового сертификата
# (http://secure.globalsign.com/cacert/gsgccr6alphasslca2025.crt), официальный публичный CA,
# просто добавляем недостающее звено к системному списку, а не заменяем его целиком.
_MISSING_INTERMEDIATE_HOSTS: dict[str, Path] = {
    "etprf.ru": _CERTS_DIR / "globalsign_gcc_r6_alphassl_2025.pem",
}


@lru_cache(maxsize=8)
def _context_with_extra_ca(extra_cert_path: Path) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(extra_cert_path))
    return context


def resolve_verify(url: str) -> str | bool | ssl.SSLContext:
    """Какой набор доверенных CA использовать для запроса к данному URL — по умолчанию
    стандартный системный список (`True`), для отдельных проблемных доменов — точечное
    исключение (см. константы выше)."""

    host = urlparse(url).hostname or ""

    if any(host == h or host.endswith(f".{h}") for h in _RUSSIAN_CA_HOSTS):
        if RUSSIAN_CA_BUNDLE.exists():
            return str(RUSSIAN_CA_BUNDLE)
        logger.warning(
            f"Бандл Russian Trusted CA не найден по пути {RUSSIAN_CA_BUNDLE} — используется "
            f"системный список доверенных CA (запрос к {host} может не пройти)."
        )
        return True

    for suffix, cert_path in _MISSING_INTERMEDIATE_HOSTS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            if cert_path.exists():
                return _context_with_extra_ca(cert_path)
            logger.warning(
                f"Дополнительный сертификат не найден по пути {cert_path} — используется "
                f"системный список доверенных CA (запрос к {host} может не пройти)."
            )
            return True

    return True


def fetch_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_attempts: int | None = None,
    backoff_base: float | None = None,
    **kwargs,
) -> httpx.Response:
    """Выполняет запрос с повторными попытками при сетевых ошибках или HTTP 5xx/429.
    Не глотает окончательную ошибку — вызывающий код (сервис опроса) сам решает, как её
    залогировать и изолировать от остальных источников/документов (раздел 5.9 ТЗ)."""

    settings = get_settings()
    attempts = max_attempts if max_attempts is not None else settings.http_retry_attempts
    base = backoff_base if backoff_base is not None else settings.http_retry_backoff_base

    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code >= 500 or response.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            return response
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < attempts:
                delay = base * (2 ** (attempt - 1))
                logger.warning(
                    f"Попытка {attempt}/{attempts} загрузки {url} не удалась ({exc}); "
                    f"повтор через {delay:.1f}с"
                )
                time.sleep(delay)

    assert last_exc is not None
    raise last_exc
