"""Условия поставки: модель поставки, места, график, заявки и документы процедуры (05.11.2026).

Модель не вызывается: ответ подставляется, проверяется то, что делает код вокруг неё —
какой текст уходит модели, проверка ИНН/КПП, недопустимая связь без позиций, постоянные ключи
строк Bitrix24 и строки документов.
"""

from __future__ import annotations

import uuid

from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import TenderDocument
from app.services import bitrix_ai_service, delivery_terms_service as service
from app.services.delivery_terms_service import (
    DeliveryAnswer,
    PointAnswer,
    RequestsAnswer,
    StageAnswer,
    SupplyModelAnswer,
)

CONTRACT_TEXT = """Проект контракта
1. Предмет. Поставщик поставляет приборы учёта по заявкам Заказчика.
5. Срок поставки: в течение 60 дней с даты заключения договора.
Грузополучатель: ООО «Сети», ИНН 1234567890, КПП 123456789, адрес: г. Казань, ул. Мира, 1.
Прочий текст без отношения к делу.
""" + "\n".join(f"Строка {i}" for i in range(200))


def _tender(db) -> Tender:
    source = Source(key=f"del_{uuid.uuid4().hex[:8]}", name="ЕИС", url="https://example.test", type="eis")
    db.add(source)
    db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"3261{uuid.uuid4().int % 10**7:07d}",
        title="Поставка счётчиков",
        currency="RUB",
        source_url="https://zakupki.gov.ru/epz/order/notice/ea20/view/common-info.html?regNumber=1",
    )
    db.add(tender)
    db.flush()
    db.add(
        TenderDocument(
            tender_id=tender.id,
            file_name="Проект контракта.docx",
            source_url="https://example.test/contract.docx",
            extracted_text=CONTRACT_TEXT,
            document_class="contract",
        )
    )
    db.flush()
    return tender


def _model(**kw) -> SupplyModelAnswer:
    base = dict(
        delivery_mode="MULTIPLE", delivery_order="REQUESTS", general_delivery_terms="60 дней с даты договора",
        nomenclature_link="NONE", contract_type="FRAMEWORK", document="Проект контракта.docx", quote="по заявкам",
    )
    return SupplyModelAnswer(**{**base, **kw})


def _point(**kw) -> PointAnswer:
    base = dict(
        consignee_name="ООО «Сети»", consignee_inn="1234567890", consignee_kpp="123456789",
        delivery_address="г. Казань, ул. Мира, 1", lot="1", related_positions="", relation_status="NOT_ESTABLISHED",
        document="Проект контракта.docx", quote="Грузополучатель",
    )
    return PointAnswer(**{**base, **kw})


def _stage(**kw) -> StageAnswer:
    base = dict(
        stage_number="1", delivery_period="До 15.03.2027", trigger_event="с даты заключения договора",
        quantity_or_volume="100 шт.", consignee_name="", delivery_address="", lot="1", related_positions="",
        relation_status="NOT_ESTABLISHED", document="Проект контракта.docx", quote="",
    )
    return StageAnswer(**{**base, **kw})


def _requests(**kw) -> RequestsAnswer:
    base = dict(
        present=True, request_method="На электронную почту", fulfillment_term="10 рабочих дней", min_volume="",
        max_volume="", request_period="в течение срока договора", nomenclature_defined_in_request="yes",
        quantity_defined_in_request="yes", address_defined_in_request="unknown", consignee_defined_in_request="no",
        document="Проект контракта.docx", quote="",
    )
    return RequestsAnswer(**{**base, **kw})


def _answer(**kw) -> DeliveryAnswer:
    base = dict(supply_model=_model(), points=[_point()], stages=[_stage()], requests=_requests())
    return DeliveryAnswer(**{**base, **kw})


def test_text_for_model_has_delivery_fragments_with_document_name(db_session, client):
    tender = _tender(db_session)
    text = service.delivery_text(db_session, tender, 5000)
    assert "=== Проект контракта.docx ===" in text and "Грузополучатель" in text
    assert "Строка 150" not in text  # далёкие от поставки строки не уходят в окно модели


def test_normalize_validates_inn_kpp_and_dedupes():
    answer = _answer(
        points=[
            _point(),
            _point(),  # тот же грузополучатель и адрес
            _point(consignee_inn="123", consignee_kpp="12", delivery_address="г. Уфа"),
        ]
    )
    data = service.normalize(answer)
    assert len(data["points"]) == 2
    assert data["points"][0]["consignee_inn"] == "1234567890" and data["points"][0]["consignee_kpp"] == "123456789"
    assert "consignee_inn" not in data["points"][1] and "consignee_kpp" not in data["points"][1]


