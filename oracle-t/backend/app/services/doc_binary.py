"""Извлечение текста из документов Word 97-2003 (`.doc`) — формат [MS-DOC].

Зачем свой разбор. Документация тендеров на коммерческих площадках до сих пор массово
выкладывается в старом `.doc` (на ЭТП ГПБ — крупнейшем нашем источнике — это основной
формат), а без текста такой документ бесполезен: ИИ-анализ требований работать по нему не
может. Готовых решений три, и все не подошли:

- `antiword` / `catdoc` — внешние бинарники, которых больше нет в Homebrew (для разработки на
  macOS их пришлось бы собирать из исходников);
- LibreOffice в headless-режиме — полтора гигабайта в образе ради одного формата;
- пакеты PyPI, которые «умеют .doc», либо обёртки над теми же бинарниками, либо платные.

Поэтому — прямой разбор бинарного формата. Он документирован Microsoft ([MS-DOC]) и в части
извлечения текста несложен: нужно найти piece table и склеить куски текста, на которые она
указывает.

**Как устроен формат.** Файл `.doc` — это OLE-контейнер (Compound File), внутри которого
лежат потоки. Нужны два: `WordDocument` (в его начале — FIB, File Information Block) и
таблица (`1Table` или `0Table` — какая именно, сказано битом `fWhichTblStm` в FIB). В FIB по
смещению 0x01A2 лежат `fcClx`/`lcbClx` — где в таблице искать структуру CLX. Внутри CLX —
`PlcPcd`: массив позиций символов и дескрипторов кусков (PCD). Каждый PCD указывает смещение
в `WordDocument` и способ кодирования: старший бит смещения означает «текст однобайтовый»
(cp1251 для русских документов), иначе — UTF-16LE.

Такая «кусочная» раскладка — не прихоть формата: Word при редактировании дописывает текст в
конец потока, а порядок восстанавливает как раз piece table. Поэтому прочитать поток
`WordDocument` подряд и получить читаемый текст нельзя — куски пойдут в порядке правок, а не
в порядке документа.
"""

from __future__ import annotations

import io
import re
import struct

from loguru import logger

# Смещение fcClx в FIB и размер записи PCD — константы формата [MS-DOC].
_FIB_FC_CLX_OFFSET = 0x01A2
_PCD_SIZE = 8
_CP_SIZE = 4

# Служебные символы Word: маркеры полей, картинок, ячеек таблицы, конца секции. В тексте
# требований они мусор, но некоторые из них — реальные границы, поэтому часть заменяется на
# перевод строки, а не выбрасывается.
_CONTROL_TO_NEWLINE = {"\r", "\x07", "\x0b", "\x0c", "\x0e"}
_CONTROL_TO_DROP = {"\x01", "\x02", "\x05", "\x08", "\x13", "\x14", "\x15", "\x1e", "\x1f"}


class DocParseError(RuntimeError):
    """Файл не является .doc или его структура не разобрана."""


def _read_fib_clx(word_stream: bytes) -> tuple[int, int, bool]:
    """Возвращает (fcClx, lcbClx, использовать ли 1Table) из FIB."""

    if len(word_stream) < _FIB_FC_CLX_OFFSET + 8:
        raise DocParseError("Поток WordDocument короче, чем FIB")

    # Бит 9 флагов FIB (смещение 0x000A) — какой из двух потоков-таблиц актуален.
    flags = struct.unpack_from("<H", word_stream, 0x000A)[0]
    use_first_table = bool(flags & 0x0200)

    fc_clx, lcb_clx = struct.unpack_from("<II", word_stream, _FIB_FC_CLX_OFFSET)
    if lcb_clx == 0:
        raise DocParseError("В FIB нет piece table (lcbClx = 0)")
    return fc_clx, lcb_clx, use_first_table


def _extract_plcpcd(clx: bytes) -> bytes:
    """Вырезает PlcPcd из CLX.

    CLX — последовательность записей: 0x01 — Prc (свойства, пропускаем по длине), 0x02 —
    искомый PlcPcd, за маркером идёт его длина. Без этого прохода нельзя: количество и размер
    Prc-записей в начале произвольны.
    """

    offset = 0
    while offset < len(clx):
        marker = clx[offset]
        if marker == 0x01:
            if offset + 3 > len(clx):
                break
            size = struct.unpack_from("<H", clx, offset + 1)[0]
            offset += 3 + size
        elif marker == 0x02:
            size = struct.unpack_from("<I", clx, offset + 1)[0]
            start = offset + 5
            return clx[start : start + size]
        else:
            break
    raise DocParseError("В CLX не найден PlcPcd")


