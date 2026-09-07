import uuid

from app.adapters.base import DocumentRef, SourceAdapter
from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _StubDocsAdapter(SourceAdapter):
    source_key = "stub_docs"

    def list_new_tenders(self, since):
        raise NotImplementedError

    def get_tender_details(self, external_id):
        raise NotImplementedError

    def download_documents(self, external_id, source_url=None):
        return [
            DocumentRef(file_name="Заметка.docx", url="https://example.test/note.docx"),
            DocumentRef(file_name="Архив.zip", url="https://example.test/archive.zip"),
        ]


def _make_tender(db) -> Tender:
    source = Source(
        key=f"docstest_{uuid.uuid4().hex[:8]}",
        name="Тестовая площадка",
        url="https://example.test",
        type="etp_federal_commercial",
        adapter_key="stub_docs",
        adapter_status="implemented",
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    tender = Tender(
        source_id=source.id,
        external_id="DOC-1",
        title="Тендер с документами",
        currency="RUB",
        source_url="https://example.test/tender/1",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


def test_get_tender_by_id(client, admin_token):
    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.get(f"/tenders/{tender_id}", headers=_auth_headers(admin_token))
    assert response.status_code == 200
    assert response.json()["title"] == "Тендер с документами"


def test_get_tender_by_id_404(client, admin_token):
    response = client.get(
        f"/tenders/{uuid.uuid4()}", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 404


def _docx_bytes(text: str) -> bytes:
    """Настоящий .docx с заданным текстом — собранный python-docx, а не подделка из байтов:
    проверять извлечение текста имеет смысл только на файле, который действительно
    открывается."""

    import io

    from docx import Document

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_get_tender_documents_downloads_and_extracts(client, admin_token, monkeypatch):
    """Документ и архив с документом внутри должны дать текст.

    Архив здесь не декоративный: половина площадок публикует комплект документации одним
    ZIP, и если его не разворачивать, тендер выглядит как «документов нет», а ИИ-анализ по
    нему невозможен."""

    import app.services.document_service as document_service_module

    monkeypatch.setattr(
        document_service_module, "get_adapter", lambda key: _StubDocsAdapter()
    )

    note = _docx_bytes("Требуется поставка приборов учёта электрической энергии")

    def fake_download(url: str) -> bytes:
        if url.endswith(".docx"):
            return note
        return _zip_bytes({"Документация/Техническое задание.docx": note})

    monkeypatch.setattr(document_service_module, "_download_bytes", fake_download)

    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    response = client.get(
        f"/tenders/{tender_id}/documents", headers=_auth_headers(admin_token)
    )
    assert response.status_code == 200, response.text
    docs = response.json()
    assert len(docs) == 2
    names = {d["file_name"] for d in docs}
    assert names == {"Заметка.docx", "Архив.zip"}

    docx_doc = next(d for d in docs if d["file_name"] == "Заметка.docx")
    assert docx_doc["parse_status"] == "success"
    assert docx_doc["has_text"] is True

    zip_doc = next(d for d in docs if d["file_name"] == "Архив.zip")
    assert zip_doc["parse_status"] == "success"
    assert zip_doc["has_text"] is True, "текст из документа внутри архива не извлечён"


def test_broken_archive_is_reported_not_silently_empty(client, admin_token, monkeypatch):
    """Битый архив раньше сохранялся со статусом «успех» и пустым текстом — по карточке
    нельзя было отличить «в архиве нечего читать» от «архив не открылся»."""

    import app.services.document_service as document_service_module

    monkeypatch.setattr(
        document_service_module, "get_adapter", lambda key: _StubDocsAdapter()
    )
    monkeypatch.setattr(
        document_service_module, "_download_bytes", lambda url: b"PK\x03\x04 not an archive"
    )

    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    docs = client.get(
        f"/tenders/{tender_id}/documents", headers=_auth_headers(admin_token)
    ).json()

    zip_doc = next(d for d in docs if d["file_name"] == "Архив.zip")
    assert zip_doc["parse_status"] == "error"
    assert zip_doc["parse_error"]

    # повторный запрос не должен опять дёргать адаптер/скачивание — просто отдать сохранённое
    monkeypatch.setattr(
        document_service_module,
        "get_adapter",
        lambda key: (_ for _ in ()).throw(AssertionError("не должен вызываться повторно")),
    )
    response2 = client.get(
        f"/tenders/{tender_id}/documents", headers=_auth_headers(admin_token)
    )
    assert response2.status_code == 200
    assert len(response2.json()) == 2


def test_download_tender_document_streams_file(client, admin_token, monkeypatch):
    import app.services.document_service as document_service_module

    monkeypatch.setattr(
        document_service_module, "get_adapter", lambda key: _StubDocsAdapter()
    )
    monkeypatch.setattr(
        document_service_module, "_download_bytes", lambda url: b"hello world content"
    )

    db = SessionLocal()
    try:
        tender = _make_tender(db)
        tender_id = str(tender.id)
    finally:
        db.close()

    docs_response = client.get(
        f"/tenders/{tender_id}/documents", headers=_auth_headers(admin_token)
    )
    doc_id = docs_response.json()[0]["id"]

    download_response = client.get(
        f"/tenders/{tender_id}/documents/{doc_id}/download", headers=_auth_headers(admin_token)
    )
    assert download_response.status_code == 200
    assert download_response.content == b"hello world content"
