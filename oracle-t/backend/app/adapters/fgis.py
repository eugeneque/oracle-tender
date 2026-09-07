"""Адаптер реестра ФГИС Росстандарта (раздел 4.2, 5.3 ТЗ) — поиск кодов СИ по производителю,
получение карточки типа и документа «Описание типа».

**Переписан по живому API** (разведка 30.08.2026). Прошлая версия писалась вслепую — с машины
разработчика `fgis.gost.ru` не резолвился, — и опиралась на два неверных предположения:

1. **Не тот реестр.** URL из раздела 4.2 ТЗ (`/fundmetrology/cm/results`) — это «Сведения
   о результатах поверки СИ»: 726 млн записей вида «прибор с заводским номером X поверен
   тогда-то». Ни «Описания типа», ни списка модификаций там нет в принципе. Нужен другой
   раздел ФИФ ОЕИ — «Утверждённые типы СИ» (`/fundmetrology/cm/mits`, ~107 тыс. записей),
   и у него другой API (константы ниже).
2. **Не те поля.** Реальные имена — `number`/`title`/`notation`/`manufacturers`/`mit_uuid`,
   ни один из перебиравшихся ранее алиасов (`mit_number`, `type_name`, `description_url`…)
   не существует.

Работа с реестром — три шага, каждый со своим эндпоинтом:

- `LIST_URL` — поиск по реестру, отдаёт «шапку» записи (номер ГРСИ, наименование,
  обозначение, изготовители, `mit_uuid`);
- `CARD_URL` — карточка типа по `mit_uuid`: структура условного обозначения, МПИ, срок
  действия и **список версий** документа «Описание типа» с их `doc_uuid`;
- `FILE_URL` — собственно файл документа по `doc_uuid`.

Два практических ограничения живого сервиса, ради которых здесь есть неочевидный код:

- **`FILE_URL` нестабилен**: отдаёт 504 либо висит дольше минуты (воспроизведено и через
  curl, и из браузера с живой сессией сайта). Поэтому у скачивания свой короткий таймаут
  и всего две попытки — иначе один документ съедает минуты, — а при неудаче используется
  зеркало `all-pribors.ru`, где тот же файл отдаётся за доли секунды (см. `_mirror_url`).
- **Поиск полнотекстовый, а не по полю изготовителя.** Параметр `fq` уходит в Solr как
  строка запроса, и это ломает стратегию «искать по точному юридическому названию» из
  раздела 5.3 ТЗ сразу с двух сторон: кавычки из ЕГРЮЛ ломают разбор запроса
  (`*ООО "МИРТЕК"*` вернул весь реестр целиком — 106 913 записей), а пробел работает как
  OR (`*Нижегородский завод*` → 2808 записей, среди них «Завод EJF, ЧЕХИЯ» и «Завод
  Электроаппарат, СССР»). Поэтому запрос строится по одному самому характерному токену
  бренда, а отсев по изготовителю делается на нашей стороне (`_manufacturer_matches`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry, resolve_verify

LIST_URL = "https://fgis.gost.ru/fundmetrology/cm/xcdb/mit24/list"
CARD_URL = "https://fgis.gost.ru/fundmetrology/cm/xcdb/mit24/get"
FILE_URL = "https://fgis.gost.ru/fundmetrology/api/downloadfile/{doc_uuid}"

# Зеркало Госреестра СИ: публикует те же PDF «Описания типа» под именем файла из ФГИС.
# Используется только как запасной вариант, когда сам ФГИС файл не отдал (см. докстринг).
MIRROR_FILE_URL = "https://all-pribors.ru/docs/{filename}"

SEARCH_TIMEOUT_SECONDS = 45.0
# Короче, чем у остальных документов системы, и осознанно: FILE_URL регулярно «висит», а не
# отвечает ошибкой. Лучше быстро сдаться и уйти на зеркало, чем блокировать очередь.
DOWNLOAD_TIMEOUT_SECONDS = 30.0
DOWNLOAD_ATTEMPTS = 2

# Размер страницы выдачи и потолок сканирования. Потолок нужен, потому что термин поиска
# бывает вынужденно широким: у НПО «МИР» единственное значимое слово названия — «мир», и
# полнотекстовый `*мир*` находит 2807 записей (Владимир, МИРТЕК, «мировой» в наименованиях).
# Своих среди них десятки, но лежат они вперемешку по всей выдаче — обрезать её нельзя,
# иначе производитель просто «потеряет» часть типов.
SEARCH_ROWS = 500
MAX_SCAN_RECORDS = 6000

_RAW_RESPONSE_LOG_LIMIT = 500

# Организационно-правовые формы и «шумные» слова юридического названия: в реестре
# изготовитель записан как попало («ООО "МИРТЕК"», «Общество с ограниченной ответственностью
# «МИРТЕК»», просто «ООО «МИРТЕК», РОССИЯ, 347927, …»), поэтому для поиска и сопоставления
# они выбрасываются с обеих сторон.
# Выбрасываются только собственно организационно-правовые формы и вода. Отраслевые
# аббревиатуры (НПО, НПП, НПК, НТЦ) намеренно оставлены: они не шум, а различитель —
# без «нпо» название ООО «НПО "МИР"» вырождается в один токен «мир» и подтягивает
# ООО «Мир» из Казани, ГУП НИИ «Мир-Продмаш» и ООО «Сиб МИР» как своих.
_LEGAL_FORM_WORDS = {
    "ооо", "оао", "зао", "пао", "ао", "нао", "ип",
    "фгуп", "гуп", "муп", "фгбу", "фку", "фгку", "гк",
    "общество", "с", "ограниченной", "ответственностью",
    "акционерное", "публичное", "непубличное", "закрытое", "открытое",
    "федеральное", "государственное", "унитарное", "предприятие", "учреждение",
    "концерн", "компания", "фирма", "группа", "холдинг", "корпорация",
    "научно", "производственное", "объединение", "предприятия",
    "завод", "заводы", "комбинат", "фабрика",
    "имени", "им", "россия", "рф",
}

# Кавычки всех сортов, которыми в разных источниках обрамляют название.
_QUOTES = "\"'«»„“”‟‹›`"


def _normalize(value: str) -> str:
    """Приводит название к виду, пригодному для сравнения: нижний регистр, без кавычек,
    без пунктуации, одиночные пробелы. «ООО «МИР-ТЕК»» и 'ООО "МИР ТЕК"' дают одно и то же."""

    lowered = value.lower().replace("ё", "е")
    cleaned = re.sub(rf"[{re.escape(_QUOTES)}]", " ", lowered)
    cleaned = re.sub(r"[^0-9a-zа-я]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def brand_tokens(legal_name: str) -> list[str]:
    """Значимые слова названия — то, что отличает этого производителя от остальных.
    'Общество с ограниченной ответственностью "МИРТЕК"' → ['миртек'];
    'ФГУП «Нижегородский завод им. М.В. Фрунзе»' → ['нижегородский', 'фрунзе']
    (слово «завод» отброшено как шумное — именно из-за него поиск по этому названию
    возвращал 2808 чужих записей).

    Односимвольные обрывки инициалов отбрасываются: они дают совпадение где угодно."""

    return [
        token
        for token in _normalize(legal_name).split()
        if len(token) > 1 and token not in _LEGAL_FORM_WORDS
    ]


def _search_term(tokens: list[str]) -> str | None:
    """Один токен для запроса к реестру. Пробел в `fq` работает как OR, поэтому многословный
    запрос только размывает выдачу — берём самый длинный токен как самый характерный,
    а остальные применяем локальным фильтром."""

    return max(tokens, key=len) if tokens else None


def _manufacturer_matches(doc_manufacturers: Any, tokens: list[str]) -> bool:
    """Запись действительно принадлежит нужному производителю: в поле изготовителей
    присутствуют **все** значимые токены названия. Именно этот фильтр отсекает мусор
    полнотекстового поиска — «Завод EJF, ЧЕХИЯ» не содержит ни «нижегородский», ни «фрунзе».

    Сравнение пословное, а не по подстроке, и это принципиально: среди производителей
    раздела 4.3 ТЗ есть короткие названия («МИР», «КПЗ», «РиМ»), и подстрочный матч отдал бы
    НПО «МИР» все записи МИРТЕК, а РиМ — любое слово с «рим» внутри."""

    if not tokens:
        return False
    words = set(_normalize(_as_text(doc_manufacturers)).split())
    return all(token in words for token in tokens)


def _as_text(value: Any) -> str:
    """Поля реестра приходят то строкой, то списком, то JSON-строкой со списком внутри."""

    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("[", "{")):
            try:
                return _as_text(json.loads(stripped))
            except ValueError:
                return stripped
        return stripped
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value)


@dataclass
class DescriptionTypeDoc:
    """Одна версия документа «Описание типа». Версий у типа бывает несколько (у 61891-15 на
    момент разведки — четыре: 2024 год и три за 2026-й, каждая по своему приказу
    Росстандарта), и сравнивать тендер надо с актуальной, иначе система будет проверять
    требования по отменённым характеристикам."""

    doc_uuid: str
    filename: str | None = None
    version_num: int | None = None
    order_number: str | None = None
    order_date: str | None = None

    @property
    def url(self) -> str:
        return FILE_URL.format(doc_uuid=self.doc_uuid)

    @property
    def mirror_url(self) -> str | None:
        return _mirror_url(self.filename)


@dataclass
class SiSearchResult:
    """Запись реестра утверждённых типов СИ.

    `si_code` — **номер в Госреестре** (`61891-15`), а не обозначение типа: обозначение
    приходит в разных кавычках («МИРТЕК-212-РУ», "МИРТЕК-312-РУ", МИРТЕК-101), лежит в
    поле-списке и как ключ ненадёжно. Номер ГРСИ уникален и стабилен."""

    si_code: str
    type_name: str | None = None
    notation: str | None = None
    manufacturer_name: str | None = None
    mit_uuid: str | None = None
    # Как запись связана с производителем: `legal` — изготовитель в реестре совпал с
    # юридическим названием из справочника (надёжно), `brand` — совпало только торговое имя
    # (правдоподобно, но требует внимания человека: под тем же брендом может работать чужое
    # юрлицо — так «Пульсар» ловит одноимённые компании, не связанные с ТЕПЛОВОДОХРАН).
    matched_by: str = "legal"
    description_type_url: str | None = None
    description_type_mirror_url: str | None = None
    description_type_version: str | None = None
    allowed_modifications: str | None = None
    mpi_months: int | None = None
    valid_to: date | None = None
    is_actual: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _mirror_url(filename: str | None) -> str | None:
    """Ссылка на тот же документ в зеркале. Имя файла в ФГИС уже включает год и номер ГРСИ
    (`2026-61891-15.pdf`) и в зеркале совпадает.

    Оговорка: у разных версий одного типа имя файла в ФГИС одинаковое, а зеркало нумерует
    их суффиксом (`…-3.pdf`, `…-4.pdf`). Поэтому зеркало гарантирует документ по этому типу,
    но не обязательно ту же самую версию — вызывающий код помечает источник в логе, чтобы
    расхождение было видно человеку при проверке."""

    if not filename:
        return None
    safe = Path(filename).name  # имя из ответа ФГИС в путь зеркала подставляем без каталогов
    if not safe.lower().endswith(".pdf"):
        return None
    return MIRROR_FILE_URL.format(filename=safe)


class FgisAdapter:
    def search_by_manufacturer(
        self, legal_name: str, *, brand_name: str | None = None, fetch_cards: bool = True
    ) -> list[SiSearchResult]:
        """Поиск типов СИ производителя (раздел 5.3 ТЗ, п.1 алгоритма заполнения каталога).

        Поиск и отбор здесь намеренно разведены, потому что бренд и юрлицо играют разные
        роли. Как **поисковый термин** годятся оба — какое из имён попало в реестр, заранее
        неизвестно (ООО «Телематические Решения» продаёт под маркой Waviot, а АО «Радио и
        Микроэлектроника» записано как «АО «РиМ»»), поэтому запрашиваются оба и выдача
        объединяется. А вот **признать запись своей** по одному лишь совпадению бренда
        нельзя: торговая марка не всегда принадлежит нужному юрлицу — поиск по бренду
        «Пульсар» находит 75 записей одноимённых компаний, никак не связанных с
        ООО «НПП "ТЕПЛОВОДОХРАН"». Поэтому такие записи возвращаются с `matched_by="brand"`
        и идут после надёжных — человек при проверке (раздел 5.3 ТЗ) видит разницу.

        Ошибка сети/формата — не исключение, а пустой список с записью в лог: один
        производитель не должен ронять заполнение каталога целиком (тот же принцип
        изоляции, что и в адаптерах площадок)."""

        legal_tokens = brand_tokens(legal_name)
        brand_tok = brand_tokens(brand_name or "")
        if not legal_tokens and not brand_tok:
            logger.warning(
                f"ФГИС: из названия «{brand_name or legal_name}» не удалось выделить ни одного "
                "значимого слова для поиска — запрос не отправлен"
            )
            return []

        # Собираем выдачу по обоим терминам в один словарь: один и тот же тип может прийти
        # из обоих запросов, дедупликация — по номеру ГРСИ.
        docs: dict[str, dict[str, Any]] = {}
        searched_terms: list[str] = []
        for tokens in (legal_tokens, brand_tok):
            term = _search_term(tokens)
            if term is None or term in searched_terms:
                continue
            searched_terms.append(term)
            for doc in self._scan(term, label=legal_name):
                si_code = _as_text(doc.get("number")).strip()
                if si_code:
                    docs.setdefault(si_code, doc)

        matched: list[SiSearchResult] = []
        by_brand_only = 0
        for si_code, doc in docs.items():
            manufacturers = doc.get("manufacturers")
            if legal_tokens and _manufacturer_matches(manufacturers, legal_tokens):
                matched_by = "legal"
            elif brand_tok and _manufacturer_matches(manufacturers, brand_tok):
                matched_by = "brand"
                by_brand_only += 1
            else:
                continue
            matched.append(
                SiSearchResult(
                    si_code=si_code,
                    type_name=_as_text(doc.get("title")) or None,
                    notation=_as_text(doc.get("notation")).strip(_QUOTES + " ") or None,
                    manufacturer_name=_as_text(doc.get("manufacturers")) or None,
                    mit_uuid=_as_text(doc.get("mit_uuid")) or None,
                    matched_by=matched_by,
                    raw=doc,
                )
            )

        if not matched and docs:
            # Строгий отбор («все значимые слова названия присутствуют») ничего не дал, хотя
            # выдача не пуста: скорее всего, в реестре компания записана короче, чем в нашем
            # справочнике (ООО «Завод Нартис» → «ООО «Нартис»»). Ослабляем до ядра названия,
            # но помечаем результат как требующий проверки.
            core = _search_term(legal_tokens) or _search_term(brand_tok)
            for si_code, doc in docs.items():
                if core and _manufacturer_matches(doc.get("manufacturers"), [core]):
                    matched.append(
                        SiSearchResult(
                            si_code=si_code,
                            type_name=_as_text(doc.get("title")) or None,
                            notation=_as_text(doc.get("notation")).strip(_QUOTES + " ") or None,
                            manufacturer_name=_as_text(doc.get("manufacturers")) or None,
                            mit_uuid=_as_text(doc.get("mit_uuid")) or None,
                            matched_by="brand",
                            raw=doc,
                        )
                    )
            if matched:
                logger.info(
                    f"ФГИС: по «{legal_name}» точных совпадений по названию нет, отобрано "
                    f"{len(matched)} записей по ядру «{core}» — требуют проверки человеком"
                )

        # Надёжные совпадения — первыми, дальше по убыванию номера ГРСИ (свежие сверху).
        matched.sort(key=lambda r: (r.matched_by != "legal", _sort_key(r.si_code)), reverse=False)

        logger.info(
            f"ФГИС: по «{legal_name}» найдено {len(matched)} типов СИ "
            f"(просмотрено записей выдачи: {len(docs)}, из них совпали только по бренду: {by_brand_only})"
        )

        if fetch_cards:
            for result in matched:
                self.enrich_from_card(result)
        return matched

    def _scan(self, term: str, *, label: str) -> list[dict[str, Any]]:
        """Постранично забирает всю выдачу по термину (до `MAX_SCAN_RECORDS`). Без пагинации
        широкий термин молча терял бы бо́льшую часть записей производителя — они лежат
        вперемешку с чужими по всей выдаче, а не в начале."""

        collected: list[dict[str, Any]] = []
        start = 0
        while start < MAX_SCAN_RECORDS:
            payload = self._get_json(
                LIST_URL,
                params={
                    "fq": f"*{term}*",
                    "sort": "num1 desc,num2 desc",
                    "start": start,
                    "rows": SEARCH_ROWS,
                },
                what=f"поиск по «{label}» (токен «{term}», записи с {start})",
            )
            if payload is None:
                break

            page = self._extract_docs(payload)
            if page is None:
                top_level = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
                logger.warning(
                    f"ФГИС: в ответе на поиск по «{label}» нет списка записей "
                    f"(верхний уровень: {top_level})"
                )
                break

            collected.extend(doc for doc in page if isinstance(doc, dict))

            total = _total_found(payload)
            start += SEARCH_ROWS
            if len(page) < SEARCH_ROWS or (total is not None and start >= total):
                break
        else:
            logger.warning(
                f"ФГИС: выдача по токену «{term}» превысила потолок сканирования "
                f"({MAX_SCAN_RECORDS} записей) — часть типов СИ могла не попасть в результат; "
                "уточните торговое имя производителя в справочнике"
            )
        return collected

    def enrich_from_card(self, result: SiSearchResult) -> SiSearchResult:
        """Дополняет запись данными карточки типа: структура условного обозначения (из неё
        и определяется входимость комплектующих — интерфейсы, реле, датчики), МПИ, срок
        действия и ссылка на актуальную версию «Описания типа». Мутирует и возвращает
        переданный объект; при ошибке — оставляет поля пустыми, не бросая исключение."""

        if not result.mit_uuid:
            return result

        payload = self._get_json(
            CARD_URL,
            params={"q": f"mit_uuid:{result.mit_uuid}"},
            what=f"карточка типа {result.si_code}",
        )
        if payload is None:
            return result

        docs = self._extract_docs(payload) or []
        card = next((doc for doc in docs if isinstance(doc, dict)), None)
        if card is None:
            logger.warning(f"ФГИС: карточка типа {result.si_code} пуста")
            return result

        result.raw = {**result.raw, "card": card}
        result.allowed_modifications = _as_text(card.get("j_modification")) or None
        result.is_actual = card.get("is_actual") if isinstance(card.get("is_actual"), bool) else None
        result.valid_to = _parse_date(card.get("valid_to"))
        result.mpi_months = _parse_mpi_months(card.get("j_mpis"))
        if not result.notation:
            result.notation = _as_text(card.get("j_notation")).strip(_QUOTES + " ") or None

        latest = latest_description_type(card.get("j_specifications"))
        if latest is not None:
            result.description_type_url = latest.url
            result.description_type_mirror_url = latest.mirror_url
            result.description_type_version = (
                str(latest.version_num) if latest.version_num is not None else None
            )
        return result

    def fetch_description_type_text(self, url: str, *, mirror_url: str | None = None) -> str | None:
        """Скачивает «Описание типа» и извлекает текст тем же парсером, что и документы
        тендеров (`app/services/document_extraction.py`).

        Сначала ФГИС, при неудаче — зеркало (см. докстринг модуля: штатный эндпоинт файлов
        регулярно недоступен). `None`, если не получилось нигде: вызывающий код помечает
        `SiType` как не покрытый автозаполнением и не падает (раздел 5.9 ТЗ)."""

        from app.services.document_extraction import extract_text

        for candidate, origin in ((url, "ФГИС"), (mirror_url, "зеркало all-pribors.ru")):
            if not candidate:
                continue
            content = self._download(candidate, origin)
            if content is None:
                continue
            suffix = Path(urlparse(candidate).path).suffix or ".pdf"
            try:
                text = extract_text(suffix, content)
            except Exception as exc:  # noqa: BLE001 - битый файл не должен ронять заполнение каталога
                logger.warning(f"ФГИС: не удалось извлечь текст «Описания типа» ({candidate}): {exc}")
                continue
            if text:
                if origin != "ФГИС":
                    logger.info(
                        f"«Описание типа» получено через {origin} ({candidate}) — ФГИС файл "
                        "не отдал; версия документа может отличаться, требуется проверка человеком"
                    )
                return text

        logger.warning(f"ФГИС: «Описание типа» не удалось получить ни из ФГИС ({url}), ни из зеркала")
        return None

    def _download(self, url: str, origin: str) -> bytes | None:
        try:
            with httpx.Client(
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                verify=resolve_verify(url),
                follow_redirects=True,
            ) as client:
                response = fetch_with_retry(client, "GET", url, max_attempts=DOWNLOAD_ATTEMPTS)
        except Exception as exc:  # noqa: BLE001 - см. fetch_description_type_text
            logger.warning(f"Не удалось скачать «Описание типа» из {origin} ({url}): {exc}")
            return None

        content = response.content
        if not content:
            logger.warning(f"{origin} вернул пустой ответ на «Описание типа» ({url})")
            return None
        return content

    def _get_json(self, url: str, *, params: dict[str, Any], what: str) -> Any | None:
        try:
            with httpx.Client(
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                timeout=SEARCH_TIMEOUT_SECONDS,
                verify=resolve_verify(url),
            ) as client:
                response = fetch_with_retry(client, "GET", url, params=params)
        except Exception as exc:  # noqa: BLE001 - недоступность ФГИС не прерывает заполнение каталога
            logger.warning(f"ФГИС: запрос «{what}» не выполнен: {exc}")
            return None

        try:
            return response.json()
        except ValueError:
            # Характерный симптом неверного пути: SPA-оболочка отдаётся с кодом 200 и
            # Content-Type text/html — именно так вёл себя старый URL из раздела 4.2 ТЗ.
            logger.warning(
                f"ФГИС: ответ на «{what}» не является JSON (первые {_RAW_RESPONSE_LOG_LIMIT} симв.: "
                f"{response.text[:_RAW_RESPONSE_LOG_LIMIT]!r}) — проверьте, что запрос идёт "
                f"в реестр утверждённых типов ({LIST_URL}), а не на страницу интерфейса"
            )
            return None

    @staticmethod
    def _extract_docs(payload: Any) -> list[Any] | None:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            response_block = payload.get("response")
            if isinstance(response_block, dict) and isinstance(response_block.get("docs"), list):
                return response_block["docs"]
            for key in ("docs", "items", "results", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return None


def latest_description_type(raw_specifications: Any) -> DescriptionTypeDoc | None:
    """Актуальная версия «Описания типа» из поля `j_specifications` карточки.

    Порядок в ответе не гарантирует свежесть, поэтому сортируем по `version_num`, а при его
    отсутствии (у старых записей его нет) — по дате приказа. Документы других видов
    (методики поверки и т.п.) в этом поле тоже встречаются и отсеиваются по `title`."""

    specs = raw_specifications
    if isinstance(specs, str):
        try:
            specs = json.loads(specs)
        except ValueError:
            return None
    if not isinstance(specs, list):
        return None

    candidates: list[DescriptionTypeDoc] = []
    for item in specs:
        if not isinstance(item, dict):
            continue
        if "описание типа" not in str(item.get("title", "")).lower():
            continue
        doc_uuid = str(item.get("doc_uuid") or "").strip()
        if not doc_uuid:
            continue
        candidates.append(
            DescriptionTypeDoc(
                doc_uuid=doc_uuid,
                filename=item.get("filename"),
                version_num=_parse_int(item.get("version_num")),
                order_number=item.get("order_number"),
                order_date=item.get("order_date"),
            )
        )

    if not candidates:
        return None
    return max(candidates, key=lambda doc: (doc.version_num or 0, doc.order_date or ""))


def _total_found(payload: Any) -> int | None:
    """`numFound` из Solr-обёртки — сколько всего записей нашлось по термину."""

    if isinstance(payload, dict):
        block = payload.get("response")
        if isinstance(block, dict):
            return _parse_int(block.get("numFound"))
    return None


def _sort_key(si_code: str) -> tuple[int, int]:
    """Номер ГРСИ «61891-15» → (-61891, -15): свежие номера выше. Нечисловые формы
    (в реестре встречаются) уходят в конец, а не роняют сортировку."""

    match = re.match(r"^(\d+)-(\d+)$", si_code.strip())
    if not match:
        return (1, 0)
    return (-int(match.group(1)), -int(match.group(2)))


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_mpi_months(raw: Any) -> int | None:
    """МПИ из `j_mpis` — список вида `[{"mpi": 192}]`, значение в месяцах."""

    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                months = _parse_int(item.get("mpi"))
                if months:
                    return months
    return None


def _parse_date(value: Any) -> date | None:
    """`valid_to` приходит в ISO с временем и зоной (`2030-06-29T00:00:00Z`)."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
