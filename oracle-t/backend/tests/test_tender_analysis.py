"""Тесты ИИ-анализа тендера (раздел 5.4 ТЗ — Этап 5).

Обращения к модели подменяются: проверяется не качество формулировок YandexGPT, а то, что
код вокруг него ведёт себя предсказуемо — берёт ОКПД2 из текста, а не из ответа модели,
сверяет регион со справочником, не теряет требования на перекрытии кусков и не падает,
когда модель недоступна или ответила мусором.
"""

from __future__ import annotations

import uuid

import pytest

import app.services.tender_analysis as analysis_module
from app.models.analysis import Criticality, Requirement
from app.models.source import Source
from app.models.tender import Tender, TenderType
from app.models.user import User
from app.services.tender_analysis import (
    ClassificationResult,
    ExtractedRequirement,
    RequirementsResult,
    analyze_tender,
    extract_okpd2_from_text,
    is_relevant_okpd2,
    match_region,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Поставка счетчиков, код ОКПД2 26.51.63.130", "26.51.63.130"),
        # Приоритетный код выигрывает у соседних: тендер на счётчики упоминает и монтажные
        # работы, но релевантность определяет код товара (раздел 5.4 ТЗ, п.5).
        ("ОКПД2 43.21.10 работы; товар 26.51.63.130", "26.51.63.130"),
        ("Счетчики воды 26.51.63.120", "26.51.63.120"),
        # Код вне нашей ниши берётся только с явной меткой: по форме он неотличим от
        # сметной расценки, и без метки решение отдаётся модели (см. docstring функции).
        ("ОКПД2 43.21.10 — только работы", "43.21.10"),
        ("Только работы: 43.21.10", None),
        # Сметный шифр машин и механизмов — форма кода, смысл другой.
        ("расценка 91.05.01 машины и механизмы", None),
        ("Кодов нет", None),
        # Дата, слипшаяся с буквой: между «4» и «г» границы слова нет, и такая дата
        # оставалась в тексте кодом «16.08» — на закупке счётчиков он подменял настоящий.
        ("доверенность №144 от 16.08.2024г., с одной стороны", None),
        # Время работы заказчика: нулевых группировок в ОКПД2 не бывает.
        ("заявки принимаются с 09:00 до 17:00 (в пятницу — до 16.00)", None),
        # Нумерация пункта документа: во второй группе кода всегда две цифры.
        ("согласно п. 12.7 настоящего договора", None),
        # Проверка, что маскировка дат по-прежнему не съедает настоящие коды рядом с ними.
        ("Протокол от 23.12.2026, ОКПД2 71.12.40", "71.12.40"),
    ],
)
def test_extract_okpd2_prefers_priority_code(text, expected):
    assert extract_okpd2_from_text(text) == expected


def test_is_relevant_okpd2():
    assert is_relevant_okpd2("26.51.63.130")
    assert is_relevant_okpd2("26.51.63.110")
    assert not is_relevant_okpd2("43.21.10")
    assert not is_relevant_okpd2(None)


def test_match_region_tolerates_different_spellings(db_session):
    # Справочник Приложения H засеян миграцией — тест опирается на реальные записи,
    # а не создаёт свои.
    assert match_region(db_session, "Ростовская область").code == "61"
    # В документах регион пишут по-разному — сверка идёт по значащей части названия.
    assert match_region(db_session, "Респ. Татарстан").code == "16"
    assert match_region(db_session, "  ") is None
    assert match_region(db_session, "Марсианская область") is None


def _make_tender(db_session, title: str = "Поставка счетчиков электрической энергии") -> Tender:
    # Уникальные ключи в каждом тесте: сервисы коммитят сами, общего отката между тестами нет.
    suffix = uuid.uuid4().hex[:8]
    source = Source(
        key=f"test_{suffix}",
        name="Тестовый источник",
        url=f"https://example.test/{suffix}",
        type="eis",
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=f"T-{suffix}", title=title)
    db_session.add(tender)
    db_session.commit()
    db_session.refresh(tender)
    return tender


def _classification(**overrides) -> ClassificationResult:
    """Полный набор полей: в схеме ответа они обязательные (см. докстринг
    `ClassificationResult`), значения по умолчанию задаются здесь, а не в модели."""

    data = {
        "tender_type": TenderType.OTHER.value,
        "okpd2_code": "",
        "region_name": "",
        "delivery_region_name": "",
        "summary": "",
    }
    data.update(overrides)
    return ClassificationResult(**data)


def _requirement(**overrides) -> ExtractedRequirement:
    data = {
        "text": "Требование",
        "normalized_text": "",
        "criticality": Criticality.IMPORTANT.value,
        "group_name": "",
    }
    data.update(overrides)
    return ExtractedRequirement(**data)


def _patch_model(monkeypatch, *, classification=None, requirements=None):
    """Подменяет вызов модели: тип ответа определяется запрошенной схемой."""

    def _fake(db, *, system_prompt, user_text, response_model, **kwargs):
        if response_model is ClassificationResult:
            if classification is None:
                raise RuntimeError("модель недоступна")
            return classification
        if requirements is None:
            raise RuntimeError("модель недоступна")
        return requirements

    monkeypatch.setattr(analysis_module, "run_structured", _fake)


