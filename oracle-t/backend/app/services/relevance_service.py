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
from app.models.search_profile import KeywordMatchMode, SearchKeywordGroup, SearchProfile
from app.models.tender import Tender
from app.seed.search_profile_data import (
    DEFAULT_AI_SCORE_THRESHOLD,
    DEFAULT_MIN_NMCK,
    KEYWORD_GROUPS,
)

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
    """Результат проверки одной группы."""

    group_id: uuid.UUID | None
    group_name: str
    matched: bool
    reason: str


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


def check_group(tokens: list[str], group: SearchKeywordGroup) -> GroupMatch:
    """Проверяет одну группу: сначала исключения, потом положительные ключи."""

    for excluded in group.exclusion_keywords or []:
        if matches_keyword(tokens, excluded):
            return GroupMatch(
                group_id=group.id,
                group_name=group.name,
                matched=False,
                reason=f"исключено ключом «{excluded}»",
            )

    keywords = group.keywords or []
    if not keywords:
        return GroupMatch(group.id, group.name, False, "в группе нет ключевых слов")

    hits = [keyword for keyword in keywords if matches_keyword(tokens, keyword)]
    if group.match_mode == KeywordMatchMode.ALL.value:
        matched = len(hits) == len(keywords)
    else:
        matched = bool(hits)

    return GroupMatch(
        group_id=group.id,
        group_name=group.name,
        matched=matched,
        reason=(f"совпало: {', '.join(hits[:3])}" if matched else "нет совпадений"),
    )


def _okpd2_matches(group: SearchKeywordGroup, okpd2_code: str | None) -> bool:
    """Совпадает ли код ОКПД2 тендера с кодами группы (с учётом вложенности)."""

    if not okpd2_code or not group.okpd2_codes:
        return False
    return any(okpd2_code.startswith(code) for code in group.okpd2_codes)


def evaluate(
    tokens: list[str],
    groups: list[SearchKeywordGroup],
    *,
    okpd2_code: str | None = None,
) -> RelevanceOutcome:
    """Применяет все группы. Достаточно одной сработавшей.

    Код ОКПД2 усиливает, но не заменяет ключевые слова: совпадение кода само по себе
    засчитывается только если группа не отклонила тендер своими исключениями. Иначе закупка
    воды по коду 26.51.63.120 попадала бы к нам через группу, где вода прямо запрещена.
    """

    active = [group for group in groups if group.is_active]
    rejected: list[str] = []
    okpd2_candidates: list[SearchKeywordGroup] = []

    # Первый проход — по ключевым словам. Он идёт раньше и целиком, а не вперемешку со
    # вторым: иначе группа, совпавшая всего лишь кодом ОКПД2, перехватывала бы тендер у
    # группы, которая совпала по смыслу. Само решение «релевантен» от этого не менялось бы,
    # но в карточке было бы написано не то, и настроить профиль по такой подсказке нельзя.
    for group in active:
        result = check_group(tokens, group)
        if result.matched:
            return RelevanceOutcome(True, result.group_id, result.group_name, result.reason)
        if result.reason.startswith("исключено"):
            rejected.append(f"{group.name}: {result.reason}")
            continue
        if _okpd2_matches(group, okpd2_code):
            okpd2_candidates.append(group)

    # Второй проход — по кодам ОКПД2, среди групп, которые тендер не отклонили.
    if okpd2_candidates:
        group = okpd2_candidates[0]
        return RelevanceOutcome(True, group.id, group.name, f"совпал код ОКПД2 {okpd2_code}")

    reason = rejected[0] if rejected else "не совпала ни одна группа профиля"
    return RelevanceOutcome(False, None, None, reason)


# --- работа с профилем в базе -------------------------------------------------------------


def get_profile(db: Session) -> SearchProfile | None:
    return db.scalar(select(SearchProfile).limit(1))


