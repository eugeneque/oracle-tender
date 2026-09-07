"""Разбор карточки компании на rusprofile.ru (раздел 7 ТЗ, юридические данные профиля).

Сеть не трогаем: проверяется разбор сохранённой разметки и поведение на краях — ссылка в
разном виде и страница, которая карточкой не является. Живая проверка на реальной карточке
МИРТЕК делалась при реализации; в прогоне тестов внешний сайт участвовать не должен.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.adapters.rusprofile import RusprofileError, extract_card_id, parse_card

CARD_HTML = """
<html><body>
  <h1 itemprop="name">ООО ТД "Миртек"</h1>
  <span itemprop="legalName">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ ТОРГОВЫЙ ДОМ "МИРТЕК"</span>
  <span itemprop="foundingDate">28.03.2013</span>
  <span id="clip_ogrn">1132651008335</span>
  <span id="clip_inn">2635819741</span>
  <span id="clip_kpp">263501001</span>
  <span id="clip_address">355037 , Ставропольский край , г. Ставрополь , ул. Доваторцев, д. 33 а</span>
</body></html>
"""


def test_card_is_parsed_into_profile_fields():
    company = parse_card(CARD_HTML, source_url="https://www.rusprofile.ru/id/6723224")

    assert company.inn == "2635819741"
    # КПП — главная причина ходить сюда: в выдаче ЕГРЮЛ его нет, а в реквизитах заявки он нужен.
    assert company.kpp == "263501001"
    assert company.ogrn == "1132651008335"
    assert company.registration_date == date(2013, 3, 28)
    assert company.legal_name.startswith("ОБЩЕСТВО С ОГРАНИЧЕННОЙ")
    assert company.short_name == 'ООО ТД "Миртек"'
    # Адрес на странице свёрстан по частям и склеивается с пробелами перед запятыми.
    assert company.legal_address == "355037, Ставропольский край, г. Ставрополь, ул. Доваторцев, д. 33 а"


def test_page_without_inn_is_rejected():
    """Страница без ИНН — не карточка компании, а заглушка, ошибка или проверка на робота.

    Молча вернуть пустого кандидата хуже, чем сказать «не разобрали»: пустые поля в форме
    человек может принять за «в реестре пусто».
    """

    with pytest.raises(RusprofileError):
        parse_card("<html><body><h1>Проверка браузера</h1></body></html>")


@pytest.mark.parametrize(
    "value",
    [
        "https://www.rusprofile.ru/id/6723224",
        "  https://www.rusprofile.ru/id/6723224?utm_source=x  ",
        "rusprofile.ru/id/6723224",
        "6723224",
    ],
)
def test_card_id_is_extracted_from_any_reasonable_form(value):
    """Человек копирует адрес из строки браузера как есть — с протоколом, хвостом и пробелами."""

    assert extract_card_id(value) == "6723224"


@pytest.mark.parametrize("value", ["", "   ", "ООО Миртек"])
def test_non_link_is_rejected_with_a_hint(value):
    with pytest.raises(RusprofileError):
        extract_card_id(value)