def test_analyze_fills_type_okpd2_and_region(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session, "Поставка счетчиков. ОКПД2 26.51.63.130")
    _patch_model(
        monkeypatch,
        classification=_classification(
            tender_type=TenderType.SUPPLY_ONLY.value,
            okpd2_code="",
            region_name="Ростовская область",
            delivery_region_name="",
            summary="Закупка однофазных счётчиков",
        ),
        requirements=RequirementsResult(
            requirements=[
                _requirement(
                    text="Класс точности не хуже 1,0",
                    normalized_text="Класс точности: не хуже 1,0",
                    criticality=Criticality.CRITICAL.value,
                    group_name="Метрологические характеристики",
                )
            ]
        ),
    )

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert tender.tender_type == TenderType.SUPPLY_ONLY.value
    assert tender.okpd2_code == "26.51.63.130"
    assert tender.region_organizer_code == "61"
    assert tender.federal_district_code == 3
    # Регион поставки отдельно не указан — дублируется регион организатора (раздел 5.4 ТЗ, п.6)
    assert tender.region_delivery_code == "61"
    assert tender.ai_comment == "Закупка однофазных счётчиков"
    assert outcome.requirements_saved == 1


def test_analysis_keeps_organizer_region_from_card(db_session, admin_user: User, monkeypatch):
    """Регион организатора, снятый с карточки закупки, не перезаписывается регионом из текста.

    В документации почти всегда назван регион поставки; если пустить его в
    `region_organizer_code`, в выгрузке появляется заказчик из Ростова с регионом
    «Краснодарский край», а вместе с регионом уезжает ФО и ответственный (Приложение D ТЗ).
    """

    tender = _make_tender(db_session, "Работы в Краснодарском крае")
    tender.region_organizer_code = "61"  # Ростовская область — с адреса заказчика
    tender.federal_district_code = 3
    db_session.flush()

    _patch_model(
        monkeypatch,
        classification=_classification(
            tender_type=TenderType.WORKS_ONLY.value,
            region_name="Краснодарский край",
            delivery_region_name="Краснодарский край",
        ),
        requirements=RequirementsResult(requirements=[]),
    )

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert tender.region_organizer_code == "61"
    assert tender.federal_district_code == 3
    # Место поставки при этом обновляется — оно и есть то, что названо в документации.
    assert tender.region_delivery_code == "23"
    assert any("Регион организатора оставлен прежним" in m for m in outcome.messages)


def test_okpd2_from_text_wins_over_model_answer(db_session, admin_user: User, monkeypatch):
    """Модель охотно «вспоминает» правдоподобный, но неверный код; в тексте он проверяем."""

    tender = _make_tender(db_session, "Закупка. ОКПД2 26.51.63.130 счетчики электроэнергии")
    _patch_model(
        monkeypatch,
        classification=_classification(okpd2_code="26.51.63.120"),
        requirements=RequirementsResult(requirements=[]),
    )

    analyze_tender(db_session, tender, actor=admin_user)

    assert tender.okpd2_code == "26.51.63.130"


def test_model_okpd2_used_only_when_text_has_none(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session, "Закупка приборов учёта без указания кода")
    _patch_model(
        monkeypatch,
        classification=_classification(okpd2_code="26.51.63.130"),
        requirements=RequirementsResult(requirements=[]),
    )

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert tender.okpd2_code == "26.51.63.130"
    assert any("из ответа модели" in message for message in outcome.messages)


def test_duplicate_requirements_from_overlapping_chunks_saved_once(
    db_session, admin_user: User, monkeypatch
):
    tender = _make_tender(db_session)
    duplicate = _requirement(text="Класс точности   не хуже 1,0")
    _patch_model(
        monkeypatch,
        classification=_classification(),
        requirements=RequirementsResult(
            requirements=[
                duplicate,
                _requirement(text="класс точности не хуже 1,0"),  # тот же текст иначе
                _requirement(text="Наличие реле управления нагрузкой"),
            ]
        ),
    )

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert outcome.requirements_saved == 2


def test_unknown_criticality_falls_back_to_important(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session)
    _patch_model(
        monkeypatch,
        classification=_classification(),
        requirements=RequirementsResult(
            requirements=[_requirement(text="Требование", criticality="очень важно")]
        ),
    )

    analyze_tender(db_session, tender, actor=admin_user)

    saved = db_session.query(Requirement).filter(Requirement.tender_id == tender.id).one()
    assert saved.criticality == Criticality.IMPORTANT.value


def test_reanalysis_keeps_requirements_confirmed_by_human(
    db_session, admin_user: User, monkeypatch
):
    """Правка человека имеет приоритет над автоматикой — тот же принцип, что в каталоге."""

    tender = _make_tender(db_session)
    db_session.add(
        Requirement(
            tender_id=tender.id,
            text="Подтверждённое человеком требование",
            criticality=Criticality.CRITICAL.value,
            verified_by_user=True,
        )
    )
    db_session.add(
        Requirement(tender_id=tender.id, text="Автоматическое старое требование", criticality="minor")
    )
    db_session.commit()

    _patch_model(
        monkeypatch,
        classification=_classification(),
        requirements=RequirementsResult(requirements=[_requirement(text="Новое требование")]),
    )
    analyze_tender(db_session, tender, actor=admin_user)

    texts = {
        requirement.text
        for requirement in db_session.query(Requirement).filter(Requirement.tender_id == tender.id)
    }
    assert "Подтверждённое человеком требование" in texts
    assert "Автоматическое старое требование" not in texts
    assert "Новое требование" in texts


