"""Тесты привязки моделей приборов к утверждённым типам СИ.

Данные фикстур — **фактические** обозначения и номера ГРСИ МИРТЕК из Госреестра и реальные
артикулы с `mirtekgroup.com`, а не выдуманные: главный проверяемый здесь случай именно на
них и ловится — у таганрогского и владивостокского исполнений одно название модели
(«МИРТЕК-12-РУ-D17»), но **разные типы СИ** (61891-15 и 67662-17), и различить их можно
только по артикулу.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.manufacturer import (
    Manufacturer,
    Product,
    ReviewStatus,
    SiType,
    SiTypeSource,
)
from app.services import si_type_linking
from app.services.si_type_linking import designation_slug, pick_si_type_for_product


def _si_type(si_code: str, notation: str, type_name: str) -> SiType:
    return SiType(
        manufacturer_id=uuid.uuid4(),
        si_code=si_code,
        notation=notation,
        type_name=type_name,
        source=SiTypeSource.AUTO_SEARCH.value,
    )


def _product(model_name: str, article: str | None) -> Product:
    return Product(manufacturer_id=uuid.uuid4(), model_name=model_name, article=article)


# Реальные записи Госреестра по МИРТЕК.
TAGANROG_1PH = _si_type("61891-15", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные многофункциональные")
VLADIVOSTOK_1PH = _si_type("67662-17", "МИРТЕК-212-РУ", "Счетчики электрической энергии однофазные многофункциональные")
TAGANROG_3PH = _si_type("65634-16", "МИРТЕК-32-РУ", "Счетчики электрической энергии трехфазные многофункциональные")
VLADIVOSTOK_3PH = _si_type("67661-17", "МИРТЕК-232-РУ", "Счетчики электрической энергии трехфазные многофункциональные")
OLD_1PH = _si_type("53474-13", "МИРТЕК-1-РУ", "Счетчики электрической энергии однофазные многофункциональные")
HEAT_METER = _si_type("64908-16", "МИРТЕК-42-РУ", "Теплосчетчики")
WATER_METER = _si_type("78389-20", "МИРТЕК-71-РУ", "Счетчики холодной и горячей воды крыльчатые")

ALL_METERS = [TAGANROG_1PH, VLADIVOSTOK_1PH, TAGANROG_3PH, VLADIVOSTOK_3PH, OLD_1PH]


class TestDesignationSlug:
    def test_cyrillic_designation_matches_latin_article(self):
        """Артикул — латинский slug сайта, обозначение — кириллица из реестра. Без
        транслитерации совпадений нет вообще ни одного."""

        assert designation_slug("МИРТЕК-212-РУ") == "mirtek212ru"
        assert designation_slug("mirtek-212-ru-d17") == "mirtek212rud17"
        assert designation_slug("mirtek-212-ru-d17").startswith(designation_slug("МИРТЕК-212-РУ"))

    def test_quotes_and_dashes_are_ignored(self):
        """В реестре обозначение приходит в разных кавычках и с разными дефисами."""

        assert designation_slug('"МИРТЕК-12-РУ"') == designation_slug("«МИРТЕК 12 РУ»") == "mirtek12ru"

    def test_empty_input(self):
        assert designation_slug(None) == ""
        assert designation_slug("") == ""


class TestPickSiType:
    def test_factory_executions_get_different_si_types(self):
        """**Главный случай.** Название модели у обоих исполнений одинаковое, тип СИ —
        разный. Связывание по названию приписало бы обоим таганрогский тип и подставило бы
        в тендер характеристики не того прибора."""

        taganrog = _product("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17")
        vladivostok = _product("МИРТЕК-12-РУ-D17", "mirtek-212-ru-d17")

        assert taganrog.model_name == vladivostok.model_name  # различие только в артикуле
        assert pick_si_type_for_product(taganrog, ALL_METERS).si_type is TAGANROG_1PH
        assert pick_si_type_for_product(vladivostok, ALL_METERS).si_type is VLADIVOSTOK_1PH

    def test_three_phase_executions(self):
        assert pick_si_type_for_product(_product("МИРТЕК-32-РУ-D37", "mirtek-32-ru-d37"), ALL_METERS).si_type is TAGANROG_3PH
        assert pick_si_type_for_product(_product("МИРТЕК-32-РУ-D37", "mirtek-232-ru-d37"), ALL_METERS).si_type is VLADIVOSTOK_3PH

    def test_shorter_designation_is_not_a_prefix(self):
        """«МИРТЕК-1-РУ» кандидатом даже не становится: `mirtek1ru` не является префиксом
        `mirtek12ruw9` (после «mirtek1» идёт «2», а не «r»). Это важно проверить явно —
        именно на таких парах ошибается сравнение «по вхождению подстроки» вместо префикса."""

        decision = pick_si_type_for_product(_product("МИРТЕК-12-РУ-W9", "mirtek-12-ru-w9"), ALL_METERS)

        assert decision.si_type is TAGANROG_1PH
        assert OLD_1PH not in decision.candidates

    def test_longest_prefix_wins(self):
        """Когда одному артикулу соответствуют вложенные обозначения, побеждает самое
        специфичное: иначе семейный тип «МИРТЕК-12» перехватывал бы модели, у которых есть
        собственный, более точный тип «МИРТЕК-12-РУ»."""

        family = _si_type("50000-12", "МИРТЕК-12", "Счетчики электрической энергии однофазные")
        decision = pick_si_type_for_product(
            _product("МИРТЕК-12-РУ-W9", "mirtek-12-ru-w9"), [family, TAGANROG_1PH]
        )

        assert decision.si_type is TAGANROG_1PH
        assert family in decision.candidates  # рассматривался, но проиграл по длине

    def test_similar_designations_do_not_cross_match(self):
        """«МИРТЕК-232-РУ» не должен подходить к артикулу «mirtek-32-ru-*» и наоборот —
        иначе владивостокский и таганрогский типы менялись бы местами."""

        decision = pick_si_type_for_product(_product("МИРТЕК-32-РУ-W32", "mirtek-32-ru-w32"), ALL_METERS)
        assert decision.si_type is TAGANROG_3PH
        assert VLADIVOSTOK_3PH not in decision.candidates

    def test_article_wins_over_model_name(self):
        """Артикул — единственный источник, когда он есть: у владивостокской записи название
        модели указывает на таганрогский тип, и полагаться на него нельзя."""

        vladivostok = _product("МИРТЕК-12-РУ-D17", "mirtek-212-ru-d17")
        assert pick_si_type_for_product(vladivostok, ALL_METERS).si_type is VLADIVOSTOK_1PH

    def test_model_name_used_only_without_article(self):
        """Записи, заведённые руками или импортом из CSV, артикула не имеют — для них
        название модели остаётся единственным ключом."""

        manual = _product("МИРТЕК-32-РУ-SP31", None)
        assert pick_si_type_for_product(manual, ALL_METERS).si_type is TAGANROG_3PH

    def test_no_matching_type_is_not_an_error(self):
        """Штатный случай: код СИ для модели ещё не найден автопоиском."""

        decision = pick_si_type_for_product(_product("НЕИЗВЕСТНЫЙ-1", "unknown-1"), ALL_METERS)

        assert decision.si_type is None
        assert decision.needs_review is False

    def test_same_designation_under_two_numbers_takes_the_fresher(self):
        """Одно обозначение под несколькими номерами ГРСИ — обычное дело: тип
        переутверждают, старая запись остаётся в реестре (у Энергомеры так живут
        85594-22 и 85594-26 с обозначением «CE318BY»). Это разрешимо без человека —
        берётся запись свежего года утверждения."""

        newer = _si_type("61891-24", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные")
        decision = pick_si_type_for_product(
            _product("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17"), [TAGANROG_1PH, newer]
        )

        assert decision.si_type is newer  # 61891-24 свежее, чем 61891-15

    def test_truly_indistinguishable_candidates_need_review(self):
        """А вот когда номера различить нечем, выбирать за человека нельзя."""

        twin = _si_type("61891-15", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные")
        decision = pick_si_type_for_product(
            _product("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17"), [TAGANROG_1PH, twin]
        )

        assert decision.si_type is None
        assert decision.needs_review is True
        assert "61891-15" in decision.reason


class TestLinkingInDatabase:
    @pytest.fixture()
    def manufacturer(self, db_session) -> Manufacturer:
        item = Manufacturer(legal_name=f'ООО «Тест {uuid.uuid4().hex[:8]}»', brand_name="Тест")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    def _seed(self, db_session, manufacturer, *, si_specs, product_specs):
        # Номер вида «номер-год», как в настоящем Госреестре: код различает записи номерной
        # частью, а год остаётся общим. Случайный hex-суффикс здесь был ошибкой — он иногда
        # оказывался целиком цифровым, разбирался как год утверждения, и тест на
        # неоднозначность то падал, то проходил.
        si_types = []
        for index, (si_code, notation, type_name) in enumerate(si_specs):
            si_type = SiType(
                manufacturer_id=manufacturer.id,
                si_code=f"{si_code}{index}-15",
                notation=notation,
                type_name=type_name,
                source=SiTypeSource.AUTO_SEARCH.value,
            )
            db_session.add(si_type)
            si_types.append(si_type)
        products = []
        for model_name, article in product_specs:
            product = Product(
                manufacturer_id=manufacturer.id, model_name=model_name, article=article
            )
            db_session.add(product)
            products.append(product)
        db_session.commit()
        return si_types, products

    def test_links_both_executions_to_their_own_types(self, db_session, manufacturer):
        si_types, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[
                ("61891", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные"),
                ("67662", "МИРТЕК-212-РУ", "Счетчики электрической энергии однофазные"),
            ],
            product_specs=[
                ("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17"),
                ("МИРТЕК-12-РУ-D17", "mirtek-212-ru-d17"),
            ],
        )

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.linked == 2
        db_session.refresh(products[0])
        db_session.refresh(products[1])
        assert products[0].si_type_id == si_types[0].id
        assert products[1].si_type_id == si_types[1].id

    def test_non_electricity_types_are_excluded(self, db_session, manufacturer):
        """Теплосчётчик «МИРТЕК-42-РУ» — законная запись производителя, но привязываться
        к моделям каталога он не должен."""

        _, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[("64908", "МИРТЕК-42-РУ", "Теплосчетчики")],
            product_specs=[("МИРТЕК-42-РУ-X", "mirtek-42-ru-x")],
        )

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.linked == 0
        db_session.refresh(products[0])
        assert products[0].si_type_id is None

    def test_existing_link_is_not_overwritten(self, db_session, manufacturer):
        """Раздел 5.3 ТЗ: ручной ввод в приоритете — уже проставленный код СИ автоматика
        не трогает."""

        si_types, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[
                ("61891", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные"),
                ("99999", "МИРТЕК-99-РУ", "Счетчики электрической энергии однофазные"),
            ],
            product_specs=[("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17")],
        )
        products[0].si_type_id = si_types[1].id  # человек привязал «неправильный» тип
        db_session.commit()

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.linked == 0
        assert outcome.already_linked == 1
        db_session.refresh(products[0])
        assert products[0].si_type_id == si_types[1].id

    def test_relink_recomputes_existing_links(self, db_session, manufacturer):
        si_types, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[
                ("61891", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные"),
                ("99999", "МИРТЕК-99-РУ", "Счетчики электрической энергии однофазные"),
            ],
            product_specs=[("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17")],
        )
        products[0].si_type_id = si_types[1].id
        db_session.commit()

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer, relink=True)

        assert outcome.linked == 1
        db_session.refresh(products[0])
        assert products[0].si_type_id == si_types[0].id

    def test_repeat_run_is_idempotent(self, db_session, manufacturer):
        _, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[("61891", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные")],
            product_specs=[("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17")],
        )

        first = si_type_linking.link_products_to_si_types(db_session, manufacturer)
        second = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert first.linked == 1
        assert second.linked == 0 and second.already_linked == 1

    def test_ambiguity_flags_the_product(self, db_session, manufacturer):
        """Кандидаты, которые нечем упорядочить, — на проверку.

        Номера здесь не разбираются как «номер-год» (в базе такие встречаются: ручной ввод,
        импорт, записи вроде «65515-16-ebab»), поэтому правило «берём свежую редакцию»
        не применимо и решать должен человек."""

        _, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[
                ("РУЧНОЙ-A", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные"),
                ("РУЧНОЙ-B", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные"),
            ],
            product_specs=[("МИРТЕК-12-РУ-D17", "mirtek-12-ru-D17")],
        )

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.needs_review == 1
        db_session.refresh(products[0])
        assert products[0].si_type_id is None
        assert products[0].review_status == ReviewStatus.NEEDS_REVIEW.value
        assert si_type_linking.UNLINKED_REASON_PREFIX in (products[0].review_reason or "")

    def test_missing_type_does_not_flag_the_product(self, db_session, manufacturer):
        """Отсутствие подходящего типа СИ — не повод для пометки: у половины каталога код СИ
        может быть ещё не найден, и пометка на каждой записи стала бы фоном."""

        _, products = self._seed(
            db_session,
            manufacturer,
            si_specs=[("61891", "МИРТЕК-12-РУ", "Счетчики электрической энергии однофазные")],
            product_specs=[("ЧУЖАЯ-МОДЕЛЬ-1", "chuzhaya-1")],
        )

        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.not_found == 1
        db_session.refresh(products[0])
        assert products[0].review_status == ReviewStatus.OK.value

    def test_no_si_types_at_all(self, db_session, manufacturer):
        self._seed(
            db_session, manufacturer, si_specs=[], product_specs=[("МОДЕЛЬ", "model-1")]
        )
        outcome = si_type_linking.link_products_to_si_types(db_session, manufacturer)

        assert outcome.linked == 0
        assert "автопоиск" in outcome.details[0]


class TestAutomaticLinking:
    """Код СИ должен подставляться сам, без отдельной кнопки: автопоиск ради того и
    запускают, чтобы у моделей появился код, а не чтобы получить второй несвязанный список."""

    def _manufacturer(self, db_session) -> Manufacturer:
        item = Manufacturer(legal_name=f'ООО «Тест {uuid.uuid4().hex[:8]}»', brand_name="Тест")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    def test_fgis_search_links_existing_products(self, db_session, admin_user, monkeypatch):
        """Модели уже заведены (например, обходом каталога), кодов СИ ещё нет. После
        автопоиска модели должны получить код без дополнительных действий человека."""

        from app.adapters.fgis import SiSearchResult
        from app.services import product_catalog_service

        manufacturer = self._manufacturer(db_session)
        taganrog = Product(
            manufacturer_id=manufacturer.id,
            model_name="ТЕСТ-12-РУ-D17",
            article="test-12-ru-d17",
        )
        vladivostok = Product(
            manufacturer_id=manufacturer.id,
            model_name="ТЕСТ-12-РУ-D17",
            article="test-212-ru-d17",
        )
        db_session.add_all([taganrog, vladivostok])
        db_session.commit()

        code_1 = f"61891-{uuid.uuid4().hex[:3]}"
        code_2 = f"67662-{uuid.uuid4().hex[:3]}"

        class _Stub:
            def search_by_manufacturer(self, legal_name, *, brand_name=None, fetch_cards=True):
                return [
                    SiSearchResult(
                        si_code=code_1,
                        type_name="Счетчики электрической энергии однофазные многофункциональные",
                        notation="ТЕСТ-12-РУ",
                        manufacturer_name=legal_name,
                    ),
                    SiSearchResult(
                        si_code=code_2,
                        type_name="Счетчики электрической энергии однофазные многофункциональные",
                        notation="ТЕСТ-212-РУ",
                        manufacturer_name=legal_name,
                    ),
                ]

        monkeypatch.setattr(product_catalog_service, "FgisAdapter", lambda: _Stub())
        product_catalog_service.search_si_types(db_session, manufacturer, actor=admin_user)

        db_session.refresh(taganrog)
        db_session.refresh(vladivostok)
        by_id = {
            s.id: s
            for s in db_session.scalars(
                select(SiType).where(SiType.manufacturer_id == manufacturer.id)
            )
        }
        # Каждое исполнение получило СВОЙ тип — тот же случай, что и у МИРТЕК.
        assert by_id[taganrog.si_type_id].si_code == code_1
        assert by_id[vladivostok.si_type_id].si_code == code_2

    def test_new_product_gets_si_type_on_creation(self, db_session, admin_user):
        """Модель, добавленная руками после автопоиска, тоже должна получить код сразу —
        иначе человеку пришлось бы помнить про отдельную кнопку после каждого добавления."""

        from app.services import product_catalog_service

        manufacturer = self._manufacturer(db_session)
        si_type = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"61891-{uuid.uuid4().hex[:3]}",
            notation="ТЕСТ-12-РУ",
            type_name="Счетчики электрической энергии однофазные многофункциональные",
            source=SiTypeSource.AUTO_SEARCH.value,
        )
        db_session.add(si_type)
        db_session.commit()

        product = product_catalog_service.create_product(
            db_session,
            manufacturer,
            model_name="ТЕСТ-12-РУ-W9",
            si_type_id=None,
            article="test-12-ru-w9",
            device_type=None,
            actor=admin_user,
        )

        assert product.si_type_id == si_type.id

    def test_explicit_si_type_wins_over_autolink(self, db_session, admin_user):
        """Явно переданный код СИ — это решение человека, автоподстановка его не трогает."""

        from app.services import product_catalog_service

        manufacturer = self._manufacturer(db_session)
        auto = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"61891-{uuid.uuid4().hex[:3]}",
            notation="ТЕСТ-12-РУ",
            type_name="Счетчики электрической энергии однофазные",
            source=SiTypeSource.AUTO_SEARCH.value,
        )
        chosen = SiType(
            manufacturer_id=manufacturer.id,
            si_code=f"99999-{uuid.uuid4().hex[:3]}",
            notation="ТЕСТ-99-РУ",
            type_name="Счетчики электрической энергии однофазные",
            source=SiTypeSource.MANUAL.value,
        )
        db_session.add_all([auto, chosen])
        db_session.commit()

        product = product_catalog_service.create_product(
            db_session,
            manufacturer,
            model_name="ТЕСТ-12-РУ-W9",
            si_type_id=chosen.id,
            article="test-12-ru-w9",
            device_type=None,
            actor=admin_user,
        )

        assert product.si_type_id == chosen.id
