"""Отчёт «Рынок ПУ» и справочник (раздел 79 ARCHITECTURE.md, правила подсчёта раздела 3 ТЗ)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.services import meter_market_report as report


def _id(db, sql: str, **params) -> int:
    return db.execute(text(sql + " RETURNING id"), params).scalar_one()


@pytest.fixture()
def market(db_session):
    """Два производителя, один из которых в 2025 — в группе «Энергомера», с 2026 — в «НАРТИС»
    (как АтомЦифроСбыт), локализованная модификация с датой внесения в середине месяца и
    неразмеченное написание."""

    db = db_session
    db.execute(text(
        "TRUNCATE meter_verifications, meter_verification_monthly, meter_market_loads, meter_market_variants, "
        "meter_market_modifications, meter_market_types, meter_market_group_members, meter_market_groups, "
        "meter_market_manufacturers RESTART IDENTITY CASCADE"
    ))
    nartis = _id(db, "INSERT INTO meter_market_manufacturers (name, synonyms) VALUES ('Завод НАРТИС', '[]')")
    atom = _id(db, "INSERT INTO meter_market_manufacturers (name, synonyms) VALUES ('АтомЦифроСбыт', '[]')")
    energo = _id(db, "INSERT INTO meter_market_manufacturers (name, synonyms) VALUES ('Энергомера', '[]')")
    g_nartis = _id(db, "INSERT INTO meter_market_groups (name) VALUES ('Завод НАРТИС')")
    g_energo = _id(db, "INSERT INTO meter_market_groups (name) VALUES ('Энергомера')")
    for group, manufacturer, y_from, y_to in ((g_nartis, nartis, None, None), (g_nartis, atom, 2026, None),
                                             (g_energo, energo, None, None), (g_energo, atom, 2022, 2025)):
        db.execute(text("INSERT INTO meter_market_group_members (group_id, manufacturer_id, year_from, year_to) "
                        "VALUES (:g, :m, :f, :t)"), {"g": group, "m": manufacturer, "f": y_from, "t": y_to})
    t_nartis = _id(db, "INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report, review_status) "
                       "VALUES ('77904-20', :m, true, 'included')", m=nartis)
    t_atom = _id(db, "INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report, review_status) "
                     "VALUES ('90000-23', :m, true, 'included')", m=atom)
    t_out = _id(db, "INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report, review_status) "
                    "VALUES ('11111-11', :m, false, 'new')", m=energo)
    m_din = _id(db, "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, in_rpp, score, "
                    "rpp_included_at) VALUES (:t, 'НАРТИС-100.121RIL', '1ф', 'din', 'GSM', true, 114, '2026-08-15')",
                t=t_nartis)
    m_split = _id(db, "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, in_rpp, score) "
                      "VALUES (:t, 'АТОМ 3', '3ф', 'split', 'RF', true, 95)", t=t_atom)
    variants = {}
    for key, type_id, raw, mod in (("din", t_nartis, "НАРТИС-100.121RIL", m_din), ("din2", t_nartis, "HAPTИC-100.121RIL", m_din),
                                   ("split", t_atom, "АТОМ 3", m_split), ("raw", t_nartis, "НАРТИС ???", None),
                                   ("out", t_out, "чужой", None)):
        variants[key] = _id(db, "INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id) "
                                "VALUES (:t, :r, :r, :m)", t=type_id, r=raw, m=mod)
    monthly = [("din", "2026-07-01", 100), ("din2", "2026-08-01", 40), ("split", "2025-05-01", 30),
               ("split", "2026-08-01", 20), ("raw", "2026-08-01", 5), ("out", "2026-08-01", 999)]
    for key, month, count in monthly:
        db.execute(text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, :m, :c)"),
                   {"v": variants[key], "m": month, "c": count})
    # Сырые поверки августа по локализованной модификации: 10 до 15-го, 30 — с 15-го.
    for i in range(40):
        db.execute(text(
            "INSERT INTO meter_verifications (global_id, variant_id, verification_date, applicable, annulled) "
            "VALUES (:g, :v, :d, true, false)"
        ), {"g": 1000 + i, "v": variants["din2"], "d": date(2026, 8, 5 if i < 10 else 20)})
    db.execute(text("INSERT INTO meter_market_loads (package_id, kind, file_name, status, finished_at) "
                    "VALUES ('20261029', 'delta', 'x', 'done', now())"))
    db.flush()
    return {"nartis": nartis, "atom": atom, "variants": variants, "t_nartis": t_nartis, "m_din": m_din}


def test_period_defaults_to_year_through_last_full_quarter(market, db_session):
    edge = report.data_edge(db_session)
    assert edge == date(2026, 10, 1)
    period = report.parse_period(None, edge)
    assert (period.start, period.end, period.label) == (date(2026, 1, 1), date(2026, 10, 1), "2026 г. (1–3 кв.)")
    assert report.parse_period("quarter:2026-3", edge).label == "3 кв. 2026 г."
    with pytest.raises(ValueError):
        report.parse_period("week:12", edge)


def test_overview_counts_report_types_only_and_keeps_unmapped_visible(market, db_session):
    data = report.overview(db_session, report.parse_period("quarter:2026-3", date(2026, 10, 1)), report.Filters())
    assert data["total"] == 165  # «чужой» тип не в отчёте — 999 не считаются
    assert data["unmapped"] == 5
    by_name = {r["name"]: r["count"] for r in data["manufacturers"]}
    assert by_name == {"Завод НАРТИС": 145, "АтомЦифроСбыт": 20}
    assert {r["key"]: r["count"] for r in data["designs"]} == {"DIN-рейка": 140, "Сплит": 20, "нет данных": 5}


def test_cross_filter_narrows_other_blocks_but_not_its_own(market, db_session):
    period = report.parse_period("quarter:2026-3", date(2026, 10, 1))
    data = report.overview(db_session, period, report.Filters(design="Сплит"))
    assert {r["key"]: r["count"] for r in data["phases"]} == {"3ф": 20}
    assert len(data["designs"]) == 3  # свой блок не сужается — видно, из чего выбирали
    assert [r["name"] for r in data["manufacturers"]] == ["АтомЦифроСбыт"]


def test_groups_follow_membership_years(market, db_session):
    data = report.groups_by_year(db_session, date(2026, 10, 1))
    groups = {r["name"]: r for r in data["rows"]}
    assert groups["Энергомера"]["years"][2025] == 30  # АтомЦифроСбыт в 2025 — у «Энергомеры»
    assert groups["Завод НАРТИС"]["years"][2026] == 165  # а с 2026 — у «НАРТИС»


def test_localization_counts_from_rpp_inclusion_date_and_filters_by_requirement(market, db_session):
    data = report.localization(db_session, date(2026, 10, 1))
    rows = {r["name"]: r for r in data["rows"]}
    # Внесено 15.08.2026: июль (100) не в счёт, в августе — 30 поверок с 15-го, а не все 40.
    assert rows["Завод НАРТИС"]["quarters"]["2026-3"] == 30
    # Дата внесения неизвестна — не учитывается (решение 11.11.2026), но видно, сколько таких поверок.
    assert "АтомЦифроСбыт" not in rows and data["undated"] == 50
    only_2025 = report.localization(db_session, date(2026, 10, 1), requirement=2025)
    assert [r["name"] for r in only_2025["rows"]] == ["Завод НАРТИС"]


def test_requirement_year_by_thresholds():
    limits = [(2024, 94), (2025, 113)]
    assert report.requirement_year(95, limits) == 2024
    assert report.requirement_year(113, limits) == 2025
    assert report.requirement_year(80, limits) is None
    assert report.requirement_year(None, limits) is None


def test_drill_goes_manufacturer_type_modification_variants(market, db_session):
    period = report.parse_period("quarter:2026-3", date(2026, 10, 1))
    types = report.drill(db_session, period, manufacturer_id=market["nartis"])["rows"]
    assert [(t["mit_number"], t["count"]) for t in types] == [("77904-20", 145)]
    mods = report.drill(db_session, period, type_id=market["t_nartis"])["rows"]
    assert {m["name"]: m["count"] for m in mods} == {"НАРТИС-100.121RIL": 140, "нет данных — написания не размечены": 5}
    variants = report.drill(db_session, period, modification_id=market["m_din"])["rows"]
    assert {v["raw"]: v["count"] for v in variants} == {"НАРТИС-100.121RIL": 100, "HAPTИC-100.121RIL": 40}


# ── API и права ────────────────────────────────────────────────────────────────────────────


def _user_token(client, admin_token: str, *, role: str = "user") -> str:
    username = f"{role}_{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/users",
        json={"username": username, "password": "Analyst123!", "full_name": "Сотрудник", "role": role},
        headers=headers,
    )
    assert created.status_code in (200, 201), created.text
    assert created.json()["role"] == role
    login = client.post("/auth/login", json={"username": username, "password": "Analyst123!"})
    return login.json()["access_token"]


def test_analyst_role_opens_analytics_and_closes_catalog(client, admin_token):
    """Роль «Аналитик» (29.10.2026): «Аналитика» открыта, «Каталог продукции» закрыт — кроме
    журнала опросов реестров, который читают «Настройки → Источники»."""

    analyst = {"Authorization": f"Bearer {_user_token(client, admin_token, role='analyst')}"}
    assert client.get("/analytics/overview", headers=analyst).status_code == 200
    assert client.get("/tenders", headers=analyst).status_code == 200
    assert client.get("/manufacturers", headers=analyst).status_code == 403
    assert "Каталог продукции" in client.get("/catalog/queue", headers=analyst).json()["detail"]
    assert client.get("/catalog/registries/polls", headers=analyst).status_code == 200
    assert client.get("/users", headers=analyst).status_code == 403
    user = {"Authorization": f"Bearer {_user_token(client, admin_token)}"}
    assert client.get("/manufacturers", headers=user).status_code == 200


def test_dictionary_editing_requires_right_and_maps_same_key_spellings(client, admin_token):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("TRUNCATE meter_market_variants, meter_market_modifications, meter_market_types RESTART IDENTITY CASCADE"))
        type_id = _id(db, "INSERT INTO meter_market_types (mit_number, in_report, review_status) VALUES ('1-26', true, 'included')")
        v1 = _id(db, "INSERT INTO meter_market_variants (type_id, raw, norm_key) VALUES (:t, 'ABC 1', 'abc1')", t=type_id)
        _id(db, "INSERT INTO meter_market_variants (type_id, raw, norm_key) VALUES (:t, 'АВС-1', 'abc1')", t=type_id)
        db.commit()
    finally:
        db.close()

    viewer = {"Authorization": f"Bearer {_user_token(client, admin_token)}"}
    body = {"variant_id": v1, "name": "ABC-1", "phase": "1ф", "design": "din", "interface": "rs"}
    # «Аналитика» обычному пользователю закрыта целиком — и отчёт, и справочник.
    assert client.get("/meter-market/unmapped", headers=viewer).status_code == 403
    assert client.get("/analytics/overview", headers=viewer).status_code == 403
    assert client.post(f"/meter-market/types/{type_id}/modifications", json=body, headers=viewer).status_code == 403

    editor = {"Authorization": f"Bearer {_user_token(client, admin_token, role='analyst')}"}
    assert client.get("/meter-market/status", headers=editor).json()["can_edit"] is True
    assert client.get("/meter-market/unmapped", headers=editor).json()["variants"] == 2
    created = client.post(f"/meter-market/types/{type_id}/modifications", json=body, headers=editor)
    assert created.status_code == 200, created.text
    assert client.get("/meter-market/unmapped", headers=editor).json()["variants"] == 0
    mods = client.get(f"/meter-market/types/{type_id}/modifications", headers=editor).json()
    assert mods[0]["interface"] == "RS" and mods[0]["variants"] == 2

    bad = client.put("/meter-market/thresholds", json={"thresholds": [{"year": 2025, "min_score": 113},
                                                                      {"year": 2025, "min_score": 100}]}, headers=editor)
    assert bad.status_code == 422


def test_report_without_loaded_data_explains_itself(client, admin_token):
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("TRUNCATE meter_market_loads"))
        db.commit()
    finally:
        db.close()
    response = client.get("/meter-market/overview", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 409 and "ещё не загружены" in response.json()["detail"]


def test_gisp_rows_build_record_history_without_overriding_analyst_scores(market, db_session):
    from app.adapters.admission_registries import RegistryRow
    from app.services.meter_market_gisp import apply_gisp_rows, match_rows, name_pattern

    assert name_pattern("307") is None  # слишком коротко — нашлось бы в половине реестра
    # Самый длинный префикс полного кода поверки, который есть в реестре.
    head, found = match_rows("НАРТИС-И100-W112-2-A1R1-230-5-80A", [
        (RegistryRow("1", "x"), "счётчик haptиc-и1oo-w112-2"), (RegistryRow("2", "y"), "счётчик haptиc-и1oo-w113-2"),
    ])
    assert head == "haptиc-и1oo-w112-2" and [r.record_number for r in found] == ["1"]

    rows = [
        RegistryRow("10001", "Счетчик электрической энергии HAPTИC-100.121RIL/2", score=113, issued_at=date(2026, 3, 1)),
        RegistryRow("10002", "Счетчик НАРТИС-100.121RIL", score=114, issued_at=date(2025, 6, 10)),
        RegistryRow("10003", "Счетчик НАРТИС-100.121RILX", score=120, issued_at=date(2024, 1, 1)),  # другое исполнение
        RegistryRow("10004", "Счетчики АТОМ 3 (модификаций АТОМ 3-R34-…)", score=105, issued_at=date(2025, 2, 3)),
    ]
    db_session.execute(text("UPDATE meter_market_modifications SET localization_source = 'import', rpp_included_at = NULL"))
    db_session.execute(text("UPDATE meter_market_modifications SET score = 117 WHERE name = 'АТОМ 3'"))
    db_session.execute(text("UPDATE meter_market_types SET notation = 'АТОМ 3' WHERE mit_number = '90000-23'"))
    stats = apply_gisp_rows(db_session, rows, today=date(2026, 10, 29))
    assert (stats.matched, stats.family_only, stats.records, stats.score_mismatch) == (2, 1, 2, 1)
    got = dict(db_session.execute(text("SELECT name, (score, rpp_included_at, rpp_record_number)::text FROM meter_market_modifications")).all())
    # Баллы аналитика не тронуты, дата — первого внесения, номер — действующей записи.
    assert got["НАРТИС-100.121RIL"] == "(114,2025-06-10,10001)"
    # Совпало только семейство «АТОМ 3» — только дата, без истории записей.
    assert got["АТОМ 3"] == "(117,2025-02-03,)"
    assert db_session.execute(text("SELECT count(*) FROM meter_market_rpp_records")).scalar() == 2


def test_gisp_dates_no_modification_by_type_notation(market, db_session):
    """КПЗ, 95882-25 «M2M-1С 4g» (С — кириллица): весь тип одной строкой «(без модификации)».
    Дата внесения ищется по обозначению типа, баллы остаются из справочника (12.11.2026)."""

    from app.adapters.admission_registries import RegistryRow
    from app.services.meter_market_gisp import apply_gisp_rows

    t_kpz = _id(db_session, "INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report, review_status, "
                            "notation) VALUES ('95882-25', :m, true, 'included', 'M2M-1С 4g')", m=market["nartis"])
    db_session.execute(text(
        "INSERT INTO meter_market_modifications (type_id, name, in_rpp, score, localization_source) "
        "VALUES (:t, '(без модификации)', true, 115, 'import')"
    ), {"t": t_kpz})
    rows = [
        RegistryRow("20001", "Счетчик электрической энергии однофазный компактный M2M-1C-1/2-100D-4GB",
                    score=115, issued_at=date(2025, 3, 28)),
        RegistryRow("20002", "Счетчик M2M-1C-1/2-100D-4G-SB", score=115, issued_at=date(2025, 6, 1)),
    ]
    stats = apply_gisp_rows(db_session, rows, today=date(2026, 11, 12))
    assert stats.family_only == 1
    got = db_session.execute(text(
        "SELECT (score, rpp_included_at)::text FROM meter_market_modifications WHERE type_id = :t"
    ), {"t": t_kpz}).scalar()
    assert got == "(115,2025-03-28)"


def test_localization_follows_rpp_record_history(market, db_session):
    """НАРТИС-И100-W112-2 в ГИСП: 95 баллов с 12.11.2024, 113 — с 14.05.2026. Поверки до мая
    2026 года — требования 2024-го, после — 2025-го; месяц смены делится по дням."""

    db_session.execute(text(
        "INSERT INTO meter_market_rpp_records (modification_id, issued_at, score, source) VALUES "
        "(:m, '2024-11-12', 95, 'gisp'), (:m, '2026-08-15', 113, 'gisp')"
    ), {"m": market["m_din"]})
    data = report.localization(db_session, date(2026, 10, 1))
    nartis = next(r for r in data["rows"] if r["name"] == "Завод НАРТИС")
    by_score = {s["score"]: s["quarters"]["2026-3"] for s in nartis["scores"]}
    # Июль (100) и август до 15-го (10) — ещё 95 баллов, с 15 августа (30) — 113.
    assert by_score == {95.0: 110, 113.0: 30}
    only_2025 = report.localization(db_session, date(2026, 10, 1), requirement=2025)
    assert next(r for r in only_2025["rows"] if r["name"] == "Завод НАРТИС")["total"] == 30


# ── Детализация показателей и статусы (замечания 09.11.2026) ────────────────────────────────


def test_overview_has_no_undetermined_status_and_counts_nodata(market, db_session):
    data = report.overview(db_session, report.parse_period("quarter:2026-3", date(2026, 10, 1)), report.Filters())
    # Признак ПП 890 в фикстуре не проставлен — всё «нет данных», и это видно числом.
    assert data["smart_counts"] == {"yes": 0, "no": 0, "nodata": 165}
    keys = {r["key"] for block in ("designs", "phases", "interfaces") for r in data[block]}
    assert "не размечено" not in keys and "н/д" not in keys


def test_explain_overview_sums_to_shown_number_and_lists_exclusions(market, db_session):
    from app.services import meter_market_explain as explain

    db_session.execute(text(
        "INSERT INTO meter_verifications (global_id, variant_id, verification_date, applicable, annulled) VALUES "
        "(5001, :v, '2026-08-10', false, false), (5002, :v, '2026-08-11', true, true)"
    ), {"v": market["variants"]["din"]})
    period = report.parse_period("quarter:2026-3", date(2026, 10, 1))
    result = explain.explain_overview(db_session, period, report.Filters())
    assert result["value"] == report.overview(db_session, period, report.Filters())["total"] == 165
    reasons = {r["reason"]: r["count"] for r in result["reasons"] if not r["included"]}
    assert reasons[explain.REASON_NOT_IN_REPORT] == 999
    assert reasons[explain.REASON_UNFIT] == 1 and reasons[explain.REASON_ANNULLED] == 1
    unmapped = next(p for p in result["positions"] if p.get("raw") == "НАРТИС ???")
    assert unmapped["smart"]["status"] == "Нет данных" and "не размечено" in unmapped["smart"]["reason"]
    # С фильтром по производителю — только его позиции, сумма совпадает с таблицей.
    narrowed = explain.explain_overview(db_session, period, report.Filters(manufacturer_id=market["nartis"]))
    assert narrowed["value"] == 145
    assert explain.to_xlsx(result, "тест")[:2] == b"PK"


def test_explain_groups_follow_membership_year(market, db_session):
    from app.services import meter_market_explain as explain

    edge = date(2026, 10, 1)
    result = explain.explain_overview(db_session, report.parse_period("year:2025", edge), report.Filters(group="Энергомера"))
    assert result["value"] == report.groups_by_year(db_session, edge)["rows"][1]["years"][2025] == 30


def test_explain_localization_matches_cell_and_names_reasons(market, db_session):
    from app.services import meter_market_explain as explain

    edge = date(2026, 10, 1)
    result = explain.explain_localization(db_session, edge, manufacturer_id=market["nartis"], quarter="2026-3")
    assert result["value"] == 30
    reasons = {r["reason"]: r["count"] for r in result["reasons"] if not r["included"]}
    assert reasons["поверка до внесения в РПП (15.08.2026)"] == 110  # июль 100 + 10 до 15 августа
    assert reasons[explain.REASON_UNMAPPED_LOC] == 5
    only_score = explain.explain_localization(db_session, edge, manufacturer_id=market["nartis"], score=114.0, quarter="2026-3")
    assert only_score["value"] == 30 and only_score["excluded"] == 0


def test_localization_counts_unmapped_spelling_when_all_type_modifications_agree(market, db_session):
    """Замечание 11.11.2026: «…трансформаторного включения M2M-3Т» без интерфейса (89846-23) не
    попадало в локализацию, хотя оба исполнения типа в РПП со 115 баллами. Неразмеченное
    написание учитывается по общей записи, если все исполнения типа в РПП с одинаковыми баллами."""

    from app.services import meter_market_explain as explain

    db = db_session
    t = _id(db, "INSERT INTO meter_market_types (mit_number, manufacturer_id, in_report, review_status) "
                "VALUES ('89846-23', :m, true, 'included')", m=market["atom"])
    mods = [
        _id(db, "INSERT INTO meter_market_modifications (type_id, name, design, interface, in_rpp, score, "
                "rpp_included_at) VALUES (:t, :n, 'cabinet', :i, true, 115, '2025-03-28')", t=t, n=n, i=i)
        for n, i in (("M2M-3T 2G", "GSM"), ("M2M-3T 2G-RS", "GSM и RS"))
    ]
    raw = _id(db, "INSERT INTO meter_market_variants (type_id, raw, norm_key) VALUES (:t, 'M2M-3Т', 'm2m3t')", t=t)
    db.execute(text("INSERT INTO meter_verification_monthly (variant_id, month, count) VALUES (:v, '2026-04-01', 96)"),
               {"v": raw})
    edge = date(2026, 10, 1)

    def cell() -> int:
        row = next(r for r in report.localization(db, edge)["rows"] if r["name"] == "АтомЦифроСбыт")
        return row["quarters"]["2026-2"]

    assert cell() == 96
    result = explain.explain_localization(db, edge, manufacturer_id=market["atom"], quarter="2026-2")
    assert result["value"] == 96 and result["excluded"] == 0
    assert result["positions"][0]["basis"] == report.REASON_TYPE_INHERITED

    # Баллы исполнений расходятся — какое поверено, неизвестно: не угадываем, исключаем.
    db.execute(text("UPDATE meter_market_modifications SET score = 95 WHERE id = :m"), {"m": mods[1]})
    assert not any(r["name"] == "АтомЦифроСбыт" and r["quarters"]["2026-2"] for r in report.localization(db, edge)["rows"])
    result = explain.explain_localization(db, edge, manufacturer_id=market["atom"], quarter="2026-2")
    assert {r["reason"]: r["count"] for r in result["reasons"]}[explain.REASON_UNMAPPED_LOC] == 96


def test_rpp_record_without_score_or_expired_does_not_hide_valid_one():
    """НАРТИС-И100-W113-2 в ГИСП: 83 балла с 2023 года и запись 22.09–07.10.2025 без баллов.
    Раньше всё после 22.09.2025 выпадало; теперь действует запись с баллами, пока не истекла."""

    records = [
        report.RppRecord(date(2023, 12, 21), date(2026, 3, 1), 83.0, "1"),
        report.RppRecord(date(2025, 9, 22), date(2025, 10, 7), None, "2"),
    ]
    assert report.rpp_verdict(records, date(2025, 9, 30)).score == 83.0
    assert report.rpp_verdict(records, date(2025, 12, 1)).included
    expired = report.rpp_verdict(records, date(2026, 3, 2))
    assert not expired.included and expired.reason == "срок записи РПП истёк 01.03.2026"
    assert report.rpp_verdict(records, date(2023, 1, 1)).reason.startswith("поверка до внесения")
