"""rusprofile.ru как источник сведений о компании (раздел 7 ТЗ; расширено 18.09.2026).

Два режима работы.

**Без учётной записи** — как и прежде: разбор карточки по прямой ссылке даёт юридические
данные (наименование, ИНН/КПП, ОГРН, дата регистрации, адрес) для автопоиска в профиле.
Этого хватало, пока от rusprofile требовался только КПП, которого нет в выдаче ЕГРЮЛ.

**С учётной записью** (`RusprofileSession`, учётные данные — в «Интеграции → Rusprofile»)
сайт отдаёт то, что без подписки закрыто символами «░»: полный список госзакупок компании
**с проигрышами**, лицензии, численность, финансы, учредителей. Это меняет смысл раздела
«Моя компания»: допуски, реализованные проекты и историю участий больше не нужно вводить
руками — они приходят с сайта одной кнопкой (`app/services/rusprofile_service.py`).

Почему именно проигрыши важны: реестр контрактов ЕИС знает только о победах (см.
`app/models/company_participation.py`), а rusprofile показывает закупки, где компания
участвовала и не выиграла, — единственный найденный открытый источник, где это есть.

Как устроен сайт (проверено 18.09.2026):

* поиск — `GET /ajax.php?query=<ИНН|название>&action=search`, JSON `{"ul": [{"link":
  "/id/6723224", "inn": "!~~2635819741~~!", ...}]}` (ИНН обёрнут маркерами подсветки);
* вход — `POST /auth.php?action=login` с полями `login`, `password`; ответ JSON
  `{"success": bool, "code": int, "message": str}`. Код 255 — сайт требует капчу: это
  случается после нескольких неудачных попыток, лечится входом через браузер;
* карточка — `/id/<номер>`; разделы — `/gz/<номер>/supplier` (закупки, 20 на страницу,
  пагинация `?page=N`, фильтр `purchase_status=completed_winner|completed_loser`),
  `/licenses/<номер>` (404, если лицензий нет), `/finance/<номер>` и т. д.;
* строки закупок и лицензий свёрстаны одинаково — блок `.snippet` с парами
  «ключ → значение» (`.snippet__row-key` / `.snippet__row-value`), поэтому разбор общий;
* закрытые подпиской значения заменены символом «░» — такое значение считается
  отсутствующим, а не пустой строкой, и досье помнит, что часть данных скрыта.

Данные из вёрстки берутся по служебным атрибутам (`id="clip_*"`, `itemprop`, `data-name`
плиток) и подписям строк, а не по положению на странице: подписи меняются реже оформления.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag
from loguru import logger

from app.adapters.http_utils import DEFAULT_USER_AGENT, fetch_with_retry

BASE_URL = "https://www.rusprofile.ru"
CARD_PATH = "/id/{card_id}"
SEARCH_PATH = "/ajax.php"
LOGIN_PATH = "/auth.php"
PURCHASES_PATH = "/gz/{card_id}/supplier"
LICENSES_PATH = "/licenses/{card_id}"

# Символ, которым сайт маскирует закрытые подпиской значения.
MASK_CHAR = "░"
# Код ответа `auth.php`, означающий «пройдите капчу».
CAPTCHA_CODE = 255
# Пауза между страницами одного раздела: в личном кабинете страницы отдаются быстро, но
# десяток запросов подряд без пауз — самый простой способ получить капчу на весь аккаунт.
PAGE_DELAY_SECONDS = 0.7
# Предохранитель от бесконечной пагинации, если сайт вдруг зациклит ссылку «дальше».
MAX_PAGES = 60

_CARD_ID_RE = re.compile(r"/id/(\d+)")
_DIGITS_ONLY_RE = re.compile(r"^\d+$")
_MONEY_RE = re.compile(r"(-?[\d\s ]+(?:[.,]\d+)?)\s*(млрд|млн|тыс\.?)?\s*руб", re.IGNORECASE)
_PURCHASE_TITLE_RE = re.compile(
    r"№\s*(?P<number>[\w\-/]+)\s*(?:от\s*(?P<date>\d{2}\.\d{2}\.\d{4}))?\s*(?:\((?P<law>[^)]+)\))?"
)
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_RU_DATE_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.IGNORECASE)


class RusprofileError(RuntimeError):
    """Карточка не разобрана, вход не удался или сервис недоступен — эндпоинт превращает в
    4xx с текстом."""


class RusprofileAuthError(RusprofileError):
    """Учётная запись не принята: неверный пароль, требуется капча, нет подписки."""


# --- общие мелочи ---------------------------------------------------------------------------


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _is_masked(text: str | None) -> bool:
    return bool(text) and MASK_CHAR in text


def _unmasked(text: str | None) -> str | None:
    """Значение, закрытое подпиской, — это отсутствие значения, а не строка из «░»."""

    cleaned = _norm(text)
    if cleaned is None or _is_masked(cleaned):
        return None
    return cleaned


def _text(element: Tag | None) -> str | None:
    if element is None:
        return None
    return _norm(element.get_text(" ", strip=True))


def _parse_date(value: str | None) -> date | None:
    """`28.03.2013` или `28 марта 2013 г.` — оба написания встречаются на одной странице."""

    if not value or _is_masked(value):
        return None
    value = value.strip()
    try:
        return datetime.strptime(value[:10], "%d.%m.%Y").date()
    except ValueError:
        pass
    match = _RU_DATE_RE.search(value)
    if match and match.group(2).lower() in _MONTHS:
        try:
            return date(int(match.group(3)), _MONTHS[match.group(2).lower()], int(match.group(1)))
        except ValueError:
            return None
    return None


def _parse_money(value: str | None) -> Decimal | None:
    """«525 018,00 руб.» → 525018.00; «2,2 млрд руб.» → 2200000000. Скрытое — None."""

    if not value or _is_masked(value):
        return None
    match = _MONEY_RE.search(value)
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(number)
    except InvalidOperation:
        return None
    unit = (match.group(2) or "").lower().rstrip(".")
    multiplier = {"млрд": 10**9, "млн": 10**6, "тыс": 10**3}.get(unit, 1)
    return (amount * multiplier).quantize(Decimal("0.01"))


def _parse_int(value: str | None) -> int | None:
    if not value or _is_masked(value):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _normalize_address(value: str | None) -> str | None:
    """Убирает пробелы перед запятыми: адрес на странице свёрстан по частям и склеивается
    как «355037 , Ставропольский край , г. Ставрополь»."""

    if not value:
        return None
    return re.sub(r"\s+,", ",", value).strip() or None


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
    return _unmasked(_text(element))


def _itemprop(soup: BeautifulSoup, name: str) -> str | None:
    element = soup.select_one(f"[itemprop={name}]")
    if element is None:
        return None
    value = element.get("content") or element.get_text(" ", strip=True)
    return _unmasked(value)


# --- юридические данные (прежний контракт автопоиска) --------------------------------------


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


# --- досье: всё, что есть на сводной странице карточки ---------------------------------------


@dataclass
class RusprofileDossier:
    """Сводная страница карточки в структурированном виде.

    Это не «модель компании», а слепок того, что показывает сайт: поля необязательные,
    потому что у разных компаний разделы разные, а без подписки половина значений скрыта
    (`data_hidden`). Хранится целиком в `company_profile.rusprofile_data`, чтобы интерфейс и
    промпт AI-оценки читали одно и то же и не ходили на сайт повторно.
    """

    card_id: str
    source_url: str
    fetched_at: str
    legal_name: str | None = None
    short_name: str | None = None
    status: str | None = None
    inn: str | None = None
    kpp: str | None = None
    ogrn: str | None = None
    registration_date: str | None = None
    legal_address: str | None = None
    authorized_capital: str | None = None
    ceo_name: str | None = None
    ceo_position: str | None = None
    ceo_since: str | None = None
    headcount: int | None = None
    headcount_year: int | None = None
    average_salary: str | None = None
    tax_regime: str | None = None
    msp_status: str | None = None
    main_okved_code: str | None = None
    main_okved_name: str | None = None
    okved_count: int | None = None
    tax_authority: str | None = None
    codes: dict[str, str] = field(default_factory=dict)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    website: str | None = None
    finance: dict[str, Any] = field(default_factory=dict)
    founders: list[dict[str, Any]] = field(default_factory=list)
    purchases_summary: dict[str, Any] = field(default_factory=dict)
    arbitration: str | None = None
    inspections: str | None = None
    enforcement: str | None = None
    licenses_note: str | None = None
    branches: str | None = None
    trademarks: str | None = None
    reliability: list[str] = field(default_factory=list)
    summary_text: str | None = None
    data_hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items()}


def _rows_by_title(anketa: Tag) -> dict[str, Tag]:
    """Строки анкеты по подписи: «Руководитель», «Контакты», «Основной вид деятельности»…"""

    rows: dict[str, Tag] = {}
    # Часть реквизитов (ОГРН, уставный капитал) свёрстана как `dl` внутри колонок без
    # класса строки, поэтому собираются оба вида блоков.
    for row in anketa.select(".company-row, dl"):
        title = row.select_one(".company-info__title")
        if title is None:
            continue
        key = _text(title)
        if key and key not in rows:
            rows[key] = row
    return rows


def _row_value(row: Tag | None) -> str | None:
    """Текст строки без её подписи."""

    if row is None:
        return None
    title = row.select_one(".company-info__title")
    title_text = _text(title) or ""
    full = _text(row) or ""
    if title_text and full.startswith(title_text):
        full = full[len(title_text):]
    return _unmasked(full)


def _parse_headcount(row: Tag | None) -> tuple[int | None, int | None]:
    """«6 сотрудников в 2025» → (6, 2025). Без подписки цифры скрыты, но сайт оставляет
    запасное значение в `.company-info__quetip_fell` — берём его, без года."""

    if row is None:
        return None, None
    value = _text(row.select_one("dd, .company-info__text")) or ""
    match = re.search(r"(\d[\d\s]*)\s*сотрудник\w*\s*в\s*(\d{4})", value)
    if match:
        return _parse_int(match.group(1)), int(match.group(2))
    fallback = _parse_int(_text(row.select_one(".company-info__quetip_fell")))
    return fallback, None


def _parse_ceo(row: Tag | None) -> tuple[str | None, str | None, str | None]:
    if row is None:
        return None, None, None
    titles = [_text(el) for el in row.select(".chief-title")]
    name = _unmasked(_text(row.select_one(".company-info__text a")) or _text(row.select_one(".company-info__text")))
    if name:
        # У ссылки на персону есть хвост «подробнее» — это псевдоэлемент, но в тексте он
        # иногда всплывает.
        name = re.sub(r"\s*подробнее\s*$", "", name)
    position = next((t for t in titles if t and not t.lower().startswith("с ")), None)
    since = next((t for t in titles if t and t.lower().startswith("с ")), None)
    return name, position, since


def _parse_contacts(row: Tag | None) -> tuple[list[str], list[str], str | None]:
    """Контакты сгруппированы блоками `.company-info__contact.phone|mail|site`, внутри —
    значения (ссылки `tel:`, `mailto:` или адрес сайта)."""

    phones: list[str] = []
    emails: list[str] = []
    website: str | None = None
    if row is None:
        return phones, emails, website
    for group in row.select(".company-info__contact.iconer"):
        classes = group.get("class") or []
        values = [
            v
            for v in (_unmasked(_text(el)) for el in group.select(".company-info__contact"))
            if v and not v.lower().startswith(("ещё", "еще"))
        ]
        if "phone" in classes:
            phones.extend(values)
        elif "mail" in classes:
            emails.extend(values)
        elif "site" in classes and values:
            website = values[0]
    return phones, emails, website


def _parse_okved(row: Tag | None) -> tuple[str | None, str | None, int | None]:
    if row is None:
        return None, None, None
    text_el = row.select_one(".company-info__text")
    value = _unmasked(_text(text_el)) or ""
    code_match = re.search(r"\((\d{2}(?:\.\d+)*)\)\s*$", value)
    code = code_match.group(1) if code_match else None
    name = re.sub(r"\s*\(\d{2}(?:\.\d+)*\)\s*$", "", value).strip() or None
    count_match = re.search(r"Все виды деятельности\s*\((\d+)\)", _text(row) or "")
    count = int(count_match.group(1)) if count_match else None
    return code, name, count


def _parse_codes(anketa: Tag) -> dict[str, str]:
    codes: dict[str, str] = {}
    for key in ("okpo", "okato", "oktmo", "okfs", "okogu", "okopf"):
        value = _unmasked(_text(anketa.select_one(f"#clip_{key}")))
        if value:
            codes[key] = value
    return codes


def _tile(soup: BeautifulSoup, name: str) -> Tag | None:
    return soup.select_one(f".tiles__item[data-name={name}]")


def _parse_finance(tile: Tag | None) -> dict[str, Any]:
    """Плитка «Финансы»: год, выручка/прибыль/стоимость (число + единица) и три оценки
    состояния. Без подписки прибыль и стоимость закрыты — тогда их просто нет."""

    if tile is None:
        return {}
    result: dict[str, Any] = {}
    year_match = re.search(r"за\s+(\d{4})\s+год", _text(tile.select_one(".tile-item__text")) or "")
    if year_match:
        result["year"] = int(year_match.group(1))
    labels = {"tab_revenue": "revenue", "tab_profit": "profit", "tab_value": "net_assets"}
    for col in tile.select(".finance-col"):
        opener = col.select_one(".tab-opener")
        key = labels.get(opener.get("data-tab_name", "") if opener else "")
        if not key:
            continue
        number = _unmasked(_text(col.select_one(".num")))
        unit = _unmasked(_text(col.select_one(".num-text")))
        diff = _unmasked(_text(col.select_one(".diff")))
        if number:
            result[key] = f"{number} {unit}".strip() if unit else number
            result[f"{key}_amount"] = str(_parse_money(f"{number} {unit or ''} руб") or "")
        if diff:
            result[f"{key}_change"] = diff.replace("↓", "").replace("↑", "").strip()
    # Оценки «Финансовое состояние: нормальная» и т. п. идут парами подпись/значение.
    for row in tile.select(".finance-state__item, .finance-rating__item, .finance-list__item, li"):
        text = _text(row) or ""
        match = re.match(
            r"(Финансовое состояние|Финансовая устойчивость|Платежеспособность|Эффективность)\s*[:\-]?\s*(.+)$",
            text,
        )
        if match:
            result.setdefault("ratings", {})[match.group(1)] = _unmasked(match.group(2))
    return result


def _parse_founders(tile: Tag | None) -> list[dict[str, Any]]:
    founders: list[dict[str, Any]] = []
    if tile is None:
        return founders
    for item in tile.select(".founder-item"):
        name = _unmasked(_text(item.select_one(".founder-item__title a span, .founder-item__title")))
        if name:
            name = re.sub(r"\s*подробнее\s*$", "", name)
        entry: dict[str, Any] = {"name": name}
        for dt in item.select("dt"):
            label = (_text(dt) or "").rstrip(":").lower()
            dd = dt.find_next_sibling("dd")
            value = _unmasked(_text(dd))
            if label.startswith("доля") and value:
                entry["share"] = value
            elif label.startswith("инн") and value:
                entry["inn"] = value
        if entry.get("name"):
            founders.append(entry)
    return founders


def _parse_purchases_summary(tile: Tag | None) -> dict[str, Any]:
    """Плитка «Госзакупки»: сколько закупок и контрактов, выиграно/не выиграно, топ заказчиков."""

    if tile is None:
        return {}
    text = _text(tile) or ""
    result: dict[str, Any] = {}
    match = re.search(r"(\d+)\s+закуп\w+\s+на сумму\s+([\d\s,\.]+\s*(?:млрд|млн|тыс\.?)?\s*руб\.?)", text)
    if match:
        result["purchases_count"] = int(match.group(1))
        result["purchases_sum"] = _norm(match.group(2))
    match = re.search(r"(\d+)\s+контракт\w+\s+заключено\s+на сумму\s+([\d\s,\.]+\s*(?:млрд|млн|тыс\.?)?\s*руб\.?)", text)
    if match:
        result["contracts_count"] = int(match.group(1))
        result["contracts_sum"] = _norm(match.group(2))
    match = re.search(r"Выиграно\s+(\d+)", text)
    if match:
        result["won"] = int(match.group(1))
    match = re.search(r"Не выиграно\s+(\d+)", text)
    if match:
        result["lost"] = int(match.group(1))
    match = re.search(r"(\d+)\s+закуп\w+\s+на\s+([\d\s,\.]+\s*(?:млрд|млн|тыс\.?)?\s*руб\.?)", text)
    if match and "purchases_count" not in result:
        result["purchases_count"] = int(match.group(1))
        result["purchases_sum"] = _norm(match.group(2))
    customers: list[dict[str, Any]] = []
    for item in tile.select(".founder-item"):
        name = _unmasked(_text(item.select_one(".founder-item__title")))
        if not name:
            continue
        name = re.sub(r"\s*подробнее\s*$", "", name)
        row_text = _text(item.select_one(".founder-item__dl")) or ""
        count = re.search(r"(\d+)\s+закуп", row_text)
        amount = re.search(r"на\s+([\d\s\u00a0]+)\s*руб", row_text)
        customers.append(
            {
                "name": name,
                "purchases": int(count.group(1)) if count else None,
                "sum": (_norm(amount.group(1)) + " руб.") if amount else None,
            }
        )
    if customers:
        result["top_customers"] = customers[:10]
    return result


def _tile_text(tile: Tag | None, *, drop_title: bool = True) -> str | None:
    if tile is None:
        return None
    text = _text(tile) or ""
    title = _text(tile.select_one(".tile-item__title"))
    if drop_title and title and text.startswith(title):
        text = text[len(title):].strip()
    return _norm(text)[:600] if text else None


def parse_dossier(html: str, *, card_id: str, source_url: str) -> RusprofileDossier:
    soup = BeautifulSoup(html, "lxml")
    for junk in soup(["script", "style", "svg", "noscript"]):
        junk.decompose()

    base = parse_card(html, source_url=source_url)
    dossier = RusprofileDossier(
        card_id=card_id,
        source_url=source_url,
        fetched_at=datetime.now().isoformat(timespec="seconds"),
        legal_name=base.legal_name,
        short_name=base.short_name,
        inn=base.inn,
        kpp=base.kpp,
        ogrn=base.ogrn,
        registration_date=base.registration_date.isoformat() if base.registration_date else None,
        legal_address=base.legal_address,
        status=_unmasked(
            _text(soup.select_one(".company-header__status .company-header__icon"))
        ),
        data_hidden=MASK_CHAR in html,
    )

    anketa = soup.select_one("#anketa") or soup
    rows = _rows_by_title(anketa)
    for title, row in rows.items():
        if title.startswith("Уставный капитал"):
            dossier.authorized_capital = _row_value(row)
        elif title.startswith("Руководитель"):
            dossier.ceo_name, dossier.ceo_position, dossier.ceo_since = _parse_ceo(row)
        elif title.startswith("Среднесписочная численность"):
            dossier.headcount, dossier.headcount_year = _parse_headcount(row)
        elif title.startswith("Среднемесячная зарплата"):
            dossier.average_salary = _row_value(row)
        elif title.startswith("Специальный налоговый режим"):
            dossier.tax_regime = _row_value(row)
        elif title.startswith("Реестр МСП"):
            dossier.msp_status = _row_value(row)
        elif title.startswith("Основной вид деятельности"):
            dossier.main_okved_code, dossier.main_okved_name, dossier.okved_count = _parse_okved(row)
        elif title.startswith("Налоговый орган"):
            value = _row_value(row)
            dossier.tax_authority = value
        elif title.startswith("Контакты"):
            dossier.phones, dossier.emails, dossier.website = _parse_contacts(row)
    dossier.codes = _parse_codes(anketa)

    dossier.finance = _parse_finance(_tile(soup, "accounting"))
    dossier.founders = _parse_founders(_tile(soup, "founders"))
    dossier.purchases_summary = _parse_purchases_summary(_tile(soup, "gz"))
    dossier.arbitration = _tile_text(_tile(soup, "arbitr"))
    dossier.inspections = _tile_text(_tile(soup, "inspections"))
    dossier.enforcement = _tile_text(_tile(soup, "fssp"))
    dossier.licenses_note = _tile_text(_tile(soup, "licenses"))
    dossier.branches = _tile_text(_tile(soup, "branches"))
    dossier.trademarks = _tile_text(_tile(soup, "trademarks"))
    reliability = _tile(soup, "reliability")
    if reliability is not None:
        dossier.reliability = [
            t for t in (_unmasked(_text(el)) for el in reliability.select(".tile-item__text, li, p")) if t
        ][:10]
    intro = soup.select_one(".company-description__intro-text")
    dossier.summary_text = _unmasked(_text(intro))
    return dossier


# --- сниппеты: закупки и лицензии --------------------------------------------------------------


@dataclass
class Snippet:
    """Один блок `.snippet`: статус, заголовок и пары «ключ → значение» (со ссылками)."""

    status: str | None
    title: str | None
    fields: dict[str, str]
    links: dict[str, list[tuple[str, str]]]


def parse_snippets(html: str) -> list[Snippet]:
    soup = BeautifulSoup(html, "lxml")
    snippets: list[Snippet] = []
    for block in soup.select(".snippet"):
        status = _unmasked(_text(block.select_one(".snippet__status")))
        title = _unmasked(_text(block.select_one(".snippet__row-value.--title")))
        if title:
            title = re.sub(r"^(Закупка|Контракт|Лицензия)\s*", "", title)
        fields: dict[str, str] = {}
        links: dict[str, list[tuple[str, str]]] = {}
        for row in block.select(".snippet__row"):
            key = _text(row.select_one(".snippet__row-key"))
            value_el = row.select_one(".snippet__row-value")
            if not key or value_el is None or "--title" in (value_el.get("class") or []):
                continue
            value = _unmasked(_text(value_el))
            if value:
                fields[key] = value
            anchors = [
                (_text(a) or "", a.get("href") or "")
                for a in value_el.select("a[href]")
            ]
            if anchors:
                links[key] = anchors
        if status or title or fields:
            snippets.append(Snippet(status=status, title=title, fields=fields, links=links))
    return snippets


def _next_page_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    nxt = soup.select_one(".filters-pagination__nav.--next[href]")
    if nxt is None or nxt.has_attr("disabled"):
        return None
    return nxt.get("href") or None


def _total_from_notice(html: str) -> int | None:
    match = re.search(r"из\s+(\d+)\s*<", html)
    return int(match.group(1)) if match else None


@dataclass
class PurchaseRecord:
    """Закупка из раздела «Госзакупки» в роли поставщика."""

    number: str | None
    date: date | None
    law: str | None
    status: str | None  # «Выиграно» / «Не выиграно» / другое
    won: bool | None
    subject: str | None
    customer: str | None
    customer_card_id: str | None
    participants: list[str]
    initial_price: Decimal | None
    winner_price: Decimal | None
    method: str | None
    contract_number: str | None
    contract_date: date | None
    contract_status: str | None
    contract_price: Decimal | None
    zakupki_url: str | None


def parse_purchases(html: str, *, our_card_id: str | None = None) -> list[PurchaseRecord]:
    """Строки раздела «Госзакупки». Закупка — блок `.snippet`, её контракт — следующий за ним
    блок `.snippet.sub` в той же строке списка. Скрытые подпиской строки (в них скрыт даже
    номер) не возвращаются: запись без номера не с чем сопоставить и не о чём сообщить."""

    records: list[PurchaseRecord] = []
    soup = BeautifulSoup(html, "lxml")
    for block in soup.select(".snippet"):
        if "sub" in (block.get("class") or []):
            continue
        snippet = parse_snippets(str(block))
        if not snippet or not snippet[0].title:
            continue
        item = snippet[0]
        match = _PURCHASE_TITLE_RE.search(item.title)
        if not match:
            continue
        number = match.group("number")

        contract_number = contract_date = contract_status = None
        contract_price: Decimal | None = None
        sibling = block.find_next_sibling(class_="snippet")
        if sibling is not None and "sub" in (sibling.get("class") or []):
            contract = parse_snippets(str(sibling))
            if contract and contract[0].title:
                cmatch = _PURCHASE_TITLE_RE.search(contract[0].title)
                if cmatch:
                    contract_number = cmatch.group("number")
                    contract_date = _parse_date(cmatch.group("date"))
                contract_status = contract[0].fields.get("Статус")
                contract_price = _parse_money(contract[0].fields.get("Цена"))

        participant_links = item.links.get("Участник", [])
        participants = [name for name, _href in participant_links if name]
        winner_marked = "признан победителем" in (item.fields.get("Участник") or "")
        status = item.status
        won: bool | None
        if status and status.lower().startswith("выигр"):
            won = True
        elif status and status.lower().startswith("не выигр"):
            won = False
        elif winner_marked and our_card_id:
            won = any(f"/id/{our_card_id}" in href for _name, href in participant_links)
        else:
            won = None

        customer_links = item.links.get("Заказчик", [])
        customer_card = None
        if customer_links:
            cmatch = _CARD_ID_RE.search(customer_links[0][1])
            customer_card = cmatch.group(1) if cmatch else None
        zakupki = next(
            (href for _name, href in item.links.get("Подробнее", []) if "zakupki.gov.ru" in href),
            None,
        )
        records.append(
            PurchaseRecord(
                number=number,
                date=_parse_date(match.group("date")),
                law=_norm(match.group("law")),
                status=status,
                won=won,
                subject=item.fields.get("Объект закупки"),
                customer=item.fields.get("Заказчик"),
                customer_card_id=customer_card,
                participants=participants,
                initial_price=_parse_money(item.fields.get("Начальная цена")),
                winner_price=_parse_money(item.fields.get("Предложение победителя")),
                method=item.fields.get("Способ отбора"),
                contract_number=contract_number,
                contract_date=contract_date,
                contract_status=contract_status,
                contract_price=contract_price,
                zakupki_url=zakupki,
            )
        )
    return records


@dataclass
class LicenseRecord:
    number: str | None
    issued_at: date | None
    status: str | None
    activity: str | None
    issuer: str | None
    valid_from: date | None
    valid_until: date | None
    source: str | None


def parse_licenses(html: str) -> list[LicenseRecord]:
    records: list[LicenseRecord] = []
    for item in parse_snippets(html):
        if not item.title:
            continue
        match = _PURCHASE_TITLE_RE.search(item.title)
        number = match.group("number") if match else item.title
        issued = _parse_date(match.group("date")) if match else None
        activity = item.fields.get("Вид деятельности") or item.fields.get("Виды деятельности")
        if not activity and not number:
            continue
        records.append(
            LicenseRecord(
                number=number,
                issued_at=_parse_date(item.fields.get("Дата выдачи")) or issued,
                status=item.status,
                activity=activity,
                issuer=item.fields.get("Лиценз. орган") or item.fields.get("Лицензирующий орган"),
                valid_from=_parse_date(item.fields.get("Дата начала действия")),
                valid_until=_parse_date(item.fields.get("Дата окончания действия")),
                source=item.fields.get("Источник"),
            )
        )
    return records


# --- HTTP: анонимный и авторизованный доступ ------------------------------------------------------

# Личный кабинет открывается только браузерному User-Agent: с подписью бота сайт отдаёт
# анонимную версию страниц независимо от cookie. Это не маскировка — сессия принадлежит
# учётной записи заказчика, а не боту.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def _new_client(*, user_agent: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )


@dataclass
class SearchHit:
    card_id: str
    name: str | None
    inn: str | None
    ogrn: str | None
    region: str | None
    address: str | None
    ceo_name: str | None
    inactive: bool


def _search(client: httpx.Client, query: str) -> list[SearchHit]:
    response = fetch_with_retry(
        client,
        "GET",
        SEARCH_PATH,
        params={"query": query, "action": "search"},
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        max_attempts=2,
    )
    if response.status_code >= 400:
        raise RusprofileError(f"rusprofile.ru ответил HTTP {response.status_code} на поиск «{query}».")
    try:
        data = response.json() or {}
    except ValueError as exc:
        raise RusprofileError("rusprofile.ru вернул не JSON на поиск — вероятно, проверка на робота.") from exc
    hits: list[SearchHit] = []
    for row in data.get("ul") or []:
        if not isinstance(row, dict):
            continue
        link = row.get("link") or row.get("url") or ""
        match = _CARD_ID_RE.search(link)
        if not match:
            continue
        hits.append(
            SearchHit(
                card_id=match.group(1),
                name=_norm(row.get("name") or row.get("raw_name")),
                # ИНН обёрнут маркерами подсветки совпадения: `!~~2635819741~~!`.
                inn=re.sub(r"[^\d]", "", str(row.get("inn") or "")) or None,
                ogrn=re.sub(r"[^\d]", "", str(row.get("ogrn") or row.get("raw_ogrn") or "")) or None,
                region=_norm(row.get("region")),
                address=_norm(row.get("address")),
                ceo_name=_norm(row.get("ceo_name")),
                inactive=bool(row.get("inactive")),
            )
        )
    return hits


def search_by_inn(inn: str, *, client: httpx.Client | None = None) -> SearchHit:
    """Карточка по ИНН. Поиск отдаёт и однофамильцев по названию, поэтому выбирается
    строка с точным совпадением ИНН; действующая организация — в приоритете."""

    digits = re.sub(r"\D", "", inn or "")
    if len(digits) not in (10, 12):
        raise RusprofileError(f"«{inn}» не похоже на ИНН: ожидается 10 или 12 цифр.")
    own_client = client is None
    client = client or _new_client(user_agent=BROWSER_USER_AGENT)
    try:
        hits = [hit for hit in _search(client, digits) if hit.inn == digits]
    except httpx.HTTPError as exc:
        raise RusprofileError(f"Не удалось обратиться к rusprofile.ru: {exc}") from exc
    finally:
        if own_client:
            client.close()
    if not hits:
        raise RusprofileError(f"На rusprofile.ru не найдено компании с ИНН {digits}.")
    hits.sort(key=lambda hit: hit.inactive)
    return hits[0]


def fetch_company(url_or_id: str) -> RusprofileCompany:
    """Юридические данные по ссылке на карточку — анонимно, как и раньше."""

    card_id = extract_card_id(url_or_id)
    path = CARD_PATH.format(card_id=card_id)
    try:
        with _new_client(user_agent=DEFAULT_USER_AGENT) as client:
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


class RusprofileSession:
    """Сессия личного кабинета: вход по логину/паролю, затем обычные GET по разделам карточки.

    Один объект — один вход: cookie живут в `httpx.Client`, и все страницы синхронизации
    читаются в рамках одной авторизации. Повторно логиниться на каждую страницу нельзя —
    сайт считает частые входы подозрительными и включает капчу.
    """

    def __init__(self, login: str, password: str, *, page_delay: float = PAGE_DELAY_SECONDS):
        if not login or not password:
            raise RusprofileAuthError(
                "Учётная запись rusprofile не настроена: заполните логин и пароль в разделе "
                "«Интеграции → Rusprofile»."
            )
        self._login = login.strip()
        self._password = password
        self._page_delay = page_delay
        self._client = _new_client(user_agent=BROWSER_USER_AGENT)
        self._authenticated = False
        self.has_paid_access: bool | None = None

    def __enter__(self) -> "RusprofileSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- вход --

    def login(self) -> None:
        """`POST /auth.php?action=login`. Успех — `success: true`; код 255 — капча."""

        try:
            # Сначала обычная страница: сайт выдаёт сессионные cookie и CSRF-токен, без
            # которых вход отвечает «ошибка CSRF».
            fetch_with_retry(self._client, "GET", "/", max_attempts=2)
            csrf = self._client.cookies.get("__Host-csrf-token") or ""
            response = fetch_with_retry(
                self._client,
                "POST",
                LOGIN_PATH,
                params={"action": "login"},
                data={"login": self._login, "password": self._password},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-Csrf-Token": csrf,
                    "Referer": f"{BASE_URL}/",
                },
                max_attempts=1,
            )
        except httpx.HTTPError as exc:
            raise RusprofileError(f"Не удалось обратиться к rusprofile.ru: {exc}") from exc

        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code >= 400 and not data:
            raise RusprofileAuthError(
                f"rusprofile.ru ответил HTTP {response.status_code} на попытку входа."
            )
        code = data.get("code")
        if data.get("success"):
            self._authenticated = True
            fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
            paid = fields.get("hasPaidSubscription")
            self.has_paid_access = bool(paid) if paid is not None else None
            return
        if code == CAPTCHA_CODE:
            raise RusprofileAuthError(
                "rusprofile.ru требует пройти проверку «я не робот» для этой учётной записи. "
                "Войдите один раз в браузере под этим логином, затем повторите синхронизацию."
            )
        message = _norm(str(data.get("message") or "")) or "неизвестная ошибка"
        raise RusprofileAuthError(
            f"rusprofile.ru не принял учётные данные: {message}. Проверьте логин и пароль в "
            "разделе «Интеграции → Rusprofile»."
        )

    def _ensure_login(self) -> None:
        if not self._authenticated:
            self.login()

    # -- страницы --

    def get_html(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        self._ensure_login()
        try:
            response = fetch_with_retry(self._client, "GET", path, params=params, max_attempts=2)
        except httpx.HTTPError as exc:
            raise RusprofileError(f"Не удалось загрузить {path} с rusprofile.ru: {exc}") from exc
        if response.status_code == 404:
            return ""
        if response.status_code >= 400:
            raise RusprofileError(f"rusprofile.ru ответил HTTP {response.status_code} на {path}.")
        return response.text

    def check_access(self) -> dict[str, Any]:
        """Проверка подключения: вход и то, что на карточке нет масок «░». Возвращает
        сведения для сообщения администратору."""

        self.login()
        html = self.get_html("/")
        user_id = re.search(r'RPF\.store\.user\s*=\s*\{"id":(\d+)', html)
        has_pro = re.search(r"RPF\.has_pro\s*=\s*(true|false)", html)
        return {
            "logged_in": bool(user_id),
            "user_id": user_id.group(1) if user_id else None,
            "has_pro": (has_pro.group(1) == "true") if has_pro else self.has_paid_access,
        }

    def resolve_card_id(self, *, inn: str | None, card_hint: str | None) -> str:
        """Номер карточки: из сохранённой ссылки, иначе поиском по ИНН."""

        if card_hint:
            try:
                return extract_card_id(card_hint)
            except RusprofileError:
                pass
        if not inn:
            raise RusprofileError(
                "Чтобы найти компанию на rusprofile.ru, в профиле нужен ИНН или ссылка на карточку."
            )
        self._ensure_login()
        return search_by_inn(inn, client=self._client).card_id

    def fetch_dossier(self, card_id: str) -> RusprofileDossier:
        path = CARD_PATH.format(card_id=card_id)
        html = self.get_html(path)
        if not html:
            raise RusprofileError(f"Карточка {card_id} на rusprofile.ru не найдена (HTTP 404).")
        return parse_dossier(html, card_id=card_id, source_url=f"{BASE_URL}{path}")

    def fetch_purchases(self, card_id: str) -> tuple[list[PurchaseRecord], int | None]:
        """Все страницы раздела «Госзакупки» в роли поставщика. Дедупликация по номеру закупки:
        сайт иногда показывает одну закупку с двумя контрактами двумя строками."""

        path = PURCHASES_PATH.format(card_id=card_id)
        html = self.get_html(path)
        total = _total_from_notice(html)
        seen: dict[str, PurchaseRecord] = {}
        pages = 0
        next_url: str | None = path
        while next_url and pages < MAX_PAGES:
            if pages:
                time.sleep(self._page_delay)
                html = self.get_html(next_url)
            pages += 1
            page_records = parse_purchases(html, our_card_id=card_id)
            if not page_records:
                break
            new = 0
            for record in page_records:
                if record.number and record.number not in seen:
                    seen[record.number] = record
                    new += 1
            if new == 0:
                # Сайт вернул ту же страницу — без подписки пагинация недоступна.
                break
            next_url = _next_page_url(html)
            if next_url and not next_url.startswith("/"):
                next_url = urljoin(BASE_URL, next_url).removeprefix(BASE_URL)
        return list(seen.values()), total

    def fetch_licenses(self, card_id: str) -> list[LicenseRecord]:
        html = self.get_html(LICENSES_PATH.format(card_id=card_id))
        if not html:
            return []
        records: dict[str, LicenseRecord] = {}
        pages = 0
        next_url: str | None = None
        while pages < MAX_PAGES:
            pages += 1
            for record in parse_licenses(html):
                key = record.number or f"{record.activity}|{record.issued_at}"
                records.setdefault(key, record)
            next_url = _next_page_url(html)
            if not next_url:
                break
            time.sleep(self._page_delay)
            html = self.get_html(next_url)
            if not html:
                break
        return list(records.values())
