"""Интеграция с rusprofile.ru (18.09.2026): разбор страниц и синхронизация «Моей компании».

Сеть подменяется целиком — проверяется наш код: что скрытые подпиской значения не
превращаются в нули и пустые строки, что контракт склеивается со своей закупкой, что
проигрыши попадают в историю участий, а ручные записи и подтверждённые человеком поля
синхронизация не затирает.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.adapters import rusprofile
from app.models.company_participation import (
    CompanyParticipation,
    ParticipationOutcome,
    ParticipationSource,
)
from app.services import company_participation_service, company_profile_service, rusprofile_service

FIXTURES = Path(__file__).parent / "fixtures" / "rusprofile"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- разбор страниц -----------------------------------------------------------------------------

MINIMAL_CARD_HTML = """
<html><body>
  <h1 itemprop="name">ООО ТД "Миртек"</h1>
  <span itemprop="legalName">ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ ТОРГОВЫЙ ДОМ "МИРТЕК"</span>
  <span itemprop="foundingDate">28.03.2013</span>
  <span id="clip_ogrn">1132651008335</span>
  <span id="clip_inn">2635819741</span>
  <span id="clip_kpp">263501001</span>
  <span id="clip_address">355037 , Ставропольский край , г. Ставрополь , ул. Доваторцев, д. 33 а</span>
