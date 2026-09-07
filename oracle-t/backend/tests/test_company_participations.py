"""История участий: сводка, ручной ввод и выгрузка из реестра контрактов ЕИС (разделы 5.6, 7 ТЗ).

Проверяется поведение нашего кода, а не доступность внешнего сервиса: сеть подменяется, а под
наблюдением остаются решения, которые легко сломать незаметно — раздельный учёт
`lost`/`disqualified`, знаменатель win-rate, то, что повторная выгрузка не плодит дубли и не
затирает написанное человеком, и признак «выборка знает только о победах».
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.adapters.eis_contracts import ParticipationRecord, parse_card
from app.models.company_participation import (
    CompanyParticipation,
    ParticipationOutcome,
    ParticipationSource,
)
from app.models.manufacturer import Manufacturer
from app.services import company_participation_service, company_profile_service


@pytest.fixture()
def mirtek_profile(db_session):
    """Профиль МИРТЕК с ИНН — без него выгрузка не запускается по определению."""

    profile = company_profile_service.get_or_create(db_session)
    profile.inn = "6167102308"
    db_session.commit()
    return profile


def _add(db, *, outcome: str, **overrides) -> CompanyParticipation:
    mirtek = db.scalars(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True))).first()
    record = CompanyParticipation(
        manufacturer_id=mirtek.id,
        external_tender_id=overrides.pop("external_tender_id", f"EXT-{uuid.uuid4().hex[:8]}"),
        outcome=outcome,
        source=ParticipationSource.MANUAL.value,
        **overrides,
    )
    db.add(record)
    db.flush()
    return record


def test_summary_counts_losses_and_disqualifications_apart(db_session, mirtek_profile):
    _add(db_session, outcome=ParticipationOutcome.WON.value)
    _add(db_session, outcome=ParticipationOutcome.LOST.value)
    _add(db_session, outcome=ParticipationOutcome.DISQUALIFIED.value)
    db_session.flush()

    summary = company_participation_service.summary(db_session)

    assert summary.total == 3
    assert (summary.won, summary.lost, summary.disqualified) == (1, 1, 1)
    # Три исхода известны, победа одна: 1/3.
    assert summary.win_rate == Decimal("33.33")


def test_win_rate_ignores_unknown_outcomes(db_session, mirtek_profile):
    """Записи без исхода не занижают win-rate: неполнота данных — не поражение."""

    _add(db_session, outcome=ParticipationOutcome.WON.value)
    _add(db_session, outcome=ParticipationOutcome.UNKNOWN.value)
    _add(db_session, outcome=ParticipationOutcome.UNKNOWN.value)
    db_session.flush()

    summary = company_participation_service.summary(db_session)

    assert summary.total == 3 and summary.unknown == 2
    assert summary.win_rate == Decimal("100.00")


def test_win_rate_is_null_without_decided_participations(db_session, mirtek_profile):
    _add(db_session, outcome=ParticipationOutcome.UNKNOWN.value)
    db_session.flush()

    assert company_participation_service.summary(db_session).win_rate is None


def test_sync_requires_inn(db_session):
    """Без ИНН выгрузка не запускается: реестр контрактов ищется именно по нему."""

    profile = company_profile_service.get_or_create(db_session)
    profile.inn = None
    db_session.commit()

    with pytest.raises(company_participation_service.ParticipationError) as exc:
        company_participation_service.sync_from_eis(db_session, actor=None)
    assert "ИНН" in str(exc.value)


def _record(
    external_id: str, outcome: str = "won", title: str = "Поставка ПУ"
) -> ParticipationRecord:
    return ParticipationRecord(
        external_tender_id=external_id,
        tender_title=title,
        customer_name="МУП «Водоканал»",
        final_contract_value=Decimal("1200000"),
        executed_at=date(2025, 4, 17),
        outcome=outcome,
    )


def test_sync_is_idempotent_and_keeps_manual_notes(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """Повторная выгрузка обновляет запись, но не плодит дубли и не трогает заметку.

    Заметку «Выводы» пишет человек, а внешний источник о ней не знает — перезапись обнуляла
    бы единственное поле, ради которого запись открывают повторно.
    """

    monkeypatch.setattr(
        "app.adapters.eis_contracts.EisContractsAdapter.fetch_participations",
        lambda self, inn: [_record("0158300012325000123", ParticipationOutcome.WON.value)],
    )

    first = company_participation_service.sync_from_eis(db_session, actor=admin_user)
    assert (first.fetched, first.created, first.updated) == (1, 1, 0)

    saved = db_session.scalars(select(CompanyParticipation)).one()
    saved.lessons_learned_md = "Выиграли за счёт срока поставки"
    db_session.commit()

    second = company_participation_service.sync_from_eis(db_session, actor=admin_user)
    assert (second.fetched, second.created, second.updated) == (1, 0, 1)

    rows = db_session.scalars(select(CompanyParticipation)).all()
    assert len(rows) == 1
    assert rows[0].lessons_learned_md == "Выиграли за счёт срока поставки"
    assert rows[0].outcome == ParticipationOutcome.WON.value
    assert rows[0].source == ParticipationSource.EIS_CONTRACTS.value


def test_sync_does_not_overwrite_manual_outcome_with_unknown(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """`unknown` из источника не стирает исход, который человек уже проставил руками."""

    monkeypatch.setattr(
        "app.adapters.eis_contracts.EisContractsAdapter.fetch_participations",
        lambda self, inn: [_record("0158300012325000999", ParticipationOutcome.WON.value)],
    )
    company_participation_service.sync_from_eis(db_session, actor=admin_user)

    monkeypatch.setattr(
        "app.adapters.eis_contracts.EisContractsAdapter.fetch_participations",
        lambda self, inn: [_record("0158300012325000999", ParticipationOutcome.UNKNOWN.value)],
    )
    company_participation_service.sync_from_eis(db_session, actor=admin_user)

    saved = db_session.scalars(select(CompanyParticipation)).one()
    assert saved.outcome == ParticipationOutcome.WON.value


def test_card_without_identifier_is_skipped():
    """Карточка без номера закупки и без номера контракта пропускается, а не сохраняется.

    Без идентификатора повторная выгрузка создала бы запись заново — история распухала бы с
    каждым запуском.
    """

    from bs4 import BeautifulSoup

    card = BeautifulSoup(
        '<div class="search-registry-entry-block"><div class="price-block__value">1,00 ₽</div></div>',
        "lxml",
    ).select_one(".search-registry-entry-block")
    assert parse_card(card) is None


def test_card_prefers_notice_number_over_contract_number():
    """Ключ записи — номер ИЗВЕЩЕНИЯ: по нему участие связывается с тендером в нашей базе.

    Тендеры хранятся под номером извещения, а не контракта; выбери код номер контракта —
    связка не нашлась бы никогда.
    """

    from bs4 import BeautifulSoup

    html = """
    <div class="search-registry-entry-block">
      <div class="registry-entry__header-mid__number"><a href="/x?reestrNumber=2770503167425000125">№ 2770503167425000125</a></div>
      <a href="/epz/order/notice/view/common-info.html?regNumber=0339300286426000023">Сведения закупки</a>
      <div class="registry-entry__body-block">
        <div class="registry-entry__body-title">Заказчик</div>
        <div class="registry-entry__body-value">МУП «Водоканал»</div>
      </div>
      <div class="lots-wrap-content__body--item">
        <div class="lots-wrap-content__body__title">Объекты закупки</div>
        <div class="lots-wrap-content__body__val">Счётчики электрической энергии</div>
      </div>
      <div class="price-block__value">1 200 000,00 ₽</div>
      <div class="data-block">
        <div class="data-block__title">Заключение контракта</div>
        <div class="data-block__value">17.04.2025</div>
      </div>
    </div>
    """
    card = BeautifulSoup(html, "lxml").select_one(".search-registry-entry-block")
    record = parse_card(card)

    assert record.external_tender_id == "0339300286426000023"
    assert record.tender_title == "Счётчики электрической энергии"
    assert record.customer_name == "МУП «Водоканал»"
    assert record.final_contract_value == Decimal("1200000.00")
    assert record.executed_at == date(2025, 4, 17)
    # Контракт в реестре означает выигранную закупку — иных исходов этот источник не знает.
    assert record.outcome == ParticipationOutcome.WON.value


def test_wins_only_source_is_flagged(db_session, mirtek_profile, admin_user, monkeypatch):
    """История целиком из реестра контрактов помечается как «знает только о победах».

    Интерфейс по этому признаку предупреждает, что win-rate 100% — свойство выборки, а не
    факт о компании.
    """

    monkeypatch.setattr(
        "app.adapters.eis_contracts.EisContractsAdapter.fetch_participations",
        lambda self, inn: [_record("0158300012325000111"), _record("0158300012325000222")],
    )
    company_participation_service.sync_from_eis(db_session, actor=admin_user)

    assert company_participation_service.has_only_wins_source(db_session) is True

    # Одна ручная запись о проигрыше — и выборка перестаёт быть однобокой.
    _add(db_session, outcome=ParticipationOutcome.LOST.value)
    db_session.flush()
    assert company_participation_service.has_only_wins_source(db_session) is False


# --- вывод проигрышей из пайплайна --------------------------------------------------------
#
# Проигрыши не публикуются: реестра протоколов с поиском по участнику в ЕИС нет, а в самих
# протоколах участники обезличены. Поэтому они вычисляются — «заявку подавали, контракт
# достался другому». Тесты стерегут именно границы этого вывода, где легко начать врать.


def _submitted_tender(db, registry_number: str, **overrides):
    from app.models.source import Source
    from app.models.tender import Tender, TenderStage

    source = Source(
        key=f"pipeline_{uuid.uuid4().hex[:8]}",
        name="Источник для теста пайплайна",
        url="https://example.test",
        type="etp_federal_commercial",
    )
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=registry_number,
        registry_number=registry_number,
        title="Поставка приборов учёта",
        currency="RUB",
        stage=TenderStage.APPLICATION_SUBMITTED.value,
        **overrides,
    )
    db.add(tender)
    db.flush()
    return tender


def _outcome(winner: str | None, value=None):
    from app.adapters.eis_results import PurchaseOutcome

    return lambda registry_number: PurchaseOutcome(
        registry_number=registry_number,
        winner_name=winner,
        contract_value=value,
        contract_registry_number=None,
        source_url=None,
    )


def test_loss_is_recorded_when_contract_went_to_someone_else(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    tender = _submitted_tender(db_session, "0158300012325000301")
    monkeypatch.setattr(
        "app.services.company_participation_service.fetch_outcome",
        _outcome('ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "КОНКУРЕНТ"', Decimal("990000")),
    )

    checked, losses = company_participation_service.derive_outcomes_from_pipeline(
        db_session, actor=admin_user
    )
    db_session.flush()

    assert (checked, losses) == (1, 1)
    saved = db_session.scalars(select(CompanyParticipation)).one()
    assert saved.outcome == ParticipationOutcome.LOST.value
    assert saved.tender_id == tender.id
    assert saved.source == ParticipationSource.EIS_RESULTS.value
    # Победителя фиксируем текстом: без него проигрыш нечем объяснить человеку.
    assert "КОНКУРЕНТ" in saved.lessons_learned_md


def test_our_own_win_is_not_recorded_as_loss(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """Контракт достался нам — это не проигрыш, и дубль победы тоже не нужен.

    Победы приходят из реестра контрактов; запись их ещё и здесь удвоила бы знаменатель
    win-rate.
    """

    _submitted_tender(db_session, "0158300012325000302")
    monkeypatch.setattr(
        "app.services.company_participation_service.fetch_outcome",
        _outcome('ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ ТОРГОВЫЙ ДОМ "МИРТЕК"'),
    )

    checked, losses = company_participation_service.derive_outcomes_from_pipeline(
        db_session, actor=admin_user
    )

    assert (checked, losses) == (1, 0)
    assert db_session.scalars(select(CompanyParticipation)).all() == []


def test_unfinished_purchase_is_not_counted_as_loss(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """Контракт ещё не заключён — исход неизвестен, а не проигран.

    Записать здесь `lost` значило бы штрафовать компанию за то, что закупка просто идёт.
    """

    _submitted_tender(db_session, "0158300012325000303")
    monkeypatch.setattr(
        "app.services.company_participation_service.fetch_outcome",
        _outcome(None),
    )

    checked, losses = company_participation_service.derive_outcomes_from_pipeline(
        db_session, actor=admin_user
    )

    assert (checked, losses) == (1, 0)
    assert db_session.scalars(select(CompanyParticipation)).all() == []


def test_unavailable_purchase_does_not_break_the_rest(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """Одна недоступная закупка не должна обрывать разбор остальных (раздел 5.9 ТЗ)."""

    from app.adapters.eis_results import EisResultsError, PurchaseOutcome

    broken = _submitted_tender(db_session, "0158300012325000304")

    def flaky(registry_number: str):
        if registry_number == broken.registry_number:
            raise EisResultsError("ЕИС ответил HTTP 503")
        return PurchaseOutcome(
            registry_number=registry_number,
            winner_name="ООО «Другой поставщик»",
            contract_value=None,
            contract_registry_number=None,
            source_url=None,
        )

    _submitted_tender(db_session, "0158300012325000305")
    monkeypatch.setattr(
        "app.services.company_participation_service.fetch_outcome", flaky
    )

    checked, losses = company_participation_service.derive_outcomes_from_pipeline(
        db_session, actor=admin_user
    )

    assert checked == 2 and losses == 1


def test_pipeline_does_not_duplicate_existing_participation(
    db_session, mirtek_profile, admin_user, monkeypatch
):
    """Повторный запуск не создаёт вторую запись по той же закупке."""

    tender = _submitted_tender(db_session, "0158300012325000306")
    monkeypatch.setattr(
        "app.services.company_participation_service.fetch_outcome",
        _outcome("ООО «Конкурент»"),
    )

    company_participation_service.derive_outcomes_from_pipeline(db_session, actor=admin_user)
    db_session.commit()
    company_participation_service.derive_outcomes_from_pipeline(db_session, actor=admin_user)
    db_session.flush()

    rows = db_session.scalars(
        select(CompanyParticipation).where(CompanyParticipation.tender_id == tender.id)
    ).all()
    assert len(rows) == 1
