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


def test_mirtek_rule_touches_only_base_rows_and_keeps_manual_decision(conn):
    """Раздел 119: «соответствует» — только у базовых строк; ручное «не соответствует» не перезаписывается."""

    types, _, plain, _ = _seed(conn)
    sp1 = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface) "
        "VALUES (:t, 'МИРТЕК-12-РУ SP1', '1ф', 'nd', 'н/д') RETURNING id",
        t=types["61891-15"],
    )
    rules.apply_mirtek_smart(conn)
    assert _scalar(conn, "SELECT smart_890 FROM meter_market_modifications WHERE id = :i", i=sp1) is None
    assert _scalar(conn, "SELECT smart_890 FROM meter_market_modifications WHERE id = :i", i=plain) is True
    # Аналитик передумала: ручное «нет» остаётся после повторного запуска правил.
    conn.execute(text("UPDATE meter_market_modifications SET smart_890 = false, smart_890_source = 'manual' WHERE id = :i"), {"i": plain})
    assert rules.apply_mirtek_smart(conn) == 0
    assert _scalar(conn, "SELECT smart_890 FROM meter_market_modifications WHERE id = :i", i=plain) is False


def test_stale_blanket_note_removed_only_from_non_base_modifications(conn):
    types, _, plain, _ = _seed(conn)
    sp3 = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, smart_890, smart_890_source, smart_890_note) "
        "VALUES (:t, 'МИРТЕК-12-РУ SP3', true, 'manual', :n) RETURNING id",
        t=types["61891-15"], n=rules.MIRTEK_PRIMARY_NOTE,
    )
    manual = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, smart_890, smart_890_source, smart_890_note) "
        "VALUES (:t, 'МИРТЕК-12-РУ W9', false, 'manual', 'решение аналитика') RETURNING id",
        t=types["61891-15"],
    )
    conn.execute(
        text("UPDATE meter_market_modifications SET smart_890 = true, smart_890_source = 'manual', smart_890_note = :n WHERE id = :i"),
        {"n": rules.MIRTEK_PRIMARY_NOTE, "i": plain},
    )

    assert rules.apply_mirtek_stale_blanket_smart(conn) == 1
    row = lambda i: tuple(conn.execute(text("SELECT smart_890, smart_890_source FROM meter_market_modifications WHERE id = :i"), {"i": i}).one())
    assert row(sp3) == (None, None)
    assert row(plain) == (True, "manual")  # базовая строка
    assert row(manual) == (False, "manual")  # настоящее решение аналитика
    assert rules.apply_mirtek_stale_blanket_smart(conn) == 0


