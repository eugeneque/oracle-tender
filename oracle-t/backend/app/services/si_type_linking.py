"""Привязка моделей приборов к утверждённым типам СИ (`products.si_type_id`).

**Зачем это отдельный шаг.** Каталог и реестр наполняются независимо: модели приходят с
сайта производителя, коды СИ — из ФГИС, и между собой они никак не связаны. Пока связи нет,
модуль сопоставления видит по производителю два несвязанных набора фактов и не может сказать,
какое «Описание типа» относится к конкретной модели: в карточке производителя оказываются
метрологические данные всех типов сразу, включая чужие (у МИРТЕК в реестре есть и
теплосчётчики, и счётчики воды, и поверочные установки).

**По чему связываем — по артикулу, а не по названию модели.** Это не деталь реализации, а
суть: на сайте производителя у таганрогского и владивостокского исполнений **одно и то же
название модели** («МИРТЕК-12-РУ-D17»), но **разные типы СИ** — 61891-15 «МИРТЕК-12-РУ» и
67662-17 «МИРТЕК-212-РУ» соответственно. Завод зашит только в артикул (`mirtek-12-ru-d17`
против `mirtek-212-ru-d17`). Связывание по названию модели молча приписало бы обоим
исполнениям таганрогский тип и подставило бы в тендер характеристики не того прибора.
Поэтому название модели используется только там, где артикула нет вовсе (записи, заведённые
руками или импортом из CSV).

**Почему сравнение идёт в двух нормализациях сразу.** Кириллица и латиница смешиваются
здесь двумя разными способами, и одна схема приведения не покрывает оба:

* *Транслитерация* нужна МИРТЕК: артикул — это slug сайта (`mirtek-212-ru-d17`), а
  обозначение в реестре кириллическое («МИРТЕК-212-РУ»), и «Р» там читается как `r`.
* *Омоглифы* нужны Энергомере: на сайте модель называется «CE101 R5 145 M6» **латиницей**,
  а в Госреестре тот же прибор записан «СЕ 101» **кириллицей** — визуально неотличимо, но
  это разные символы, и «С» здесь соответствует `c`, а не `s`.

Схемы прямо противоречат друг другу (кириллическая «Р»: `r` против `p`), поэтому для каждой
строки строится **набор** ключей, и совпадением считается совпадение хотя бы по одному.
Без этого у Энергомеры не связывалась ни одна из 200 моделей.

**Три ключа, а не один.** Пробуются по очереди, от точного к общему:

* `model_code` — обозначение, вычлененное адаптером из наименования («НЕВА МТ 113 AS OP»
  из «НЕВА МТ 113 AS OP 5(100) А»). Появился вместе с требованием хранить наименование как
  у производителя: внутри полного названия обозначение префиксным сравнением не находится.
* `article` — у МИРТЕК только он различает заводские исполнения. У Энергомеры, наоборот,
  это складской номер (`101001003007791`, 146 моделей из 200), совпадений не дающий вовсе.
* `model_name` — для записей ручного ввода и CSV-импорта, где ни кода, ни артикула нет.

**Совпадение — префиксное.** Обозначение типа покрывает семейство исполнений:
«МИРТЕК-12-РУ» → «МИРТЕК-12-РУ-D17», «…-SP17», «…-W9». Из нескольких подходящих берётся
самый длинный префикс (наиболее специфичный) — иначе «МИРТЕК-1-РУ» перехватывал бы модели
«МИРТЕК-12-РУ-*». Если и после этого кандидатов больше одного, привязка не делается:
запись помечается «требует ручной проверки», как и при неоднозначности в реестре
(см. `app/adapters/fgis_matching.py`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.fgis_matching import DeviceKind, classify_si_type
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, ReviewStatus, SiType
from app.services.audit import log_action

COMPONENT = "catalog_sync"

# Начало пометки «не удалось привязать код СИ». Вынесено в константу, потому что по нему же
# пометка и снимается при следующем удачном связывании: пометки на записи бывают разной
# природы (например, «модель пропала с сайта»), и снимать нужно только свою.
UNLINKED_REASON_PREFIX = "Не удалось однозначно определить код СИ"

# Кириллица → латиница по правилам slug'ов сайта производителя (`odnofaznye-schyotchiki`,
# `tryohfaznye-schyotchiki`). Для обозначений приборов задействована лишь часть таблицы —
# буквы из «МИРТЕК», «РУ», «АМ», «ТВ», — но таблица полная: обозначения других
# производителей содержат другие буквы, а гадать по месту хуже, чем перечислить один раз.
_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


# Кириллические буквы, визуально неотличимые от латинских. Отдельно от транслитерации:
# «СЕ 101» из Госреестра и «CE101» с сайта Энергомеры — один и тот же прибор, но «С» здесь
# читается как `c`, а не как `s`.
_CYRILLIC_HOMOGLYPHS = {
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
}


def designation_slug(value: str | None) -> str:
    """Обозначение типа или артикул → общий вид для сравнения (транслитерация).

    «МИРТЕК-212-РУ» и `mirtek-212-ru-d17` дают `mirtek212ru` и `mirtek212rud17` — первое
    оказывается префиксом второго. Пунктуация и регистр отбрасываются: в реестре обозначение
    приходит в разных кавычках и с разными дефисами."""

    return _apply_table(value, _CYRILLIC_TO_LATIN)


def designation_homoglyph_slug(value: str | None) -> str:
    """То же, но кириллица приводится к визуально совпадающей латинице.

    «СЕ 101» (кириллица) и «CE101» (латиница) дают один ключ `ce101`."""

    return _apply_table(value, _CYRILLIC_HOMOGLYPHS)


def _apply_table(value: str | None, table: dict[str, str]) -> str:
    lowered = (value or "").lower()
    latin = "".join(table.get(char, char) for char in lowered)
    return re.sub(r"[^0-9a-zа-яё]+", "", latin)


def designation_keys(value: str | None) -> set[str]:
    """Все варианты нормализации строки. Пустые отбрасываются."""

    return {key for key in (designation_slug(value), designation_homoglyph_slug(value)) if key}


@dataclass
class LinkDecision:
    """Решение по одной модели.

    `si_type is None и not needs_review` — подходящего типа СИ в справочнике просто нет
    (частый и штатный случай: автопоиск по производителю ещё не запускали). `needs_review`
    — кандидатов несколько, и выбирать за человека нельзя."""

    si_type: SiType | None = None
    needs_review: bool = False
    reason: str = ""
    candidates: list[SiType] = field(default_factory=list)


@dataclass
class LinkOutcome:
    linked: int = 0
    already_linked: int = 0
    needs_review: int = 0
    not_found: int = 0
    details: list[str] = field(default_factory=list)


def electricity_meter_types(db: Session, manufacturer_id: uuid.UUID) -> list[SiType]:
    """Типы СИ производителя, которые действительно являются счётчиками электроэнергии.

    Фильтр обязателен: у МИРТЕК в Госреестре 28 типов, и лишь половина — электросчётчики,
    остальное теплосчётчики, счётчики воды и газа, УСПД, поверочные установки. Без фильтра
    «МИРТЕК-42-РУ» (теплосчётчик) стал бы кандидатом на префиксное совпадение наравне со
    счётчиками и мог бы перехватить модель."""

    return [
        si_type
        for si_type in db.scalars(
            select(SiType).where(SiType.manufacturer_id == manufacturer_id)
        )
        if si_type.notation
        and classify_si_type(si_type.type_name, si_type.notation) is DeviceKind.ELECTRICITY_METER
    ]


# Минимальная длина обозначения, годного для префиксного сравнения. У Энергомеры в реестре
# есть тип с обозначением «СЕ» — это название всей серии, и как префикс он подошёл бы к
# любой модели «CE1xx», «CE2xx», «CE3xx» разом.
MIN_DESIGNATION_LENGTH = 3

# Служебное начало обозначения в Госреестре: «серии РиМ 189», «тип СЕ208».
_SERIES_PREFIX_RE = re.compile(r"^(?:сери[ияй]|тип[аы]?)\s+", re.IGNORECASE)

# Джокер в обозначении каталога: «РиМ 189.4X» — это все приборы от 189.40 до 189.49, а
# «РиМ 289.2Х» покрывает 289.21…289.24. Латинская X и кириллическая Х выглядят одинаково и
# на сайтах встречаются обе.
_WILDCARD_TAIL_RE = re.compile(r"[xх]+$", re.IGNORECASE)

# Несколько обозначений в одном поле реестра: у КПЗ тип 79474-20 записан как
# «М2М-1 и М2М-1S» — это два прибора, и без разбиения не сматчится ни один.
_DESIGNATION_SEPARATORS_RE = re.compile(r"\s+и\s+|\s*[,;/]\s*", re.IGNORECASE)


def notation_variants(notation: str | None) -> list[str]:
    """Обозначения, перечисленные в одном поле карточки типа."""

    if not notation:
        return []
    parts = [part.strip() for part in _DESIGNATION_SEPARATORS_RE.split(notation)]
    # «серии РиМ 189» — так в Госреестре записан тип, охватывающий всё семейство. Слово
    # «серии» к обозначению не относится, но в ключ попадает и рвёт префиксное сравнение:
    # без него ни одна модель РиМ 189.хх не находила свой тип.
    parts = [_SERIES_PREFIX_RE.sub("", part).strip() for part in parts]
    return [part for part in parts if part]


def _si_type_keys(si_type: SiType) -> set[str]:
    """Ключи всех обозначений типа, годных для префиксного сравнения."""

    keys: set[str] = set()
    for variant in notation_variants(si_type.notation):
        keys |= {k for k in designation_keys(variant) if len(k) >= MIN_DESIGNATION_LENGTH}
    return keys


def _candidates_for(key: str, si_types: list[SiType]) -> list[tuple[SiType, int]]:
    """Типы СИ, обозначение которых является префиксом ключа, вместе с длиной совпадения."""

    found: list[tuple[SiType, int]] = []
    for si_type in si_types:
        matched = [k for k in _si_type_keys(si_type) if key.startswith(k)]
        if matched:
            found.append((si_type, max(len(k) for k in matched)))
    return found


def pick_si_type_for_product(product: Product, si_types: list[SiType]) -> LinkDecision:
    """Какой тип СИ соответствует модели. Чистая функция — тестируется без БД.

    Ключей у модели два — артикул и название, — и порядок между ними не косметический:
    у МИРТЕК только артикул различает заводские исполнения (см. докстринг модуля), поэтому
    он пробуется первым. Но у Энергомеры артикул — складской номер, к обозначению отношения
    не имеющий, и там срабатывает название. Поэтому не «артикул вместо названия», а
    «артикул раньше названия»."""

    # Порядок источников ключа — от самого точного к самому общему:
    # `model_code` — обозначение, вычлененное адаптером из наименования («НЕВА МТ 113»);
    # артикул — у МИРТЕК он единственный различает заводские исполнения;
    # наименование целиком — для записей ручного ввода и импорта, где кода нет.
    for source in (product.model_code, product.article, product.model_name):
        for key in sorted(designation_keys(source), key=len, reverse=True):
            decision = _decide(key, si_types, product)
            if decision is not None:
                return decision

    # Обобщённое обозначение каталога («РиМ 189.4X») не является ни префиксом конкретного
    # обозначения из реестра, ни его продолжением — джокер стоит на месте цифры. Такая
    # позиция каталога охватывает семейство, поэтому сравнение идёт в обратную сторону:
    # подходит тип, обозначение которого начинается с части до джокера.
    for source in (product.model_code, product.model_name):
        decision = _decide_by_wildcard(source, si_types, product)
        if decision is not None:
            return decision

    return LinkDecision(
        reason=(
            f"В справочнике нет типа СИ, обозначение которого совпадало бы с "
            f"«{product.article or product.model_name}»"
        )
    )


def _decide_by_wildcard(
    source: str | None, si_types: list[SiType], product: Product
) -> LinkDecision | None:
    """Решение по обозначению с джокером на конце. `None` — джокера нет либо нет кандидатов."""

    for key in sorted(designation_keys(source), key=len, reverse=True):
        stem = _WILDCARD_TAIL_RE.sub("", key)
        if stem == key or len(stem) < MIN_DESIGNATION_LENGTH:
            continue

        candidates = [
            (si_type, len(stem))
            for si_type in si_types
            if any(si_key.startswith(stem) for si_key in _si_type_keys(si_type))
        ]
        if candidates:
            return _pick_best(candidates, product)
    return None


def _decide(key: str, si_types: list[SiType], product: Product) -> LinkDecision | None:
    """Решение по одному ключу. `None` — по этому ключу кандидатов нет, надо пробовать
    следующий."""

    candidates = _candidates_for(key, si_types)
    if not candidates:
        return None

    return _pick_best(candidates, product)


def _pick_best(
    candidates: list[tuple[SiType, int]], product: Product
) -> LinkDecision:
    """Один тип из кандидатов либо `needs_review`, если выбрать за человека нельзя."""

    # Самый длинный префикс — самый специфичный: «МИРТЕК-12-РУ» должен выиграть у
    # «МИРТЕК-1-РУ», а «СЕ 101» — у «СЕ», иначе короткое обозначение перехватывало бы
    # чужое семейство моделей.
    longest = max(length for _, length in candidates)
    best = [si_type for si_type, length in candidates if length == longest]

    if len(best) == 1:
        return LinkDecision(si_type=best[0], candidates=[c for c, _ in candidates])

    # Одно и то же обозначение под несколькими номерами ГРСИ — обычное дело: тип
    # переутверждают, и старая запись остаётся в реестре. Берём самую свежую по номеру
    # (год утверждения — вторая часть номера), а не отправляем человеку заведомо
    # разрешимый выбор.
    freshest = sorted(best, key=_registry_sort_key, reverse=True)
    if _registry_sort_key(freshest[0]) > _registry_sort_key(freshest[1]):
        return LinkDecision(si_type=freshest[0], candidates=[c for c, _ in candidates])

    return LinkDecision(
        needs_review=True,
        reason=(
            f"Обозначению «{product.article or product.model_name}» одинаково соответствуют "
            f"{len(best)} типов СИ: " + ", ".join(f"{s.si_code} «{s.notation}»" for s in best)
        ),
        candidates=best,
    )


def _registry_sort_key(si_type: SiType) -> tuple[int, int]:
    """Номер в Госреестре как пара чисел («61891-15» → (15, 61891)).

    Год утверждения первым: 61891-15 старше, чем 12345-19, несмотря на больший номер."""

    match = re.match(r"^(\d+)-(\d+)", si_type.si_code or "")
    if not match:
        return (0, 0)
    number, year = int(match.group(1)), int(match.group(2))
    # Двузначный год реестра: 05 — это 2005-й, 26 — 2026-й; всё в пределах одного века.
    return (year, number)


def link_product(
    db: Session, product: Product, *, si_types: list[SiType] | None = None
) -> SiType | None:
    """Привязывает одну модель. Возвращает найденный тип СИ либо `None`.

    Нужна там, где модель появляется по одной — ручное создание в админке, обработка
    события из модуля сопоставления, — чтобы не гонять весь справочник производителя ради
    единственной записи. Уже проставленный код СИ не трогается (раздел 5.3 ТЗ).

    Не коммитит: вызывающий код сохраняет привязку вместе со своей бизнес-операцией."""

    if product.si_type_id is not None:
        return db.get(SiType, product.si_type_id)

    if si_types is None:
        si_types = electricity_meter_types(db, product.manufacturer_id)

    decision = pick_si_type_for_product(product, si_types)
    if decision.si_type is not None:
        product.si_type_id = decision.si_type.id
        _clear_unlinked_flag(product)
        return decision.si_type

    if decision.needs_review:
        product.review_status = ReviewStatus.NEEDS_REVIEW.value
        product.review_reason = f"{UNLINKED_REASON_PREFIX}. {decision.reason}"
    return None


def link_products_to_si_types(
    db: Session,
    manufacturer: Manufacturer,
    *,
    actor_id: uuid.UUID | None = None,
    relink: bool = False,
) -> LinkOutcome:
    """Привязывает модели производителя к типам СИ.

    `relink=False` (по умолчанию) не трогает уже привязанные модели: код СИ мог быть
    выставлен человеком, а раздел 5.3 ТЗ отдаёт ручному вводу приоритет над автоматикой.
    `relink=True` — для случая, когда в реестре появился более точный тип и связи нужно
    пересчитать заново; такой запуск делается осознанно, из админки.
    """

    outcome = LinkOutcome()
    si_types = electricity_meter_types(db, manufacturer.id)
    products = list(
        db.scalars(
            select(Product)
            .where(Product.manufacturer_id == manufacturer.id)
            .order_by(Product.model_name, Product.article)
        )
    )

    if not si_types:
        outcome.details.append(
            "У производителя нет ни одного типа СИ — сначала выполните автопоиск в ФГИС"
        )
        log_action(
            db,
            component=COMPONENT,
            action=f"link_si_types:{manufacturer.legal_name}",
            result="empty",
            level=LogLevel.WARNING,
            details=outcome.details[-1],
            user_id=actor_id,
        )
        db.commit()
        return outcome

    for product in products:
        if product.si_type_id is not None and not relink:
            outcome.already_linked += 1
            continue

        decision = pick_si_type_for_product(product, si_types)

        if decision.si_type is not None:
            product.si_type_id = decision.si_type.id
            _clear_unlinked_flag(product)
            outcome.linked += 1
            continue

        if decision.needs_review:
            outcome.needs_review += 1
            product.review_status = ReviewStatus.NEEDS_REVIEW.value
            product.review_reason = f"{UNLINKED_REASON_PREFIX}. {decision.reason}"
            outcome.details.append(f"{product.model_name} ({product.article or '—'}): {decision.reason}")
            continue

        # Подходящего типа просто нет — не повод для пометки: у половины моделей код СИ
        # может ещё не быть найден, и «требует проверки» на каждой из них превратило бы
        # пометку в фон, который перестают замечать.
        outcome.not_found += 1

    log_action(
        db,
        component=COMPONENT,
        action=f"link_si_types:{manufacturer.legal_name}",
        result="success" if outcome.linked else ("needs_review" if outcome.needs_review else "empty"),
        level=LogLevel.WARNING if outcome.needs_review else LogLevel.INFO,
        details=(
            f"Привязано моделей: {outcome.linked}, было привязано ранее: {outcome.already_linked}, "
            f"неоднозначно (требует проверки): {outcome.needs_review}, "
            f"без подходящего типа СИ: {outcome.not_found}"
        ),
        user_id=actor_id,
    )
    db.commit()
    logger.info(
        f"Привязка кодов СИ ({manufacturer.legal_name}): привязано {outcome.linked}, "
        f"ранее {outcome.already_linked}, на проверку {outcome.needs_review}, "
        f"без типа {outcome.not_found}"
    )
    return outcome


def _clear_unlinked_flag(product: Product) -> None:
    """Снимает ровно свою пометку. Пометку другой природы («модель пропала с сайта»,
    неоднозначность в реестре ФГИС) удачная привязка не отменяет."""

    if (product.review_reason or "").startswith(UNLINKED_REASON_PREFIX):
        product.review_status = ReviewStatus.OK.value
        product.review_reason = None
