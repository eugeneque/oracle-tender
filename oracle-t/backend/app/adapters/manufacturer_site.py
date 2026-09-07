"""Адаптер сайтов производителей (раздел 4.3, 5.3 ТЗ, п.3 алгоритма — Этап 4).

Задача: по сайту производителя и названию модели найти документ «Руководство пользователя»
(он же «Руководство по эксплуатации», «РЭ») — источник тех групп характеристик Приложения C,
которых нет в «Описании типа» ФГИС (интерфейсы, протоколы, функциональные возможности и т.д.).

В отличие от адаптера ФГИС, писался **по реальной разметке живых сайтов** — они доступны.
Что показала разведка по сайтам из раздела 4.3 ТЗ:

- Единого шаблона нет: у «Энергомеры» карточка модели вида
  `/ru/products/meters/ce101-r5-145-m6` со ссылкой «Руководство по эксплуатации» →
  `.../ce101_re.pdf`; у «Тайпит» текст ссылки — просто «PDF», а имя файла осмысленное
  (`manual-nevapro.pdf`); у «Милур» текст ссылки — «Скачать», а название документа зашито в
  имя файла («Руководство по эксплуатации....pdf»).
- Отсюда главное правило поиска: ключевые слова ищутся **и в тексте ссылки, и в её URL** —
  на реальных сайтах информативно то одно, то другое, и опора только на текст (интуитивно
  очевидный вариант) пропустила бы «Тайпит» и «Милур».

Поэтому адаптер не пытается угадывать структуру конкретного сайта, а обходит его вширь
от стартовой страницы, отдавая приоритет страницам, похожим на карточку нужной модели.
Обход намеренно ограничен (`max_pages`, `max_depth`, задержка между запросами): цель — найти
один документ, а не выкачать сайт целиком.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

REQUEST_TIMEOUT_SECONDS = 25.0
# Вежливая пауза между запросами к одному сайту: мы ходим по чужому публичному сайту без
# договорённости, агрессивный обход недопустим.
CRAWL_DELAY_SECONDS = 0.5
DEFAULT_MAX_PAGES = 25
DEFAULT_MAX_DEPTH = 3

# Признаки нужного документа. «рэ» — с границами слова, иначе матчится внутри любого слова
# с этими буквами.
_MANUAL_KEYWORDS = re.compile(
    r"руководств|эксплуатац|инструкц|manual|(?:^|[^а-яё])рэ(?:[^а-яё]|$)", re.IGNORECASE
)
# Документы, которые НЕ являются руководством, но лежат рядом на тех же карточках и легко
# ловятся общим фильтром (см. разведку по «Энергомере»: ce101_ot.pdf, ce101_st.pdf и т.д.).
_NEGATIVE_KEYWORDS = re.compile(
    r"сертификат|деклараци|формуляр|описание\s*типа|политик|прайс|price", re.IGNORECASE
)
_SKIP_URL_PARTS = re.compile(
    r"/(news|blog|press|vacanc|contact|about|career|search|login|cart|basket)(/|$)", re.IGNORECASE
)


@dataclass
class ManualCandidate:
    """Найденный документ-кандидат. `score` — насколько уверенно это именно руководство
    именно для нужной модели; вызывающий код берёт лучший, но видит и остальные."""

    url: str
    title: str
    found_on: str
    score: float

    def __repr__(self) -> str:  # для читаемых логов
        return f"ManualCandidate(score={self.score:.1f}, title={self.title!r}, url={self.url!r})"


def _model_tokens(model_name: str) -> list[str]:
    """Значимые части названия модели: «CE101 R5 145 M6» → ['ce101','r5','145','m6'].
    Односимвольные куски отбрасываются — они дают ложные совпадения где угодно."""

    return [token for token in re.split(r"[\s\-_/.,]+", model_name.lower()) if len(token) > 1]


def _looks_like_model_page(url: str, text: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    haystack = f"{url.lower()} {text.lower()}"
    matched = sum(1 for token in tokens if token in haystack)
    return matched / len(tokens)


class ManufacturerSiteAdapter:
    def __init__(
        self,
        *,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        crawl_delay: float = CRAWL_DELAY_SECONDS,
    ) -> None:
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.crawl_delay = crawl_delay

    def find_user_manual(self, website: str, model_name: str) -> list[ManualCandidate]:
        """Возвращает кандидатов, отсортированных по убыванию `score` (лучший — первый).
        Пустой список — не ошибка: у части производителей руководства просто нет в открытом
        доступе, вызывающий код должен уметь это пережить (раздел 5.9 ТЗ)."""

        tokens = _model_tokens(model_name)
        base_host = urlparse(website).netloc
        seen_pages: set[str] = set()
        candidates: list[ManualCandidate] = []
        # (глубина, приоритет, url); приоритет — «похожесть» ссылки на карточку модели,
        # чтобы при ограниченном бюджете страниц сначала смотреть самое перспективное.
        queue: deque[tuple[int, float, str]] = deque([(0, 1.0, website)])

        with httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            verify=resolve_verify(website),
        ) as client:
            while queue and len(seen_pages) < self.max_pages:
                depth, _, url = queue.popleft()
                normalised = url.split("#", 1)[0].rstrip("/")
                if normalised in seen_pages:
                    continue
                seen_pages.add(normalised)

                try:
                    if seen_pages:
                        time.sleep(self.crawl_delay)
                    response = fetch_with_retry(client, "GET", url, max_attempts=2)
                    if "html" not in response.headers.get("content-type", "").lower():
                        continue
                    soup = BeautifulSoup(response.text, "lxml")
                except Exception as exc:  # noqa: BLE001 - недоступная страница не должна прерывать обход
                    logger.debug(f"Сайт производителя: страница {url} не обработана: {exc}")
                    continue

                page_relevance = _looks_like_model_page(url, soup.get_text(" ", strip=True)[:3000], tokens)
                links = soup.find_all("a", href=True)

                for anchor in links:
                    href = anchor["href"]
                    text = anchor.get_text(" ", strip=True)
                    absolute = urljoin(url, href)

                    if self._is_document_link(absolute):
                        candidate = self._score_document(absolute, text, url, tokens, page_relevance)
                        if candidate is not None:
                            candidates.append(candidate)
                        continue

                    if depth >= self.max_depth:
                        continue
                    if urlparse(absolute).netloc != base_host:
                        continue
                    if _SKIP_URL_PARTS.search(urlparse(absolute).path):
                        continue
                    if absolute.split("#", 1)[0].rstrip("/") in seen_pages:
                        continue

                    link_relevance = _looks_like_model_page(absolute, text, tokens)
                    queue.append((depth + 1, link_relevance, absolute))

                # Сначала обходим ссылки, наиболее похожие на карточку искомой модели.
                queue = deque(sorted(queue, key=lambda item: (-item[1], item[0])))

        deduped: dict[str, ManualCandidate] = {}
        for candidate in candidates:
            existing = deduped.get(candidate.url)
            if existing is None or candidate.score > existing.score:
                deduped[candidate.url] = candidate
        return sorted(deduped.values(), key=lambda c: c.score, reverse=True)

    @staticmethod
    def _is_document_link(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith((".pdf", ".doc", ".docx"))

    @staticmethod
    def _score_document(
        url: str, text: str, found_on: str, tokens: list[str], page_relevance: float
    ) -> ManualCandidate | None:
        # Ключевые слова ищем и в подписи, и в URL: на реальных сайтах информативно то одно,
        # то другое (см. докстринг модуля).
        haystack = f"{text} {url}"
        if not _MANUAL_KEYWORDS.search(haystack):
            return None
        if _NEGATIVE_KEYWORDS.search(haystack):
            return None

        score = 1.0
        if _MANUAL_KEYWORDS.search(text):
            score += 1.0  # явная подпись «Руководство по эксплуатации» — сильный признак
        # Совпадение модели в имени файла/подписи важнее, чем на странице целиком:
        # на карточке модели могут висеть документы соседних моделей.
        score += 2.0 * _looks_like_model_page(url, text, tokens)
        score += page_relevance

        return ManualCandidate(url=url, title=text or urlparse(url).path.rsplit("/", 1)[-1], found_on=found_on, score=score)


def download_document_text(url: str) -> str | None:
    """Скачивает найденный документ и извлекает текст парсерами Этапа 3 (PDF с OCR-fallback,
    DOCX). `None` при любой ошибке — вызывающий сервис логирует и продолжает (раздел 5.9 ТЗ)."""

    from app.services.document_extraction import extract_text

    try:
        with httpx.Client(
            headers={"User-Agent": DEFAULT_USER_AGENT},
            timeout=60.0,
            follow_redirects=True,
            verify=resolve_verify(url),
        ) as client:
            response = fetch_with_retry(client, "GET", url, max_attempts=2)
    except Exception as exc:  # noqa: BLE001 - см. docstring
        logger.warning(f"Сайт производителя: не удалось скачать документ {url}: {exc}")
        return None

    suffix = "." + urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ".pdf"
    try:
        return extract_text(suffix, response.content)
    except Exception as exc:  # noqa: BLE001 - см. docstring
        logger.warning(f"Сайт производителя: не удалось извлечь текст из {url}: {exc}")
        return None