def test_migration_0111_resets_blanket_rule_and_drops_foreign_modifications(conn, monkeypatch):
    import importlib.util
    from pathlib import Path

    from alembic import op

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0111_mirtek_smart_rollback.py"
    spec = importlib.util.spec_from_file_location("migration_0111", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    types, _, _, _ = _seed(conn)
    t = types["61891-15"]

    def add(name, smart, at="2026-11-19 08:23:56", note=migration.NOTE, source="manual"):
        return _scalar(
            conn,
            "INSERT INTO meter_market_modifications (type_id, name, smart_890, smart_890_source, smart_890_note, updated_at) "
            "VALUES (:t, :n, :s, :src, :note, :at) RETURNING id",
            t=t, n=name, s=smart, src=source, note=note, at=at,
        )

    base = add("МИРТЕК-232-РУ", True)
    blanket = add("МИРТЕК-12-РУ SP1", True)
    touched = add("МИРТЕК-12-РУ W2", False, at="2026-11-20 12:30:00")  # правка аналитика после 0109
    foreign = add("Ладога 0102", True)
    variant = _variant(conn, t, "Ладога 0102", foreign, "ai")

    monkeypatch.setattr(op, "get_bind", lambda: conn, raising=False)
    migration.upgrade()

    row = lambda i: conn.execute(text("SELECT smart_890, smart_890_source FROM meter_market_modifications WHERE id = :i"), {"i": i}).one()
    assert tuple(row(base)) == (True, "manual")
    assert tuple(row(blanket)) == (None, None)
    assert tuple(row(touched)) == (False, "manual")
    assert _scalar(conn, "SELECT count(*) FROM meter_market_modifications WHERE id = :i", i=foreign) == 0
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=variant) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Явное «нет» (скриншоты аналитика): есть блок функций, нет ни Pn, ни D.
        ("МИРТЕК-12-РУ-SP1-A1R1-230-5-60A-ST-RF433/1-RF2400/3-HKMOQ1V3", False),
        ("МИРТЕК-12-РУ-W2-A1-230-5-60A-S-RF433/1-LOQ1V3", False),
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-HKMOQ1V3", False),
        ("МИРТЕК-32-РУ-W32-A0,5R1-230-5-10A-T-RS485-HLMOQ2V3", False),
        ("МИРТЕК-32-РУ-W32-A1R1-230-10-100A-T-RS485-HKLMOQ2V3", False),
        ("W1-A1-230-5-60A-SS-RF433/1-V3", False),
        # Есть и Pn, и D — «соответствует».
        ("МИРТЕК-32-РУ-SP31-A1R1-230-5-100A-T-RF433/1-RF2400/2-P2-HKMOV3-D", True),
        ("МИРТЕК-32-РУ-D37-1-A1R1-230-5-100A-T-RS485-MOD-P1-B-D", True),
        ("МИРТЕК-12-РУ-SP3-А1R1-230-5-80А-ST-RF433/1-Р2-HKMOQ1V3-D", True),  # кириллические «А», «Р»
        # Уточнение 24.11.2026: решает Pn; его отсутствие не компенсируется D, а пропущенный D — ошибка поверителя.
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-RF2400/3-P2-HKMOQ1V3", True),
        ("МИРТЕК-12-РУ-D1-A1R1-230-5-60A-ST-RS485-P2-HKLMOQ2V3", True),
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-HKMOQ1V3-D", False),
        # Неполное обозначение — сомнительное считаем соответствующим.
        ("МИРТЕК-12-РУ -D1-A1R1-230-5-80A-ST-G", True),
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF240", True),
        ("МИРТЕК-32-РУ-SP31-A1R1-230-5-100A-T-RF-4", True),
        ("МИРТЕК-232-РУ", True),
        ("Миртек-32-РУ-W32-A15R1", True),
        ("МИРТЕК-12-РУ-SP1-AIRI-230-5-60A", True),
        ("Нет модификации", True),
        (None, None),
    ],
)
def test_protocol_verdict(raw, expected):
    assert rules.protocol_verdict(raw)[0] is expected


def test_mirtek_protocol_applied_to_variants_and_respects_manual(conn):
    types, _, plain, _ = _seed(conn)
    t = types["61891-15"]
    with_p = _variant(conn, t, "МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-P2-HKMOQ1V3-D", plain, "ai")
    without_p = _variant(conn, t, "МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-HKMOQ1V3", plain, "ai")
    short = _variant(conn, t, "МИРТЕК-232-РУ", plain, "ai")
    foreign = _variant(conn, types["87373-22"], "X-A1R1-230-5-60A-S-RF433", None)
    conn.execute(text("UPDATE meter_market_variants SET smart_890 = true, smart_890_source = 'manual' WHERE id = :v"), {"v": without_p})

    assert rules.apply_mirtek_protocol(conn) == 2  # с Pn и «МИРТЕК-232-РУ» (неполное — сомнительное = «соответствует»)
    get = lambda v: tuple(conn.execute(text("SELECT smart_890, smart_890_source FROM meter_market_variants WHERE id = :v"), {"v": v}).one())
    assert get(with_p) == (True, "rule")
    assert get(without_p) == (True, "manual")  # ручное решение не перезаписано
    assert get(short) == (True, "rule")
    assert get(foreign) == (None, None)  # не МИРТЕК
    assert rules.apply_mirtek_protocol(conn) == 0


