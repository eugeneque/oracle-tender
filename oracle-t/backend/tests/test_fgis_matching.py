"""Тесты дисамбигуации кандидатов реестра ФГИС (п.1.2 задания).

Главный кейс — **«Пульсар»**, воспроизведённая коллизия наименований: под этой торговой
маркой в Госреестре лежат приборы разных видов измерений от разных юрлиц, и поиск по бренду
возвращает их вперемешку. Ровно на этом система раньше подкладывала в справочник «Описание
типа» пожарного извещателя вместо счётчика электроэнергии.

Фикстуры воспроизводят структуру записи реестра (`title`/`notation`/`manufacturers`) и её
неудобную особенность: наименование типа — единственное поле, по которому вообще можно
понять, что за прибор перед нами.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.fgis_matching import (
    DeviceKind,
    classify_si_type,
    foreign_kind_label,
    pick_candidate,
)


@dataclass
class _Candidate:
    """Минимальный кандидат — тот же набор полей, что у `SiSearchResult`."""

    si_code: str
    type_name: str | None = None
    notation: str | None = None
    manufacturer_name: str | None = None
    matched_by: str = "legal"


# --- Реальные формулировки Госреестра по марке «Пульсар» ---

PULSAR_METER = _Candidate(
    si_code="72033-18",
    type_name="Счетчики электрической энергии статические однофазные многотарифные",
    notation="Пульсар",
    manufacturer_name='Общество с ограниченной ответственностью «НПП "ТЕПЛОВОДОХРАН"», г. Рязань',
)
PULSAR_FIRE_DETECTOR = _Candidate(
    si_code="55555-13",
    type_name="Извещатели пожарные дымовые оптико-электронные",
    notation="Пульсар",
    manufacturer_name='ООО «Пульсар», г. Москва',
    matched_by="brand",
)
PULSAR_WATER_METER = _Candidate(
    si_code="66666-17",
    type_name="Счетчики воды крыльчатые",
    notation="Пульсар",
    manufacturer_name='ООО «НПП "ТЕПЛОВОДОХРАН"»',
)
PULSAR_FLOWMETER = _Candidate(
    si_code="77777-19",
    type_name="Расходомеры-счетчики ультразвуковые",
    notation="Пульсар",
    manufacturer_name='ООО «Пульсар-Прибор»',
    matched_by="brand",
)


class TestClassify:
    def test_electricity_meter_recognised_in_registry_wording(self):
        # Реестр формулирует наименование по-разному, и все варианты равно законны.
        for title in (
            "Счетчики электрической энергии статические однофазные многотарифные",
            "Счетчики электроэнергии статические трехфазные",
            "Счётчики электрической энергии однофазные многофункциональные",
            "Счетчики активной и реактивной электрической энергии трехфазные",
        ):
            assert classify_si_type(title) is DeviceKind.ELECTRICITY_METER, title

    def test_foreign_devices_are_not_electricity_meters(self):
        for title in (
            "Извещатели пожарные дымовые оптико-электронные",
            "Счетчики воды крыльчатые",
            "Счетчики газа объемные диафрагменные",
            "Расходомеры-счетчики ультразвуковые",
            "Трансформаторы напряжения",
        ):
            assert classify_si_type(title) is not DeviceKind.ELECTRICITY_METER, title

    def test_unknown_wording_is_not_classified(self):
        # «Не опознан» — не то же самое, что «чужой прибор»: такой кандидат уходит человеку.
        assert classify_si_type("Приборы учёта ресурсов серии Х") is None
        assert classify_si_type(None) is None
        assert classify_si_type("") is None

    def test_foreign_kind_label_names_the_actual_device(self):
        assert foreign_kind_label("Извещатели пожарные дымовые") == "извещатель пожарный"
        assert foreign_kind_label("Счетчики воды крыльчатые") == "счётчик воды"
        # У законного кандидата чужого вида нет — иначе фильтр отбрасывал бы своих.
        assert foreign_kind_label(PULSAR_METER.type_name) is None


class TestPulsarCollision:
    """Кейс «Пульсар» целиком: несколько производителей и приборов с одинаковым названием."""

    def test_electricity_meter_wins_over_fire_detector(self):
        decision = pick_candidate(
            [PULSAR_FIRE_DETECTOR, PULSAR_METER, PULSAR_WATER_METER, PULSAR_FLOWMETER]
        )

        assert decision.accepted is PULSAR_METER
        assert decision.needs_review is False
        # Отвергнутые не теряются: человек при проверке должен видеть, что именно отсеяно
        # и почему, — иначе фильтр выглядит как чёрный ящик.
        rejected_codes = {item.si_code for item in decision.rejected}
        assert rejected_codes == {
            PULSAR_FIRE_DETECTOR.si_code,
            PULSAR_WATER_METER.si_code,
            PULSAR_FLOWMETER.si_code,
        }
        assert any("извещатель пожарный" in item.reason for item in decision.rejected)

    def test_only_foreign_devices_means_review_not_silent_save(self):
        """Ключевая защита: если электросчётчика среди кандидатов нет вовсе, система не
        сохраняет «хоть что-нибудь», а требует проверки человеком."""

        decision = pick_candidate([PULSAR_FIRE_DETECTOR, PULSAR_WATER_METER])

        assert decision.accepted is None
        assert decision.needs_review is True
        assert "не является" in decision.reason
        assert "извещатель пожарный" in decision.describe_candidates()

    def test_two_electricity_meters_of_same_brand_need_review(self):
        """Два законных счётчика под одной маркой — выбрать не из чего, идёт человеку."""

        second_meter = _Candidate(
            si_code="72034-18",
            type_name="Счетчики электрической энергии статические трехфазные",
            notation="Пульсар",
            manufacturer_name='ООО «НПП "ТЕПЛОВОДОХРАН"»',
        )
        decision = pick_candidate([PULSAR_METER, second_meter])

        assert decision.accepted is None
        assert decision.needs_review is True
        assert "72033-18" in decision.describe_candidates()
        assert "72034-18" in decision.describe_candidates()

    def test_model_name_resolves_ambiguity_between_two_meters(self):
        """Когда известна искомая модель, обозначение типа снимает неоднозначность — иначе
        любой производитель с широкой линейкой всегда уходил бы «на проверку»."""

        single_phase = _Candidate(
            si_code="61891-15",
            type_name="Счетчики электрической энергии однофазные",
            notation="МИРТЕК-12-РУ",
            manufacturer_name='ООО «МИРТЕК»',
        )
        three_phase = _Candidate(
            si_code="61892-15",
            type_name="Счетчики электрической энергии трехфазные",
            notation="МИРТЕК-32-РУ",
            manufacturer_name='ООО «МИРТЕК»',
        )

        decision = pick_candidate([single_phase, three_phase], model_name="МИРТЕК-12-РУ-D17")
        assert decision.accepted is single_phase
        assert decision.needs_review is False

    def test_legal_match_preferred_over_brand_only_match(self):
        """Совпадение по юрлицу надёжнее совпадения по марке: марка не обязана принадлежать
        нужной компании — на этом и ловится «Пульсар»."""

        own = _Candidate(
            si_code="72033-18",
            type_name="Счетчики электрической энергии статические однофазные",
            notation="Пульсар-Э",
            manufacturer_name='ООО «НПП "ТЕПЛОВОДОХРАН"»',
            matched_by="legal",
        )
        stranger = _Candidate(
            si_code="88888-20",
            type_name="Счетчики электрической энергии статические однофазные",
            notation="Пульсар-1",
            manufacturer_name='ООО «Пульсар Энерго», г. Санкт-Петербург',
            matched_by="brand",
        )

        decision = pick_candidate([own, stranger])
        assert decision.accepted is own


class TestEdgeCases:
    def test_empty_registry_output_is_not_review(self):
        """Пустая выдача — это «типа СИ нет», штатный исход, а не неоднозначность."""

        decision = pick_candidate([])
        assert decision.accepted is None
        assert decision.needs_review is False
        assert decision.has_result is False

    def test_unrecognised_wording_goes_to_review_not_to_trash(self):
        """Наименование с непривычной формулировкой не отбрасывается молча: реестр
        формулируется свободно, и глухой отказ терял бы законные записи."""

        odd = _Candidate(
            si_code="99999-21",
            type_name="Приборы учёта электроэнергии серии Х",
            notation="X-100",
            manufacturer_name="ООО «Тест»",
        )
        decision = pick_candidate([odd])

        assert decision.accepted is None
        assert decision.needs_review is True
        assert "99999-21" in decision.describe_candidates()

    def test_single_meter_is_accepted_without_review(self):
        decision = pick_candidate([PULSAR_METER])
        assert decision.accepted is PULSAR_METER
        assert decision.needs_review is False
        assert decision.rejected == []

    def test_latin_homoglyphs_in_notation_still_match_model(self):
        """«МИРТЕК» с латинскими М, И, Р визуально не отличается от кириллического, но
        это разные кодовые точки — сравнение обязано это переживать."""

        latin_notation = _Candidate(
            si_code="61891-15",
            type_name="Счетчики электрической энергии однофазные",
            notation="MИPTEK-12-PУ",  # M, P, T, E, K — латиница
            manufacturer_name='ООО «МИРТЕК»',
        )
        other = _Candidate(
            si_code="61892-15",
            type_name="Счетчики электрической энергии трехфазные",
            notation="МИРТЕК-32-РУ",
            manufacturer_name='ООО «МИРТЕК»',
        )

        decision = pick_candidate([latin_notation, other], model_name="МИРТЕК-12-РУ-D17")
        assert decision.accepted is latin_notation
