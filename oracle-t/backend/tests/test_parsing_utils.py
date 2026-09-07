"""Тесты общих хелперов разбора выдачи площадок (`app/adapters/parsing_utils.py`).

Отдельный файл появился после того, как выяснилось, что `parse_price` тихо портит суммы:
формат «977 525,00 руб.» с неразрывным пробелом терялся целиком (`None`), «977.525»
превращалось в 977.525 вместо 977525 — ошибка в тысячу раз, — а «977 525,00 (НДС 20%)»
склеивалось в 977525.0020. Функция общая для всех девяти адаптеров, поэтому набор кейсов
здесь — реальные написания сумм, встреченные в выдаче площадок.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters.parsing_utils import parse_price, parse_ru_date


@pytest.mark.parametrize(
    "text,expected",
    [
        ("977 525,00 ₽", Decimal("977525.00")),
        ("977\xa0525,00 руб.", Decimal("977525.00")),  # неразрывный пробел + текст после суммы
        ("115 439,25", Decimal("115439.25")),
        ("321089.18", Decimal("321089.18")),  # точка как десятичный разделитель
        ("1 234 567,89 ₽", Decimal("1234567.89")),
        ("1.234.567,89", Decimal("1234567.89")),  # точки как разряды, запятая — копейки
        ("977.525", Decimal("977525")),  # три цифры после точки — это разряд, а не копейки
        ("0,00", Decimal("0.00")),
        ("-1 500,50", Decimal("-1500.50")),
        ("5", Decimal("5")),
    ],
)
def test_parse_price_handles_platform_formats(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        # Рядом с суммой в ячейке часто стоят посторонние числа — берётся первое.
        ("977 525,00 (в том числе НДС 20%)", Decimal("977525.00")),
        ("Начальная цена 100 000 руб., шаг 5%", Decimal("100000")),
        ("от 10 000,00 до 20 000,00", Decimal("10000.00")),
    ],
)
def test_parse_price_takes_first_number_only(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text", [None, "", "Цена не указана", "—", "руб."])
def test_parse_price_returns_none_without_number(text):
    assert parse_price(text) is None


def test_parse_ru_date():
    assert parse_ru_date("Опубликовано 29.06.2030 в 10:00") == date(2030, 6, 29)
    assert parse_ru_date("32.13.2026") is None
    assert parse_ru_date(None) is None
