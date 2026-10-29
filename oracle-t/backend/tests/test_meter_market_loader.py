"""Загрузка поверок ФГИС и стартовый справочник рынка ПУ (раздел 79 ARCHITECTURE.md).

Формат — по живым файлам 29.10.2026: CSV без строки заголовка, слепок — несжатые вложенные
архивы (предыдущий слепок + изменения по дням), аннулирование — отдельной строкой со своим
`global_id` и датой `ver_date_end`."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import text

from app.services import meter_market_loader as loader
from app.services.meter_market_import import import_file, read_rows
from app.services.meter_market_normalize import (
    manufacturer_display_name,
    manufacturer_key,
    modification_key,
    normalize_design,
    normalize_interface,
)

TABLES = (
    "meter_verifications, meter_verification_monthly, meter_market_loads, meter_market_orgs, "
    "meter_market_variants, meter_market_modifications, meter_market_types, meter_market_group_members, "
    "meter_market_groups, meter_market_manufacturers"
)


def _row(global_id, *, number="77904-20", title="Счетчики электрической энергии однофазные интеллектуальные",
         modification="НАРТИС-100.121RIL", verified="2026-08-14", applicable="1", ended="", prev="", nxt="",
         reason="", org='ФБУ "ТЕСТ"', mi_number="0001"):
    return ";".join(
        [str(global_id), f'"{org.replace(chr(34), chr(34) * 2)}"', number, title, "НАРТИС-100", modification,
         mi_number, verified, "2042-08-13", f"С-Т/{global_id}", applicable, "2026-08-15 10:00:00.1", ended,
         prev, nxt, reason]
    )


def _csv_zip(name: str, lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


@pytest.fixture()
def conn():
    connection = loader._open_connection()
    try:
        yield connection
    finally:
        connection.rollback()
        connection.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        connection.commit()
        loader._close_connection(connection)


def _count(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()


# ── Нормализация ───────────────────────────────────────────────────────────────────────────


def test_modification_key_folds_homoglyphs_zero_spaces_and_punctuation():
    # Латинские H/A/P/T/C вперемешку с кириллицей, «0» вместо «О», табуляция и двойной пробел.
    assert modification_key("НАРТИС-И300") == modification_key("HAPTИC-И3OO")
    assert modification_key("NP73  s\t") == modification_key("np73 S")
    assert modification_key("НАРТИС-100.121RIL") == modification_key("НАРТИС-100 121 RIL")
    assert modification_key("NP73 rs") != modification_key("NP73 rs g")


def test_interface_design_and_manufacturer_normalization():
    assert normalize_interface("LoRaWan и RS") == "LoRaWAN и RS"
    assert normalize_interface("GSM ") == "GSM"
    assert normalize_interface("NBIot и RS ") == "NB-IoT и RS"
    assert normalize_interface("RS,  ZigBee, Bluetooth, GSM ") == "RS, ZigBee, Bluetooth, GSM"
    assert normalize_design("шкафное") == normalize_design("Шкафное") == "cabinet"
    assert normalize_design("Din- рейка") == "din"
    assert normalize_design("н/д") == "nd"
    assert manufacturer_key('ООО"МИРТЕК"') == manufacturer_key('ООО "МИРТЕК"')
    assert manufacturer_display_name('ООО"Завод НАРТИС"') == "Завод НАРТИС"
    assert manufacturer_display_name('ООО "НПК "Инкотекс"') == "НПК «Инкотекс»"
    assert manufacturer_display_name('НПО "МИР"') == "НПО «МИР»"
    assert manufacturer_display_name('\tАО "Радио и Микроэлектроника"') == "Радио и Микроэлектроника"


def test_parse_date_accepts_iso_with_time_and_russian_format():
    assert loader.parse_date("2026-08-14") == date(2026, 8, 14)
    assert loader.parse_date("2026-08-14 10:00:00.12") == date(2026, 8, 14)
    assert loader.parse_date("14.08.2026") == date(2026, 8, 14)
    assert loader.parse_date("14/08/2026") is None
    assert loader.parse_date("") is None


def test_ee_title_filter():
    assert loader.is_ee_title("Счетчики ватт-часов активной энергии переменного тока статические")
    assert loader.is_ee_title("Счётчики электрической энергии статические")
    assert not loader.is_ee_title("Счетчики воды электронные")
    assert not loader.is_ee_title("Установки для поверки счетчиков электрической энергии")


# ── Цепочка пакетов ────────────────────────────────────────────────────────────────────────


MANIFEST = {
    "currentId": "20261003",
    "snapshots": [
        {"id": "20260901", "relativeUri": "poverki.snapshot.20260901.csv.zip", "size": 1, "md5sum": "a"},
        {"id": "20261001", "relativeUri": "poverki.snapshot.20261001.csv.zip", "size": 1, "md5sum": "b"},
    ],
    "deltas": [
        {"id": f"202610{d:02d}", "prevId": f"202610{d - 1:02d}" if d > 1 else "20260930",
         "relativeUri": f"poverki.delta.x--202610{d:02d}.csv.zip", "size": 1, "md5sum": "c"}
        for d in (1, 2, 3)
    ],
}


def test_plan_chain_from_scratch_takes_latest_snapshot_and_deltas():
    chain = loader.plan_chain(MANIFEST, None)
    assert [(p.kind, p.id) for p in chain] == [("snapshot", "20261001"), ("delta", "20261002"), ("delta", "20261003")]


def test_plan_chain_from_state_and_when_state_is_too_old():
    assert [p.id for p in loader.plan_chain(MANIFEST, "20261002")] == ["20261003"]
    assert loader.plan_chain(MANIFEST, "20261003") == []
    # Изменений от нашего состояния в манифесте уже нет — заново от слепка.
    assert loader.plan_chain(MANIFEST, "20260515")[0].kind == "snapshot"


# ── Применение пакетов ─────────────────────────────────────────────────────────────────────


def test_nested_snapshot_is_read_base_first_then_deltas(tmp_path: Path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("poverki.snapshot.20220901.csv.zip", _csv_zip("poverki.snapshot.20220901.csv", [_row(1)]))
    outer_path = tmp_path / "poverki.snapshot.20221001.csv.zip"
    with zipfile.ZipFile(outer_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("usr/exporter/public/poverki.snapshot.20220901.csv.zip", inner.getvalue())
        zf.writestr("usr/exporter/public/poverki.delta.20220901--20220902.csv.zip",
                    _csv_zip("poverki.delta.20220901--20220902.csv", [_row(2)]))
    names = [name for name, _ in loader.iter_csv_members(outer_path)]
    assert names == ["poverki.snapshot.20220901.csv", "poverki.delta.20220901--20220902.csv"]


def test_delta_applies_annulment_and_rebuilds_affected_months(conn, tmp_path: Path):
    first = tmp_path / "poverki.delta.20261001--20261002.csv.zip"
    first.write_bytes(_csv_zip("d1.csv", [
        _row(10), _row(11, verified="2026-07-02"), _row(12, applicable="0"),
        _row(13, title="Счетчики воды электронные", number="11111-11"),
        _row(14, verified="2019-05-05"),
        _row(15, modification="HAPTИC-100.121RIL"),  # другое написание того же исполнения
    ]))
    loader.apply_package_file(conn, loader._package_from_name(first), first)
    assert _count(conn, "SELECT count(*) FROM meter_verifications") == 4  # вода и 2019 год — мимо
    assert _count(conn, "SELECT sum(count) FROM meter_verification_monthly") == 3  # непригодная — мимо
    assert _count(conn, "SELECT count(*) FROM meter_market_types WHERE review_status = 'new'") == 1

    # Поверку 11 аннулировали с заменой на 16 (ошибка в дате — уехала в сентябрь).
    second = tmp_path / "poverki.delta.20261002--20261003.csv.zip"
    second.write_bytes(_csv_zip("d2.csv", [
        _row(16, verified="2026-09-01", prev="11"),
        _row(11, verified="2026-07-02", ended="2026-10-02 09:00:00.5", nxt="16", reason="ОШИБКА В ДАТЕ"),
    ]))
    loader.apply_package_file(conn, loader._package_from_name(second), second)
    assert _count(conn, "SELECT annulled FROM meter_verifications WHERE global_id = 11") is True
    monthly = dict(conn.execute(text("SELECT month, sum(count) FROM meter_verification_monthly GROUP BY 1")).all())
    assert monthly == {date(2026, 8, 1): 2, date(2026, 9, 1): 1}
    assert loader.current_state(conn) == "20261003"

    # Повторное применение того же пакета ничего не удваивает.
    loader.apply_package_file(conn, loader._package_from_name(second), second)
    assert _count(conn, "SELECT sum(count) FROM meter_verification_monthly") == 3


def test_new_spelling_maps_to_existing_modification_automatically(conn, tmp_path: Path):
    path = tmp_path / "poverki.delta.20261001--20261002.csv.zip"
    path.write_bytes(_csv_zip("d.csv", [_row(1), _row(2, modification="HAPTИC-100.121RIL"), _row(3, modification="НАРТИС-100.999")]))
    conn.execute(text("INSERT INTO meter_market_types (mit_number, in_report, review_status) VALUES ('77904-20', true, 'included')"))
    conn.execute(text(
        "INSERT INTO meter_market_modifications (type_id, name, design) "
        "SELECT id, 'НАРТИС-100.121RIL', 'din' FROM meter_market_types"
    ))
    conn.commit()
    loader.apply_package_file(conn, loader._package_from_name(path), path)
    mapped = dict(conn.execute(text("SELECT raw, mapped_by FROM meter_market_variants")).all())
    assert mapped == {"НАРТИС-100.121RIL": "auto", "HAPTИC-100.121RIL": "auto", "НАРТИС-100.999": None}
    # Наименование типа из справочника аналитика дописано по первой поверке.
    assert _count(conn, "SELECT title FROM meter_market_types") == "Счетчики электрической энергии однофазные интеллектуальные"


def test_resume_skips_members_before_checkpoint(conn, tmp_path: Path):
    path = tmp_path / "poverki.snapshot.20221001.csv.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.csv.zip", _csv_zip("a.csv", [_row(1)]))
        zf.writestr("b.csv.zip", _csv_zip("b.csv", [_row(2)]))
    conn.execute(text(
        "INSERT INTO meter_market_loads (package_id, kind, file_name, status, checkpoint) "
        "VALUES ('20221001', 'snapshot', 'x', 'failed', 'a.csv')"
    ))
    conn.commit()
    loader.apply_package_file(conn, loader._package_from_name(path), path)
    assert [r[0] for r in conn.execute(text("SELECT global_id FROM meter_verifications"))] == [2]


# ── Импорт справочника аналитика ───────────────────────────────────────────────────────────


HEADER = ["Производитель", "Регистрационный номер типа СИ", "Срок свидетельства", "Тип ПУ", "Исполнение", "Период",
          "Количество", "Интерфейс", "Межповерочный интервал", "Наименование типа СИ", "Модификация СИ",
          "Дополнительно", "В Реестре российской промышленной продукции", "Соответствие требованиям года",
          "Кол-во баллов"]


def _xlsx(rows: list[list]) -> io.BytesIO:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Лист1"
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def test_import_builds_types_variants_groups_and_keeps_dirty_values_sane(db_session):
    from app.models.meter_market import MeterMarketGroup, MeterMarketModification, MeterMarketType, MeterMarketVariant

    rows = [
        ['ООО"Завод НАРТИС"', "77904-20", "09.12.2026", "1ф", "Din- рейка", None, 120, "GSM ", 16, "НАРТИС-100",
         "НАРТИС-100.121RIL\t", None, " да", 2025, 114],
        ['ООО "Завод НАРТИС"', "77904-20", None, "1ф", "Din- рейка", None, None, "GSM", 16, "НАРТИС-100",
         "НАРТИС-100.121RIL\t", None, "да", 2025, 114],  # дубль без количества
        ['ООО"МИРТЕК"', "61891-15", None, "3ф", "шкафное", None, 10, "RS", "н/д", 307, "МИРТЕК-32", "комм.", "нет",
         "-", "-"],
        ['ООО "Умный учет"', "61891-15", None, "3ф", "Шкафное", None, 3, "RS", 16, 307, "МИРТЕК-32 У", None, "нет",
         "-", "-"],
        ['ООО"МИРТЕК"', "61891-15", None, "3ф", "Шкафное", None, 4, "RS", 16, 307, "МИРТЕК-32-1", None, "нет", "-", "-"],
    ]
    parsed = read_rows(_xlsx(rows))
    assert len(parsed) == 5 and parsed[0].in_rpp and parsed[0].interface == "GSM" and parsed[2].mpi is None
    stats = import_file(db_session, _xlsx(rows))
    assert (stats.types, stats.variants, stats.quantity) == (2, 4, 137)

    mirtek = db_session.query(MeterMarketType).filter_by(mit_number="61891-15").one()
    assert mirtek.in_report and mirtek.review_status == "included"
    smart = db_session.query(MeterMarketModification).filter_by(name="МИРТЕК-32 У").one()
    assert smart.manufacturer_override_id is not None and smart.design == "cabinet"
    nartis = db_session.query(MeterMarketModification).filter_by(name="НАРТИС-100.121RIL").one()
    assert (nartis.in_rpp, nartis.score, nartis.localization_source) == (True, 114, "import")
    assert db_session.query(MeterMarketVariant).filter_by(raw="НАРТИС-100.121RIL\t").one().mapped_by == "import"
    assert {g.name for g in db_session.query(MeterMarketGroup)} >= {"МИРТЕК", "Завод НАРТИС"}

    # Повторный импорт не плодит записи.
    again = import_file(db_session, _xlsx(rows))
    assert (again.types, again.variants, again.modifications) == (0, 0, 0)


def test_analyst_names_are_search_patterns_not_raw_spellings():
    """Сверка 29.10.2026: названия модификаций в файле аналитика — шаблоны её `ILIKE`, а не
    написания из поверок. Из 1 013 строк буквально в ФГИС нашлись 384."""

    from app.services.meter_market_normalize import EMPTY_MODIFICATION, ModificationMatcher, modification_key

    mods = [
        (1, 10, "CE207 R7 oa."),
        (2, 10, "CE207 R7 GS04 oag"),
        (3, 10, "CE207 R7 GS01"),
        (4, 20, "НЕBA СП111 G3/R4"),
        (5, 20, "НЕBA СП111 G4/R4"),
        (6, 30, "i-prom.3-1/2-M-RG-Y-Y"),
        (7, 40, EMPTY_MODIFICATION),
        (8, 50, "МИР С-05 G2Z1B e / G5Z1B"),
        (9, 60, "NP73 rs"),
        (10, 60, "NP73 rs g"),
    ]
    matcher = ModificationMatcher(mods, {30: "i-prom.3", 20: "НЕВА СП111"})
    key = modification_key
    # Подстрока и любой порядок слов: OAG стоит раньше GS04.
    assert matcher.match(10, key("CE207 R7.749.2.OAG.QUVLF GS04 SPDs*")) == 2
    assert matcher.match(10, key("CE207 R7.849.2.OA.QUVLF SPDs")) == 1
    # «G3/R4» без пробела — часть кода, а не «G3 или R4».
    assert matcher.match(20, key("НЕВА СП111 2AR2S 29 BCS G3/R4")) == 4
    assert matcher.match(20, key("НЕВА СП111 2AR2S 29 BCS G4/R4")) == 5
    # Поверитель опустил начало обозначения типа.
    assert matcher.match(30, key("3-1/2-M-RG-Y-Y")) == 6
    # Пустая модификация — все поверки типа.
    assert matcher.match(40, key("Счетчик однофазный компактный M2M-1С")) == 7
    # Косая с пробелами — «или»: вторая альтернатива заменяет похожее слово.
    assert matcher.match(50, key("МИР С-05.10-230-5(80)-G5Z1B-KNQ-E-D")) == 8
    # Самый специфичный шаблон побеждает, равные — неоднозначность.
    assert matcher.match(60, key("NP73 rs g")) == 10
    assert matcher.match(60, key("NP73 ZZ")) is None
