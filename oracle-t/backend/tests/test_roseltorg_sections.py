"""Корпоративные секции Росэлторга (20.10.2026): фильтр секции и листание выдачи в адаптере,
перенос закупки от общей витрины к секции при сборе — без сети."""

import uuid

import httpx
from sqlalchemy import select

from app.adapters import roseltorg
from app.adapters.base import TenderSummary
from app.adapters.registry import get_adapter
from app.models.source import Source
from app.models.tender import Tender
from app.services.tender_service import _upsert_tender


def _item(number: str) -> str:
    return f"""
    <div class="search-results__item" data-feature-favorite-lots-procedure-number="{number}">
      <div class="search-results__subject"><a href="/procedure/{number}/1">Поставка счётчиков</a></div>
    </div>"""


def _page(numbers: list[str]) -> str:
    return "<html><body>" + "".join(_item(n) for n in numbers) + "</body></html>"


def test_section_adapter_filters_by_section_and_pages(monkeypatch):
    pages = {
        0: _page([f"ROSSETI{i:011d}" for i in range(10)]),
        1: _page([f"ROSSETI{i:011d}" for i in range(10, 13)]),
    }
    calls = []

    def fake_fetch(client, method, url, params=None, **kwargs):
        calls.append(params)
        return httpx.Response(
            200, text=pages.get(params.get("page", 0), _page([])), request=httpx.Request(method, "https://x")
        )

    monkeypatch.setattr(roseltorg, "fetch_with_retry", fake_fetch)
    adapter = get_adapter("roseltorg_rosseti", search_keywords=["счетчик"])
    outcome = adapter.list_new_tenders(since=None)

    assert outcome.errors == []
    assert len(outcome.tenders) == 13
    assert all(call["source[]"] == ["18"] for call in calls)
    # Вторая страница неполная — третью не запрашиваем.
    assert [call.get("page", 0) for call in calls] == [0, 1]


def test_section_empty_result_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        roseltorg,
        "fetch_with_retry",
        lambda *a, **k: httpx.Response(200, text=_page([]), request=httpx.Request("GET", "https://x")),
    )
    outcome = get_adapter("roseltorg_interrao", search_keywords=["счетчик"]).list_new_tenders(None)
    assert outcome.errors == [] and outcome.tenders == []


def _source(db, key: str) -> Source:
    return db.scalar(select(Source).where(Source.key == key))


def test_section_takes_over_tender_from_general_showcase(db_session):
    general = _source(db_session, "roseltorg")
    section = _source(db_session, "roseltorg_rosseti")
    number = f"ROSSETI{uuid.uuid4().int % 10**11:011d}"
    summary = TenderSummary(external_id=number, title="Поставка счётчиков", source_url="https://x")

    assert _upsert_tender(db_session, general, summary, groups=[]) is True
    db_session.flush()
    # Секция нашла ту же процедуру — запись одна и теперь числится за секцией.
    assert _upsert_tender(db_session, section, summary, groups=[]) is False
    db_session.flush()
    rows = db_session.scalars(select(Tender).where(Tender.external_id == number)).all()
    assert [row.source_id for row in rows] == [section.id]
    # Повторный сбор общей витриной дубля не создаёт и запись у секции не отнимает.
    assert _upsert_tender(db_session, general, summary, groups=[]) is False
    db_session.flush()
    rows = db_session.scalars(select(Tender).where(Tender.external_id == number)).all()
    assert [row.source_id for row in rows] == [section.id]