def test_report_takes_smart_from_variant(conn):
    from app.services import meter_market_report as report

    types, _, plain, _ = _seed(conn)
    t = types["61891-15"]
    sp3 = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, smart_890, smart_890_source) "
        "VALUES (:t, 'МИРТЕК-12-РУ SP3', '1ф', 'nd', 'RF', null, null) RETURNING id", t=t,
    )
    yes = _variant(conn, t, "МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-P2-HKMOQ1V3-D", sp3, "ai")
    no = _variant(conn, t, "МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-HKMOQ1V3", sp3, "ai")
    rules.apply_mirtek_protocol(conn)
    for gid, v in ((1, yes), (2, no)):
        _verification(conn, gid, v, f"S{gid}")
    _monthly(conn)
    rows = conn.execute(
        text(f"SELECT v.id, {report.SMART_SQL} AS smart, {report.SMART_SOURCE_SQL} AS src "
             "FROM meter_market_variants v LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
             "WHERE v.id = ANY(:ids) ORDER BY v.id"), {"ids": [yes, no]},
    ).all()
    assert [(r.smart, r.src) for r in rows] == [(True, "rule"), (False, "rule")]


def test_mirtek_foreign_old_types_and_typos(conn):
    """Ответ аналитика 23.11.2026: чужие приборы и старые описания типа не учитываются, опечатки — МИРТЕК-232-РУ."""

    mir = _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('МИРТЕК') RETURNING id")
    other = _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('Инкотекс') RETURNING id")
    t232 = _scalar(conn, "INSERT INTO meter_market_types (mit_number, title, manufacturer_id, in_report) "
                         "VALUES ('67661-17', 'Счетчики', :m, true) RETURNING id", m=mir)
    t_old = _scalar(conn, "INSERT INTO meter_market_types (mit_number, title, manufacturer_id, in_report) "
                          "VALUES ('65634-16', 'Счетчики', :m, true) RETURNING id", m=mir)
    t_other = _scalar(conn, "INSERT INTO meter_market_types (mit_number, title, manufacturer_id, in_report) "
                            "VALUES ('11111-11', 'Счетчики', :m, true) RETURNING id", m=other)

    def mod(type_id, name):
        return _scalar(conn, "INSERT INTO meter_market_modifications (type_id, name) VALUES (:t, :n) RETURNING id", t=type_id, n=name)

    right = mod(t232, "МИРТЕК-232-РУ")
    typo_2232, typo_323, old = mod(t232, "МИРТЕК-2232-РУ"), mod(t232, "МИРТЕК-323-РУ"), mod(t_old, "МИРТЕК-301")
    ladoga = mod(t_old, "Ладога 0102")
    v_2232 = _variant(conn, t232, "МИРТЕК-2232-РУ", typo_2232, "ai")
    v_323 = _variant(conn, t232, "МИРТЕК-323-РУ", typo_323, "ai")
    v_old = _variant(conn, t_old, "Миртек-301", old, "ai")
    v_old2 = _variant(conn, t_old, "МИРТЕК-3-РУ-W32", None)
    v_ok32 = _variant(conn, t_old, "МИРТЕК-32-РУ-W32", None)  # «3-РУ» внутри «32-РУ» не путать
    v_ladoga = _variant(conn, t_old, "Ладога 0102", ladoga, "ai")
    v_foreign_ok = _variant(conn, t_other, "Меркурий 234", None)  # у другого производителя — настоящий счётчик
    for gid, v in enumerate((v_old, v_ladoga, v_foreign_ok), start=1):
        _verification(conn, gid, v, f"S{gid}")
    _monthly(conn)

    assert rules.apply_mirtek_exclusions(conn) == 3
    reason = lambda v: _scalar(conn, "SELECT excluded_reason FROM meter_market_variants WHERE id = :v", v=v)
    assert reason(v_old) == reason(v_old2) == rules.OLD_TYPE_REASON and reason(v_ladoga) == rules.FOREIGN_REASON
    assert reason(v_ok32) is None and reason(v_foreign_ok) is None
    assert _scalar(conn, "SELECT count(*) FROM meter_verification_monthly WHERE variant_id = ANY(:v)", v=[v_old, v_ladoga]) == 0
    assert _scalar(conn, "SELECT count(*) FROM meter_market_modifications WHERE id = ANY(:i)", i=[old, ladoga]) == 0

    assert rules.apply_mirtek_typos(conn) == 2
    mod_of = lambda v: _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=v)
    assert mod_of(v_2232) == mod_of(v_323) == right
    assert _scalar(conn, "SELECT count(*) FROM meter_market_modifications WHERE id = ANY(:i)", i=[typo_2232, typo_323]) == 0
    assert rules.apply_mirtek_exclusions(conn) == 0 and rules.apply_mirtek_typos(conn) == 0
    # Загрузчик сразу ведёт новое написание-опечатку к верной строке.
    assert rules.rule_candidates("67661-17", "МИРТЕК-323-РУ") == ("МИРТЕК-232-РУ",)
    assert rules.rule_candidates("65634-16", "МИРТЕК-323-РУ") == ()


