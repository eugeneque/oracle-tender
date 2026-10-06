"""Профиль релевантности: дешёвый фильтр до дорогого ИИ-анализа (раздел 5.1.1 ТЗ).

Отвечает на вопрос «стоит ли вообще тратить на этот тендер вызов модели». Работает по
заголовку и краткому описанию сразу после сбора — до скачивания документации и до LLM.

**Синтаксис ключей** (раздел 5.1.1 ТЗ):

* `слово*` — по основе слова: `электросчетчик*` ловит все словоформы;
* `(a* b* c*)~N` — все слова должны встретиться в пределах N слов друг от друга;
* `-слово*` — исключающий ключ: встретилось — группа не срабатывает, даже если совпали
  положительные.

**Почему сопоставление своё, а не `tsquery`.** ТЗ предлагает `to_tsquery` с оператором
расстояния, и для поиска по базе это верно. Но фильтр применяется к одному тендеру в момент
его сохранения, когда текст ещё в памяти: гонять ради каждой записи запрос в БД — лишний
круг, а поведение `to_tsquery` пришлось бы всё равно воспроизводить в тестах. Стемминг здесь
усечённый (сравнение по основе до окончания), и этого достаточно: ключи пишутся людьми под
конкретные формулировки закупок, а не под лингвистическую полноту.

**Тендер, не прошедший фильтр, не удаляется** — он помечается и остаётся видимым при снятии
фильтра в списке. Профиль настраивается людьми и вполне может оказаться слишком узким;
молча выбрасывать данные из-за настройки нельзя.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.manufacturer import Manufacturer
from app.models.relevance_profile import CollectionTerm, RelevanceProfile
from app.models.search_profile import KeywordMatchMode, SearchKeywordGroup, SearchProfile
from app.models.source import Source
from app.models.tender import Tender
from app.seed.search_profile_data import (
    DEFAULT_AI_SCORE_THRESHOLD,
    DEFAULT_MIN_NMCK,
    KEYWORD_GROUPS,
)
from app.services import okpd2_service

# Слово текста: буквы, цифры и дефис. Всё остальное — разделители.
_WORD_RE = re.compile(r"[а-яёa-z0-9]+(?:-[а-яёa-z0-9]+)*", re.IGNORECASE)
# `(a* b*)~N` — группа слов с ограничением расстояния.
_PROXIMITY_RE = re.compile(r"^\((?P<terms>[^)]+)\)~(?P<distance>\d+)$")

# Длина префикса, по которому слова считаются одной основой. Именно фиксированный префикс,
# а не «отрезать N символов с конца»: при переменном усечении «счетчик» давал бы основу из
# четырёх букв, а «счетчиков» — из шести, и одно и то же слово переставало совпадать само с
# собой. Пять — компромисс: «поверка»/«поверке» и «счетчик»/«счетчиков» сходятся, а слова
# короче уже сравниваются целиком.
_STEM_PREFIX = 5
# На сколько букв слово текста может быть длиннее ключа при сравнении по основе. Русские
# окончания не длиннее трёх букв («-ами», «-ого»), а всё, что длиннее, — уже другое слово с
# той же основой: без этого ограничения ключ «миртек» ловил заказчика «МИРТЕЛЕКОМ», и в
# список попадали оптические муфты и грозозащита.
_MAX_ENDING = 3


@dataclass
class GroupMatch:
    """Результат проверки одного профиля (до 05.10.2026 — группы)."""

    group_id: uuid.UUID | None
    group_name: str
    matched: bool
    reason: str
    # Чем совпало: "keywords" | "okpd2" | None. Нужно, чтобы закупку, совпавшую по смыслу,
    # приписать профилю со словами, а не тому, что совпал одним кодом.
    via: str | None = None


@dataclass
class RelevanceOutcome:
    """Итог фильтра по тендеру."""

    passed: bool
    group_id: uuid.UUID | None
    group_name: str | None
    reason: str


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(text or "")]


def _stem(word: str) -> str:
    """Грубая основа слова: усечение до неизменяемой части.

    Полноценная лемматизация здесь была бы избыточной — ключи пишет человек под конкретные
    формулировки закупок, и ему нужно, чтобы `счетчик*` поймал «счетчиков», а не чтобы
    система разобрала падеж.
    """

    return word[:_STEM_PREFIX] if len(word) > _STEM_PREFIX else word


def _term_positions(tokens: list[str], term: str) -> list[int]:
    """Позиции слов, подходящих под один термин ключа.

    `слово*` — совпадение по началу; слово без звёздочки — точное совпадение либо совпадение
    по общей основе (иначе ключ «поверка» не нашёл бы «поверке», а требовать звёздочку в
    каждом ключе — лишняя обязанность для человека, который их пишет).
    """

    term = term.strip().lower()
    if not term:
        return []
    if term.endswith("*"):
        prefix = term[:-1]
        return [i for i, token in enumerate(tokens) if token.startswith(prefix)]
    # Сравнение по основе включается только для достаточно длинных ключей: у коротких
    # («воды», «газа») общий префикс означал бы совпадение с «водитель» и «газета». И только
    # для слов сопоставимой длины: общая основа у «миртек» и «миртелеком» есть, а слово —
    # другое.
    stem = _stem(term)
    by_stem = len(term) > _STEM_PREFIX
    longest = len(term) + _MAX_ENDING
    return [
        i
        for i, token in enumerate(tokens)
        if token == term or (by_stem and len(token) <= longest and _stem(token) == stem)
    ]


def _phrase_positions(tokens: list[str], phrase: str) -> list[int]:
    """Позиции многословной фразы без оператора близости — слова подряд."""

    parts = phrase.split()
    if len(parts) == 1:
        return _term_positions(tokens, parts[0])

    first = _term_positions(tokens, parts[0])
    result = []
    for start in first:
        if all(
            (start + offset) < len(tokens)
            and _term_positions(tokens[start + offset : start + offset + 1], part)
            for offset, part in enumerate(parts)
        ):
            result.append(start)
    return result


def matches_keyword(tokens: list[str], keyword: str) -> bool:
    """Совпал ли один ключ (положительный или, без минуса, исключающий)."""

    keyword = (keyword or "").strip().lstrip("-").strip()
    if not keyword:
        return False

    proximity = _PROXIMITY_RE.match(keyword)
    if proximity:
        terms = proximity.group("terms").split()
        distance = int(proximity.group("distance"))
        positions = [_term_positions(tokens, term) for term in terms]
        if any(not places for places in positions):
            return False
        # Все слова должны уместиться в окно длиной `distance`: берём самое раннее и самое
        # позднее вхождение в каждой комбинации — проверяем разброс, а не порядок, потому
        # что в закупках слова идут как угодно («поверка счётчиков» / «счётчиков поверка»).
        return _fits_window(positions, distance)

    return bool(_phrase_positions(tokens, keyword))


def _fits_window(positions: list[list[int]], distance: int) -> bool:
    """Есть ли комбинация вхождений, укладывающаяся в окно из `distance` слов.

    Жадный проход вместо перебора всех сочетаний: для каждого вхождения первого термина
    берётся ближайшее подходящее вхождение остальных. На длине ключа в 2–6 слов этого
    достаточно, а перебор рос бы произведением списков позиций.
    """

    for start in positions[0]:
        chosen = [start]
        for places in positions[1:]:
            nearest = min(places, key=lambda place: abs(place - start), default=None)
            if nearest is None:
                return False
            chosen.append(nearest)
        if max(chosen) - min(chosen) <= distance:
            return True
    return False


OKPD2_NARROW = "narrow"
OKPD2_EITHER = "either"


def match_profile(
    tokens: list[str], profile, okpd2_code: str | None = None
) -> GroupMatch:
    """Единый движок: подходит ли закупка профилю.

    Принимает и профиль (`RelevanceProfile`), и прежнюю группу (`SearchKeywordGroup`) — поля у
    них одинаковые. Порядок: исключения — слова — код ОКПД2. Слова и код сочетаются по
    `okpd2_mode`: `narrow` — нужно и то и другое из заданного, `either` — достаточно одного
    (так работали группы системного профиля: код товара ловит безликое «Поставка
    оборудования»).
    """

    name = profile.name
    for excluded in profile.exclusion_keywords or []:
        if matches_keyword(tokens, excluded):
            return GroupMatch(profile.id, name, False, f"исключено ключом «{excluded}»")

    keywords = profile.keywords or []
    keyword_hit: bool | None = None
    hits: list[str] = []
    if keywords:
        hits = [keyword for keyword in keywords if matches_keyword(tokens, keyword)]
        if profile.match_mode == KeywordMatchMode.ALL.value:
            keyword_hit = len(hits) == len(keywords)
        else:
            keyword_hit = bool(hits)

    codes = profile.okpd2_codes or []
    code_hit: bool | None = None
    if codes:
        code_hit = bool(okpd2_code) and any(
            okpd2_service.covers(code, okpd2_code) for code in codes
        )

    if keyword_hit is None and code_hit is None:
        return GroupMatch(profile.id, name, False, "в профиле нет ни слов, ни кодов ОКПД2")

    # Профиль без явного режима — «сужение» (так их заводят специалисты); прежние группы
    # системного профиля, у которых поля нет, — «достаточно одного», как они и работали.
    mode = getattr(profile, "okpd2_mode", None) or (
        OKPD2_NARROW if isinstance(profile, RelevanceProfile) else OKPD2_EITHER
    )
    if mode == OKPD2_NARROW:
        matched = all(hit for hit in (keyword_hit, code_hit) if hit is not None)
    else:
        matched = bool(keyword_hit) or bool(code_hit)
    if not matched:
        return GroupMatch(profile.id, name, False, "нет совпадений")
    if keyword_hit:
        return GroupMatch(profile.id, name, True, f"совпало: {', '.join(hits[:3])}", "keywords")
    return GroupMatch(profile.id, name, True, f"совпал код ОКПД2 {okpd2_code}", "okpd2")


def check_group(tokens: list[str], group) -> GroupMatch:
    """Прежнее имя `match_profile` — без учёта кода ОКПД2."""

    return match_profile(tokens, group)


def evaluate(
    tokens: list[str],
    groups: list,
    *,
    okpd2_code: str | None = None,
) -> RelevanceOutcome:
    """Применяет набор профилей. Достаточно одного сработавшего.

    Закупка приписывается профилю, совпавшему по словам, даже если раньше по списку стоит
    профиль, совпавший одним кодом: само решение «прошла» от этого не меняется, но в карточке
    и в воронке было бы написано не то, и настроить профиль по такой подсказке нельзя.
    """

    active = [group for group in groups if getattr(group, "is_active", True)]
    rejected: list[str] = []
    by_code: GroupMatch | None = None
    for group in active:
        result = match_profile(tokens, group, okpd2_code)
        if result.matched and result.via == "keywords":
            return RelevanceOutcome(True, result.group_id, result.group_name, result.reason)
        if result.matched and by_code is None:
            by_code = result
        elif result.reason.startswith("исключено"):
            rejected.append(f"{group.name}: {result.reason}")
    if by_code is not None:
        return RelevanceOutcome(True, by_code.group_id, by_code.group_name, by_code.reason)
    reason = rejected[0] if rejected else "не совпал ни один профиль"
    return RelevanceOutcome(False, None, None, reason)


# --- работа с профилями в базе ------------------------------------------------------------


def get_profile(db: Session) -> SearchProfile | None:
    return db.scalar(select(SearchProfile).limit(1))


def get_or_create_profile(db: Session) -> SearchProfile:
    """Настройки охвата; при первом обращении — с общими профилями и фразами сбора по ТЗ.

    Засев — кодом, а не миграцией: это содержательные настройки, которые люди правят. Строка
    `SearchProfile` служит отметкой «засеяно»: если администратор удалит все общие профили,
    перезапуск не вернёт их обратно.
    """

    profile = get_profile(db)
    if profile is not None:
        return profile

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    if mirtek is None:
        raise RuntimeError("В справочнике производителей нет записи МИРТЕК")

    profile = SearchProfile(
        manufacturer_id=mirtek.id,
        min_nmck=Decimal(DEFAULT_MIN_NMCK),
        ai_score_threshold=Decimal(DEFAULT_AI_SCORE_THRESHOLD),
    )
    db.add(profile)
    db.flush()

    has_defaults = db.scalar(select(RelevanceProfile.id).where(RelevanceProfile.is_default.is_(True)).limit(1))
    if has_defaults is None:
        for item in KEYWORD_GROUPS:
            db.add(
                RelevanceProfile(
                    name=item["name"],
                    keywords=item["keywords"],
                    exclusion_keywords=item["exclusion_keywords"],
                    okpd2_codes=item["okpd2_codes"] or [],
                    match_mode=KeywordMatchMode.ANY.value,
                    okpd2_mode=OKPD2_EITHER,
                    is_default=True,
                    source_keys=[],
                )
            )
    known = {phrase.lower() for phrase in db.scalars(select(CollectionTerm.phrase))}
    for item in KEYWORD_GROUPS:
        for phrase in item["search_queries"]:
            if phrase.strip() and phrase.strip().lower() not in known:
                known.add(phrase.strip().lower())
                db.add(CollectionTerm(phrase=phrase.strip()))
    db.flush()
    logger.info(f"Созданы общие профили отбора ({len(KEYWORD_GROUPS)}) и фразы сбора")
    return profile


def bootstrap(db: Session) -> dict[str, int]:
    """Заводит профили и применяет их к закупкам без отметки. Вызывается при старте.

    Пока профиль создавался лениво (при первом открытии настроек), на сервере без такого
    визита отбор не работал вовсе: каждый собранный тендер оставался с NULL, а список
    трактует NULL как «показывать» — вся выдача ЭТП ГПБ висела «по профилю».
    """

    get_or_create_profile(db)
    db.commit()
    return backfill(db, only_unprocessed=True)


def default_profiles(db: Session) -> list[RelevanceProfile]:
    """Действующие общие профили — то, что отбирает закупки по умолчанию."""

    return list(
        db.scalars(
            select(RelevanceProfile)
            .where(RelevanceProfile.is_default.is_(True), RelevanceProfile.is_active.is_(True))
            .order_by(RelevanceProfile.name)
        )
    )


def active_groups(db: Session) -> list[RelevanceProfile]:
    """Прежнее имя `default_profiles` (до 05.10.2026 отбирали группы)."""

    return default_profiles(db)


def search_queries(db: Session) -> list[str]:
    """Фразы для поиска на площадках — то, чем система реально ходит за закупками."""

    return list(
        db.scalars(
            select(CollectionTerm.phrase)
            .where(CollectionTerm.is_active.is_(True))
            .order_by(CollectionTerm.created_at, CollectionTerm.phrase)
        )
    )


def tender_text(tender: Tender) -> str:
    """Текст, по которому работает фильтр: заголовок и то, что известно сразу при сборе."""

    parts = [tender.title, tender.customer_name, tender.procurement_method]
    return " ".join(part for part in parts if part)


def _applicable(profiles: list, source_key: str | None) -> list:
    """Профили, которые действуют на площадку: без привязки — на все."""

    return [
        profile
        for profile in profiles
        if not getattr(profile, "source_keys", None) or (source_key in profile.source_keys)
    ]


def _decide(profiles: list, tender, source_key: str | None) -> RelevanceOutcome | None:
    applicable = _applicable(profiles, source_key)
    if not applicable:
        # Ни один общий профиль не привязан к этой площадке — она не сужается (то же правило,
        # что на странице тендеров). Закупка считается прошедшей, иначе модель её не проверит.
        return RelevanceOutcome(True, None, None, "на эту площадку общие профили не действуют")
    return evaluate(tokenize(tender_text(tender)), applicable, okpd2_code=tender.okpd2_code)


def _set_mark(tender: Tender, outcome: RelevanceOutcome) -> None:
    tender.passed_relevance_filter = outcome.passed
    profile_id = outcome.group_id if isinstance(outcome.group_id, uuid.UUID) else None
    tender.matched_profile_id = profile_id
    tender.matched_keyword_group_id = None


def apply_to_tender(
    db: Session, tender: Tender, groups: list | None = None
) -> RelevanceOutcome:
    """Проставляет закупке отметку общих профилей. Ничего не удаляет и не скрывает."""

    profiles = default_profiles(db) if groups is None else groups
    if not profiles:
        # Профили не настроены — честно оставляем `None` («не проверяли»), а не `False`.
        return RelevanceOutcome(True, None, None, "общие профили не настроены")

    source = db.get(Source, tender.source_id) if tender.source_id else None
    outcome = _decide(profiles, tender, source.key if source else None)
    if outcome is not None:
        _set_mark(tender, outcome)
    return outcome


def backfill(db: Session, *, only_unprocessed: bool = False) -> dict[str, int]:
    """Прогоняет общие профили по уже собранным закупкам.

    Вызывается после правки общего профиля и при старте (`only_unprocessed` — только записи
    без отметки), чтобы отметки не оставались от прежних настроек.
    """

    profiles = default_profiles(db)
    if not profiles:
        return {"processed": 0, "passed": 0, "rejected": 0}

    source_keys = dict(db.execute(select(Source.id, Source.key)).all())
    query = select(Tender)
    if only_unprocessed:
        query = query.where(Tender.passed_relevance_filter.is_(None))

    processed = passed = 0
    for tender in db.scalars(query):
        outcome = _decide(profiles, tender, source_keys.get(tender.source_id))
        _set_mark(tender, outcome)
        processed += 1
        passed += int(outcome.passed)

    db.commit()
    logger.info(
        f"Общие профили применены к {processed} закупкам: прошли {passed}, "
        f"отсеяно {processed - passed}"
    )
    return {"processed": processed, "passed": passed, "rejected": processed - passed}
