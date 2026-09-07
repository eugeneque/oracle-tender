"""Общие хелперы разбора HTML-карточек тендеров: даты в формате ДД.ММ.ГГГГ, суммы с
разделителями разрядов, извлечение текста без артефактов от тегов подсветки поискового
термина. Вынесены из `app/adapters/eis.py`, когда те же проблемы разбора повторились в
`app/adapters/zakazrf.py` — площадки используют одинаковый формат дат/сумм в русской
локализации, и повторять эти функции в каждом адаптере не было смысла."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")


def parse_ru_date(text: str | None) -> date | None:
    if not text:
        return None
    match = _DATE_RE.search(text)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


# Пробельные символы, которыми площадки разделяют разряды: обычный пробел, неразрывный,
# узкий неразрывный, тонкий, цифровой. В одной и той же выдаче встречаются разные.
_GROUP_SEPARATORS = " \t\xa0   ⁠'"

# Первое число в строке: цифры с разделителями разрядов и, возможно, дробной частью.
# Разбор идёт именно по первому вхождению, а не по всей строке: рядом с суммой в ячейке
# часто стоят другие числа («в том числе НДС 20%», «шаг 5%», «от … до …»), и склейка всех
# цифр подряд давала неверную сумму.
_PRICE_TOKEN_RE = re.compile(rf"-?\d[\d{re.escape(_GROUP_SEPARATORS)}]*(?:[.,]\d+)*")


def parse_price(text: str | None) -> Decimal | None:
    """Сумма из ячейки выдачи площадки.

    Разбор нетривиален, потому что площадки пишут суммы по-разному, а ошибка здесь тихая:
    неверно понятый разделитель меняет сумму в тысячу раз, и в фильтре по цене (раздел 5.6 ТЗ)
    это не бросается в глаза. Правила:

    - берётся **первое** число строки — остальные числа рядом обычно относятся к НДС, шагу
      аукциона или второй границе диапазона;
    - разделитель считается десятичным, только если за ним идут одна-две цифры
      (`977 525,00` → 977525.00), иначе он разрядный (`977.525` → 977525) — копейки в две
      цифры и группы разрядов в три различимы именно так;
    - разрядные разделители любых видов (включая неразрывные пробелы) отбрасываются.
    """

    if not text:
        return None

    match = _PRICE_TOKEN_RE.search(text)
    if match is None:
        return None

    token = match.group(0)
    for separator in _GROUP_SEPARATORS:
        token = token.replace(separator, "")

    negative = token.startswith("-")
    parts = re.split(r"[.,]", token.lstrip("-"))
    if not parts or not parts[0]:
        return None

    if len(parts) == 1:
        cleaned = parts[0]
    elif len(parts[-1]) in (1, 2):
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    else:
        # Последняя группа из трёх цифр — это разряд, а не копейки.
        cleaned = "".join(parts)

    if not cleaned.replace(".", "").isdigit():
        return None

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def element_text(node) -> str | None:
    if node is None:
        return None
    # `get_text(strip=True)` обрезает пробелы у КАЖДОГО текстового фрагмента отдельно перед
    # склейкой — из-за этого пропадают настоящие пробелы между словами на границах тегов
    # подсветки совпавшего ключевого слова (например, "Поставка <span>счетчик</span>ов
    # <span>электрической</span> энергии" превращалось бы в "Поставкасчетчиковэлектрическую...").
    # Поэтому берём текст без пофрагментной обрезки (`strip=False`, разделитель по умолчанию —
    # пустая строка, как в исходной вёрстке) и схлопываем пробелы/переносы строк уже в
    # получившейся цельной строке.
    value = re.sub(r"\s+", " ", node.get_text(strip=False)).strip()
    return value or None