# ── Правки 24.11.2026: НАРТИС и корпуса МИРТЕК ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mit", "raw", "expected"),
    [
        ("86199-22", "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RS485-P1-D", ("86199-22", "НАРТИС-И100-W113-2", "din")),
        ("86199-22", "НАРТИС-И100-W112-2-A1R1-230-5-80A-ST-RS485-P1-D", ("86199-22", "НАРТИС-И100-W112-2", "cabinet")),
        ("86200-22", "НАРТИС-И300-W132-2-A5SR1-230-5-10A-TN-RS485-P1-D", ("86200-22", "НАРТИС-И300 W132-2", "cabinet")),
        ("86200-22", "НАРТИС-И300-W133-2-A5SR1-230-5-10A-TN-RS485-P1-D", ("86200-22", "НАРТИС-И300 W133-2", "din")),
        ("86199-22", "НАРТИС-Р1-С-1010-230-100-ZB", ("97366-25", "НАРТИС-Р1-С-1010-230-100-ZB", "split")),
        ("86200-22", "НАРТИС-Р3-М-1010-400-100-ZB", ("97367-25", "НАРТИС-Р3-М-1010-400-100-ZB", "cabinet")),
        ("86200-22", "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RS485-P1-D", ("86199-22", "НАРТИС-И100-W113-2", "din")),
    ],
)
def test_nartis_target_from_analyst_corrections(mit, raw, expected):
    assert rules.nartis_target(mit, raw) == expected


