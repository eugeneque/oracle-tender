"""Интерфейс по коду в написании (раздел 122 ARCHITECTURE.md): замечание аналитика 23.11.2026 по «Заводу НАРТИС».

«Зелёные» строки (интерфейса в написании нет) — «н/д», а не интерфейс модификации; «фиолетовая»
(`…-ST-G/5-P1-…`) — NB-IoT; RF2400/1 — ZigBee, RF2400/2 — Bluetooth. Спорное — на согласование."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.services import meter_market_dictionary as dictionary
from app.services import meter_market_explain as explain
from app.services import meter_market_interface as iface
from app.services import meter_market_report as report
from app.services.meter_market_normalize import modification_key

TABLE = iface.parse_table(iface.NARTIS_INTERFACE_CODES)
MIRTEK_TABLE = iface.parse_table(iface.MIRTEK_INTERFACE_CODES)

GREEN = (
    "НАРТИС-И100 -W113-2-A1R1-230-5-80A-S",
    "НАРТИС-И100-W113-A1R1-230-5-60A",
    "НАРТИС-И100-W113-A1R1-230-5-80A-ST-P1-EHKLMOQ1V3-D",  # после ST сразу протокол
)
VIOLET = "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-G/5-P1-EHKLMOQ1V3-D"


@pytest.mark.parametrize("raw", GREEN)
def test_no_interface_code_is_no_data(raw):
    decision = iface.decode(raw, TABLE)
    assert decision.label == iface.NO_INTERFACE and decision.review is None


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        (VIOLET, "NB-IoT"),
        ("НАРТИС-И100-SP1-A1R1-230-5-100-SS-RF2400", "ZigBee"),  # без номера модуля — ZigBee
        ("НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RS485-RF2400-P1-EHKLMOQ1V3-D", "RS, ZigBee"),
        ("НАРТИС И100-W113-A1R1-230-5-60A-ST-G/2-P1-EHKLMOQ1V3-D", "GSM"),  # G/n — GSM, кроме G/5
        ("НАРТИС-И300-W131-A5SR1-230-5-10A-TN-RS485-G/1-P1-EHKLMOQ1V3-D", "RS, GSM"),
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-SS-RF2400/2-RS485-RF2400/1-P1-EHKMOQ1V3-D", "RS, ZigBee, Bluetooth"),
        ("НАРТИС-И100-SP1-A1R1-230-5-100A-ST-RF2400/1-RF433/1-P1-EНKMOQ1V3-D", "RF, ZigBee"),  # кириллическая Н
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-ST-RF2400/1-RS485-P1-EHKMOQ1V3-D", "RS, ZigBee"),
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-ST-RF2400/2-RS485-P1-EHKMOQ1V3-D", "RS, Bluetooth"),
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-ST-RF433/1-RS485-RF2400/1-P1-EHKMOQ1V3-D", "RS, RF, ZigBee"),
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-ST-RF2400/2-RS485-RF868/1-P1-EHKMOQ1V3-D", "RS, RF, Bluetooth"),
        ("НАРТИС-И100-SP1-2-A1R1-230-5-100A-ST-RF868/1-RS485/1-RF433/1-P1-EНKMOQ1V3-D", "RS, RF"),
        ("НАРТИС-И300 -W133-2-A5SR1-230-5-10A-TN-RS485-P1-EHLOQ1V3Z/1-D", "RS"),
        ("НАРТИС-И100-W111-2-A1R1-230-5-100A-ST-2×RS485-P1-EHKLMOQ1V3-D", "RS"),
        ("НАРТИС-И300-W133-2-A5SR1-230-5-10A-TN-RS485-RF-868/1-P1-EHLMOQ1V3Z/1-D", "RS, RF"),  # опечатка «RF-868/1»
        ("НАРТИС-И300-W131-A1R1-230-5-100A-TN-RS485-RF2400/1P1-EHKLMOQ1V3-D", "RS, ZigBee"),  # «/1P1» без дефиса
        ("НАРТИС-И300-W132-2-А5SR1-230-5-10А-TN-2хRS485-P1-HLMOQ2V3Z/1-D", "RS"),  # кириллическая «х»
    ],
)
def test_interface_codes_from_analyst_table(raw, label):
    decision = iface.decode(raw, TABLE)
    assert decision.label == label and decision.review is None, decision


@pytest.mark.parametrize(
    ("raw", "suggested", "why"),
    [
        ("НАРТИС-И100-SP1-A1R1-230-5-100A-SS-2400/1-RF433/1-P1-EHKMOQ1V3-D", "RF", "«2400/1» не расшифрован"),
        ("НАРТИС-И100-SP1-A1R1-230-5-100A-SS-RF433/1-RF2400/2-P1-EHKMOQ1V3-D", "RF, Bluetooth", "сочетание"),
        ("НАРТИС-И300 W131 G1 rs485", "RS", "«G1»"),
    ],
)
def test_disputed_codes_go_to_review(raw, suggested, why):
    decision = iface.decode(raw, TABLE)
    assert decision.label is None and decision.suggested == suggested and why in decision.review, decision


def test_combo_needs_exact_set():
    """«RF433 и RF2400/2» под вопросом только без других кодов: с RS485 это обычное «RS, RF, Bluetooth»."""

    decision = iface.decode("НАРТИС-И100-SP1-2-A1R1-230-5-100A-SS-RF433/1-RF2400/2-RS485-P1-EHKMOQ1V3-D", TABLE)
    assert decision.label == "RS, RF, Bluetooth" and decision.review is None


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-G/5-P2-HKMOQ1V3-D", "RF, NB-IoT"),
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-G/1-P2-HKMOQ1V3-D", "RF, GSM"),
        ("МИРТЕК-12-РУ-SP3-A1R1-230-5-80A-ST-RF433/1-RF2400/3-P2-HKMOQ1V3-D", "RF"),
        ("МИРТЕК-12-РУ-D17-2-A1R1-230-5-80A-ST-RS485-MOD/2-P1-B-D", "RS, mod"),
        ("МИРТЕК-12-РУ-D1-A1R1-230-5-60A-ST-RS485-P2-HKLMOQ2V3-D", "RS"),
        ("МИРТЕК-12-РУ-D1-A1R1-230-5-80A-ST-G/5-P2-HKLMOQ2V3-D", "NB-IoT"),
        ("МИРТЕК-12-РУ-W9-A1R1-230-5-80A-ST-RS485-G/5.1-P2-HKLMOV3-D", "RS, NB-IoT"),
        ("МИРТЕК-12-РУ-SP1-A1R1-230-5-60A-ST-RF433/1-RF24003-P2-HKMOQ1V3", "RF"),  # опечатка без косой
    ],
)
def test_mirtek_interface_codes(raw, label):
    decision = iface.decode(raw, MIRTEK_TABLE)
    assert decision.label == label and decision.review is None, decision


def test_mirtek_without_interface_code_is_no_data():
    decision = iface.decode("МИРТЕК-12-РУ-W2-A1R1-230-5-60A-S-KLMOQ1V3", MIRTEK_TABLE)
    assert decision.label == iface.NO_INTERFACE and decision.review is None


def test_draft_from_description_marks_ambiguous_codes():
    description = (
        "Основной интерфейс:\nRS485 – интерфейс RS-485\nRF433/n – радиоинтерфейс 433 МГц, где n – номер модификации модуля\n"
        "RF2400/n – радиоинтерфейс 2400 МГц, где n – номер модификации модуля\nДополнительные интерфейсы*:\n"
        "G/n – радиоинтерфейс GSM/GPRS, где n – номер модификации модуля интерфейса\n(Нет символа) – интерфейс отсутствует\n"
        "Поддерживаемые протоколы передачи данных:\nP1 – протоколы DLMS/COSEM/СПОДЭС, ПИРС\n"
    )
    draft = iface.codes_from_description(description)
    table = iface.parse_table(draft)
    assert table.exact["RS485"].label == "RS" and table.family["RF433"].label == "RF"
    assert table.family["RF2400"].review and table.family["G"].review and table.family["G"].label == "GSM"
    assert "P1" not in draft
    assert all(e.from_description for e in (*table.exact.values(), *table.family.values()))


# ── Справочник, отчёт, детализация ─────────────────────────────────────────────────────────


@pytest.fixture()
def nartis(db_session):
    db = db_session
    maker = db.execute(text("INSERT INTO meter_market_manufacturers (name) VALUES ('Завод НАРТИС') RETURNING id")).scalar()
    type_id = db.execute(
        text("INSERT INTO meter_market_types (mit_number, notation, title, manufacturer_id, in_report, review_status) "
             "VALUES ('86199-22', 'НАРТИС-И100', 'Счетчики', :m, true, 'included') RETURNING id"),
        {"m": maker},
    ).scalar()
    w113 = db.execute(
        text("INSERT INTO meter_market_modifications (type_id, name, phase, design, interface) "
             "VALUES (:t, 'НАРТИС-И100-W113-2', '1ф', 'nd', 'RF и RS') RETURNING id"),
        {"t": type_id},
    ).scalar()
    raws = {
        "green": GREEN[0],
        "violet": VIOLET,
        "zigbee": "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RF2400/1-RS485-P1-EHKLMOQ1V3-D",
        "disputed": "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-RS485-2400/1-P1-EHKLMOQ1V3-D",  # «2400/1» без RF
    }
    variants = {}
    for i, (key, raw) in enumerate(raws.items()):
        variants[key] = db.execute(
            text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id, mapped_by) "
                 "VALUES (:t, :r, :k, :m, 'auto') RETURNING id"),
            {"t": type_id, "r": raw, "k": modification_key(raw), "m": w113},
        ).scalar()
        db.execute(
            text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, '2026-08-01', :c)"),
            {"v": variants[key], "c": 10 * (i + 1)},
        )
    db.flush()
    return {"type_id": type_id, "modification_id": w113, "variants": variants}


def _period():
    return report.parse_period("quarter:2026-3", date(2026, 10, 1))


def test_nartis_table_drives_report_and_review(nartis, db_session):
    db = db_session
    before = report.overview(db, _period(), report.Filters())["interfaces"]
    assert iface.apply_nartis_codes(db.connection()) == 1
    assert iface.apply_nartis_codes(db.connection()) == 0  # таблица уже есть — не перезаписывается
    rows = dict(db.execute(text("SELECT raw, coalesce(interface, '—') FROM meter_market_variants")).all())
    assert rows[GREEN[0]] == "н/д" and rows[VIOLET] == "NB-IoT"
    assert rows[nartis_raw(nartis, db, "zigbee")] == "RS, ZigBee"
    assert rows[nartis_raw(nartis, db, "disputed")] == "—"  # на согласовании — отчёт берёт модификацию

    assert {r["key"]: r["count"] for r in before} == {"RF и RS": 100}
    after = report.overview(db, _period(), report.Filters())["interfaces"]
    assert {r["key"]: r["count"] for r in after} == {"н/д": 10, "NB-IoT": 20, "RS, ZigBee": 30, "RF и RS": 40}

    # Фильтр «интерфейс» и детализация считают так же, как срез.
    positions = explain.explain_overview(db, _period(), report.Filters(interface="NB-IoT"))["positions"]
    assert [p["variant_id"] for p in positions] == [nartis["variants"]["violet"]]
    assert positions[0]["interface"] == "NB-IoT"
    assert any("G/5 — NB-IoT" in r for r in positions[0]["rules"])

    queue = dictionary.interface_review_queue(db)
    assert queue["total"] == 1 and queue["items"][0]["id"] == nartis["variants"]["disputed"]
    assert queue["items"][0]["suggested"] == "RS" and queue["items"][0]["modification_interface"] == "RF и RS"
    assert report.status(db)["interface_review"] == 1


def nartis_raw(nartis, db, key):
    return db.execute(text("SELECT raw FROM meter_market_variants WHERE id = :v"), {"v": nartis["variants"][key]}).scalar()


def test_manual_interface_wins_and_can_be_cleared(nartis, db_session):
    db = db_session
    iface.apply_nartis_codes(db.connection())
    disputed = nartis["variants"]["disputed"]
    variant = dictionary.set_variant_interface(db, disputed, "ZigBee, RS")
    assert (variant.interface, variant.interface_source, variant.interface_review) == ("RS, ZigBee", "manual", None)
    iface.apply_interface_codes(db.connection())  # правило ручное не трогает
    assert dictionary.interface_review_queue(db)["total"] == 0
    positions = explain.explain_overview(db, _period(), report.Filters(interface="RS, ZigBee"))["positions"]
    assert disputed in [p["variant_id"] for p in positions]
    positions = [p for p in positions if p["variant_id"] == disputed]
    assert positions[0]["variant_interface"]["source"] == "manual"

    variant = dictionary.set_variant_interface(db, disputed, None)  # снять — снова на согласовании
    assert variant.interface is None and variant.interface_review and variant.interface_source == "rule"

    # Исправление в таблице кодов типа («2400/1 = ZigBee») пересчитывает написания сразу.
    codes = iface.NARTIS_INTERFACE_CODES + "2400/1 = ZigBee  # аналитик\n"
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": codes})
    db.refresh(variant)
    assert variant.interface == "RS, ZigBee" and variant.interface_review is None


def test_description_mismatch_with_modification_goes_to_review(nartis, db_session):
    """Строка из описания типа (не от аналитика) — расшифровка, не совпавшая с модификацией, идёт на согласование."""

    db = db_session
    draft = "RS485 = RS  # из описания типа: интерфейс RS-485\nRF2400/n = RF  # из описания типа\nG/n = GSM  # из описания типа\n"
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": draft})
    rows = {
        r.id: r for r in db.execute(text("SELECT id, interface, interface_review, interface_suggested FROM meter_market_variants")).all()
    }
    same = rows[nartis["variants"]["zigbee"]]  # «RS, RF» = «RF и RS» модификации
    assert same.interface == "RS, RF" and same.interface_review is None
    differs = rows[nartis["variants"]["violet"]]
    assert differs.interface is None and differs.interface_suggested == "GSM" and "расходится" in differs.interface_review
    assert rows[nartis["variants"]["green"]].interface == "н/д"
    # без таблицы — значения правила снимаются, отчёт снова на модификации
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": ""})
    assert db.execute(text("SELECT count(*) FROM meter_market_variants WHERE interface_source IS NOT NULL")).scalar() == 0


def test_http_routes(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert "interface_review" in client.get("/meter-market/status", headers=headers).json()
    queue = client.get("/meter-market/interface-review", headers=headers)
    assert queue.status_code == 200 and "items" in queue.json()
    assert client.put("/meter-market/variants/999999/interface", json={"interface": "RS"}, headers=headers).status_code == 404
    assert client.get("/meter-market/types/999999/interface-codes/draft", headers=headers).status_code == 404


def test_nartis_gsm_line_replaces_question_from_0114(nartis, db_session):
    """Таблица, заведённая 0114 со строкой «G/n = ? GSM», получает «G/n = GSM»; правки аналитика не трогаются."""

    db = db_session
    old = iface.NARTIS_INTERFACE_CODES.replace(iface.NARTIS_GSM_LINE, iface.NARTIS_GSM_OLD_LINE)
    gsm = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id) VALUES (:t, :r, :k, :m) RETURNING id"),
        {"t": nartis["type_id"], "r": "НАРТИС-И100-W111-A1R1-230-5-60A-ST-G/1-P1-EHKLMOQ1V3-D", "k": "g1", "m": nartis["modification_id"]},
    ).scalar()
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": old})
    assert db.execute(text("SELECT interface_review FROM meter_market_variants WHERE id = :v"), {"v": gsm}).scalar()
    assert iface.apply_nartis_gsm(db.connection()) == 1
    assert iface.apply_nartis_gsm(db.connection()) == 0
    row = db.execute(text("SELECT interface, interface_review FROM meter_market_variants WHERE id = :v"), {"v": gsm}).one()
    assert (row.interface, row.interface_review) == ("GSM", None)


def test_nartis_rf2400_line_added_once(nartis, db_session):
    """«RF2400» без номера — ZigBee: строка дописывается в таблицу 0114 один раз, решение аналитика не перекрывается."""

    db = db_session
    old = iface.NARTIS_INTERFACE_CODES.replace(iface.NARTIS_RF2400_LINE, "")
    rf = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key) VALUES (:t, :r, 'rf') RETURNING id"),
        {"t": nartis["type_id"], "r": "НАРТИС-И100-SP1-A1R1-230-5-100-SS-RF2400"},
    ).scalar()
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": old})
    assert db.execute(text("SELECT interface_review FROM meter_market_variants WHERE id = :v"), {"v": rf}).scalar()
    assert iface.apply_nartis_rf2400(db.connection()) == 1
    assert iface.apply_nartis_rf2400(db.connection()) == 0
    row = db.execute(text("SELECT interface, interface_review FROM meter_market_variants WHERE id = :v"), {"v": rf}).one()
    assert (row.interface, row.interface_review) == ("ZigBee", None)
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": old + "RF2400 = Bluetooth  # аналитик\n"})
    assert iface.apply_nartis_rf2400(db.connection()) == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CE208 C4.846.2.OGR1.QYUDVFZ GB04 SPDs", "Bluetooth, GSM"),
        ("CE308 C36.746.OGR1.QYUDVFZM GB04cмPLCRf", "Bluetooth, GSM + mod (PLC+RF)"),
        ("CE208 C4.846.2.OGR1.QYUDVFZM GB04+plcrf", "Bluetooth, GSM + mod (PLC+RF)"),
        ("CE208 R7.749.2.OR1G.QYUVLFZM LG14 spDS", "GSM, LoRaWAN"),
        ("CE308 S34.746.OA.QYUVLFZSPDsGE", "RS"),
        ("CE308 S34.746.OA.QYUVLFZ SPds GE", "RS"),
        ("CE207 R7.749.2.OAG.QUVLF NBO2 SPDs", "RS, NB-IoT"),
        ("CE207 R7.749.2.OAG.QUVLF NB02 SPDs", "RS, NB-IoT"),
        ("CE207 R7.749.2.OG.QUVLF NBO1 spDS", "NB-IoT"),
        ("CE207 R7.749.2.OG.QUVLF NB01 spDS", "NB-IoT"),
        ("CE207 R7.749.2.OG.QUVLF GS01", None),
    ],
)
def test_energomera_interface(raw, expected):
    decision = iface.energomera_interface(raw)
    assert (decision[0] if decision else None) == expected


def test_energomera_interfaces_applied_once_and_preserve_manual(db_session):
    db = db_session
    maker = db.execute(text("INSERT INTO meter_market_manufacturers (name) VALUES ('Энергомера') RETURNING id")).scalar()
    type_id = db.execute(
        text("INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report) VALUES ('55454-13', :m, true) RETURNING id"),
        {"m": maker},
    ).scalar()
    mod = db.execute(
        text("INSERT INTO meter_market_modifications (type_id, name, interface) VALUES (:t, 'CE208 C4', 'GSM') RETURNING id"),
        {"t": type_id},
    ).scalar()
    gb = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id) VALUES (:t, 'CE208 C4 GB04', 'gb', :m) RETURNING id"),
        {"t": type_id, "m": mod},
    ).scalar()
    unknown = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id) VALUES (:t, 'CE208 C4 GS01', 'gs', :m) RETURNING id"),
        {"t": type_id, "m": mod},
    ).scalar()
    manual = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id, interface, interface_source) "
             "VALUES (:t, 'CE208 R7 LG14', 'lg', :m, 'RS', 'manual') RETURNING id"),
        {"t": type_id, "m": mod},
    ).scalar()

    assert iface.apply_energomera_interfaces(db.connection()) == 1
    assert db.execute(text("SELECT interface FROM meter_market_variants WHERE id = :v"), {"v": gb}).scalar() == "Bluetooth, GSM"
    assert db.execute(text("SELECT interface FROM meter_market_variants WHERE id = :v"), {"v": unknown}).scalar() is None
    assert db.execute(text("SELECT interface FROM meter_market_variants WHERE id = :v"), {"v": manual}).scalar() == "RS"
    assert iface.apply_energomera_interfaces(db.connection()) == 0


def test_energomera_ge_modification_is_corrected_and_mapped(db_session):
    db = db_session
    maker = db.execute(text("INSERT INTO meter_market_manufacturers (name) VALUES ('Энергомера') RETURNING id")).scalar()
    type_id = db.execute(
        text("INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report) VALUES ('59520-14', :m, true) RETURNING id"),
        {"m": maker},
    ).scalar()
    mod = db.execute(
        text("INSERT INTO meter_market_modifications (type_id, name, interface) VALUES (:t, 'CE308 S34 GE', 'NB-IoT') RETURNING id"),
        {"t": type_id},
    ).scalar()
    raw = "CE308 S34.746.OA.QYUVLFZ SPds GE"
    variant = db.execute(
        text("INSERT INTO meter_market_variants (type_id, raw, norm_key) VALUES (:t, :r, 'ge') RETURNING id"),
        {"t": type_id, "r": raw},
    ).scalar()

    assert iface.apply_energomera_interfaces(db.connection()) == 3  # модификация, привязка, интерфейс написания
    row = db.execute(
        text("SELECT v.modification_id, v.interface, m.interface FROM meter_market_variants v "
             "JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = :v"),
        {"v": variant},
    ).one()
    assert tuple(row) == (mod, "RS", "RS")
    assert iface.apply_energomera_interfaces(db.connection()) == 0


def test_energomera_nb01_gets_own_modification(db_session):
    db = db_session
    maker = db.execute(text("INSERT INTO meter_market_manufacturers (name) VALUES ('Энергомера') RETURNING id")).scalar()
    type_id = db.execute(
        text("INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report) VALUES ('72632-18', :m, true) RETURNING id"),
        {"m": maker},
    ).scalar()
    wrong = db.execute(
        text("INSERT INTO meter_market_modifications (type_id, name, interface) VALUES (:t, 'CE207 R7 oa.', 'RS') RETURNING id"),
        {"t": type_id},
    ).scalar()
    raws = (
        ("CE207 R7.749.2.OAG.QUVLF NB01 SPDs~", wrong),
        ("CE207 R7.749.2.OG.QUVLF NB01 spDS~", None),
    )
    ids = []
    for i, (raw, mod) in enumerate(raws):
        ids.append(db.execute(
            text("INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id) VALUES (:t, :r, :k, :m) RETURNING id"),
            {"t": type_id, "r": raw, "k": f"nb{i}", "m": mod},
        ).scalar())

    assert iface.apply_energomera_interfaces(db.connection()) == 5  # новая модификация, две привязки, два интерфейса
    rows = db.execute(
        text("SELECT v.raw, v.interface, m.name, m.interface FROM meter_market_variants v "
             "JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = ANY(:ids) ORDER BY v.raw"),
        {"ids": ids},
    ).all()
    assert [(r.raw, r.interface, r.name, r.interface) for r in rows] == [
        (raw, "NB-IoT", "CE207 R7 NB01", "NB-IoT") for raw, _ in raws
    ]
    assert iface.apply_energomera_interfaces(db.connection()) == 0
