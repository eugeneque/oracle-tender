"""Определение региона заказчика по реквизитам карточки (Приложение G, H ТЗ).

**Зачем.** Регион — один из главных фильтров раздела 5.6 ТЗ и два поля Excel-выгрузки, но до
сих пор он заполнялся только ИИ-анализом документации: если анализ не запускали (а на тысячах
собранных закупок его никто не запускал), тендер оставался «регион не определён». При этом
на карточке ЕИС регион есть всегда — в адресе заказчика и в его ИНН.

**Три источника, в порядке надёжности.**

1. Название региона прямо в адресе («Саратовская обл, г Саратов») — сверяется со
   справочником Приложения H, а не угадывается.
2. Первые две цифры ИНН — код субъекта РФ по справочнику ФНС; он совпадает с кодами
   Приложения H, кроме нескольких случаев, где у региона несколько кодов (Москва — 77, 97,
   99; Санкт-Петербург — 78, 98; Московская область — 50, 90 и т.д.).
3. Название города для городов федерального значения и явных случаев вроде «г Саратов» —
   когда область в адресе не написана, а индекс и ИНН расходятся.

Почему не ИИ: регион — это справочное соответствие, а не суждение. Модель здесь и медленнее,
и дороже, и способна выдумать регион, которого в адресе нет. ИИ подключается только там, где
формального признака не нашлось вовсе (см. `app/services/tender_insights.py`).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.region import Region

# Коды ИНН, не совпадающие с кодом субъекта из Приложения H. У крупных регионов ФНС выдала
# несколько кодов, когда исчерпались номера в исходном диапазоне.
_INN_CODE_ALIASES: dict[str, str] = {
    "97": "77",  # Москва
    "99": "77",
    "98": "78",  # Санкт-Петербург
    "90": "50",  # Московская область
    "93": "23",  # Краснодарский край
    "96": "66",  # Свердловская область
    "94": "99",  # территории за пределами РФ (Байконур) — код Приложения H
}

# Города, по которым регион однозначен, а в адресе он может не упоминаться вовсе.
_CITY_TO_REGION: dict[str, str] = {
    "москва": "77",
    "санкт-петербург": "78",
    "севастополь": "92",
    "саратов": "64",
    "екатеринбург": "66",
    "новосибирск": "54",
    "казань": "16",
    "нижний новгород": "52",
    "самара": "63",
    "омск": "55",
    "челябинск": "74",
    "ростов-на-дону": "61",
    "уфа": "02",
    "красноярск": "24",
    "воронеж": "36",
    "пермь": "59",
    "волгоград": "34",
    "краснодар": "23",
}

# «обл» → «область» и т.п.: в адресах ЕИС части названия почти всегда сокращены, а в
# справочнике Приложения H записаны полностью.
_ABBREVIATIONS: list[tuple[str, str]] = [
    (r"\bобл\.?\b", "область"),
    (r"\bресп\.?\b", "республика"),
    (r"\bкр\.?\b", "край"),
    (r"\bао\b", "автономный округ"),
    (r"\bа\.?о\.?\b", "автономный округ"),
    (r"\bг\.?\s", " "),
]


def _normalize(text: str) -> str:
    lowered = text.lower().replace("ё", "е")
    for pattern, replacement in _ABBREVIATIONS:
        lowered = re.sub(pattern, replacement, lowered)
    return re.sub(r"[^a-zа-я0-9\s-]", " ", lowered)


# Маркеры «уличной» части адреса. Всё, что идёт после них, из поиска региона выбрасывается:
# в адресе «410012, Саратовская обл, г Саратов, ул Московская, дом 66» слово «Московская» —
# это улица, и без такой отсечки закупка из Саратова уезжала в Московскую область.
_STREET_MARKERS = (
    "ул ", "улица", "пер ", "переулок", "пр-кт", "проспект", "пр-д", "проезд", "пл ",
    "площадь", "наб ", "набережная", "б-р", "бульвар", "шоссе", "туп ", "тракт", "мкр",
    "микрорайон", "влд ", "владение", "д ", "дом ", "корп", "стр ", "строение", "литер",
    "офис", "оф ", "помещ", "кв ", "квартал",
)


def _strip_street_part(normalized: str) -> str:
    """Оставляет от адреса только «территориальную» часть — до первого уличного маркера."""

    cut = len(normalized)
    for marker in _STREET_MARKERS:
        position = normalized.find(marker)
        if position != -1:
            cut = min(cut, position)
    return normalized[:cut].strip()


def region_from_address(db: Session, address: str | None) -> Region | None:
    """Регион по тексту адреса — сверкой со справочником, а не по догадке."""

    if not address:
        return None

    normalized = _strip_street_part(_normalize(address))
    if not normalized:
        return None

    regions = db.scalars(select(Region)).all()

    # Сначала — полное название («саратовская область»): оно однозначно. Только если полного
    # совпадения нет, пробуем «ядро» без слова «область»/«край» — в адресах встречается и
    # «Саратовская обл», и просто «Саратовская».
    best: Region | None = None
    best_length = 0
    for region in regions:
        name = _normalize(region.name)
        if len(name) >= 4 and name in normalized and len(name) > best_length:
            best = region
            best_length = len(name)
    if best is not None:
        return best

    for region in regions:
        core = re.sub(
            r"\b(область|край|республика|автономный округ|автономная область)\b",
            "",
            _normalize(region.name),
        ).strip()
        if len(core) >= 5 and core in normalized and len(core) > best_length:
            best = region
            best_length = len(core)
    if best is not None:
        return best

    for city, code in _CITY_TO_REGION.items():
        if city in normalized:
            return db.get(Region, code)
    return None


def region_from_inn(db: Session, inn: str | None) -> Region | None:
    """Регион по первым двум цифрам ИНН (код субъекта в справочнике ФНС)."""

    if not inn:
        return None

    digits = re.sub(r"\D", "", inn)
    if len(digits) < 10:
        return None

    code = digits[:2]
    code = _INN_CODE_ALIASES.get(code, code)
    return db.get(Region, code)


def resolve_region(
    db: Session, *, address: str | None = None, inn: str | None = None, extra_text: str | None = None
) -> tuple[Region | None, str | None]:
    """Регион и человекочитаемое объяснение, откуда он взялся.

    Объяснение возвращается вместе со значением намеренно: регион, подставленный системой,
    пользователь должен иметь возможность проверить, а не принимать на веру — по нему
    строятся фильтры и назначается ответственный.
    """

    by_address = region_from_address(db, address)
    by_inn = region_from_inn(db, inn)

    if by_address is not None and by_inn is not None and by_address.code != by_inn.code:
        # Расхождение — обычная ситуация: у организации может быть филиал в другом регионе,
        # а ИНН остаётся головной. Регион закупки определяет адрес заказчика, указанный в
        # извещении, поэтому берём его, но говорим о расхождении прямо: пользователь должен
        # видеть, что здесь есть неоднозначность, и при необходимости поправить вручную.
        return by_address, (
            f"по адресу заказчика: {address}. Обратите внимание: код региона в ИНН "
            f"({inn[:2]}) указывает на другой субъект — {by_inn.name}"
        )

    if by_address is not None:
        return by_address, f"по адресу заказчика: {address}"

    if by_inn is not None:
        return by_inn, f"по коду региона в ИНН заказчика ({inn[:2]})"

    region = region_from_address(db, extra_text)
    if region is not None:
        return region, "по упоминанию региона в карточке закупки"

    return None, None