def test_nartis_corrections_remap_move_and_merge(conn):
    nartis = _scalar(conn, "INSERT INTO meter_market_manufacturers (name) VALUES ('Завод НАРТИС') RETURNING id")
    types = {}
    for number, notation in (("86199-22", "НАРТИС-И100"), ("86200-22", "НАРТИС-И300"), ("97366-25", "НАРТИС-Р1"), ("97367-25", "НАРТИС-Р3")):
        types[number] = _scalar(
            conn,
            "INSERT INTO meter_market_types (mit_number, notation, title, manufacturer_id, in_report) "
            "VALUES (:n, :no, 'Счетчики', :m, true) RETURNING id",
            n=number, no=notation, m=nartis,
        )
    w113 = _scalar(conn, "INSERT INTO meter_market_modifications (type_id, name, design) VALUES (:t, 'НАРТИС-И100-W113', 'din') RETURNING id", t=types["86199-22"])
    w113_2 = _scalar(
        conn,
        "INSERT INTO meter_market_modifications (type_id, name, design, in_rpp, score, rpp_included_at, localization_source) "
        "VALUES (:t, 'НАРТИС-И100-W113-2', 'din', true, 113, '2023-12-21', 'import') RETURNING id",
        t=types["86199-22"],
    )
    r1_zb = _scalar(conn, "INSERT INTO meter_market_modifications (type_id, name, design) VALUES (:t, 'НАРТИС-Р1-С-1010-230-100-ZB', 'split') RETURNING id", t=types["97366-25"])

    raw_w113 = "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RS485-P1-EHKLMOQ1V3-D"
    wrong_map = _variant(conn, types["86199-22"], raw_w113, w113, "import")
    wrong_type = _variant(conn, types["86200-22"], raw_w113, None, None)
    raw_r1 = "НАРТИС-Р1-С-1010-230-100-ZB"
    source_r1 = _variant(conn, types["86199-22"], raw_r1, None, None)
    target_r1 = _variant(conn, types["97366-25"], raw_r1, r1_zb, "auto")
    for gid, variant in enumerate((wrong_map, wrong_type, source_r1, target_r1), start=1):
        _verification(conn, gid, variant, f"S{gid}")
    _monthly(conn)

    stats = rules.apply_nartis_corrections(conn)
    assert stats["types_moved"] == 2 and stats["remapped"] >= 1
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=wrong_map) == w113_2
    # Такое же написание уже было под верным типом: дубль из 86200-22 слит в него, поверки не потеряны.
    assert _scalar(conn, "SELECT count(*) FROM meter_market_variants WHERE raw = :r", r=raw_w113) == 1
    assert _scalar(conn, "SELECT type_id FROM meter_market_variants WHERE id = :v", v=wrong_map) == types["86199-22"]
    assert _scalar(conn, "SELECT sum(count) FROM meter_verification_monthly WHERE variant_id = :v", v=wrong_map) == 2
    assert _scalar(conn, "SELECT count(*) FROM meter_market_variants WHERE raw = :r", r=raw_r1) == 1
    assert _scalar(conn, "SELECT type_id FROM meter_market_variants WHERE raw = :r", r=raw_r1) == types["97366-25"]
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE raw = :r", r=raw_r1) == r1_zb
    assert _scalar(conn, "SELECT sum(count) FROM meter_verification_monthly WHERE variant_id = :v", v=target_r1) == 2
    assert rules.apply_nartis_corrections(conn) == {"types_moved": 0, "remapped": 0, "mods_fixed": 0, "mods_created": 0}


def test_mirtek_body_mods_split_from_base_row(conn):
    types, _, plain, _ = _seed(conn)
    w2 = _variant(conn, types["61891-15"], "МИРТЕК-12-РУ-W2-A1R1-230-5-60A-S-RS485-KLMOQ1V3", plain, "auto")
    sp1 = _variant(conn, types["61891-15"], "МИРТЕК-12-РУ-SP1-A1R1-230-5-60A-ST-RF433/1-KMOQ1V3", plain, "auto")

    stats = rules.apply_mirtek_body_mods(conn)
    assert stats["created"] == 2 and stats["remapped"] == 2
    w2_mod = _scalar(conn, "SELECT id FROM meter_market_modifications WHERE name = 'МИРТЕК-12-РУ W2'")
    sp1_mod = _scalar(conn, "SELECT id FROM meter_market_modifications WHERE name = 'МИРТЕК-12-РУ SP1'")
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=w2) == w2_mod
    assert _scalar(conn, "SELECT design FROM meter_market_modifications WHERE id = :i", i=w2_mod) == "cabinet"
    assert _scalar(conn, "SELECT modification_id FROM meter_market_variants WHERE id = :v", v=sp1) == sp1_mod
    assert _scalar(conn, "SELECT design FROM meter_market_modifications WHERE id = :i", i=sp1_mod) == "split"
    assert rules.apply_mirtek_body_mods(conn) == {"created": 0, "fixed": 0, "remapped": 0}
