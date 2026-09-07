"""Тесты OCR-fallback для сканированных PDF без текстового слоя (раздел 5.2 ТЗ,
`app/services/document_extraction.py`)."""

from __future__ import annotations

import io
import shutil

import pytest

from app.services import document_extraction as extraction_module
from app.services.document_extraction import extract_pdf_text


def _build_scanned_pdf(text: str) -> bytes:
    """Собирает PDF из одной страницы-картинки (без текстового слоя) — как обычный скан."""

    import pypdfium2 as pdfium
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=60)
    img = Image.new("RGB", (900, 160), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 40), text, fill="black", font=font)

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(900, 160)
    image_obj = pdfium.PdfImage.new(pdf)
    image_obj.set_bitmap(pdfium.PdfBitmap.from_pil(img))
    width, height = image_obj.get_px_size()
    image_obj.set_matrix(pdfium.PdfMatrix(width, 0, 0, height, 0, 0))
    page.insert_obj(image_obj)
    page.gen_content()

    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="системный бинарник tesseract не установлен")
def test_extract_pdf_text_ocr_fallback_for_scanned_page():
    content = _build_scanned_pdf("HELLO TENDER")
    text = extract_pdf_text(content)
    assert text is not None
    assert "HELLO" in text.upper()


def test_extract_pdf_text_skips_ocr_when_text_layer_present(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(extraction_module, "_ocr_page", lambda page: calls.append("ocr") or "should-not-be-used")

    class _FakePage:
        def extract_text(self):
            return "Обычный текстовый слой PDF"

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _stream: _FakePdf())

    text = extract_pdf_text(b"irrelevant-bytes")
    assert text == "Обычный текстовый слой PDF"
    assert calls == []  # текст уже был — OCR не должен вызываться


def test_extract_pdf_text_calls_ocr_only_for_pages_without_text_layer(monkeypatch):
    ocr_calls: list[int] = []

    def fake_ocr(page):
        ocr_calls.append(page.index)
        return "распознанный OCR текст"

    monkeypatch.setattr(extraction_module, "_ocr_page", fake_ocr)

    class _FakePage:
        def __init__(self, index: int, text: str):
            self.index = index
            self._text = text

        def extract_text(self):
            return self._text

    class _FakePdf:
        pages = [_FakePage(0, "первая страница с текстом"), _FakePage(1, "")]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _stream: _FakePdf())

    text = extract_pdf_text(b"irrelevant-bytes")
    assert text == "первая страница с текстом\nраспознанный OCR текст"
    assert ocr_calls == [1]  # только вторая страница — у неё пустой текстовый слой


def test_extract_pdf_text_handles_missing_tesseract_gracefully(monkeypatch):
    """Если tesseract не установлен на сервере, `_ocr_page` возвращает `None` — документ
    не должен падать целиком, просто у этой страницы не будет извлечённого текста."""

    monkeypatch.setattr(extraction_module, "_ocr_page", lambda page: None)

    class _FakePage:
        def extract_text(self):
            return None

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _stream: _FakePdf())

    text = extract_pdf_text(b"irrelevant-bytes")
    assert text is None


def test_extract_pdf_text_respects_ocr_disabled_setting(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(extraction_module, "get_settings", lambda: settings)

    calls: list[str] = []
    monkeypatch.setattr(extraction_module, "_ocr_page", lambda page: calls.append("ocr"))

    class _FakePage:
        def extract_text(self):
            return None

    class _FakePdf:
        pages = [_FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    import pdfplumber

    monkeypatch.setattr(pdfplumber, "open", lambda _stream: _FakePdf())

    text = extract_pdf_text(b"irrelevant-bytes")
    assert text is None
    assert calls == []


def _docx_bytes(text: str) -> bytes:
    """Минимальный docx с одним абзацем."""

    from docx import Document

    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_nested_archive_is_unpacked():
    """Техническое задание внутри вложенного архива должно доходить до анализа.

    Так публикуют документацию «Россети»: комплект одним архивом, а «Приложение №1 —
    Техническое задание» отдельным архивом внутри него. Раньше вложенный архив пропускался,
    и требования к прибору не попадали в анализ вовсе."""

    inner = _zip_bytes({"ТЗ.docx": _docx_bytes("Класс точности активной энергии не хуже 1,0")})
    outer = _zip_bytes(
        {
            "Извещение.docx": _docx_bytes("Извещение о проведении закупки"),
            "Приложение №1 - Техническое задание.zip": inner,
        }
    )

    text = extraction_module.extract_zip_text(outer)

    assert text is not None
    assert "Класс точности активной энергии" in text
    assert "Извещение о проведении закупки" in text


def test_archive_nesting_is_limited_in_depth():
    content = _zip_bytes({"ТЗ.docx": _docx_bytes("Требование самого глубокого уровня")})
    for level in range(extraction_module.MAX_ARCHIVE_DEPTH + 1):
        content = _zip_bytes({f"уровень-{level}.zip": content})

    text = extraction_module.extract_zip_text(content)

    assert text is None or "Требование самого глубокого уровня" not in text


def test_archive_budget_is_shared_across_nesting():
    """Лимит на число файлов считается на всё дерево, иначе вложенность его обходит."""

    inner = _zip_bytes({f"файл-{i}.txt": f"строка {i}".encode() for i in range(30)})
    outer = _zip_bytes(
        {
            "вложенный.zip": inner,
            **{f"верхний-{i}.txt": f"верх {i}".encode() for i in range(30)},
        }
    )

    text = extraction_module.extract_zip_text(outer)

    assert text is not None
    assert text.count("===") // 2 <= extraction_module.MAX_ARCHIVE_MEMBERS


def test_same_document_in_two_formats_is_read_once():
    """«ТЗ.docx» и «ТЗ.pdf» рядом — один документ, разбирать оба незачем."""

    archive = _zip_bytes(
        {
            "ТЗ.docx": _docx_bytes("Счётчик прямого включения"),
            "ТЗ.pdf": b"%PDF-1.4 fake",
            "Смета.xlsx": b"not really xlsx",
        }
    )

    text = extraction_module.extract_zip_text(archive)

    assert text is not None
    assert "=== ТЗ.docx ===" in text
    assert "ТЗ.pdf" not in text
