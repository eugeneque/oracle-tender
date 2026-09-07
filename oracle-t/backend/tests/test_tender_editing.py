"""Тесты правок карточки, истории изменений и расширенных фильтров списка
(раздел 5.6 ТЗ — Этап 7).

Проверяется то, что ломается молча: правка, не попавшая в историю (решение по тендеру
становится необъяснимым), фильтр, который тихо игнорируется (пользователь думает, что
выборка полная), и сортировка по проценту победителя, где пустые значения не должны
вытеснять рассчитанные.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.analysis import WinPercentage
from app.models.manufacturer import Manufacturer
from app.models.region import FederalDistrict, Region
from app.models.source import Source
from app.models.tender import RelevanceStatus, Tender, TenderType
from app.models.tender_history import HistoryKind
from app.services.tender_edit_service import (
    TenderEditError,
    add_comment,
    list_history,
    set_relevance,
    update_tender,
)
from app.services.tender_service import TenderFilters, count_tenders, list_tenders


def _source(db) -> Source:
    source = Source(
        key=f"edit_{uuid.uuid4().hex[:8]}",
        name="Тестовая площадка",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    return source


def _tender(db, source, **overrides) -> Tender:
    fields = {"title": "Поставка приборов учёта", "currency": "RUB", **overrides}
    tender = Tender(
        source_id=source.id,
        external_id=f"EXT-{uuid.uuid4().hex[:6]}",
        **fields,
    )
    db.add(tender)
    db.flush()
    return tender


def _region(db, code: str, name: str, district_code: int) -> Region:
    """Регион из справочника Приложения H — сид может не содержать нужного кода в тестовой
    базе, поэтому берём существующий или создаём."""

    district = db.get(FederalDistrict, district_code)
    if district is None:
        district = FederalDistrict(code=district_code, name=f"Округ {district_code}")
        db.add(district)
        db.flush()
    region = db.get(Region, code)
    if region is None:
        region = Region(code=code, name=name, federal_district_code=district_code)
        db.add(region)
        db.flush()
    return region


def test_update_records_history_with_author(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source, tender_type=TenderType.OTHER.value)

    update_tender(
        db_session,
        tender,
        {"tender_type": TenderType.SUPPLY_ONLY.value, "okpd2_code": "26.51.63.130"},
        actor=admin_user,
    )

    entries = [entry for entry, _ in list_history(db_session, tender.id)]
    changed = {entry.field_name: (entry.old_value, entry.new_value) for entry in entries}
    assert changed["tender_type"] == (TenderType.OTHER.value, TenderType.SUPPLY_ONLY.value)
    assert changed["okpd2_code"] == (None, "26.51.63.130")
    assert all(entry.user_id == admin_user.id for entry in entries)
    assert tender.tender_type == TenderType.SUPPLY_ONLY.value


def test_update_skips_unchanged_fields(db_session, admin_user):
    """Повторное сохранение той же формы не должно засорять историю записями «было X,
    стало X» — иначе лента перестанет читаться."""

    source = _source(db_session)
    tender = _tender(db_session, source, okpd2_code="26.51.63.130")

    update_tender(db_session, tender, {"okpd2_code": "26.51.63.130"}, actor=admin_user)

    assert list_history(db_session, tender.id) == []


def test_region_change_updates_federal_district(db_session, admin_user):
    """Округ определяется регионом заказчика (Приложение G ТЗ): оставить прежний округ при
    смене региона — значит показать в карточке противоречие."""

    region = _region(db_session, "52", "Нижегородская область", 6)
    # Округ берём из справочника, а не из константы: сид Приложения H уже содержит регион,
    # и жёсткое число сделало бы тест проверкой сида, а не логики.
    other_district = 1 if region.federal_district_code != 1 else 2
    source = _source(db_session)
    tender = _tender(db_session, source, federal_district_code=other_district)

    update_tender(db_session, tender, {"region_organizer_code": "52"}, actor=admin_user)

    assert tender.federal_district_code == region.federal_district_code
    fields = {entry.field_name for entry, _ in list_history(db_session, tender.id)}
    assert fields == {"region_organizer_code", "federal_district_code"}


def test_update_rejects_unknown_values(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source)

    with pytest.raises(TenderEditError):
        update_tender(db_session, tender, {"tender_type": "нечто"}, actor=admin_user)
    with pytest.raises(TenderEditError):
        update_tender(db_session, tender, {"region_organizer_code": "ZZ"}, actor=admin_user)
    # Отказ должен быть до записи: частично применённая правка хуже отклонённой.
    assert list_history(db_session, tender.id) == []


def test_update_rejects_unknown_field(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source)

    with pytest.raises(TenderEditError):
        update_tender(db_session, tender, {"price": 1}, actor=admin_user)


def test_set_relevance_marks_irrelevant_and_logs(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source)

    set_relevance(db_session, tender, RelevanceStatus.REJECTED.value, actor=admin_user)

    assert tender.relevance_status == RelevanceStatus.REJECTED.value
    entry, user_name = list_history(db_session, tender.id)[0]
    assert entry.field_name == "relevance_status"
    assert entry.new_value == RelevanceStatus.REJECTED.value
    assert user_name == admin_user.full_name


def test_comment_appears_in_history(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source)

    add_comment(db_session, tender, "  Заказчик уточнил объём  ", actor=admin_user)

    entry, user_name = list_history(db_session, tender.id)[0]
    assert entry.kind == HistoryKind.COMMENT.value
    assert entry.comment == "Заказчик уточнил объём"
    assert user_name == admin_user.full_name


def test_empty_comment_rejected(db_session, admin_user):
    source = _source(db_session)
    tender = _tender(db_session, source)

    with pytest.raises(TenderEditError):
        add_comment(db_session, tender, "   ", actor=admin_user)


def test_filter_by_region_matches_organizer_or_delivery(db_session):
    _region(db_session, "52", "Нижегородская область", 6)
    _region(db_session, "77", "Москва", 1)
    source = _source(db_session)
    by_organizer = _tender(db_session, source, region_organizer_code="52")
    by_delivery = _tender(db_session, source, region_delivery_code="52")
    other = _tender(db_session, source, region_organizer_code="77")

    found = {
        tender.id
        for tender in list_tenders(db_session, filters=TenderFilters(region_codes=["52"]))
    }
    assert {by_organizer.id, by_delivery.id} <= found
    assert other.id not in found


def test_filter_by_okpd2_matches_prefix(db_session):
    """ОКПД2 иерархичен: «26.51» обязано находить 26.51.63.130 (Приложение F ТЗ)."""

    source = _source(db_session)
    matching = _tender(db_session, source, okpd2_code="26.51.63.130")
    other = _tender(db_session, source, okpd2_code="33.13.11.000")

    found = {
        tender.id for tender in list_tenders(db_session, filters=TenderFilters(okpd2_prefix="26.51"))
    }
    assert matching.id in found
    assert other.id not in found


def _mirtek(db) -> Manufacturer:
    manufacturer = db.query(Manufacturer).filter(Manufacturer.is_mirtek.is_(True)).first()
    if manufacturer is None:
        manufacturer = Manufacturer(legal_name='ООО "МИРТЕК"', is_mirtek=True)
        db.add(manufacturer)
        db.flush()
    return manufacturer


def test_filter_and_sort_by_win_percentage(db_session):
    manufacturer = _mirtek(db_session)
    source = _source(db_session)
    high = _tender(db_session, source, title="Высокий процент")
    low = _tender(db_session, source, title="Низкий процент")
    without = _tender(db_session, source, title="Без расчёта")
    db_session.add_all(
        [
            WinPercentage(
                tender_id=high.id,
                manufacturer_id=manufacturer.id,
                percentage=Decimal("87.50"),
                requirements_total=10,
                requirements_scored=10,
            ),
            WinPercentage(
                tender_id=low.id,
                manufacturer_id=manufacturer.id,
                percentage=Decimal("35.00"),
                requirements_total=10,
                requirements_scored=10,
            ),
        ]
    )
    db_session.flush()

    filters = TenderFilters(win_percentage_min=Decimal("80"))
    found = {tender.id for tender in list_tenders(db_session, filters=filters)}
    assert high.id in found
    assert low.id not in found
    # Тендер без расчёта не должен проходить фильтр по проценту: неизвестное — не «высокое».
    assert without.id not in found
    assert count_tenders(db_session, filters=filters) == len(found)

    ordered = list_tenders(db_session, sort_by="win_percentage", descending=True, limit=1000)
    positions = {tender.id: index for index, tender in enumerate(ordered)}
    assert positions[high.id] < positions[low.id]
    # Пустые значения — в конце, иначе нерассчитанные тендеры вытеснят рассчитанные.
    assert positions[low.id] < positions[without.id]


def test_pagination_splits_result_without_overlap(db_session):
    source = _source(db_session)
    created = [_tender(db_session, source, title=f"Тендер {index}") for index in range(5)]
    filters = TenderFilters(source_keys=[source.key])

    first = list_tenders(db_session, limit=2, offset=0, filters=filters)
    second = list_tenders(db_session, limit=2, offset=2, filters=filters)

    assert len(first) == 2
    assert len(second) == 2
    assert not {t.id for t in first} & {t.id for t in second}
    assert count_tenders(db_session, filters=filters) == len(created)


def test_history_endpoint_and_comment_via_api(client, admin_token):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        source = _source(db)
        tender = _tender(db, source)
        db.commit()
        tender_id = str(tender.id)
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {admin_token}"}
    patched = client.patch(
        f"/tenders/{tender_id}",
        json={"tender_type": TenderType.COMPLEX.value},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["tender_type"] == TenderType.COMPLEX.value

    commented = client.post(
        f"/tenders/{tender_id}/comments", json={"text": "Проверено вручную"}, headers=headers
    )
    assert commented.status_code == 201, commented.text

    history = client.get(f"/tenders/{tender_id}/history", headers=headers)
    assert history.status_code == 200
    rows = history.json()
    assert [row["kind"] for row in rows] == ["comment", "field_change"]
    assert rows[1]["field_label"] == "Тип конкурса"
    assert rows[0]["user_name"]

    rejected = client.patch(
        f"/tenders/{tender_id}", json={"tender_type": "нечто"}, headers=headers
    )
    assert rejected.status_code == 422


def test_regions_dictionary_available(client, admin_token):
    response = client.get("/dictionaries/regions", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    regions = response.json()
    assert regions, "справочник регионов пуст — не выполнен сид Приложения H"
    assert {"code", "name", "federal_district_code"} <= set(regions[0])
