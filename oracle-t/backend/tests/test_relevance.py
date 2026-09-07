"""Профиль релевантности: синтаксис ключей и решения фильтра (раздел 5.1.1 ТЗ).

Под наблюдением здесь две вещи, которые дороже всего ошибиться:

* **исключения рабочих групп** — без них «поверка счётчиков» собирает поверку воды, газа и
  медицинской техники, и список тендеров превращается в мусор;
* **разница между «не проверяли» и «не подходит»** — тендер, собранный до появления профиля,
  не должен молча пропасть из выдачи.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.search_profile import SearchKeywordGroup
from app.models.source import Source
from app.models.tender import Tender
from app.seed.search_profile_data import KEYWORD_GROUPS
from app.services import relevance_service
from app.services.relevance_service import evaluate, matches_keyword, tokenize


def _group(**overrides) -> SearchKeywordGroup:
    """Группа в памяти: сопоставление ключей чистое и базы не требует."""

    defaults = dict(
        id=uuid.uuid4(),
        search_profile_id=uuid.uuid4(),
        name="Тестовая группа",
        keywords=[],
        exclusion_keywords=[],
        okpd2_codes=None,
        match_mode="any",
        search_queries=[],
        is_active=True,
    )
    defaults.update(overrides)
    return SearchKeywordGroup(**defaults)


# --- синтаксис ключей ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "keyword", "expected"),
    [
        # `слово*` — по основе: ключ пишут один раз, а закупки склоняют его как угодно.
        ("Поставка счетчиков электроэнергии", "счетчик*", True),
        ("Поставка счётчиков", "счетчик*", False),  # ё против е — разные символы
        ("Поставка кабеля", "счетчик*", False),
        # Слово без звёздочки всё равно ловит словоформы: требовать `*` в каждом ключе —
        # лишняя обязанность для того, кто их пишет.
        ("Работы по поверке приборов", "поверка", True),
        # Близость: слова могут стоять в любом порядке, важен только разброс.
        ("Поверка счетчиков электрической энергии", "(поверк* счетчик*)~3", True),
        ("Счетчиков поверка", "(поверк* счетчик*)~3", True),
        (
            "Поверка манометров и ремонт счетчиков газа в котельной",
            "(поверк* счетчик*)~3",
            False,
        ),
        # Многословная фраза без оператора — слова подряд.
        ("Устройство сбора и передачи данных", "устройств* сбора и передачи данных", True),
    ],
)
def test_keyword_syntax(text, keyword, expected):
    assert matches_keyword(tokenize(text), keyword) is expected


def test_proximity_requires_all_terms():
    """Хотя бы одно слово отсутствует — ключ не совпал, каким бы близким ни было остальное."""

    assert matches_keyword(tokenize("Поверка оборудования"), "(поверк* счетчик*)~3") is False


# --- решения фильтра ----------------------------------------------------------------------


def test_exclusion_beats_positive_keyword():
    """Исключение сильнее совпадения: иначе «поверка счётчиков воды» пришла бы к нам."""

    group = _group(keywords=["(поверк* счетчик*)~3"], exclusion_keywords=["воды"])
    outcome = evaluate(tokenize("Поверка счетчиков холодной воды"), [group])

    assert outcome.passed is False
    assert "исключено" in outcome.reason


def test_keyword_match_wins_over_okpd2_match():
    """Группу-победителя выбирает совпадение по смыслу, а не по коду.

    Сам вердикт «релевантен» от порядка не зависит, но в карточке будет написано, какая
    группа сработала, и по неверной подсказке профиль не настроить.
    """

    by_code = _group(name="По коду", keywords=["аскуэ"], okpd2_codes=["26.51.63"])
    by_words = _group(name="По словам", keywords=["(счетчик* электроэнерг*)~3"])
    outcome = evaluate(
        tokenize("Поставка счетчиков электроэнергии"),
        [by_code, by_words],
        okpd2_code="26.51.63.130",
    )

    assert outcome.passed is True
    assert outcome.group_name == "По словам"


def test_okpd2_match_is_used_when_keywords_miss():
    group = _group(name="По коду", keywords=["аскуэ"], okpd2_codes=["26.51.63"])
    outcome = evaluate(tokenize("Закупка оборудования"), [group], okpd2_code="26.51.63.130")

    assert outcome.passed is True
    assert "ОКПД2" in outcome.reason


def test_okpd2_does_not_override_exclusion():
    """Код не воскрешает тендер, отклонённый исключением группы.

    Иначе закупка воды по коду 26.51.63.120 попадала бы к нам через группу, где вода прямо
    запрещена.
    """

    group = _group(
        keywords=["счетчик*"], exclusion_keywords=["воды"], okpd2_codes=["26.51.63"]
    )
    outcome = evaluate(
        tokenize("Поставка счетчиков воды"), [group], okpd2_code="26.51.63.120"
    )

    assert outcome.passed is False


def test_inactive_group_is_skipped():
    group = _group(keywords=["счетчик*"], is_active=False)
    assert evaluate(tokenize("Поставка счетчиков"), [group]).passed is False


# --- поведение на реальном наборе групп ---------------------------------------------------


@pytest.fixture()
def seeded_groups(db_session) -> list[SearchKeywordGroup]:
    relevance_service.get_or_create_profile(db_session)
    db_session.commit()
    return relevance_service.active_groups(db_session)


def test_all_nine_groups_are_seeded(seeded_groups):
    assert len(seeded_groups) == len(KEYWORD_GROUPS) == 9


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Ровно тот тендер, которого система не видела до появления профиля: обслуживание
        # приборов не попадало ни под одно из двух захардкоженных ключевых слов.
        ("Услуги по сервисному обслуживанию приборов LECO для филиалов", True),
        ("Поверка приборов учета электрической энергии", True),
        ("Замена приборов учета электроэнергии в многоквартирных домах", True),
        ("Монтаж приборов учета электрической энергии", True),
        ("Внедрение АСКУЭ на объектах сетевой компании", True),
        ("Поставка счетчиков электрической энергии", True),
        # Смежные сферы — те, из-за которых у рабочих групп есть исключения.
        ("Поверка счетчиков холодной и горячей воды", False),
        ("Замена газовых счетчиков в жилом фонде", False),
        ("Техническое обслуживание пожарной сигнализации", False),
        ("Поставка канцелярских товаров", False),
    ],
)
def test_real_profile_decisions(seeded_groups, title, expected):
    assert evaluate(tokenize(title), seeded_groups).passed is expected


def test_search_queries_cover_more_than_supply(db_session, seeded_groups):
    """Фраз для площадок должно быть заметно больше двух — в этом и была причина пробелов.

    До профиля каждый адаптер искал «счетчик электрической энергии» и «прибор учета
    электрической энергии», поэтому поверка, монтаж и обслуживание не попадали в систему
    вовсе.
    """

    queries = relevance_service.search_queries(db_session)

    assert len(queries) > 2
    joined = " ".join(queries).lower()
    for topic in ("поверка", "монтаж", "обслуживание", "замена", "аскуэ"):
        assert topic in joined


# --- «не проверяли» ≠ «не подходит» -------------------------------------------------------


def test_unprocessed_tender_stays_visible(db_session, seeded_groups):
    """Тендер без отметки не считается отсеянным.

    Собранные до появления профиля записи никто не проверял, и прятать их как нерелевантные
    значило бы соврать — то же правило, что с History и нулём.
    """

    from app.services.tender_service import TenderFilters, list_tenders

    source = Source(
        key=f"relevance_{uuid.uuid4().hex[:8]}",
        name="Источник для теста релевантности",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"REL-{uuid.uuid4().hex[:6]}",
        title="Закупка, собранная до появления профиля",
        currency="RUB",
        passed_relevance_filter=None,
    )
    db_session.add(tender)
    db_session.commit()

    found = list_tenders(
        db_session,
        limit=10,
        offset=0,
        filters=TenderFilters(only_profile_relevant=True, search=tender.title),
    )
    assert [item.id for item in found] == [tender.id]


def test_rejected_tender_is_hidden_but_not_deleted(db_session, seeded_groups):
    from app.services.tender_service import TenderFilters, list_tenders

    source = Source(
        key=f"relevance_{uuid.uuid4().hex[:8]}",
        name="Источник для теста релевантности",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db_session.add(source)
    db_session.flush()
    title = f"Поставка канцелярских товаров {uuid.uuid4().hex[:6]}"
    tender = Tender(
        source_id=source.id,
        external_id=f"REL-{uuid.uuid4().hex[:6]}",
        title=title,
        currency="RUB",
    )
    db_session.add(tender)
    relevance_service.apply_to_tender(db_session, tender, seeded_groups)
    db_session.commit()

    assert tender.passed_relevance_filter is False

    hidden = list_tenders(
        db_session, limit=10, offset=0, filters=TenderFilters(only_profile_relevant=True, search=title)
    )
    assert hidden == []

    # Запись не удалена — она видна, когда фильтр снят.
    visible = list_tenders(
        db_session, limit=10, offset=0, filters=TenderFilters(only_profile_relevant=False, search=title)
    )
    assert [item.id for item in visible] == [tender.id]