def test_relation_without_positions_is_not_established():
    data = service.normalize(_answer(points=[_point(relation_status="EXPLICIT", related_positions="")]))
    assert data["points"][0]["relation_status"] == "NOT_ESTABLISHED"
    data = service.normalize(_answer(points=[_point(relation_status="EXPLICIT", related_positions="Позиции 1–3")]))
    assert data["points"][0]["relation_status"] == "EXPLICIT"


def test_unknown_enums_dropped_and_empty_model_skipped():
    data = service.normalize(_answer(supply_model=_model(delivery_mode="PARTIAL??", delivery_order="x")))
    assert "delivery_mode" not in data["supply_model"] and "delivery_order" not in data["supply_model"]
    empty = service.normalize(
        _answer(supply_model=_model(delivery_mode="", delivery_order="", general_delivery_terms=""), points=[], stages=[])
    )
    assert empty["supply_model"] == {} and empty["points"] == [] and empty["stages"] == []


def test_requests_tristate_and_absent():
    data = service.normalize(_answer())
    req = data["requests"]
    assert req["nomenclature_defined_in_request"] is True and req["consignee_defined_in_request"] is False
    assert "address_defined_in_request" not in req  # «unknown» — не False
    assert req["min_volume"] == "Не указан"
    assert service.normalize(_answer(requests=_requests(present=False)))["requests"] == {}


def test_extract_replaces_previous_and_keeps_them_on_failure(db_session, client, monkeypatch):
    tender = _tender(db_session)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer())
    assert service.extract(db_session, tender) == {"points": 1, "stages": 1}
    assert service.get_terms(db_session, tender)["supply_model"]["delivery_order"] == "REQUESTS"

    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(service, "run_structured", boom)
    try:
        service.extract(db_session, tender)
    except RuntimeError:
        pass
    assert len(service.get_terms(db_session, tender)["points"]) == 1  # прежнее осталось


def test_bitrix_rows_for_contract_and_documents(db_session, client, monkeypatch):
    tender = _tender(db_session)
    monkeypatch.setattr(service, "run_structured", lambda *a, **k: _answer())
    service.extract(db_session, tender)

    sections = bitrix_ai_service.build_sections(db_session, tender)
    contract = {row["type"]: row for row in sections["contract"] if row["type"].startswith(("supply", "delivery"))}
    assert set(contract) == {"supply_model", "delivery_point", "delivery_schedule", "delivery_request_terms"}
    assert contract["supply_model"]["key"] == "supply-model"
    assert contract["supply_model"]["data"]["delivery_order"] == "REQUESTS"
    assert contract["delivery_point"]["title"] == "Место поставки №1"
    assert contract["delivery_point"]["data"]["consignee_inn"] == "1234567890"
    assert contract["delivery_schedule"]["key"] == "delivery-schedule-lot-1-stage-1"
    assert contract["delivery_request_terms"]["data"]["quantity_defined_in_request"] is True
    assert contract["delivery_point"]["evidence"][0]["document"] == "Проект контракта.docx"

    # Ключи постоянны между выгрузками — портал обновляет строку, а не плодит дубль.
    again = bitrix_ai_service.build_sections(db_session, tender)
    assert [r["key"] for r in again["contract"]] == [r["key"] for r in sections["contract"]]

    (doc_row,) = sections["documents"]
    assert doc_row["type"] == "procurement_document" and doc_row["status"] == "published"
    assert doc_row["data"]["file_name"] == "Проект контракта.docx"
    assert doc_row["data"]["document_type"] == "Проект контракта"
    assert doc_row["data"]["file_url"] == "https://example.test/contract.docx"
    assert doc_row["data"]["source_url"].startswith("https://zakupki.gov.ru/")


def test_http_file_url_is_not_sent(db_session, client):
    tender = _tender(db_session)
    doc = db_session.query(TenderDocument).filter_by(tender_id=tender.id).one()
    doc.source_url = "http://example.test/c.docx"
    db_session.flush()
    assert "file_url" not in bitrix_ai_service.document_row(tender, doc)["data"]


def test_delivery_terms_api(client, admin_token):
    from app.db.session import SessionLocal

    headers = {"Authorization": f"Bearer {admin_token}"}
    with SessionLocal() as db:
        tender = _tender(db)
        db.commit()
        tender_id = str(tender.id)
    response = client.get(f"/tenders/{tender_id}/delivery-terms", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"supply_model": {}, "points": [], "stages": [], "requests": {}}
    assert client.get(f"/tenders/{uuid.uuid4()}/delivery-terms", headers=headers).status_code == 404
