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
        ("НАРТИС И100-W113-A1R1-230-5-60A-ST-G/2-P1-EHKLMOQ1V3-D", "GSM", "номера модуля"),  # G/5 — NB-IoT, G/2 — ?
        ("НАРТИС-И100-SP1-A1R1-230-5-100A-SS-2400/1-RF433/1-P1-EHKMOQ1V3-D", "RF", "«2400/1» не расшифрован"),
        ("НАРТИС-И100-SP1-A1R1-230-5-100-SS-RF2400", None, "«RF2400» не расшифрован"),
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
        "gsm": "НАРТИС-И100-W113-2-A1R1-230-5-80A-ST-G/2-P1-EHKLMOQ1V3-D",
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
    assert rows[nartis_raw(nartis, db, "gsm")] == "—"  # на согласовании — отчёт берёт модификацию

    assert {r["key"]: r["count"] for r in before} == {"RF и RS": 100}
    after = report.overview(db, _period(), report.Filters())["interfaces"]
    assert {r["key"]: r["count"] for r in after} == {"н/д": 10, "NB-IoT": 20, "RS, ZigBee": 30, "RF и RS": 40}

    # Фильтр «интерфейс» и детализация считают так же, как срез.
    positions = explain.explain_overview(db, _period(), report.Filters(interface="NB-IoT"))["positions"]
    assert [p["variant_id"] for p in positions] == [nartis["variants"]["violet"]]
    assert positions[0]["interface"] == "NB-IoT"
    assert any("G/5 — NB-IoT" in r for r in positions[0]["rules"])

    queue = dictionary.interface_review_queue(db)
    assert queue["total"] == 1 and queue["items"][0]["id"] == nartis["variants"]["gsm"]
    assert queue["items"][0]["suggested"] == "GSM" and queue["items"][0]["modification_interface"] == "RF и RS"
    assert report.status(db)["interface_review"] == 1


def nartis_raw(nartis, db, key):
    return db.execute(text("SELECT raw FROM meter_market_variants WHERE id = :v"), {"v": nartis["variants"][key]}).scalar()


def test_manual_interface_wins_and_can_be_cleared(nartis, db_session):
    db = db_session
    iface.apply_nartis_codes(db.connection())
    gsm = nartis["variants"]["gsm"]
    variant = dictionary.set_variant_interface(db, gsm, "GSM")
    assert (variant.interface, variant.interface_source, variant.interface_review) == ("GSM", "manual", None)
    iface.apply_interface_codes(db.connection())  # правило ручное не трогает
    assert dictionary.interface_review_queue(db)["total"] == 0
    positions = explain.explain_overview(db, _period(), report.Filters(interface="GSM"))["positions"]
    assert [p["variant_id"] for p in positions] == [gsm]
    assert positions[0]["variant_interface"]["source"] == "manual"

    variant = dictionary.set_variant_interface(db, gsm, None)  # снять — снова на согласовании
    assert variant.interface is None and variant.interface_review and variant.interface_source == "rule"

    # Исправление в таблице кодов типа («G/2 = GSM») пересчитывает написания сразу.
    codes = iface.NARTIS_INTERFACE_CODES + "G/2 = GSM  # аналитик\n"
    dictionary.update_type(db, nartis["type_id"], {"interface_codes": codes})
    db.refresh(variant)
    assert variant.interface == "GSM" and variant.interface_review is None


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
