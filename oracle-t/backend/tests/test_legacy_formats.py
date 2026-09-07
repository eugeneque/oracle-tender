"""Тесты разбора старых форматов документации и архивов (раздел 5.2 ТЗ).

Половина документации коммерческих площадок — это `.doc`, `.xls` и ZIP-комплекты. Раньше
такие файлы скачивались и оставались без текста: тендер выглядел как «документов нет», и
ИИ-анализ по нему был невозможен. Здесь проверяется то, что ломается молча: не распознанная
кодировка (текст превращается в кракозябры и модель читает мусор), не развёрнутый архив
(теряется вся документация разом) и неверно определённый формат.

Тесты идут по реальным файлам из `storage/`, когда они есть: синтетический `.doc`, собранный
руками, не воспроизводит piece table настоящего Word — а именно она и составляет сложность
формата.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.services.doc_binary import DocParseError, extract_doc_text
from app.services.document_extraction import (
    extract_text,
    extract_xls_text,
    extract_zip_text,
    sniff_extension,
)

STORAGE = Path(__file__).resolve().parent.parent / "storage"


def _sample(pattern: str, min_size: int = 1000) -> Path | None:
    for path in sorted(STORAGE.rglob(pattern)):
        if path.is_file() and path.stat().st_size >= min_size:
            return path
    return None


def test_doc_text_is_readable_russian():
    """Главная ловушка `.doc`: текст лежит кусками, а однобайтовые куски закодированы
    cp1251. Прочитать поток подряд или декодировать всё как UTF-16 — получить мусор,
    который внешне похож на текст и молча уедет в ИИ-анализ."""

    sample = _sample("*.doc", min_size=10_000)
    if sample is None:
        pytest.skip("в storage/ нет .doc для проверки")

    text = extract_doc_text(sample.read_bytes())

    assert text, "из .doc не извлечён текст"
    letters = sum(1 for char in text if char.isalpha())
    cyrillic = sum(1 for char in text if "а" <= char.lower() <= "я")
    assert letters > 500, "текста подозрительно мало"
    # Документация российских закупок — кириллическая. Если кодировка выбрана неверно, доля
    # кириллицы падает почти до нуля, а строка при этом остаётся «похожей на текст».
    assert cyrillic / letters > 0.5, f"текст не похож на русский: {text[:200]!r}"
    assert "\x00" not in text


def test_doc_parser_rejects_non_ole_file():
    with pytest.raises(DocParseError):
        extract_doc_text(b"%PDF-1.4 this is not a word document")


def test_xls_text_extracted():
    sample = _sample("*.xls")
    if sample is None:
        pytest.skip("в storage/ нет .xls для проверки")

    text = extract_xls_text(sample.read_bytes())
    assert text and len(text) > 50


def test_zip_archive_is_unpacked_and_members_parsed():
    """Комплект документации одним архивом — обычная практика площадок. Без распаковки
    тендер выглядит как «документов нет»."""

    docx_sample = _sample("*.docx", min_size=5000)
    if docx_sample is None:
        pytest.skip("в storage/ нет .docx для сборки архива")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Документация/Требования.docx", docx_sample.read_bytes())
        archive.writestr("readme.unknown", b"\x00\x01binary")

    text = extract_zip_text(buffer.getvalue())

    assert text, "из архива не извлечён текст"
    assert "Требования.docx" in text, "в тексте нет заголовка с именем файла из архива"


def test_archive_member_names_from_windows_are_decoded():
    """ZIP из русской Windows хранит имена в cp866; zipfile читает их как cp437 и выдаёт
    «Åα¿½«ªÑ¡¿Ñ» вместо «Приложение». В заголовках разделов такой мусор попадёт в текст,
    который читает модель.

    Проверяется на настоящем архиве из хранилища: собрать такой архив через `zipfile` нельзя
    — Python сам выставляет флаг UTF-8, как только в имени появляется не-ASCII, и подделка
    получается не той, что приходит с площадок.
    """

    sample = None
    for path in sorted(STORAGE.rglob("*.zip")):
        if path.stat().st_size < 1000:
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                if any(
                    not (info.flag_bits & 0x800) and not info.filename.isascii()
                    for info in archive.infolist()
                ):
                    sample = path
                    break
        except zipfile.BadZipFile:
            continue

    if sample is None:
        pytest.skip("в storage/ нет архива с именами в кодировке Windows")

    text = extract_zip_text(sample.read_bytes())

    assert text
    assert "Приложение" in text or "приложение" in text.lower(), (
        f"имена файлов в архиве не декодированы: {text[:200]!r}"
    )


def test_broken_archive_reports_error_instead_of_silence():
    with pytest.raises(ValueError):
        extract_zip_text(b"PK\x03\x04 not really an archive")


def test_rtf_text_extracted():
    rtf = rb"{\rtf1\ansi\ansicpg1251 \'cf\'ee\'f1\'f2\'e0\'e2\'ea\'e0 \'f1\'f7\'e5\'f2\'f7\'e8\'ea\'ee\'e2}"
    text = extract_text(".rtf", rtf)
    assert text is not None
    assert "остав" in text.lower()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7\n...", ".pdf"),
        (rb"{\rtf1\ansi", ".rtf"),
        (b"Rar!\x1a\x07\x00", ".rar"),
        (b"just text", None),
        (b"", None),
    ],
)
def test_sniff_extension_by_signature(content: bytes, expected: str | None):
    assert sniff_extension(content) == expected


def test_sniff_distinguishes_docx_from_plain_zip():
    """`.docx` и обычный архив — оба ZIP. Ошибка здесь означает, что документ Word пойдёт
    в распаковщик архивов и вернёт пустоту."""

    docx_sample = _sample("*.docx", min_size=5000)
    if docx_sample is None:
        pytest.skip("в storage/ нет .docx для проверки")

    assert sniff_extension(docx_sample.read_bytes()) == ".docx"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", b"hello")
    assert sniff_extension(buffer.getvalue()) == ".zip"


def test_sniff_distinguishes_doc_from_xls():
    """Оба — OLE-контейнеры с одинаковой сигнатурой; различает только имя потока внутри."""

    doc_sample = _sample("*.doc", min_size=10_000)
    xls_sample = _sample("*.xls")
    if doc_sample is None or xls_sample is None:
        pytest.skip("в storage/ нет пары .doc и .xls для проверки")

    assert sniff_extension(doc_sample.read_bytes()) == ".doc"
    assert sniff_extension(xls_sample.read_bytes()) == ".xls"


def test_extract_text_falls_back_to_signature_when_extension_missing():
    """Часть площадок отдаёт файл по ссылке без расширения. Без определения по содержимому
    такой документ молча остаётся без текста."""

    doc_sample = _sample("*.doc", min_size=10_000)
    if doc_sample is None:
        pytest.skip("в storage/ нет .doc для проверки")

    text = extract_text(None, doc_sample.read_bytes())
    assert text and len(text) > 500


def test_unsupported_format_returns_none_not_error():
    assert extract_text(".exe", b"MZ\x90\x00") is None