def test_analysis_survives_model_failure(db_session, admin_user: User, monkeypatch):
    """Модель недоступна — анализ не падает, а сообщает о неудаче."""

    tender = _make_tender(db_session, "Поставка счетчиков ОКПД2 26.51.63.130")
    _patch_model(monkeypatch)  # обе ветки бросают исключение

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert outcome.requirements_saved == 0
    assert outcome.messages
    # ОКПД2 не зависит от модели — он извлечён кодом и сохранён несмотря на сбой.
    assert tender.okpd2_code == "26.51.63.130"


def test_tender_without_text_is_reported(db_session, admin_user: User, monkeypatch):
    tender = _make_tender(db_session, title="")
    _patch_model(monkeypatch, classification=_classification())

    outcome = analyze_tender(db_session, tender, actor=admin_user)

    assert outcome.requirements_saved == 0
    assert any("нет ни наименования" in message for message in outcome.messages)


def test_technical_specification_is_read_before_the_rest():
    """Техническое задание должно попадать в первые куски — их число ограничено.

    До этого разделы шли в порядке публикации, и на закупке «Россети Центр» (32616337073)
    бюджет кусков уходил на извещение и проект договора, а ТЗ оставалось за пределом: анализ
    возвращал ноль требований."""

    text = (
        "Наименование закупки\n\n"
        "=== Извещение о закупке.docx ===\nусловия проведения\n\n"
        "=== Приложение №2 - проект Договора.docx ===\nответственность сторон\n\n"
        "=== ТЗ.docx ===\nкласс точности 1,0\n\n"
        "=== Смета.xlsx ===\nстоимость работ\n"
    )

    result = analysis_module._prioritised_text(text)

    assert result.startswith("Наименование закупки")
    positions = [result.index(name) for name in ("ТЗ.docx", "Извещение", "Договора", "Смета")]
    assert positions == sorted(positions)


def test_repeated_document_is_read_once():
    """Уведомление об изменении публикуется вместе со всем комплектом заново — разбирать
    один файл дважды значит потратить половину бюджета кусков впустую."""

    text = (
        "=== ТЗ.docx ===\nпервая редакция\n\n"
        "=== Приложение №4.docx ===\nпорядок действий\n\n"
        "=== ТЗ.docx ===\nвторая редакция\n"
    )

    result = analysis_module._prioritised_text(text)

    assert result.count("=== ТЗ.docx ===") == 1
    assert "вторая редакция" in result
    assert "первая редакция" not in result


def test_text_without_document_markers_is_left_as_is():
    text = "Наименование тендера без единого документа"

    assert analysis_module._prioritised_text(text) == text


def test_nested_archive_heading_does_not_shadow_its_contents():
    """Заголовок вложенного архива идёт без собственного текста — его содержимое следует
    отдельными разделами и не должно потеряться."""

    text = (
        "=== Приложение №1 - Техническое задание.rar ===\n\n"
        "=== ТЗ.docx ===\nкласс точности 1,0\n"
    )

    result = analysis_module._prioritised_text(text)

    assert "класс точности 1,0" in result


def test_long_documentation_keeps_chunks_about_the_device():
    """Когда кусков больше предела, в модель уходят те, где говорится о приборе, а не первые
    попавшиеся: на проекте договора ИСУ в сотню страниц техническое задание с
    характеристиками лежало в приложении за пределом первых кусков, и анализ возвращал
    требования к оплате вместо требований к счётчику."""

    filler = "Порядок оплаты и ответственность сторон. " * 5
    device = "Счётчик класса точности 1,0, интерфейс RS-485, протокол СПОДЭС. " * 5
    chunks = [f"Наименование закупки {filler}"]
    chunks += [f"{index} {filler}" for index in range(analysis_module.MAX_CHUNKS_PER_TENDER + 3)]
    chunks.append(f"ТЗ {device}")
    chunks.append(f"Приложение {device}")

    selected = analysis_module._select_chunks(chunks)

    assert len(selected) == analysis_module.MAX_CHUNKS_PER_TENDER
    assert selected[0] == chunks[0], "первый кусок — наименование и начало документа"
    assert selected[-2:] == chunks[-2:], "куски о приборе из конца документа отобраны"
    # Порядок отобранных — документный, а не по убыванию «насыщенности».
    assert [chunks.index(chunk) for chunk in selected] == sorted(
        chunks.index(chunk) for chunk in selected
    )


def test_short_documentation_is_read_whole():
    chunks = [f"кусок {index}" for index in range(analysis_module.MAX_CHUNKS_PER_TENDER)]
    assert analysis_module._select_chunks(chunks) == chunks
