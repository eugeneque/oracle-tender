"""НМЦК с НДС и без НДС — из документации закупки, кодом, без модели (12.11.2026).

Запрос отдела: площадка отдаёт одну «Сумму», и по ней не понять, с НДС она или без, — а
сравнивать закупки и считать свою цену нужно в одной базе. В документации это почти всегда
написано прямо в пункте о начальной (максимальной) цене:

* «…начальная (максимальная) цена договора составляет 2 187 987,55 руб. (…), в т.ч. НДС 22%
  394 555,13 руб.» — цена с НДС, ставка и сумма налога;
* «Начальная (максимальная) цена договора: 1 729 350,07 руб. без НДС.» — цена без НДС;
* «…НДС не облагается» — цена одна.

Формулировки устойчивые, поэтому разбор — регулярными выражениями по тексту документов,
уже распознанному при загрузке (`tender_documents.extracted_text`): ни токенов, ни ожидания
модели, и работает у любой закупки с документами, а не только у разобранных ИИ.

Правила:
* учитывается только абзац о начальной (максимальной) цене / НМЦК и строка-две за ним
  (в таблицах цена стоит в соседней ячейке) — «без НДС» из другого пункта к цене не относится;
* из нескольких найденных мест берётся то, где цена совпадает с «Суммой» площадки, иначе
  первое по порядку документов (извещение и документация раньше договора);
* вторая цена, которой нет в тексте, досчитывается — по ставке из текста, а если ставка не
  названа, по основной ставке 22 % (с 01.01.2026); такая цена помечается «расчётно», чтобы
  её не принимали за указанную заказчиком.

Результат — `tenders.price_vat` (JSONB), переносится на записи-двойники вместе с прочими
полями разбора (`tender_twins.SHARED_FIELDS`). Раздел 106 ARCHITECTURE.md.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.tender_document import DocumentClass, TenderDocument

# Основная ставка НДС с 01.01.2026 (было 20 %). Нужна только для досчёта второй цены, когда
# ставка в документе не названа; такая цена всегда помечается расчётной.
DEFAULT_RATE = Decimal("22")
_KNOWN_RATES = {Decimal(v) for v in ("0", "5", "7", "10", "18", "20", "22")}

INCLUDED = "included"  # цена указана с НДС
EXCLUDED = "excluded"  # цена указана без НДС
NOT_APPLICABLE = "not_applicable"  # НДС не облагается

MODE_LABELS = {
    INCLUDED: "Цена указана с НДС",
    EXCLUDED: "Цена указана без НДС",
    NOT_APPLICABLE: "НДС не облагается",
}

_ANCHOR_RE = re.compile(
    r"начальн\w*\s*\(?\s*максимальн\w*\s*\)?\s*(?:цен|сумм|стоимост)\w*"
    r"|\bН\s?М\s?Ц\s?[КДЛ]?\b"
    r"|(?:максимальн|предельн)\w*\s+(?:цен|сумм|стоимост|значени\w*\s+цен)\w*",
    re.IGNORECASE,
)
# Слабое упоминание — «Цена договора не больше 891 975,43 рублей без НДС». Так же пишут и в
# проекте договора о цене победителя («Цена договора составляет 1 (один) руб.»), поэтому
# такое место принимается, только если сумма совпала с «Суммой» площадки.
_WEAK_ANCHOR_RE = re.compile(r"(?:цен|стоимост)\w*\s+(?:договор|контракт)\w*", re.IGNORECASE)
# Деньги: «2 187 987,55», «1729350.07», «2 187 987 руб.». Разделитель тысяч — пробел,
# неразрывный или узкий пробел.
_SPACE = "[   ]"
_MONEY = rf"\d{{1,3}}(?:{_SPACE}\d{{3}})+(?:[.,]\d{{1,2}})?|\d+(?:[.,]\d{{1,2}})?"
_RUB = r"\s*(?:руб|₽|р\.)"
_PRICE_RE = re.compile(rf"(?<![\d.,])({_MONEY})(?=\s*\(|{_RUB})", re.IGNORECASE)
_BARE_NUMBER_RE = re.compile(rf"(?<![\d.,№])({_MONEY})(?![\d%]|\s*%)")
# Копейки после суммы прописью: «35 766 123 (тридцать пять миллионов …) рублей 22 копейки».
_KOPECKS_RE = re.compile(
    r"^\s*(?:\([^()]{0,300}\)\s*)?(?:российск\w+\s+)?(?:руб\w*\.?)\s*(?:РФ\s*)?,?\s*(\d{2})\s*коп",
    re.IGNORECASE,
)

_NOT_APPLICABLE_RE = re.compile(
    r"НДС\s+не\s+(?:облага|предусмотрен)\w*|не\s+облага\w*\s+НДС|без\s+НДС\s*\(\s*НДС\s+не\s+облага"
    r"|не\s+являетс\w+\s+плательщик\w*\s+НДС",
    re.IGNORECASE,
)
_VAT = r"(?:налог\w*\s+на\s+добавленную\s+стоимость(?:\s*\(\s*НДС\s*\))?|НДС)"
_RATE_PART = r"(?:\s*\(?\s*(?:по\s+ставке\s+|в\s+размере\s+)?(?P<rate>\d{1,2}(?:[.,]\d+)?)\s*%\s*\)?)?"
_AMOUNT_PART = rf"(?:\s*[-–—:=,]?\s*(?:в\s+размере|составляет|в\s+сумме)?\s*[-–—:]?\s*(?P<amount>{_MONEY}){_RUB})?"
# «без НДС», «без учёта НДС», «кроме того, НДС 22% – 1 000 руб.», «не включает в себя НДС»
_EXCLUDED_RE = re.compile(
    rf"(?:без\s+(?:уч[её]та\s+)?|кроме\s+того,?\s+|не\s+включа\w+\s+(?:в\s+себя\s+)?(?:сумм\w+\s+)?){_VAT}"
    + _RATE_PART + _AMOUNT_PART,
    re.IGNORECASE,
)
# «в т.ч. НДС 22% 394 555,13 руб.», «в том числе НДС (20 %) – 1 000,00 руб.», «включая НДС»
_INCLUDED_RE = re.compile(
    rf"(?<!\w)(?:в\s*т\.?\s*ч\.?|в\s+том\s+числе|включая|с\s+уч[её]том|с)\s+{_VAT}" + _RATE_PART + _AMOUNT_PART,
    re.IGNORECASE,
)
_RATE_RE = re.compile(r"НДС\s*\(?\s*(?:по\s+ставке\s+)?(\d{1,2}(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_CLAUSE_START_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\S")

_LOOKAHEAD_LINES = 2
_TAIL_CHARS = 400
_QUOTE_CHARS = 400


def _money(raw: str) -> Decimal | None:
    try:
        value = Decimal(re.sub(r"[\s  ]", "", raw).replace(",", "."))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _rate(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    try:
        value = Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value in _KNOWN_RATES else None


def _is_amount(match: re.Match[str]) -> bool:
    """Сумма от тысячи и не год: «2027 (две тысячи двадцать седьмой)», «Incoterms 2020» в
    шаблоне договора рядом с «___ руб.» — не цена."""

    raw, value = match.group(1), _money(match.group(1))
    if value is None or value < 1000:
        return False
    return not (re.fullmatch(r"(?:19|20)\d\d", raw))


def _block(lines: list[str], index: int, start: int) -> str:
    """Абзац с упоминанием НМЦК от самого упоминания и следующие строки до начала нового пункта
    (в таблице цена и «в т.ч. НДС» часто стоят в соседней ячейке — отдельной строкой)."""

    parts = [lines[index][start:]]
    for line in lines[index + 1 : index + 1 + _LOOKAHEAD_LINES]:
        if _CLAUSE_START_RE.match(line) or _ANCHOR_RE.search(line):
            break
        parts.append(line)
    return " ".join(" ".join(parts).split())


def _vat_marker(text: str) -> tuple[str, re.Match[str]] | None:
    """Самое раннее упоминание НДС в тексте: какое оно и где. «Без НДС (НДС не облагается)»
    начинается там же, где «без НДС», — при равенстве побеждает «не облагается»."""

    found = []
    for priority, (mode, pattern) in enumerate(
        ((NOT_APPLICABLE, _NOT_APPLICABLE_RE), (INCLUDED, _INCLUDED_RE), (EXCLUDED, _EXCLUDED_RE))
    ):
        match = pattern.search(text)
        if match:
            found.append((match.start(), priority, mode, match))
    if not found:
        return None
    _, _, mode, match = min(found, key=lambda item: item[:2])
    return mode, match


def parse_block(block: str) -> dict[str, Any] | None:
    """Цена и НДС из одного фрагмента о НМЦК. `None` — цена не найдена или про НДС ничего."""

    price_match = next((m for m in _PRICE_RE.finditer(block) if _is_amount(m)), None)
    if price_match is None:
        # Таблица без «руб.»: «НМЦК, руб. | 1 729 350,07». Берётся первое число от тысячи —
        # мелкие числа рядом обычно номер пункта или приложения.
        price_match = next((m for m in _BARE_NUMBER_RE.finditer(block) if _is_amount(m)), None)
        if price_match is None:
            return None
    price = _money(price_match.group(1))
    if price is None:
        return None
    if price == price.to_integral_value():
        kopecks = _KOPECKS_RE.match(block[price_match.end() :])
        if kopecks:
            price += Decimal(kopecks.group(1)) / 100
    # НДС ищется после цены и недалеко: «без НДС» из следующего предложения о порядке
    # формирования цены к этой сумме не относится. Если после цены про НДС ничего нет —
    # в заголовке перед ней («НМЦК с учётом НДС: 1 000 000 руб.»).
    tail = block[price_match.end() : price_match.end() + _TAIL_CHARS]
    quote = block[: price_match.end() + _TAIL_CHARS][:_QUOTE_CHARS]
    marker = _vat_marker(tail)
    if marker is None:
        head = block[: price_match.start()]
        marker = _vat_marker(head)
        if marker is None:
            return None
        tail = head[marker[1].start() :]
    mode, match = marker

    if mode == NOT_APPLICABLE:
        return {"mode": NOT_APPLICABLE, "doc_price": price, "quote": quote}

    rate = _rate(match.group("rate"))
    amount = _money(match.group("amount")) if match.group("amount") else None
    if amount is not None and amount >= price * Decimal("0.25"):
        amount = None  # «НДС» с суммой больше четверти цены — это не налог, а соседнее число
    if mode == INCLUDED:
        return {"mode": INCLUDED, "doc_price": price, "rate": rate, "vat_amount": amount, "quote": quote}

    # «1 729 350,07 руб. без НДС, 2 109 807,09 руб. с НДС 22%» — вторая цена прямо в тексте;
    # «…руб., кроме того НДС 22% 380 457,02 руб.» — сумма налога.
    with_vat = price + amount if amount is not None else None
    # Сумму после «без учёта НДС,» шаблон мог принять за сумму налога (и отбросить как
    # слишком большую) — поэтому вторая цена ищется сразу после самих слов о НДС.
    after = tail[match.start() + len(re.match(r"\D*", match.group(0)).group(0)) :]
    second = _PRICE_RE.search(after)
    if with_vat is None and second and re.search(
        r"с\s+(?:уч[её]том\s+)?НДС|включая\s+НДС|итого", after[: second.end() + 60], re.IGNORECASE
    ):
        candidate = _money(second.group(1))
        if candidate and price < candidate <= price * Decimal("1.25"):
            with_vat = candidate
    if rate is None:
        rate_match = _RATE_RE.search(tail[: match.end() + 80])
        rate = _rate(rate_match.group(1)) if rate_match else None
    return {"mode": EXCLUDED, "doc_price": price, "rate": rate, "stated_with_vat": with_vat, "quote": quote}


def _ordered_documents(db: Session, tender: Tender) -> list[TenderDocument]:
    documents = list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id, TenderDocument.extracted_text.is_not(None))
            .order_by(TenderDocument.created_at)
        )
    )
    # НМЦК объявляют извещение и документация; в проекте договора цена обычно «___ руб.», в ТЗ
    # её нет вовсе, протоколы называют уже цены участников — их не смотрим.
    rank = {
        DocumentClass.NOTICE.value: 0,
        DocumentClass.OTHER.value: 1,
        DocumentClass.TZ_DESCRIPTION.value: 2,
        DocumentClass.CONTRACT.value: 3,
        DocumentClass.SSR.value: 4,
    }
    documents.sort(key=lambda d: (not d.is_priority_source, rank.get(d.document_class or "", 1)))
    return [d for d in documents if d.document_class != DocumentClass.PROTOCOL.value]


def _candidates(db: Session, tender: Tender, anchor: re.Pattern[str]):
    for document in _ordered_documents(db, tender):
        lines = (document.extracted_text or "").splitlines()
        for index, line in enumerate(lines):
            for match in anchor.finditer(line):
                found = parse_block(_block(lines, index, match.start()))
                if found is not None:
                    found["document"] = document.file_name
                    yield found
                    break


def _close(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(Decimal("1"), b * Decimal("0.001"))


def _guess_rate(amount: Decimal, base: Decimal) -> Decimal | None:
    """Ставка по сумме налога и цене без НДС — только если это одна из действующих ставок."""

    if base <= 0 or amount <= 0:
        return None
    guess = amount / base * 100
    return next((r for r in _KNOWN_RATES if abs(r - guess) <= Decimal("0.5")), None)


def complete(found: dict[str, Any], platform_price: Decimal | None = None) -> dict[str, Any]:
    """Досчитывает вторую цену и собирает запись для `tenders.price_vat`."""

    mode, price = found["mode"], found["doc_price"]
    rate, amount = found.get("rate"), found.get("vat_amount")
    with_vat = without_vat = None
    computed: str | None = None  # какая из цен расчётная: "with_vat" / "without_vat"
    rate_assumed = False

    if mode == NOT_APPLICABLE:
        with_vat = without_vat = price
        rate = None
    elif mode == INCLUDED:
        with_vat = price
        if amount is not None:
            without_vat = price - amount
            rate = rate or _guess_rate(amount, without_vat)
        else:
            if rate is None:
                rate, rate_assumed = DEFAULT_RATE, True
            without_vat = _round(price / (1 + rate / 100))
            amount = price - without_vat
            computed = "without_vat"
    else:  # EXCLUDED
        without_vat = price
        if found.get("stated_with_vat"):
            with_vat = found["stated_with_vat"]
            amount = with_vat - price
            rate = rate or _guess_rate(amount, price)
        else:
            if rate is None:
                rate, rate_assumed = DEFAULT_RATE, True
            with_vat = _round(price * (1 + rate / 100))
            amount = with_vat - price
            computed = "with_vat"

    def num(value: Decimal | None) -> str | None:
        return None if value is None else str(_round(value))

    record = {
        "mode": mode,
        "price_with_vat": num(with_vat),
        "price_without_vat": num(without_vat),
        "vat_rate": None if rate is None else format(rate.normalize(), "f"),
        "vat_amount": num(amount) if mode != NOT_APPLICABLE else None,
        "computed": computed,
        "rate_assumed": rate_assumed,
        "doc_price": num(price),
        # Цена в документе не совпала с «Суммой» площадки — показать, чтобы сверили вручную.
        # Площадка могла показать и цену без НДС, когда документ называет цену с НДС, — совпадение
        # с любой из двух считается.
        "matches_platform_price": None
        if platform_price is None
        else any(_close(value, platform_price) for value in (price, with_vat, without_vat)),
        "document": found.get("document"),
        "quote": found.get("quote"),
    }
    return {k: v for k, v in record.items() if v is not None}


def find(db: Session, tender: Tender) -> dict[str, Any] | None:
    """Лучшее место о НМЦК и НДС в документах закупки или `None`."""

    platform = tender.price if tender.price else None  # «0,00» у площадки — «не указана»
    first = None
    for found in _candidates(db, tender, _ANCHOR_RE):
        record = complete(found, platform)
        if record.get("matches_platform_price"):
            return record
        # Сумма в разы меньше или больше «Суммы» площадки — не НМЦК (цена единицы, размер
        # обеспечения, число из шаблона); отличие в пределах этого — показывается для сверки.
        if platform is not None and not platform / 2 <= found["doc_price"] <= platform * 2:
            continue
        first = first or record
    if first:
        return first
    if platform is None:
        return None
    for found in _candidates(db, tender, _WEAK_ANCHOR_RE):
        record = complete(found, platform)
        if record.get("matches_platform_price"):
            return record
    return None


def refresh(db: Session, tender: Tender) -> dict[str, Any] | None:
    """Пересчитывает `tender.price_vat` по документам. Ничего не нашлось — прежнее значение
    остаётся (документы могли не скачаться в этот раз), но и не появляется ложное."""

    found = find(db, tender)
    if found is not None and tender.price_vat != found:
        tender.price_vat = found
        db.flush()
    return found


def summary(record: dict[str, Any]) -> str:
    """Строка для итога анализа: «НМЦК: с НДС 2187987.55, без НДС 1793432.42 (НДС 22%)»."""

    if record["mode"] == NOT_APPLICABLE:
        return f"НМЦК: {record['price_with_vat']}, НДС не облагается"
    rate = f" (НДС {record['vat_rate']}%{', ставка не указана' if record.get('rate_assumed') else ''})" if record.get("vat_rate") else ""
    return f"НМЦК: с НДС {record['price_with_vat']}, без НДС {record['price_without_vat']}{rate}"


def backfill(db: Session, *, only_missing: bool = True) -> dict[str, int]:
    """Заполняет `price_vat` у закупок с распознанными документами (после выкладки раздела 106)."""

    from app.services import tender_twins

    query = select(Tender).where(
        Tender.analysis_tender_id.is_(None),
        select(TenderDocument.id)
        .where(TenderDocument.tender_id == Tender.id, TenderDocument.extracted_text.is_not(None))
        .exists(),
    )
    if only_missing:
        query = query.where(Tender.price_vat.is_(None))
    checked = found = 0
    for tender in db.scalars(query).all():
        checked += 1
        if refresh(db, tender):
            found += 1
            tender_twins.share_fields(db, tender)
        if checked % 200 == 0:
            db.commit()
    db.commit()
    return {"checked": checked, "found": found}


if __name__ == "__main__":  # python -m app.services.price_vat_service [--all]
    import sys

    from app.db.session import SessionLocal

    with SessionLocal() as session:
        print(backfill(session, only_missing="--all" not in sys.argv))
