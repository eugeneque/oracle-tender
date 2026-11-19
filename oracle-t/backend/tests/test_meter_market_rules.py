"""Правила аналитика по ПРОМЭНЕРГО, МИРТЕК и «Умному учёту» для всех периодов (раздел 114 ARCHITECTURE.md).

Замечания к 3 кв. 2026 («Комментарии. Промэнерго и Миртек.docx»): i-prom.2 не существует, «RR» —
два RS-485, «3T-P-RG-N» — это «i-prom.3 3T-P-RG-N» и внесён в РПП (120 баллов), 10 085 поверок
«Нет модификации» с буквой U — «Умный учет», газовые «ОМЕГА ЭТК» не считать, 1 084 + 960 + 170
первичных поверок МИРТЕК — «соответствует ПП 890»."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services import meter_market_loader as loader
from app.services import meter_market_rules as rules
from app.services.meter_market_normalize import modification_key

TABLES = (
    "meter_verifications, meter_verification_monthly, meter_market_loads, meter_market_orgs, "
    "meter_market_variants, meter_market_modifications, meter_market_types, meter_market_group_members, "
    "meter_market_groups, meter_market_manufacturers, meter_market_rpp_records"
)


# ── Чистые правила ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mit", "raw", "name", "interface", "score"),
    [
        ("87373-22", "3-1/2-M-RG-Y-Y", "i-prom.1 M-RG-Y", "GSM и RS", 115.0),  # не i-prom.3
        ("87373-22", "2-1/2-M-R-Y-Y", "i-prom.1 M-R-Y", "RS", 115.0),  # i-prom.2 не существует
        ("87373-22", "1-1/2-S-RL-Y-Y", "i-prom.1 S-RL-Y", "LoRaWAN и RS", 115.0),
        ("87373-22", "i-prom.1-3-1/M-R-Y-Y", "i-prom.1 M-R-Y", "RS", 115.0),
        ("87373-22", "3-1/2-М-RGF-Y-Y", "i-prom.1 M-RGF-Y", "RS, RF и GSM", 115.0),  # кириллическая М
        ("87388-22", "3-1-1/2-P-RR-Y-N", "i-prom.3 P-RR-Y", "RS", 115.0),  # RR = 2×RS-485
        ("87388-22", "3-1-1/2-Р-RG-Y-N", "i-prom.3 P-RG-Y", "GSM и RS", 115.0),
        ("87388-22", "3T-2-0,5S/1-P-RGF-N-N", "i-prom.3T P-RGF", "RS, RF и GSM", 120.0),
        ("95540-25", "3T-2-2-0.5S/1-P-RG-N", "i-prom.3 3T-P-RG-N", "GSM и RS", 120.0),
    ],
)
def test_iprom_decoding(mit, raw, name, interface, score):
    spec = rules.decode_iprom(mit, raw)
    assert spec is not None
    assert spec.name == name
    assert spec.interface == interface
    assert spec.score == score


@pytest.mark.parametrize(
    ("mit", "raw"),
    [
        ("87388-22", "1-1/2-P-R-Y-N"),  # ведущая «1» у трёхфазного типа — не угадываем
        ("87373-22", "3"),
        ("87373-22", "i-prom.1"),
        ("61891-15", "3-1/2-M-RG-Y-Y"),  # чужой тип
        ("95540-25", "3-1-1/2-P-RG-Y-N"),
    ],
)
def test_iprom_decoding_refuses_to_guess(mit, raw):
    assert rules.decode_iprom(mit, raw) is None


def test_gas_meters_and_serial_split():
    assert rules.gas_reason("ОМЕГА ЭТК GSM G4")
    assert rules.gas_reason("OMEGA ETK GSM G4")
    assert not rules.gas_reason("МИРТЕК-12-РУ-W9-A1R1-230-5-80A-ST-G/5-P2")
    assert rules.split_raw("61891-15", "Нет модификации", "226U167342938") == "Нет модификации" + rules.SMART_UNIT_SUFFIX
    assert rules.split_raw("61891-15", "Нет модификации", "007791") is None
    assert rules.split_raw("61891-15", "МИРТЕК-12-РУ D1", "226U167342938") is None
    assert rules.split_raw("67661-17", "Нет модификации", "226U167342938") is None


# ── Справочник и поверки ───────────────────────────────────────────────────────────────────


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


def _scalar(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


def _seed(conn):
    mir = _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('МИРТЕК') RETURNING id")
    _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('Умный учет') RETURNING id")
    prom = _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('ПРОМЭНЕРГО') RETURNING id")
    types = {}
    for number, owner in (("61891-15", mir), ("87373-22", prom), ("87388-22", prom), ("95540-25", prom)):
        types[number] = _scalar(
            conn,
            "INSERT INTO meter_market_types (mit_number, title, manufacturer_id, in_report) "
            "VALUES (:n, 'Счетчики электрической энергии', :m, true) RETURNING id",
            n=number, m=owner,
        )
    d17 = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, in_rpp, score, rpp_included_at) "
        "VALUES (:t, 'МИРТЕК-12-РУ D17-2', '1ф', 'din', 'Bluetooth + mod', true, 114, '2025-04-09') RETURNING id",
        t=types["61891-15"],
    )
    plain = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, smart_890, smart_890_source) "
        "VALUES (:t, 'МИРТЕК-12-РУ', '1ф', 'nd', 'н/д', false, 'ai') RETURNING id",
        t=types["61891-15"],
    )
    legacy = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface) "
        "VALUES (:t, '3T-P-RG-N', '3ф', 'cabinet', 'GSM и RS') RETURNING id",
        t=types["95540-25"],
    )
    return types, d17, plain, legacy


def _variant(conn, type_id, raw, modification_id=None, mapped_by=None):
    return _scalar(
        conn,
        "INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id, mapped_by) "
        "VALUES (:t, :r, :k, :m, :b) RETURNING id",
        t=type_id, r=raw, k=modification_key(raw), m=modification_id, b=mapped_by,
    )


def _verification(conn, gid, variant, serial, day="2026-08-10"):
    conn.execute(
        text(
            "INSERT INTO meter_verifications (global_id, variant_id, mi_number, verification_date, applicable) "
            "VALUES (:g, :v, :s, :d, true)"
        ),
        {"g": gid, "v": variant, "s": serial, "d": day},
    )


def _monthly(conn):
    conn.execute(text("TRUNCATE meter_verification_monthly"))
    loader.rebuild_monthly(conn)


def test_apply_all_covers_all_periods(conn):
    types, d17, plain, legacy = _seed(conn)
    none_mod = _variant(conn, types["61891-15"], "Нет модификации", plain, "ai")
    gas = _variant(conn, types["61891-15"], "ОМЕГА ЭТК GSM G4")
    i1 = _variant(conn, types["87373-22"], "2-1/2-M-R-Y-Y", None)
    i3 = _variant(conn, types["95540-25"], "3T-2-2-0.5S/1-P-RG-N", None)
    rr = _variant(conn, types["87388-22"], "3-1-1/2-P-RR-Y-N", None)
    gid = 1
    # Разные периоды: правила не привязаны к кварталу.
    for day, serial in (("2025-03-01", "226U1"), ("2026-08-10", "226U2"), ("2026-08-11", "226U3"), ("2026-08-12", "007791")):
        _verification(conn, gid, none_mod, serial, day)
        gid += 1
    for day in ("2025-05-01", "2026-08-10"):
        _verification(conn, gid, gas, "G1", day)
        gid += 1
        _verification(conn, gid, i1, "A", day)
        gid += 1
        _verification(conn, gid, i3, "B", day)
        gid += 1
    _verification(conn, gid, rr, "C")
    _monthly(conn)

    stats = rules.apply_all(conn)
    assert stats["smart_unit_rows"] == 3 and stats["gas_variants"] == 1 and stats["iprom_created"] >= 2

    # Газ: не в агрегате, сырые поверки на месте.
    assert _scalar(conn, "SELECT count(*) FROM meter_verification_monthly WHERE variant_id = :v", v=gas) == 0
    assert _scalar(conn, "SELECT count(*) FROM meter_verifications WHERE variant_id = :v", v=gas) == 2
    # U-номера — «Умному учёту», D17-2 в РПП; остальные «Нет модификации» остались у МИРТЕК.
    unit = _scalar(conn, "SELECT id FROM meter_market_modifications WHERE name = :n", n=rules.SMART_UNIT_MOD)
    owner = _scalar(conn, "SELECT manufacturer_override_id FROM meter_market_modifications WHERE id = :i", i=unit)
    assert owner == _scalar(conn, "SELECT id FROM meter_market_manufacturers WHERE name = 'Умный учет'")
    assert _scalar(conn, "SELECT in_rpp FROM meter_market_modifications WHERE id = :i", i=unit) is True
    assert _scalar(conn, "SELECT sum(count) FROM meter_verification_monthly mv JOIN meter_market_variants v ON v.id = mv.variant_id "
                         "WHERE v.modification_id = :m", m=unit) == 3
    assert _scalar(conn, "SELECT sum(count) FROM meter_verification_monthly WHERE variant_id = :v", v=none_mod) == 1
    # МИРТЕК: ИИ-«не соответствует» перекрыто решением аналитика.
    assert _scalar(conn, "SELECT smart_890 FROM meter_market_modifications WHERE id = :i", i=plain) is True
    assert _scalar(conn, "SELECT smart_890_source FROM meter_market_modifications WHERE id = :i", i=plain) == "manual"
    # i-prom: i-prom.1 (не 2), «3T-P-RG-N» → «i-prom.3 3T-P-RG-N» в РПП на 120 баллов, RR — RS.
    assert _scalar(conn, "SELECT m.name FROM meter_market_variants v JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = :v", v=i1) == "i-prom.1 M-R-Y"
    row = conn.execute(
        text("SELECT m.id, m.name, m.in_rpp, m.score, m.smart_890 FROM meter_market_variants v "
             "JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = :v"), {"v": i3}
    ).one()
    assert row.id == legacy and row.name == "i-prom.3 3T-P-RG-N" and row.in_rpp and row.score == 120 and row.smart_890
    rr_mod = conn.execute(
        text("SELECT m.interface, m.in_rpp, m.score FROM meter_market_variants v "
             "JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = :v"), {"v": rr}
    ).one()
    assert tuple(rr_mod) == ("RS", True, 115.0)

    # Повторный запуск ничего не меняет.
    again = rules.apply_all(conn)
    assert set(again.values()) == {0}


def test_loader_applies_rules_to_new_rows(conn):
    types, d17, plain, legacy = _seed(conn)
    rules.apply_all(conn)  # заводит «Умный учет D17-2» и вариант с U
    caches = loader._load_caches(conn)
    unit_variant = loader._variant_id(conn, caches, types["61891-15"], "Нет модификации", "226U9")
    assert _scalar(conn, "SELECT raw FROM meter_market_variants WHERE id = :v", v=unit_variant).endswith(rules.SMART_UNIT_SUFFIX)
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=unit_variant) is not None
    plain_variant = loader._variant_id(conn, caches, types["61891-15"], "Нет модификации", "007791")
    assert plain_variant != unit_variant
    gas = loader._variant_id(conn, caches, types["61891-15"], "ОМЕГА ЭТК GSM G4", "G1")
    assert _scalar(conn, "SELECT excluded_reason FROM meter_market_variants WHERE id = :v", v=gas)
    # i-prom: новое написание сразу идёт в исполнение справочника, а не в «не размечено».
    i1 = loader._variant_id(conn, caches, types["87373-22"], "2-1/2-M-R-Y-Y", "X")
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=i1) is None  # исполнения ещё нет
    rules.apply_iprom(conn)
    assert _scalar(conn, "SELECT m.name FROM meter_market_variants v JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = :v", v=i1) == "i-prom.1 M-R-Y"
    # Газ не попадает и в пересчёт агрегата.
    _verification(conn, 99, gas, "G1")
    _monthly(conn)
    assert _scalar(conn, "SELECT count(*) FROM meter_verification_monthly WHERE variant_id = :v", v=gas) == 0
