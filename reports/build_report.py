"""Сборка «Отчёта о выполненных работах» в Word по образцу _template_report.docx.

Из шаблона берутся стили, нумерация списков, колонтитул и настройки страницы;
document.xml генерируется заново из описания отчёта (python-файл с переменной REPORT).

    python3 reports/build_report.py reports/report_15.10.2026.py reports/Отчет.docx [pages.json]

pages.json — номера страниц разделов для оглавления ({"1. Введение": 3, …}),
их получает reports/toc_pages.applescript из Word.
"""
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
TEMPLATE = HERE / "_template_report.docx"

BORDER = "".join(
    f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="B4BCC8"/>' for s in ("top", "left", "bottom", "right")
)
CELL_MAR = ('<w:tcMar><w:top w:w="45" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
            '<w:bottom w:w="45" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar>')


def runs(text, base_rpr=""):
    """Текст с разметкой **жирный** → набор w:r."""
    out = []
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        rpr = base_rpr + ("<w:b/><w:bCs/>" if i % 2 else "")
        rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(out)


def para(ppr, body):
    return f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>"


def p_center(text, before=None, after=None, rpr=""):
    sp = ""
    if before is not None or after is not None:
        sp = "<w:spacing" + (f' w:before="{before}"' if before is not None else "") + \
             (f' w:after="{after}"' if after is not None else "") + "/>"
    return para(sp + '<w:jc w:val="center"/>', runs(text, rpr))


def p_body(text, keep_next=False):
    ppr = ("<w:keepNext/>" if keep_next else "") + \
        '<w:spacing w:after="140" w:line="340" w:lineRule="auto"/><w:ind w:firstLine="709"/><w:jc w:val="both"/>'
    return para(ppr, runs(text))


def p_li(text, level=0):
    ppr = (f'<w:pStyle w:val="a4"/><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="2"/></w:numPr>'
           '<w:spacing w:after="90" w:line="320" w:lineRule="auto"/><w:jc w:val="both"/>')
    return para(ppr, runs(text))


def p_heading(text, level):
    return para(f'<w:pStyle w:val="{level}"/>', runs(text))


def p_caption(text):
    return para('<w:spacing w:before="80" w:after="200" w:line="260" w:lineRule="auto"/><w:jc w:val="right"/>',
                runs(text, "<w:i/><w:iCs/><w:sz w:val=\"24\"/><w:szCs w:val=\"24\"/>"))