</body></html>
"""


def test_card_is_parsed_into_profile_fields():
    """Прежний контракт автопоиска по ссылке — без учётной записи."""

    company = rusprofile.parse_card(MINIMAL_CARD_HTML, source_url="https://www.rusprofile.ru/id/6723224")

    assert company.inn == "2635819741"
    # КПП — главная причина ходить сюда: в выдаче ЕГРЮЛ его нет, а в реквизитах заявки он нужен.
    assert company.kpp == "263501001"
    assert company.ogrn == "1132651008335"
    assert company.registration_date == date(2013, 3, 28)
    assert company.legal_name.startswith("ОБЩЕСТВО С ОГРАНИЧЕННОЙ")
    assert company.short_name == 'ООО ТД "Миртек"'
    # Адрес на странице свёрстан по частям и склеивается с пробелами перед запятыми.
    assert company.legal_address == "355037, Ставропольский край, г. Ставрополь, ул. Доваторцев, д. 33 а"


def test_page_without_inn_is_rejected():
    """Страница без ИНН — не карточка компании, а заглушка, ошибка или проверка на робота."""

    with pytest.raises(rusprofile.RusprofileError):
        rusprofile.parse_card("<html><body><h1>Проверка браузера</h1></body></html>")


@pytest.mark.parametrize(
    "value",
    [
        "https://www.rusprofile.ru/id/6723224",
        "  https://www.rusprofile.ru/id/6723224?utm_source=x  ",
        "rusprofile.ru/id/6723224",
        "6723224",
    ],
)
def test_card_id_is_extracted_from_any_reasonable_form(value):
    assert rusprofile.extract_card_id(value) == "6723224"


@pytest.mark.parametrize("value", ["", "   ", "ООО Миртек"])
def test_non_link_is_rejected_with_a_hint(value):
    with pytest.raises(rusprofile.RusprofileError):
        rusprofile.extract_card_id(value)



def test_dossier_reads_card_sections():
    dossier = rusprofile.parse_dossier(
        _fixture("card.html"), card_id="6723224", source_url="https://www.rusprofile.ru/id/6723224"
    )

    assert dossier.legal_name == 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ ТОРГОВЫЙ ДОМ "МИРТЕК"'
    assert dossier.short_name == 'ООО ТД "Миртек"'
    assert dossier.status == "Действующая организация"
    assert (dossier.inn, dossier.kpp, dossier.ogrn) == ("2635819741", "263501001", "1132651008335")
    assert dossier.registration_date == "2013-03-28"
    assert dossier.legal_address == "355037, Ставропольский край, г. Ставрополь, ул. Доваторцев, д. 33 а"
    assert dossier.authorized_capital == "3 000 000 руб."
    assert (dossier.ceo_name, dossier.ceo_position) == ("Гарбалев Андрей Александрович", "Директор")
    assert (dossier.headcount, dossier.headcount_year) == (6, 2025)
    # Зарплата скрыта подпиской — значения нет, а не строка из «░».
    assert dossier.average_salary is None
    assert dossier.data_hidden is True
    assert (dossier.main_okved_code, dossier.okved_count) == ("68.20.29", 26)
    assert dossier.codes["okpo"] == "10267170"
    assert dossier.phones == ["+7 (8652) 99-12-10"]
    assert dossier.emails == ["infotd@mirtekgroup.ru"]
    assert dossier.website == "mirtekgroup.ru"
    assert dossier.finance["year"] == 2025
    assert dossier.finance["revenue"] == "282 млн руб."
    assert dossier.finance["revenue_amount"] == "282000000.00"
    assert dossier.finance["profit"] == "45 млн руб."
    assert dossier.finance["ratings"]["Платежеспособность"] == "низкая"
    assert dossier.founders == [
        {"name": "Ступак Игорь Александрович", "share": "100%", "inn": "263505691006"}
    ]
    assert dossier.purchases_summary["purchases_count"] == 63
    assert dossier.purchases_summary["contracts_count"] == 46
    assert dossier.purchases_summary["won"] == 46
    assert dossier.purchases_summary["lost"] == 17
    assert dossier.purchases_summary["top_customers"][0] == {
        "name": 'АО "Энергосбыт Плюс"',
        "purchases": 9,
        "sum": "1 426 380 764 руб.",
    }
    assert "отсутствуют" in dossier.licenses_note


def test_purchases_join_contract_and_skip_masked_rows():
    records = rusprofile.parse_purchases(_fixture("purchases.html"), our_card_id="6723224")

    # Строка, скрытая подпиской, не возвращается: у неё нет даже номера.
    assert [r.number for r in records] == ["32312435285", "0318200063923000101"]

    win, loss = records
    assert win.won is True and win.status == "Выиграно"
    assert win.law == "223-ФЗ" and win.date == date(2023, 5, 30)
    assert win.customer == 'ГУП РО "РГРЭС"' and win.customer_card_id == "2057071"
    assert win.initial_price == Decimal("525018.00")
    assert win.winner_price == Decimal("500000.00")
    # Контракт — соседний блок `.snippet.sub`, а не отдельная закупка.
    assert win.contract_number == "56227007428230001460000"
    assert win.contract_date == date(2023, 6, 1)
    assert win.contract_status == "Исполнение"
    assert win.contract_price == Decimal("500000.00")
    assert "searchString=32312435285" in win.zakupki_url

    assert loss.won is False
    assert loss.participants == ['АО "Энергомера"', 'ООО ТД "Миртек"', 'ООО "Нартис"']
    assert loss.contract_number is None
    assert loss.method == "Электронный аукцион"


def test_pagination_helpers():
    html = _fixture("purchases.html")
    assert rusprofile._next_page_url(html) == "/gz/6723224/2"
    assert rusprofile._total_from_notice(html) == 144
    assert rusprofile._next_page_url(_fixture("licenses.html")) is None


def test_licenses_parse():
    records = rusprofile.parse_licenses(_fixture("licenses.html"))

    assert len(records) == 1
    item = records[0]
    assert item.number == "Л024-00107-77/01384373"
    assert item.status == "Действующая"
    assert item.activity == "Деятельность по технической защите конфиденциальной информации"
    assert item.issued_at == date(2024, 9, 11)
    assert item.valid_until == date(2029, 9, 11)
    assert item.issuer.startswith("ФЕДЕРАЛЬНАЯ СЛУЖБА")


def test_money_and_dates():
    assert rusprofile._parse_money("2,2 млрд руб.") == Decimal("2200000000.00")
    assert rusprofile._parse_money("525 018,00 руб.") == Decimal("525018.00")
    assert rusprofile._parse_money("░░░░ руб.") is None
    assert rusprofile._parse_date("28 марта 2013 г.") == date(2013, 3, 28)
    assert rusprofile._parse_date("01.06.2023") == date(2023, 6, 1)
    assert rusprofile.extract_card_id("https://www.rusprofile.ru/id/6723224?x=1") == "6723224"


def test_search_by_inn_picks_exact_match(monkeypatch):
    def fake_search(_client, _query):
        return [
            rusprofile.SearchHit("1", "ООО Другое", "2635819742", None, None, None, None, False),
            rusprofile.SearchHit("2", "ООО ТД Миртек (стар.)", "2635819741", None, None, None, None, True),
            rusprofile.SearchHit("3", "ООО ТД Миртек", "2635819741", None, None, None, None, False),
        ]

    monkeypatch.setattr(rusprofile, "_search", fake_search)
    hit = rusprofile.search_by_inn("2635819741")
    assert hit.card_id == "3"

    with pytest.raises(rusprofile.RusprofileError):
        rusprofile.search_by_inn("12345")


# --- синхронизация ------------------------------------------------------------------------------


@pytest.fixture()
def mirtek_profile(db_session):
    profile = company_profile_service.get_or_create(db_session)
    profile.inn = "2635819741"
    db_session.commit()
    return profile


def _fake_fetch(monkeypatch, *, total: int | None = 2):
    dossier = rusprofile.parse_dossier(
        _fixture("card.html"), card_id="6723224", source_url="https://www.rusprofile.ru/id/6723224"
    )
    purchases = rusprofile.parse_purchases(_fixture("purchases.html"), our_card_id="6723224")
    licenses = rusprofile.parse_licenses(_fixture("licenses.html"))

    def fake(_db, _profile):
        return rusprofile_service._Fetched(
            card_id="6723224",
            dossier=dossier,
            purchases=purchases,
            purchases_total=total,
            licenses=licenses,
        )

    monkeypatch.setattr(rusprofile_service, "_fetch", fake)


def test_sync_fills_profile_participations_and_keeps_manual_data(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    mirtek_profile.legal_name = "ООО ТД МИРТЕК (ввёл человек)"
    mirtek_profile.field_sources = {"legal_name": {"source": "manual", "verified_by_user": True}}
    mirtek_profile.licenses = [{"name": "СРО (вручную)", "number": "1"}]
    mirtek_profile.past_projects = [{"work_type": "Ручной проект", "year": 2020}]
    db_session.commit()
    _fake_fetch(monkeypatch)

    result = rusprofile_service.sync_profile(db_session, mirtek_profile, actor=admin_user)

    # Подтверждённое человеком поле не тронуто, пустые — заполнены с сайта.
    assert mirtek_profile.legal_name == "ООО ТД МИРТЕК (ввёл человек)"
    assert mirtek_profile.kpp == "263501001"
    assert mirtek_profile.ogrn == "1132651008335"
    assert mirtek_profile.registration_date == date(2013, 3, 28)
    assert mirtek_profile.field_sources["kpp"] == {"source": "rusprofile", "verified_by_user": False}
    assert mirtek_profile.years_of_experience and mirtek_profile.years_of_experience >= 13
    assert "kpp" in result.profile_fields_updated and "legal_name" not in result.profile_fields_updated

    # Ручная лицензия осталась, лицензия сайта добавилась с пометкой источника.
    names = [item["name"] for item in mirtek_profile.licenses]
    assert names[0] == "СРО (вручную)"
    assert any(item.get("source") == "rusprofile" for item in mirtek_profile.licenses)
    assert result.licenses_total == 2

    # Проекты: ручной + одна победа (проигрыш проектом не становится).
    projects = mirtek_profile.past_projects
    assert projects[0]["work_type"] == "Ручной проект"
    assert len(projects) == 2
    won = projects[1]
    assert won["source"] == "rusprofile"
    assert won["customer"] == 'ГУП РО "РГРЭС"'
    assert won["volume"] == "500 000,00 руб."
    assert won["year"] == 2023
    assert "контракт № 56227007428230001460000" in won["description"]

    # Досье сохранено целиком и попало в факты для модели.
    assert mirtek_profile.rusprofile_card_id == "6723224"
    assert mirtek_profile.rusprofile_data["ceo_name"] == "Гарбалев Андрей Александрович"
    assert mirtek_profile.rusprofile_data["purchases"] == {
        "fetched": 2, "total_on_site": 2, "wins": 1, "losses": 1, "undecided": 0,
    }
    facts = dict(company_profile_service.rusprofile_fields(mirtek_profile))
    assert "Гарбалев" in facts["rusprofile:ceo"]
    assert "282 млн руб." in facts["rusprofile:revenue"]
    assert "проигрышей 1" in facts["rusprofile:purchases"]
    snapshot = company_profile_service.snapshot(mirtek_profile)
    assert any("Гарбалев" in text for text in snapshot["rusprofile_facts"])

    # История участий: победа и проигрыш, источник не «только победы».
    rows = {
        row.external_tender_id: row
        for row in db_session.scalars(select(CompanyParticipation))
    }
    assert result.participations_created == 2 and result.wins == 1 and result.losses == 1
    win = rows["32312435285"]
    assert win.outcome == ParticipationOutcome.WON.value
    assert win.source == ParticipationSource.RUSPROFILE.value
    assert win.our_bid == Decimal("500000.00")
    assert win.final_contract_value == Decimal("500000.00")
    assert win.executed_at == date(2023, 6, 1)
    assert win.price_drop_pct == Decimal("4.77")
    loss = rows["0318200063923000101"]
    assert loss.outcome == ParticipationOutcome.LOST.value
    assert loss.competitors_count == 2
    assert company_participation_service.has_only_wins_source(db_session) is False
    assert result.data_hidden is True  # зарплата в фикстуре замаскирована

    # Итог записан в настройки — интерфейс покажет его рядом с кнопкой.
    settings = rusprofile_service._get_or_create(db_session)
    assert settings.last_sync_status == "ok"
    assert "проигрышей 1" in settings.last_sync_message


def test_sync_is_idempotent_and_keeps_notes(db_session, mirtek_profile, admin_user, monkeypatch):
    _fake_fetch(monkeypatch)
    rusprofile_service.sync_profile(db_session, mirtek_profile, actor=admin_user)
    row = db_session.scalar(
        select(CompanyParticipation).where(CompanyParticipation.external_tender_id == "32312435285")
    )
    row.lessons_learned_md = "Заметка человека"
    # Ту же закупку могла раньше принести выгрузка из ЕИС — источник записи не меняется.
    row.source = ParticipationSource.EIS_CONTRACTS.value
    db_session.commit()

    second = rusprofile_service.sync_profile(db_session, mirtek_profile, actor=admin_user)

    assert second.participations_created == 0 and second.participations_updated == 2
    db_session.refresh(row)
    assert row.lessons_learned_md == "Заметка человека"
    assert row.source == ParticipationSource.EIS_CONTRACTS.value
    assert db_session.scalar(select(CompanyParticipation).where(
        CompanyParticipation.external_tender_id == "32312435285"
    ).with_only_columns(CompanyParticipation.id)) is not None
    # Лицензии и проекты сайта не дублируются при повторе.
    assert len([l for l in mirtek_profile.licenses if l.get("source") == "rusprofile"]) == 1
    assert len([p for p in mirtek_profile.past_projects if p.get("source") == "rusprofile"]) == 1


def test_sync_reports_hidden_data_from_card_masks(db_session, mirtek_profile, admin_user, monkeypatch):
    """Признак скрытых данных — маски на карточке, а не число строк в подписи списка: та
    считает закупки вместе с контрактами и всегда больше числа закупок."""

    _fake_fetch(monkeypatch, total=144)
    result = rusprofile_service.sync_profile(db_session, mirtek_profile, actor=admin_user)
    assert result.purchases_fetched == 2 and result.purchases_total_on_site == 144
    assert result.data_hidden is True  # зарплата в фикстуре замаскирована
    assert "скрыта" in result.message


def test_sync_requires_credentials(db_session, mirtek_profile, admin_user):
    settings = rusprofile_service._get_or_create(db_session)
    settings.login = None
    settings.password = None
    db_session.commit()

    with pytest.raises(rusprofile_service.RusprofileNotConfiguredError):
        rusprofile_service.sync_profile(db_session, mirtek_profile, actor=admin_user)
    assert rusprofile_service._get_or_create(db_session).last_sync_status == "error"


def test_secondary_profile_does_not_touch_participations(db_session, mirtek_profile, admin_user, monkeypatch):
    _fake_fetch(monkeypatch)
    other = company_profile_service.create_profile(db_session, {"inn": "2635819741"})
    before = db_session.scalar(select(CompanyParticipation).limit(1))
    assert before is None

    result = rusprofile_service.sync_profile(db_session, other, actor=admin_user)

    assert result.participations_created == 0
    assert db_session.scalar(select(CompanyParticipation).limit(1)) is None
    assert other.rusprofile_card_id == "6723224"
    assert len(other.past_projects) == 1


# --- API ----------------------------------------------------------------------------------------


def test_settings_api_hides_password(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    response = client.patch(
        "/integrations/rusprofile",
        json={"login": "info@example.ru", "password": "secret"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_configured"] is True
    assert body["login"] == "info@example.ru"
    assert body["has_password"] is True
    assert "password" not in body and "secret" not in response.text

    # Только логин — пароль остаётся.
    response = client.patch("/integrations/rusprofile", json={"login": "x@example.ru"}, headers=headers)
    assert response.json()["has_password"] is True
    # Пустая строка — очистка.
    response = client.patch("/integrations/rusprofile", json={"password": ""}, headers=headers)
    assert response.json()["is_configured"] is False


def test_settings_api_admin_only(client, admin_token):
    assert client.get("/integrations/rusprofile").status_code == 401
