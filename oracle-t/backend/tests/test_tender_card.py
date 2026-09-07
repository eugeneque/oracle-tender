"""Тесты полной карточки закупки и определения региона (раздел 5.6, Приложение H ТЗ).

Регион до этой правки не определялся вовсе, пока по тендеру не запускали ИИ-анализ
документации, — при том что на странице ЕИС он есть всегда. Здесь проверяется то, из-за чего
такая подстановка опаснее пустого поля: неверно определённый регион молча уводит тендер в
чужой фильтр и назначает не того ответственного, а заметить это можно только вручную сверив
с сайтом.

Разметка в тестах — сокращённые фрагменты реальных страниц ЕИС: у 44-ФЗ и 223-ФЗ она разная,
и один разбор на двоих не работает.
"""

from __future__ import annotations

import uuid

import pytest

from app.adapters.eis_card import parse_sections, parse_table
from app.models.region import Region
from app.models.source import Source
from app.models.tender import Tender
from app.services import region_resolver
from app.services.tender_card_service import fill_gaps_from_card

CARD_223_HTML = """
<section class="common-text">
  <div class="common-text__caption">Сведения о заказчике</div>
  <div class="col-9">
    <div class="common-text__title">Наименование организации</div>
    <div class="common-text__value">АО «ОБЛКОММУНЭНЕРГО»</div>
  </div>
  <div class="row">
    <div class="col-4 d-flex">
      <div class="common-text__value common-text__value--gray">ИНН</div>
      <div class="ml-1 common-text__value">6454038461</div>
    </div>
  </div>
  <div class="col-9">
    <div class="common-text__title">Место нахождения</div>
    <div class="common-text__value">410012, Г. САРАТОВ, УЛ МОСКОВСКАЯ, Д. 66</div>
  </div>
  <div class="col-9">
    <div class="common-text__title">Почтовый адрес</div>
    <div class="common-text__value">410012, Саратовская обл, г Саратов, ул Московская, дом 66</div>
  </div>
</section>
<section class="common-text">
  <div class="common-text__caption">Контактная информация</div>
  <div class="col-9">
    <div class="common-text__title">Контактное лицо</div>
    <div class="common-text__value">Базик Т.А.</div>
  </div>
  <div class="col-9">
    <div class="common-text__title">Адрес электронной почты</div>
    <div class="common-text__value">bazikta@oke64.ru</div>
  </div>
</section>
"""

CARD_44_HTML = """
<div class="blockInfo">
  <div class="blockInfo__title">Контактная информация</div>
  <div class="blockInfo__section">
    <span class="section__title">Организация, осуществляющая размещение</span>
    <span class="section__info">МКУ ГОРОДСКОГО ОКРУГА ЩЁЛКОВО</span>
  </div>
  <div class="blockInfo__section">
    <span class="section__title">Почтовый адрес</span>
    <span class="section__info">Российская Федерация, 141100, Московская обл, Щёлково г, ул Свирская, 1</span>
  </div>
  <div class="blockInfo__section">
    <span class="section__title">Ответственное должностное лицо</span>
    <span class="section__info">Фалина Е. А.</span>
  </div>
</div>
"""


class TestCardParsing:
    def test_223fz_sections_include_requisites(self):
        """ИНН, КПП и ОГРН свёрстаны иначе, чем остальные поля — серой подписью рядом со
        значением. Без их разбора теряется главный формальный признак региона."""

        sections = parse_sections(CARD_223_HTML)
        by_title = {section.title: dict(section.fields) for section in sections}

        assert "Сведения о заказчике" in by_title
        assert by_title["Сведения о заказчике"]["ИНН"] == "6454038461"
        assert "Почтовый адрес" in by_title["Сведения о заказчике"]
        assert by_title["Контактная информация"]["Контактное лицо"] == "Базик Т.А."

    def test_44fz_sections_are_parsed_too(self):
        """У 44-ФЗ другая вёрстка — это отдельный раздел сайта, а не другой шаблон одного."""

        sections = parse_sections(CARD_44_HTML)
        by_title = {section.title: dict(section.fields) for section in sections}

        assert "Контактная информация" in by_title
        assert "Московская обл" in by_title["Контактная информация"]["Почтовый адрес"]

    def test_table_tab_is_parsed(self):
        html = """
        <table>
          <thead><tr><th>Дата</th><th>Событие</th></tr></thead>
          <tbody>
            <tr><td>09.10.2025 13:46</td><td>Размещён протокол № 1</td></tr>
            <tr><td>09.10.2025 13:44</td><td>Размещено извещение</td></tr>
          </tbody>
        </table>
        """
        table = parse_table(html, "Журнал событий")

        assert table.headers == ["Дата", "Событие"]
        assert len(table.rows) == 2
        assert "протокол" in table.rows[0][1]

    def test_empty_tab_is_distinguishable_from_broken_one(self):
        """Пустая вкладка и вкладка, которую не удалось разобрать, — разные вещи: в первом
        случае у закупки просто нет протоколов, во втором мы потеряли данные."""

        table = parse_table('<div class="no-data">Информация отсутствует</div>', "Протоколы")

        assert table.headers == []
        assert table.rows == [["Информация отсутствует"]]