def table(widths, header, rows):
    total = sum(widths)
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)

    def cell(w, text, head):
        shd = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>' if head else ""
        rpr = ("<w:b/><w:bCs/>" if head else "") + '<w:sz w:val="24"/><w:szCs w:val="24"/>'
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/><w:tcBorders>{BORDER}</w:tcBorders>{shd}{CELL_MAR}'
                f"</w:tcPr><w:p>{runs(text, rpr)}</w:p></w:tc>")

    def row(cells, head):
        trpr = "<w:tblHeader/>" if head else "<w:cantSplit/>"
        return f"<w:tr><w:trPr>{trpr}</w:trPr>" + "".join(cell(w, t, head) for w, t in zip(widths, cells)) + "</w:tr>"

    borders = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                      for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return (f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/><w:tblBorders>{borders}</w:tblBorders>'
            '<w:tblCellMar><w:left w:w="10" w:type="dxa"/><w:right w:w="10" w:type="dxa"/></w:tblCellMar>'
            '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
            f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>" + row(header, True) + "".join(row(r, False) for r in rows) + "</w:tbl>")


def toc_line(text, page, level):
    ppr = ('<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9638"/></w:tabs>'
           '<w:spacing w:after="100" w:line="320" w:lineRule="auto"/>' + ('<w:ind w:left="567"/>' if level == 2 else ""))
    title = runs(text, "<w:b/><w:bCs/>" if level == 1 else "")
    return para(ppr, title + f'<w:r><w:tab/><w:t>{page}</w:t></w:r>')


def build(report, pages):
    title_rpr = '<w:b/><w:bCs/><w:color w:val="1F3864"/>'
    out = [
        p_center("ООО «МИРТЕК»", 600, 200),
        p_center("ОТЧЁТ О ВЫПОЛНЕННЫХ РАБОТАХ", 1800, 200, title_rpr + '<w:sz w:val="44"/><w:szCs w:val="44"/>'),
        p_center(f"Период работ: {report['period']}", after=1400),
    ]
    out += ['<w:p><w:pPr><w:spacing w:after="80"/><w:jc w:val="right"/></w:pPr></w:p>'] * 20
    out += [
        para('<w:spacing w:after="80"/><w:jc w:val="center"/>', runs(f"Исполнитель: {report['author']}")),
        para('<w:jc w:val="center"/>', runs(f"Дата составления: {report['date_text']}")),
        # Титул — отдельная секция без колонтитула, как в образце.
        '<w:p><w:pPr><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" '
        'w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/><w:cols w:space="720"/>'
        '<w:docGrid w:linePitch="360"/></w:sectPr></w:pPr></w:p>',
        p_center("СОДЕРЖАНИЕ", 200, 360, title_rpr + '<w:sz w:val="32"/><w:szCs w:val="32"/>'),
    ]
    for kind, *args in report["blocks"]:
        if kind in ("h1", "h2"):
            out.append(toc_line(args[0], pages.get(args[0], ""), 1 if kind == "h1" else 2))
    out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    n_table = 0
    for kind, *args in report["blocks"]:
        if kind == "h1":
            out.append(p_heading(args[0], "1"))
        elif kind == "h2":
            out.append(p_heading(args[0], "2"))
        elif kind == "p":
            out.append(p_body(args[0]))
        elif kind == "pk":  # абзац перед списком — держится вместе со следующим
            out.append(p_body(args[0], keep_next=True))
        elif kind == "li":
            out.append(p_li(args[0], args[1] if len(args) > 1 else 0))
        elif kind == "table":
            widths, header, rows, caption = args
            n_table += 1
            out.append(table(widths, header, rows))
            out.append(p_caption(f"Таблица {n_table} — {caption}"))
    return "".join(out)


def main():
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    pages = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {}
    ns = {}
    exec(src.read_text(encoding="utf-8"), ns)
    body = build(ns["REPORT"], pages)
    global REPORT_ISO
    REPORT_ISO = ns["REPORT"].get("iso_date", "2026-10-15") + "T09:00:00Z"

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(TEMPLATE) as z:
            z.extractall(tmp)
        doc_path = Path(tmp, "word/document.xml")
        xml = doc_path.read_text(encoding="utf-8")
        start = xml.index("<w:body>") + len("<w:body>")
        sect = xml.rindex("<w:sectPr")  # последняя секция: колонтитул и нумерация со 2-й страницы
        doc_path.write_text(xml[:start] + body + xml[sect:], encoding="utf-8")
        # Колонтитул — только номер страницы: без названия, даты и линии (просьба пользователя).
        ftr_path = Path(tmp, "word/footer1.xml")
        ftr = ftr_path.read_text(encoding="utf-8")
        rpr = '<w:rPr><w:color w:val="6B7280"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'
        page_p = ('<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
                  f'<w:r>{rpr}<w:t xml:space="preserve">Стр. </w:t></w:r>'
                  f'<w:r>{rpr}<w:fldChar w:fldCharType="begin"/></w:r>'
                  f'<w:r>{rpr}<w:instrText>PAGE</w:instrText></w:r>'
                  f'<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'
                  f'<w:r>{rpr}<w:t>2</w:t></w:r>'
                  f'<w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r></w:p>')
        ftr_path.write_text(ftr[:ftr.index("<w:p ")] + page_p + "</w:ftr>", encoding="utf-8")
        # Заголовок документа в свойствах файла.
        core = Path(tmp, "docProps/core.xml")
        c = core.read_text(encoding="utf-8")
        c = re.sub(r"(<dc:title>).*?(</dc:title>)", r"\g<1>Отчёт о выполненных работах\g<2>", c)
        # Даты создания/изменения — дата отчёта, а не шаблона.
        c = re.sub(r"(<dcterms:(?:created|modified)[^>]*>)[^<]*", r"\g<1>" + REPORT_ISO, c)
        core.write_text(c, encoding="utf-8")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(Path(tmp).rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(tmp).as_posix())
    print(dst)


if __name__ == "__main__":
    main()
