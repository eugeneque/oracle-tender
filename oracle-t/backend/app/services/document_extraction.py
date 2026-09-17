"""Извлечение текста из документов тендера (раздел 5.2, 6.2 ТЗ).

ТЗ называет PDF, DOCX и XLSX, но реальная документация площадок этим не ограничивается:
на коммерческих ЭТП половина закупок публикуется одним ZIP-архивом, а внутри — старый `.doc`
или `.xls`. Такой документ скачивался, но оставался без текста, и ИИ-анализ по нему работать
не мог. Поэтому поддержаны и старые форматы, и архивы.

Разбор `.doc` — собственный (`app/services/doc_binary.py`), без внешних бинарников: почему
так, написано в докстринге того модуля.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from loguru import logger
from openpyxl import load_workbook

from app.core.config import get_settings

# Версия правил извлечения. Увеличивается, когда разбор начинает доставать из тех же файлов
# больше текста, чем доставал раньше: у уже скачанных документов текст лежит в базе, и без
# такой отметки они навсегда остались бы разобранными по старым правилам.
#   1 — исходные правила;
#   2 — разворачиваются вложенные архивы, дубли одного документа в разных форматах читаются
#       один раз.
#   3 — таблицы docx читаются на своём месте в документе, строка таблицы — одной строкой
#       текста (см. `extract_docx_text`).
EXTRACTION_VERSION = 3

# Предел на распаковку архива: защита от zip-бомбы и просто от гигантских комплектов
# документации, которые незачем разбирать целиком. Считается на всё дерево архива сразу, а не
# на каждый уровень отдельно: иначе вложенность обходила бы ограничение — десять вложенных
# архивов по сорок файлов дали бы четыреста.
MAX_ARCHIVE_MEMBERS = 40
MAX_ARCHIVE_MEMBER_BYTES = 30 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 120 * 1024 * 1024

# На сколько уровней вложенности архивов заходить. Ноль — прежнее поведение, вложенные архивы
# пропускаются; на реальной документации так терялось главное: заказчик публикует комплект
# одним RAR, а внутри «Приложение №1 - Техническое задание.rar» лежит отдельным архивом. Без
# этого ИИ-анализ видел извещение и проект договора, но не видел ни одного требования к
# прибору — и возвращал пустой список требований на закупке счётчиков.
MAX_ARCHIVE_DEPTH = 3

# Расширения, за которыми стоит архив, а не документ.
_ARCHIVE_EXTENSIONS = frozenset({".zip", ".7z", ".rar"})

# Один и тот же документ часто выкладывают сразу в двух форматах — «ТЗ.docx» и «ТЗ.pdf» рядом
# в одном архиве. Разбирать оба незачем: текст тот же, а бюджет он съедает дважды и в анализ
# требования уходят дублями. Порядок — по надёжности извлечения: у docx текст размечен, PDF
# в худшем случае скан и тянет за собой OCR.
_FORMAT_PREFERENCE = (".docx", ".doc", ".rtf", ".xlsx", ".xlsm", ".xls", ".txt", ".pdf")


@dataclass
class _ArchiveBudget:
    """Сколько ещё файлов, байт и уровней вложенности можно разобрать. Один объект живёт на
    весь разбор дерева архива и передаётся вглубь — см. `MAX_ARCHIVE_MEMBERS`."""

    members_left: int = MAX_ARCHIVE_MEMBERS
    bytes_left: int = MAX_ARCHIVE_TOTAL_BYTES
    depth_left: int = MAX_ARCHIVE_DEPTH

    def take(self, size: int) -> bool:
        """Списывает один файл размера `size`. `False` — бюджет исчерпан, файл пропускается."""

        if self.members_left <= 0 or size > self.bytes_left:
            return False
        self.members_left -= 1
        self.bytes_left -= size
        return True

    @property
    def exhausted(self) -> bool:
        return self.members_left <= 0 or self.bytes_left <= 0


def _member_text(name: str, content: bytes, budget: _ArchiveBudget) -> str | None:
    """Текст одного элемента архива. Вложенный архив разворачивается тем же бюджетом, на
    уровень глубже."""

    extension = _extension_of(name)
    if extension in _ARCHIVE_EXTENSIONS:
        if budget.depth_left <= 0:
            return None
        budget.depth_left -= 1
        try:
            return _ARCHIVE_EXTRACTORS[extension](content, budget)
        finally:
            budget.depth_left += 1

    extractor = _EXTRACTORS.get(extension)
    return extractor(content) if extractor is not None else None


def _is_readable_member(name: str) -> bool:
    extension = _extension_of(name)
    return extension in _ARCHIVE_EXTENSIONS or extension in _EXTRACTORS


def _drop_duplicate_formats(names: list[str]) -> list[str]:
    """Из «ТЗ.docx» и «ТЗ.pdf» оставляет один — см. `_FORMAT_PREFERENCE`. Имена, чьё расширение
    в перечень предпочтений не входит (архивы), проходят как есть."""

    best: dict[str, str] = {}
    passthrough: list[str] = []
    for name in names:
        extension = _extension_of(name)
        if extension not in _FORMAT_PREFERENCE:
            passthrough.append(name)
            continue
        stem = name[: -len(extension)].lower() if extension else name.lower()
        current = best.get(stem)
        if current is None or _FORMAT_PREFERENCE.index(extension) < _FORMAT_PREFERENCE.index(
            _extension_of(current)
        ):
            best[stem] = name

    kept = set(best.values()) | set(passthrough)
    return [name for name in names if name in kept]


def _ocr_page(page) -> str | None:  # type: ignore[no-untyped-def]
    """OCR одной страницы PDF (раздел 5.2 ТЗ: скан без текстового слоя). Рендерит страницу в
    изображение через pypdfium2 (уже тянется как зависимость pdfplumber — без внешнего poppler)
    и распознаёт текст `pytesseract`/tesseract. Отсутствие бинарника tesseract на сервере или
    любая иная ошибка OCR — не повод ронять разбор всего документа, только эту страницу."""

    settings = get_settings()
    try:
        import pytesseract
    except ImportError:
        return None

    try:
        image = page.to_image(resolution=settings.ocr_resolution).original
        return pytesseract.image_to_string(image, lang=settings.ocr_languages).strip() or None
    except Exception as exc:  # noqa: BLE001 - OCR недоступен/не распознал — не должен ронять документ
        logger.warning(f"OCR страницы PDF не удался: {exc}")
        return None


def extract_pdf_text(content: bytes) -> str | None:
    import pdfplumber

    settings = get_settings()
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = (page.extract_text() or "").strip()
                if not text and settings.ocr_enabled:
                    text = _ocr_page(page) or ""
                pages_text.append(text)
        text = "\n".join(pages_text).strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - повреждённый/нестандартный PDF не должен падать весь опрос
        logger.warning(f"Не удалось извлечь текст из PDF: {exc}")
        raise


def extract_docx_text(content: bytes) -> str | None:
    """Текст docx в порядке документа: абзацы и таблицы вперемежку, как они идут в теле.

    Раньше все таблицы дописывались после всех абзацев. На проекте договора в сотню страниц
    «Таблица 3» с количеством приборов уезжала за двести тысяч символов от своего заголовка —
    за предел того, что анализ вообще читает, — а в техническом задании на месте таблицы
    оставалась пустота. Строка таблицы собирается в одну строку текста через « | »: «Класс
    точности | 1,0» модель читает как пару «параметр — значение», а те же ячейки в столбик —
    как два не связанных обрывка. Объединённые ячейки python-docx отдаёт по разу на каждую
    поглощённую колонку — повторы в строке схлопываются.
    """

    document = DocxDocument(io.BytesIO(content))
    body = document.element.body
    lines: list[str] = []
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(child, document).text
            if text:
                lines.append(text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells: list[str] = []
                for cell in row.cells:
                    value = " ".join(cell.text.split())
                    if value and (not cells or cells[-1] != value):
                        cells.append(value)
                if cells:
                    lines.append(" | ".join(cells))
    text = "\n".join(lines).strip()
    return text or None


def extract_xlsx_text(content: bytes) -> str | None:
    workbook = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    try:
        lines: list[str] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                values = [str(v) for v in row if v is not None]
                if values:
                    lines.append("\t".join(values))
        text = "\n".join(lines).strip()
        return text or None
    finally:
        workbook.close()


def extract_doc_text(content: bytes) -> str | None:
    """Word 97-2003. Основной формат документации на коммерческих площадках."""

    from app.services.doc_binary import extract_doc_text as parse_doc

    return parse_doc(content)


def extract_xls_text(content: bytes) -> str | None:
    """Excel 97-2003 — через `xlrd`: openpyxl старый двоичный формат не открывает вовсе."""

    import xlrd

    book = xlrd.open_workbook(file_contents=content)
    lines: list[str] = []
    for sheet in book.sheets():
        for row_index in range(sheet.nrows):
            values = [
                str(cell.value).strip()
                for cell in sheet.row(row_index)
                if str(cell.value).strip()
            ]
            if values:
                lines.append("\t".join(values))
    text = "\n".join(lines).strip()
    return text or None


def extract_rtf_text(content: bytes) -> str | None:
    from striprtf.striprtf import rtf_to_text

    # RTF — текстовый формат, но кодировка объявляется внутри; cp1251 покрывает документы,
    # которые встречаются у российских заказчиков, а errors="replace" не даёт упасть на
    # экзотике.
    raw = content.decode("cp1251", errors="replace")
    text = rtf_to_text(raw, errors="ignore").strip()
    return text or None


def extract_zip_text(content: bytes, budget: _ArchiveBudget | None = None) -> str | None:
    """Текст всех поддержанных документов внутри архива, склеенный в один.

    Половина площадок публикует комплект документации одним ZIP — раньше такой архив
    скачивался и оставался без текста, то есть тендер выглядел как «документов нет».

    Имена файлов внутри архивов из Windows обычно в cp866 и в UTF-8 выглядят мусором; для
    заголовков разделов они восстанавливаются, но на разбор это не влияет.
    """

    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Файл не является ZIP-архивом: {exc}") from exc

    budget = budget or _ArchiveBudget()
    parts: list[str] = []
    with archive:
        members = {
            _decode_archive_name(info): info
            for info in archive.infolist()
            if not info.is_dir() and info.file_size <= MAX_ARCHIVE_MEMBER_BYTES
        }
        names = _drop_duplicate_formats([n for n in members if _is_readable_member(n)])

        for name in names:
            if budget.exhausted:
                break
            info = members[name]
            if not budget.take(info.file_size):
                continue

            try:
                member_content = archive.read(info)
            except Exception as exc:  # noqa: BLE001 - битый элемент не должен терять остальные
                logger.warning(f"Не удалось прочитать «{name}» из архива: {exc}")
                continue

            try:
                member_text = _member_text(name, member_content, budget)
            except Exception as exc:  # noqa: BLE001 - см. выше
                logger.warning(f"Не удалось разобрать «{name}» из архива: {exc}")
                continue

            if member_text:
                parts.append(f"=== {name} ===\n{member_text}")

    text = "\n\n".join(parts).strip()
    return text or None


def extract_7z_text(content: bytes, budget: _ArchiveBudget | None = None) -> str | None:
    """Текст из 7z-архива. Формат встречается на Росэлторге и Lot-online, где им пакуют весь
    комплект документации — без распаковки такая закупка выглядит как «документов нет».

    `py7zr` — чистый Python, внешний бинарник (в отличие от RAR) не нужен.
    """

    import py7zr

    try:
        archive = py7zr.SevenZipFile(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - битый или зашифрованный архив
        raise ValueError(f"Не удалось открыть 7z-архив: {exc}") from exc

    budget = budget or _ArchiveBudget()
    parts: list[str] = []
    with archive:
        names = _drop_duplicate_formats(
            [name for name in archive.getnames() if _is_readable_member(name)]
        )[: budget.members_left]
        if not names:
            return None

        for name, buffer in (archive.read(names) or {}).items():
            if budget.exhausted:
                break
            member_content = buffer.read()
            if len(member_content) > MAX_ARCHIVE_MEMBER_BYTES or not budget.take(
                len(member_content)
            ):
                continue
            try:
                member_text = _member_text(name, member_content, budget)
            except Exception as exc:  # noqa: BLE001 - один элемент не должен терять остальные
                logger.warning(f"Не удалось разобрать «{name}» из 7z-архива: {exc}")
                continue
            if member_text:
                parts.append(f"=== {name} ===\n{member_text}")

    text = "\n\n".join(parts).strip()
    return text or None


def extract_rar_text(content: bytes, budget: _ArchiveBudget | None = None) -> str | None:
    """Текст из RAR-архива.

    RAR — закрытый формат, читать его чистым Python нечем (`rarfile` — обёртка над внешним
    распаковщиком). Зато `bsdtar` входит в состав macOS и большинства Linux-дистрибутивов и
    умеет RAR через libarchive, так что отдельной установки обычно не требуется. Если его в
    системе нет, документ просто останется без текста — как и раньше.
    """

    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if shutil.which("bsdtar") is None:
        logger.warning("bsdtar не найден — RAR-архив остаётся без извлечённого текста")
        return None

    budget = budget or _ArchiveBudget()
    with tempfile.TemporaryDirectory() as directory:
        archive_path = Path(directory) / "archive.rar"
        archive_path.write_bytes(content)
        target = Path(directory) / "unpacked"
        target.mkdir()

        result = subprocess.run(
            ["bsdtar", "-xf", str(archive_path), "-C", str(target)],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise ValueError(
                f"Не удалось распаковать RAR: {result.stderr.decode('utf-8', 'replace')[:200]}"
            )

        paths = {
            str(path.relative_to(target)): path
            for path in sorted(target.rglob("*"))
            if path.is_file() and path.stat().st_size <= MAX_ARCHIVE_MEMBER_BYTES
        }
        names = _drop_duplicate_formats([n for n in paths if _is_readable_member(n)])

        parts: list[str] = []
        for name in names:
            if budget.exhausted:
                break
            path = paths[name]
            if not budget.take(path.stat().st_size):
                continue
            try:
                member_text = _member_text(name, path.read_bytes(), budget)
            except Exception as exc:  # noqa: BLE001 - один элемент не должен терять остальные
                logger.warning(f"Не удалось разобрать «{path.name}» из RAR-архива: {exc}")
                continue
            if member_text:
                parts.append(f"=== {path.name} ===\n{member_text}")

    text = "\n\n".join(parts).strip()
    return text or None


def _decode_archive_name(info: zipfile.ZipInfo) -> str:
    """Имя элемента архива в читаемом виде.

    ZIP без флага UTF-8 хранит имена в кодировке OEM (у русской Windows — cp866), а zipfile
    декодирует их как cp437 — получается «Åα¿½«ªÑ¡¿Ñ» вместо «Приложение». Возвращаем обратно
    в байты и читаем правильной кодировкой.
    """

    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _extension_of(name: str) -> str:
    _, _, tail = name.rpartition(".")
    return f".{tail.lower()}" if tail and tail != name else ""


_EXTRACTORS = {
    ".pdf": extract_pdf_text,
    ".docx": extract_docx_text,
    ".xlsx": extract_xlsx_text,
    ".xlsm": extract_xlsx_text,
    ".doc": extract_doc_text,
    ".xls": extract_xls_text,
    ".rtf": extract_rtf_text,
    ".zip": extract_zip_text,
    ".7z": extract_7z_text,
    ".rar": extract_rar_text,
    ".txt": lambda content: content.decode("utf-8", errors="replace").strip() or None,
}

# Распаковщики вынесены в отдельный реестр: в отличие от разборщиков документов они принимают
# бюджет и вызывают друг друга рекурсивно.
_ARCHIVE_EXTRACTORS = {
    ".zip": extract_zip_text,
    ".7z": extract_7z_text,
    ".rar": extract_rar_text,
}

# Сигнатуры начала файла → расширение. Нужны, когда площадка отдаёт документ без внятного
# имени и без расширения в ссылке (так делает, например, часть коммерческих ЭТП): по одному
# лишь `Content-Type` формат не отличить — под `application/octet-stream` приходит что угодно.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", ".pdf"),
    (b"PK\x03\x04", ".zip"),  # docx/xlsx — тоже zip, уточняется ниже по содержимому
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"),  # OLE2: doc/xls, уточняется ниже
    (b"{\\rtf", ".rtf"),
    (b"Rar!\x1a\x07", ".rar"),
    (b"7z\xbc\xaf\x27\x1c", ".7z"),
)


def sniff_extension(content: bytes) -> str | None:
    """Расширение по содержимому файла. `None` — формат не распознан."""

    if not content:
        return None

    for signature, extension in _SIGNATURES:
        if not content.startswith(signature):
            continue
        if extension == ".zip":
            return _refine_zip(content)
        if extension == ".doc":
            return _refine_ole(content)
        return extension
    return None


def _refine_zip(content: bytes) -> str:
    """docx, xlsx и обычный архив физически одинаковы (ZIP) — различаем по внутренним файлам."""

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile:
        return ".zip"

    if "word/document.xml" in names:
        return ".docx"
    if "xl/workbook.xml" in names:
        return ".xlsx"
    return ".zip"


def _refine_ole(content: bytes) -> str:
    """OLE-контейнер — это и .doc, и .xls; различаем по имени потока внутри."""

    try:
        import olefile

        with olefile.OleFileIO(io.BytesIO(content)) as ole:
            if ole.exists("Workbook") or ole.exists("Book"):
                return ".xls"
            if ole.exists("WordDocument"):
                return ".doc"
    except Exception as exc:  # noqa: BLE001 - не распознали контейнер — пусть остаётся .doc
        logger.warning(f"Не удалось уточнить тип OLE-документа: {exc}")
    return ".doc"


def extract_text(file_extension: str | None, content: bytes) -> str | None:
    """Возвращает извлечённый текст либо `None`, если формат не поддержан (не ошибка).
    Поднимает исключение, если извлечение для поддержанного формата не удалось —
    вызывающий код (`document_service.py`) решает, как это залогировать.

    Если расширение не задано или не распознано, тип определяется по содержимому: площадки
    нередко отдают файл по ссылке без расширения.
    """

    extension = (file_extension or "").lower()
    extractor = _EXTRACTORS.get(extension)

    if extractor is None:
        sniffed = sniff_extension(content)
        if sniffed is None:
            return None
        extractor = _EXTRACTORS.get(sniffed)
        if extractor is None:
            return None

    return extractor(content)