class TestRegionResolver:
    def test_street_name_does_not_win_over_region(self, db_session):
        """«ул Московская» в Саратове не делает закупку московской. Ровно на этом первая
        версия и ошиблась: улица в адресе перевесила название области."""

        region = region_resolver.region_from_address(
            db_session, "410012, Саратовская обл, г Саратов, р-н Кировский, ул Московская, дом 66"
        )

        assert region is not None
        assert region.code == "64"

    def test_city_without_region_in_address(self, db_session):
        """В поле «Место нахождения» области часто нет вовсе — только город."""

        region = region_resolver.region_from_address(
            db_session, "410012, Г. САРАТОВ, УЛ МОСКОВСКАЯ, Д. 66"
        )

        assert region is not None and region.code == "64"

    def test_region_from_inn(self, db_session):
        assert region_resolver.region_from_inn(db_session, "6454038461").code == "64"
        assert region_resolver.region_from_inn(db_session, "7707083893").code == "77"
        # 97 и 99 — тоже Москва: у крупных регионов ФНС выдала несколько кодов.
        assert region_resolver.region_from_inn(db_session, "9705000000").code == "77"

    def test_inn_garbage_is_ignored(self, db_session):
        assert region_resolver.region_from_inn(db_session, None) is None
        assert region_resolver.region_from_inn(db_session, "12") is None
        assert region_resolver.region_from_inn(db_session, "не указан") is None

    def test_resolve_reports_where_value_came_from(self, db_session):
        """Подставленный системой регион пользователь должен иметь возможность проверить:
        по нему строятся фильтры и назначается ответственный."""

        region, explanation = region_resolver.resolve_region(
            db_session, address="410012, Саратовская обл, г Саратов", inn="6454038461"
        )

        assert region.code == "64"
        assert "адрес" in (explanation or "").lower()

    def test_resolve_warns_when_address_and_inn_disagree(self, db_session):
        """Филиал в одном регионе, головная организация в другом — обычная ситуация. Берём
        адрес, но расхождение проговариваем, иначе оно молча уедет в фильтры."""

        region, explanation = region_resolver.resolve_region(
            db_session, address="350000, Краснодарский край, г Краснодар", inn="7707083893"
        )

        assert region.code == "23"
        assert "ИНН" in (explanation or "")

    def test_resolve_falls_back_to_inn_without_address(self, db_session):
        region, explanation = region_resolver.resolve_region(db_session, address=None, inn="6454038461")

        assert region.code == "64"
        assert "ИНН" in (explanation or "")

    def test_nothing_found_is_not_a_guess(self, db_session):
        """Лучше пустой регион, чем выдуманный: пустое поле видно, а неверное — нет."""

        region, explanation = region_resolver.resolve_region(
            db_session, address="улица без города", inn=None
        )

        assert region is None and explanation is None


class TestFillGaps:
    def _tender(self, db) -> Tender:
        source = Source(
            key=f"card_{uuid.uuid4().hex[:8]}",
            name="ЕИС",
            url="https://zakupki.gov.ru",
            type="eis",
        )
        db.add(source)
        db.flush()
        tender = Tender(
            source_id=source.id,
            external_id=f"3251{uuid.uuid4().hex[:7]}",
            title="Приобретение счетчиков электроэнергии",
            currency="RUB",
        )
        db.add(tender)
        db.flush()
        return tender

    @pytest.fixture()
    def payload(self):
        return {
            "sections": [
                {
                    "title": "Сведения о заказчике",
                    "fields": [
                        ["Наименование организации", "АО «ОБЛКОММУНЭНЕРГО»"],
                        ["Место нахождения", "410012, Г. САРАТОВ, УЛ МОСКОВСКАЯ, Д. 66"],
                        ["Почтовый адрес", "410012, Саратовская обл, г Саратов, ул Московская, 66"],
                        ["ИНН", "6454038461"],
                    ],
                },
                {
                    "title": "Сведения о закупке",
                    "fields": [
                        ["Способ осуществления закупки", "Закупка у единственного поставщика"],
                    ],
                },
            ],
            "tables": {},
        }

    def test_region_and_fields_are_filled(self, db_session, payload):
        tender = self._tender(db_session)

        filled = fill_gaps_from_card(db_session, tender, payload)

        assert tender.region_organizer_code == "64"
        assert tender.federal_district_code is not None
        assert tender.customer_name.startswith("АО")
        assert "единственного поставщика" in tender.procurement_method
        assert any("регион" in item for item in filled)

    def test_human_values_are_never_overwritten(self, db_session, payload):
        """Правка человека всегда важнее автоматики — тот же принцип, что в справочнике
        продукции и в анализе требований."""

        tender = self._tender(db_session)
        tender.region_organizer_code = "77"
        tender.customer_name = "Введено вручную"
        db_session.flush()

        fill_gaps_from_card(db_session, tender, payload)

        assert tender.region_organizer_code == "77"
        assert tender.customer_name == "Введено вручную"

    def test_autofill_is_recorded_in_history(self, db_session, payload):
        """Значение, подставленное системой, должно быть отличимо от введённого человеком."""

        from app.models.tender_history import TenderHistoryEntry

        tender = self._tender(db_session)
        fill_gaps_from_card(db_session, tender, payload)
        db_session.flush()

        entries = (
            db_session.query(TenderHistoryEntry)
            .filter(TenderHistoryEntry.tender_id == tender.id)
            .all()
        )
        assert entries, "автозаполнение не попало в историю тендера"
        assert any("карточки" in (entry.comment or "") for entry in entries)
        assert any("Саратовская" in (entry.new_value or "") for entry in entries)

    def test_missing_data_leaves_fields_empty(self, db_session):
        tender = self._tender(db_session)

        filled = fill_gaps_from_card(db_session, tender, {"sections": [], "tables": {}})

        assert filled == []
        assert tender.region_organizer_code is None
