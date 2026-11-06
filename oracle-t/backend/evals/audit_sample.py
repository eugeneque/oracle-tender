"""Выборка отсеянных профилями закупок для проверки специалистами (PLAN.md, ловушка 1).

    python -m evals.audit_sample --n 50 --out audit_sample.xlsx [--days 90]

Профили отсеивают ~88% закупок, и их никто не видит: без проверки случайной части этого потока
потерю нужного не измерить. Берутся закупки с `passed_relevance_filter = FALSE` за последние
`--days` дней (старые ссылки на площадках часто уже недоступны), без ручных заявок. Специалист
заполняет колонку «Нужна нам?» (Да / Нет) и по желанию комментарий. Идентификатор закупки — в
последней колонке, по нему ответы потом вносятся в эталон. Только чтение.
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender

HEADERS = [
    "№", "Площадка", "Название закупки", "Заказчик", "Цена, ₽", "Дата публикации",
    "ОКПД2", "Ссылка", "Нужна нам? (Да / Нет)", "Комментарий (почему)", "ID (не менять)",
]
WIDTHS = [5, 16, 70, 36, 14, 14, 12, 40, 18, 40, 38]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", type=Path, default=Path("audit_sample.xlsx"))
    args = ap.parse_args()

    since = date.today() - timedelta(days=args.days)
    with SessionLocal() as db:
        rows = db.execute(
            select(Tender, Source.key)
            .join(Source, Source.id == Tender.source_id)
            .where(Tender.passed_relevance_filter.is_(False))
            .where(Source.key != "manual")
            .where(Tender.publish_date >= since)
            .order_by(func.random())
            .limit(args.n)
        ).all()

        wb = Workbook()
        ws = wb.active
        ws.title = "Отсеянные закупки"
        ws.append(HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8EEF7")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for i, (tender, source_key) in enumerate(rows, 1):
            ws.append([
                i, source_key, tender.title, tender.customer_name,
                float(tender.price) if tender.price is not None else None,
                tender.publish_date, tender.okpd2_code, tender.source_url,
                None, None, str(tender.id),
            ])
        for idx, width in enumerate(WIDTHS, 1):
            ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        check = DataValidation(type="list", formula1='"Да,Нет"', allow_blank=True)
        ws.add_data_validation(check)
        check.add(f"I2:I{len(rows) + 1}")
        wb.save(args.out)
    print(f"Выгружено: {len(rows)} закупок за {args.days} дн. → {args.out}")


if __name__ == "__main__":
    main()
