"""Карточка компании на rusprofile.ru как источник юридических данных (раздел 7 ТЗ).

Дополняет автопоиск по ЕГРЮЛ (`app/services/egrul_service.py`), а не заменяет его: у ЕГРЮЛ
включается защита от автоматических запросов, и тогда единственным рабочим путём остаётся
разбор карточки по прямой ссылке.

**Работает только по ссылке на карточку, не по ИНН.** Поиск rusprofile для программных
клиентов закрыт — `/search?query=<ИНН>` отвечает 404 при любых заголовках и с полученной
сессионной cookie, тогда как страница `/id/<номер>` отдаётся полностью. Поэтому функция
принимает адрес карточки (или её номер), а не поисковый запрос: делать вид, что мы умеем
искать, и падать на каждом втором вызове — хуже, чем честно попросить ссылку.

Данные берутся из якорей `id="clip_*"` и микроразметки schema.org, а не из вёрстки: это
служебные атрибуты «скопировать значение», они меняются реже, чем оформление страницы.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry

BASE_URL = "https://www.rusprofile.ru"
CARD_PATH = "/id/{card_id}"

_CARD_ID_RE = re.compile(r"/id/(\d+)")
_DIGITS_ONLY_RE = re.compile(r"^\d+$")


class RusprofileError(RuntimeError):
    """Карточка не разобрана или сервис недоступен — эндпоинт превращает в 4xx с текстом."""


@dataclass
class RusprofileCompany:
    """Кандидат из карточки. Как и у ЕГРЮЛ, ничего не сохраняет — только показывается человеку."""

    legal_name: str | None
    short_name: str | None
    inn: str | None
    kpp: str | None
    ogrn: str | None
    registration_date: date | None
    legal_address: str | None
    source_url: str | None


def extract_card_id(value: str) -> str:
    """Достаёт номер карточки из ссылки или принимает его напрямую.

    Человек копирует из адресной строки целиком, с `https://`, хвостом и иногда с пробелами —
    требовать «только номер» значит гарантированно получать ошибку на первом же вызове.
    """

    cleaned = (value or "").strip()
    if not cleaned:
        raise RusprofileError("Пустая ссылка: вставьте адрес карточки компании на rusprofile.ru.")
    match = _CARD_ID_RE.search(cleaned)
    if match:
        return match.group(1)
    if _DIGITS_ONLY_RE.match(cleaned):
        return cleaned
    raise RusprofileError(
        "Не похоже на ссылку rusprofile: ожидается адрес вида "
        "https://www.rusprofile.ru/id/6723224"
    )


def _clip(soup: BeautifulSoup, name: str) -> str | None:
    element = soup.select_one(f"#clip_{name}")
    if element is None:
        return None
    text = element.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip() or None


def _itemprop(soup: BeautifulSoup, name: str) -> str | None:
    element = soup.select_one(f"[itemprop={name}]")
    if element is None:
        return None
    value = element.get("content") or element.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip() or None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%d.%m.%Y").date()
    except ValueError:
        return None


def _normalize_address(value: str | None) -> str | None:
    """Убирает пробелы перед запятыми: адрес на странице свёрстан по частям и склеивается
    как «355037 , Ставропольский край , г. Ставрополь»."""

    if not value:
        return None
    return re.sub(r"\s+,", ",", value).strip() or None


def parse_card(html: str, *, source_url: str | None = None) -> RusprofileCompany:
    soup = BeautifulSoup(html, "lxml")
    company = RusprofileCompany(
        legal_name=_itemprop(soup, "legalName") or _clip(soup, "name-long"),
        short_name=_itemprop(soup, "name"),
        inn=_clip(soup, "inn") or _itemprop(soup, "taxID"),
        kpp=_clip(soup, "kpp"),
        ogrn=_clip(soup, "ogrn"),
        registration_date=_parse_date(_itemprop(soup, "foundingDate")),
        legal_address=_normalize_address(_clip(soup, "address") or _itemprop(soup, "address")),
        source_url=source_url,
    )
    # ИНН — минимальный признак того, что перед нами карточка компании, а не заглушка,
    # страница ошибки или капча: без него разбирать нечего.
    if not company.inn:
        raise RusprofileError(
            "На странице не нашлось ИНН — возможно, ссылка ведёт не на карточку компании "
            "или сервис показал проверку на робота. Заполните данные вручную."
        )
    return company


def fetch_company(url_or_id: str) -> RusprofileCompany:
    card_id = extract_card_id(url_or_id)
    path = CARD_PATH.format(card_id=card_id)
    try:
        with httpx.Client(
            base_url=BASE_URL,
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
        ) as client:
            response = fetch_with_retry(client, "GET", path, max_attempts=2)
    except httpx.HTTPError as exc:
        logger.warning(f"Карточка rusprofile не загрузилась: {exc}")
        raise RusprofileError(
            f"Не удалось обратиться к rusprofile.ru: {exc}. Заполните данные вручную."
        ) from exc

    if response.status_code >= 400:
        raise RusprofileError(
            f"rusprofile.ru ответил HTTP {response.status_code} на карточку {card_id}. "
            "Проверьте ссылку или заполните данные вручную."
        )
    return parse_card(response.text, source_url=f"{BASE_URL}{path}")