def get_or_create_profile(db: Session) -> SearchProfile:
    """Возвращает профиль, при первом обращении создавая его с группами по образцу ТЗ.

    Группы засеиваются кодом, а не миграцией: это содержательные настройки, которые люди
    будут править, и их правки не должны конфликтовать с историей миграций.
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

    for item in KEYWORD_GROUPS:
        db.add(
            SearchKeywordGroup(
                search_profile_id=profile.id,
                name=item["name"],
                keywords=item["keywords"],
                exclusion_keywords=item["exclusion_keywords"],
                okpd2_codes=item["okpd2_codes"],
                search_queries=item["search_queries"],
            )
        )
    db.flush()
    logger.info(f"Создан профиль релевантности с {len(KEYWORD_GROUPS)} группами")
    return profile


def bootstrap(db: Session) -> dict[str, int]:
    """Заводит профиль и применяет его к тендерам без отметки. Вызывается при старте.

    До этого профиль создавался только при первом открытии раздела в настройках. На сервере,
    где туда никто не заходил, отбор не работал вовсе: `active_groups` отдавал пустой список,
    каждый собранный тендер оставался с `passed_relevance_filter = NULL`, а список трактует
    NULL как «показывать». В итоге вся выдача ЭТП ГПБ — бумага, светильники, грозозащита —
    висела в списке «по профилю» как новые закупки.
    """

    get_or_create_profile(db)
    db.commit()
    return backfill(db, only_unprocessed=True)


def active_groups(db: Session) -> list[SearchKeywordGroup]:
    profile = get_profile(db)
    if profile is None:
        return []
    return list(
        db.scalars(
            select(SearchKeywordGroup)
            .where(
                SearchKeywordGroup.search_profile_id == profile.id,
                SearchKeywordGroup.is_active.is_(True),
            )
            .order_by(SearchKeywordGroup.name)
        )
    )


def search_queries(db: Session) -> list[str]:
    """Фразы для поиска на площадках — объединение по всем активным группам.

    Именно они заменили захардкоженные в адаптерах две фразы: пока охват жил в константах
    кода, целые типы закупок (поверка, монтаж, обслуживание) вообще не попадали в систему.
    """

    seen: list[str] = []
    for group in active_groups(db):
        for query in group.search_queries or []:
            normalized = query.strip()
            if normalized and normalized not in seen:
                seen.append(normalized)
    return seen


def tender_text(tender: Tender) -> str:
    """Текст, по которому работает фильтр: заголовок и то, что известно сразу при сборе."""

    parts = [tender.title, tender.customer_name, tender.procurement_method]
    return " ".join(part for part in parts if part)


def apply_to_tender(
    db: Session, tender: Tender, groups: list[SearchKeywordGroup] | None = None
) -> RelevanceOutcome:
    """Проставляет тендеру результат фильтра. Ничего не удаляет и не скрывает."""

    groups = active_groups(db) if groups is None else groups
    if not groups:
        # Профиль не настроен — честно оставляем `None` («не проверяли»), а не `False`.
        return RelevanceOutcome(True, None, None, "профиль релевантности не настроен")

    outcome = evaluate(tokenize(tender_text(tender)), groups, okpd2_code=tender.okpd2_code)
    tender.passed_relevance_filter = outcome.passed
    tender.matched_keyword_group_id = outcome.group_id
    return outcome


def backfill(db: Session, *, only_unprocessed: bool = False) -> dict[str, int]:
    """Прогоняет фильтр по уже собранным тендерам.

    Нужен после каждой правки профиля: без пересчёта старые записи остались бы с отметками
    от прежних настроек, и список показывал бы одно, а профиль означал другое.

    `only_unprocessed` — обработать только те, у которых отметки ещё нет. Так дозаполняют
    накопленный архив, не трогая уже посчитанное.
    """

    groups = active_groups(db)
    if not groups:
        return {"processed": 0, "passed": 0, "rejected": 0}

    query = select(Tender)
    if only_unprocessed:
        query = query.where(Tender.passed_relevance_filter.is_(None))

    processed = passed = 0
    for tender in db.scalars(query):
        outcome = evaluate(
            tokenize(tender_text(tender)), groups, okpd2_code=tender.okpd2_code
        )
        tender.passed_relevance_filter = outcome.passed
        tender.matched_keyword_group_id = outcome.group_id
        processed += 1
        passed += int(outcome.passed)

    db.commit()
    logger.info(
        f"Профиль релевантности применён к {processed} тендерам: прошли {passed}, "
        f"отсеяно {processed - passed}"
    )
    return {"processed": processed, "passed": passed, "rejected": processed - passed}
