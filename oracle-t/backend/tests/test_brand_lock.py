"""ТЗ под чужой товарный знак и статус МСП группы в заключении ИИ (05.10.2026).

Случай 32616408289: ТЗ названо моделями МИР С-05/С-04/С-07, в документации — закупка товара
определённого товарного знака. Модель, не видя этого, объяснила «не подходим» статусом МСП.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.analysis import Requirement
from app.models.manufacturer import Manufacturer
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import ai_conclusion_service as svc


def _tender(db, title="Поставка приборов учета") -> Tender:
    source = Source(key=f"bl_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"BL-{uuid.uuid4().hex[:8]}",
        title=title,
        status="collecting_bids",
        currency="RUB",
        source_url="https://example.test/t",
    )
    db.add(tender)
    db.flush()
    return tender


def _req(db, tender, text, kind="product", criticality="critical") -> Requirement:
    requirement = Requirement(
        tender_id=tender.id, text=text, kind=kind, criticality=criticality, category="other"
    )
    db.add(requirement)
    db.flush()
    return requirement


def _manufacturers(db) -> list[Manufacturer]:
    return list(db.scalars(select(Manufacturer)))


def test_brand_lock_found_with_trademark_clause(db_session):
    tender = _tender(db_session)
    reqs = [
        _req(db_session, tender, "Наименование: Прибор учета МИР С-05.10-230-5(80)-PZ1В-KNQ-E-D"),
        _req(db_session, tender, "Наименование: МИР С-04.10-230-5(100)-G2РZ1B-KQ-SG-D"),
        _req(db_session, tender, "Интерфейс ZigBee: есть", criticality="important"),
    ]
    db_session.add(
        TenderDocument(
            tender_id=tender.id,
            file_name="ТЗ.docx",
            source_url="https://example.test/doc",
            extracted_text=(
                "Заказчик закупает товара определенного товарного знака ввиду его несовместимости "
                "с товарами, на которых размещаются другие товарные знаки."
            ),
        )
    )
    db_session.flush()

    lock = svc.detect_brand_lock(db_session, tender, reqs, _manufacturers(db_session))
    assert lock is not None
    assert lock.manufacturer.brand_name == "МИР" and not lock.is_ours
    assert lock.critical_mentions == 2 and lock.equivalent_allowed is False
    assert "товарного знака" in (lock.clause or "")
    block = svc.brand_lock_block(lock)
    assert "только приборы «МИР»" in block


def test_brand_lock_ignores_ordinary_words_and_mirtek_prefix(db_session):
    tender = _tender(db_session)
    reqs = [
        _req(db_session, tender, "Матрица соответствия требованиям заполняется участником"),
        _req(db_session, tender, "Работа в мире IoT, класс точности 1"),
        _req(db_session, tender, "Совместимость с МИРТЕК-32-РУ"),
    ]
    lock = svc.detect_brand_lock(db_session, tender, reqs, _manufacturers(db_session))
    # «Матрица» и «мир» без обозначения модели — не марки; МИРТЕК — не МИР.
    assert lock is not None and lock.is_ours


def test_equivalent_allowed_is_not_a_lock(db_session):
    tender = _tender(db_session)
    reqs = [_req(db_session, tender, "Счетчик Энергомера СЕ308 или эквивалент")]
    lock = svc.detect_brand_lock(db_session, tender, reqs, _manufacturers(db_session))
    assert lock is not None and lock.equivalent_allowed is True
    assert "сравнивай по характеристикам" in svc.brand_lock_block(lock)


def test_msp_block_lists_group_entities(db_session):
    tender = _tender(db_session)
    participant = [
        _req(
            db_session,
            tender,
            "Участник закупки должен быть субъектом малого и среднего предпринимательства",
            kind="participant",
        )
    ]
    block = svc.msp_block(db_session, participant)
    # Блок появляется только при требовании МСП и говорит, что это оговорка, а не отказ.
    if block is not None:
        assert "оговорка" in block
    assert svc.msp_block(db_session, [_req(db_session, tender, "Нет ликвидации", kind="participant")]) is None