def _decode_piece(word_stream: bytes, fc: int, cp_length: int) -> str:
    """Декодирует один кусок текста.

    Старший бит fc (0x40000000) означает однобайтовую кодировку: Word так сжимает текст,
    который целиком укладывается в кодовую страницу. Для русских документов это cp1251 —
    именно поэтому нельзя декодировать всё как UTF-16 и наоборот.
    """

    if fc & 0x40000000:
        start = (fc & ~0x40000000) // 2
        raw = word_stream[start : start + cp_length]
        return raw.decode("cp1251", errors="replace")

    raw = word_stream[fc : fc + cp_length * 2]
    return raw.decode("utf-16-le", errors="replace")


def _clean(text: str) -> str:
    result = []
    for char in text:
        if char in _CONTROL_TO_NEWLINE:
            result.append("\n")
        elif char in _CONTROL_TO_DROP:
            continue
        elif char == "\x00":
            continue
        else:
            result.append(char)
    cleaned = "".join(result)
    # Word щедро расставляет неразрывные пробелы и мягкие переносы — в тексте требований они
    # мешают и поиску, и модели.
    cleaned = cleaned.replace("\xa0", " ").replace("\xad", "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_doc_text(content: bytes) -> str | None:
    """Текст из .doc (Word 97-2003). `None` — если текста в документе нет.

    Ошибку поднимает только когда файл не разобран: вызывающий код различает «формат не
    поддержан» (текста не будет никогда) и «поддержан, но не удалось» (стоит показать причину).
    """

    import olefile

    if not olefile.isOleFile(io.BytesIO(content)):
        raise DocParseError("Файл не является документом Word 97-2003 (не OLE-контейнер)")

    ole = olefile.OleFileIO(io.BytesIO(content))
    try:
        if not ole.exists("WordDocument"):
            raise DocParseError("В OLE-контейнере нет потока WordDocument")

        word_stream = ole.openstream("WordDocument").read()
        fc_clx, lcb_clx, use_first_table = _read_fib_clx(word_stream)

        table_name = "1Table" if use_first_table else "0Table"
        if not ole.exists(table_name):
            # Встречается у документов, сохранённых сторонними редакторами: бит указывает на
            # один поток, а в файле лежит другой. Берём тот, что есть, — структура одинаковая.
            fallback = "0Table" if use_first_table else "1Table"
            if not ole.exists(fallback):
                raise DocParseError(f"В документе нет потока таблицы ({table_name})")
            table_name = fallback

        table_stream = ole.openstream(table_name).read()
        clx = table_stream[fc_clx : fc_clx + lcb_clx]
        plcpcd = _extract_plcpcd(clx)

        # PlcPcd: (n+1) позиций символов по 4 байта, затем n дескрипторов по 8 байт.
        pieces_count = (len(plcpcd) - _CP_SIZE) // (_CP_SIZE + _PCD_SIZE)
        if pieces_count <= 0:
            raise DocParseError("PlcPcd пуст")

        cps = [
            struct.unpack_from("<I", plcpcd, i * _CP_SIZE)[0] for i in range(pieces_count + 1)
        ]
        pcd_start = (pieces_count + 1) * _CP_SIZE

        chunks: list[str] = []
        for index in range(pieces_count):
            pcd_offset = pcd_start + index * _PCD_SIZE
            fc = struct.unpack_from("<I", plcpcd, pcd_offset + 2)[0]
            cp_length = cps[index + 1] - cps[index]
            if cp_length <= 0:
                continue
            try:
                chunks.append(_decode_piece(word_stream, fc, cp_length))
            except Exception as exc:  # noqa: BLE001 - один битый кусок не должен терять документ
                logger.warning(f"Кусок .doc не декодирован: {exc}")

        text = _clean("".join(chunks))
        return text or None
    finally:
        ole.close()
