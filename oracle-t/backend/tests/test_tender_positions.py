"""Позиции закупки: извлечение таблицы спецификации и строки `procurement_position` (23.10.2026).

Модель не вызывается: ответ подставляется, проверяется то, что делает код вокруг неё, —
какой текст уходит модели, разбор количества, замена прежних позиций, повторы из разных
документов и постоянные ключи строк портала.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import bitrix_ai_service, tender_positions
from app.services.tender_positions import PositionAnswer, PositionsAnswer, parse_quantity


def _tender(db, document: str | None = "Спецификация\nСчётчик однофазный | шт. | 120") -> Tender:
    source = Source(key=f"pos_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3261{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков электрической энергии",
        currency="RUB",
    )
    db.add(tender)
    db.flush()
    if document is not None:
        db.add(
            TenderDocument(
                tender_id=tender.id,
                file_name="ТЗ.docx",
                source_url=f"https://example.test/{uuid.uuid4().hex}.docx",
                extracted_text=document,
            )
        )
        db.flush()
    return tender


def _answer(*rows: tuple[str, str, str, str]) -> PositionsAnswer:
    return PositionsAnswer(
        positions=[
            PositionAnswer(position=no, name=name, unit=unit, quantity=qty, lot="1", document="ТЗ.docx", quote=name)
            for no, name, unit, qty in rows
        ]
    )


@pytest.fixture
def model(monkeypatch):
    """Подменённая модель: запоминает текст запроса и отдаёт заданный ответ."""

    calls: list[str] = []
    state = {"answer": _answer()}

    def fake_run_structured(db, *, system_prompt, user_text, response_model, temperature=0.0):
        calls.append(user_text)
        return state["answer"]

    monkeypatch.setattr(tender_positions, "run_structured", fake_run_structured)
    monkeypatch.setattr(tender_positions, "fit_chars", lambda db, desired, reserved: desired)
    return state, calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("120", Decimal("120")),
        ("1 500", Decimal("1500")),
        ("1 500 шт.", Decimal("1500")),
        ("12,5", Decimal("12.5")),
        ("по заявкам Заказчика", None),
        ("до 120", None),
        ("", None),
    ],
)
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected


def test_specification_text_keeps_tables_and_drops_unrelated_text(db_session):
    filler = "\n".join(f"Пункт договора {index} об ответственности сторон" for index in range(200))
    tender = _tender(
        db_session,
        f"{filler}\nСпецификация\nНаименование | Ед. изм. | Количество\nСчётчик трёхфазный | шт. | 40\n{filler}",
    )
    text = tender_positions.specification_text(db_session, tender, 100_000)
    assert "Счётчик трёхфазный | шт. | 40" in text
    assert "=== ТЗ.docx ===" in text
    assert "Пункт договора 100 " not in text


def test_extract_replaces_previous_positions(db_session, model):
    state, calls = model
    tender = _tender(db_session)
    state["answer"] = _answer(("1", "Счётчик однофазный", "шт.", "120"), ("2", "Монтаж", "усл. ед.", "по заявкам"))
    assert tender_positions.extract_positions(db_session, tender) == 2
    assert "Счётчик однофазный | шт. | 120" in calls[0]

    positions = tender_positions.list_positions(db_session, tender)
    assert [(p.position_no, p.name, p.unit, p.quantity, p.quantity_text) for p in positions] == [
        ("1", "Счётчик однофазный", "шт.", Decimal("120.000"), "120"),
        ("2", "Монтаж", "усл. ед.", None, "по заявкам"),
    ]

    # Повторный разбор заменяет позиции целиком; одна таблица из ТЗ и договора — один раз.
    state["answer"] = _answer(("1", "Счётчик трёхфазный", "шт.", "40"), ("1", "Счётчик трёхфазный", "шт.", "40"))
    assert tender_positions.extract_positions(db_session, tender) == 1
    assert [p.name for p in tender_positions.list_positions(db_session, tender)] == ["Счётчик трёхфазный"]


def test_no_documents_no_model_call(db_session, model):
    _, calls = model
    tender = _tender(db_session, document=None)
    assert tender_positions.extract_positions(db_session, tender) == 0
    assert calls == []


def test_position_rows_have_stable_keys(db_session, model):
    state, _ = model
    tender = _tender(db_session)
    state["answer"] = _answer(("1", "Счётчик однофазный", "шт.", "120"), ("", "Пломба номерная", "шт.", "по заявкам"))
    tender_positions.extract_positions(db_session, tender)
    rows = bitrix_ai_service.position_rows(db_session, tender)

    assert rows[0]["key"] == "procurement-position-lot-1-row-1"
    assert rows[0]["type"] == "procurement_position"
    assert rows[0]["title"] == "Позиция №1"
    assert rows[0]["data"] == {
        "position": "1",
        "name": "Счётчик однофазный",
        "unit": "шт.",
        "quantity": 120,
        "lot_number": "1",
    }
    assert rows[0]["evidence"] == [{"document": "ТЗ.docx", "quote": "Счётчик однофазный"}]
    # Без номера — ключ от наименования; количество текстом.
    assert rows[1]["key"].startswith("procurement-position-lot-1-name-")
    assert rows[1]["data"]["quantity"] == "по заявкам"

    # Пересоздание позиций при повторном разборе ключей не меняет.
    tender_positions.extract_positions(db_session, tender)
    assert [row["key"] for row in bitrix_ai_service.position_rows(db_session, tender)] == [row["key"] for row in rows]

    # В пакете позиции идут сразу за выжимкой.
    tz = bitrix_ai_service.build_sections(db_session, tender)["technical_specification"]
    assert [row["type"] for row in tz[:3]] == ["technical_summary", "procurement_position", "procurement_position"]
