"""Выборка отсеянных профилями закупок для проверки специалистами (PLAN.md, ловушка 1).

    python -m evals.audit_sample --near 35 --random 15 --out audit_sample.xlsx [--days 90]

Профили отсеивают ~88% закупок, и почти всё отсеянное — заведомый мусор (еда, лекарства,
мебель). Чисто случайная выборка из него бесполезна: специалисту нечего отметить, а потерю
нужного она не покажет. Поэтому выборка двух видов:

* «пограничные» — в названии есть слова из ваших же профилей, но закупка не прошла (нет кода
  ОКПД2 при режиме «сужение», сработало слово-исключение). Здесь реальный риск потери нужного;
* «случайные» — малая доля из остального отсева, чтобы оценить фон: что мусор действительно
  мусор.

Тип выборки пишется в колонку: он нужен при подсчёте потерь (разные доли отсева). В выборке
не больше `--per-source` закупок с одной площадки. Только чтение.
"""

import argparse
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender
from app.services import relevance_service as rs

HEADERS = [
    "№", "Площадка", "Название закупки", "Заказчик", "Цена, ₽", "Дата публикации",
    "ОКПД2", "Ссылка", "Нужна нам? (Да / Нет)", "Комментарий (почему)",
    "Тип выборки", "Почему отсеяна системой", "ID (не менять)",
]
WIDTHS = [5, 16, 70, 36, 14, 14, 12, 40, 18, 40, 14, 36, 38]


def _keyword_only(profile) -> SimpleNamespace:
    """Копия профиля без кода ОКПД2 и исключений: «совпали ли слова» в чистом виде."""

    return SimpleNamespace(
        id=profile.id, name=profile.name, keywords=profile.keywords or [],
        exclusion_keywords=[], okpd2_codes=[], match_mode=profile.match_mode,
        okpd2_mode=rs.OKPD2_EITHER,
    )


def _take(items: list, n: int, per_source: int, rng: random.Random) -> list:
    """Случайные n элементов, не более per_source с одной площадки."""

    rng.shuffle(items)
    taken, count, seen = [], defaultdict(int), set()
    for item in items:
        source = item[1]
        title_key = " ".join(item[0][2].lower().split())[:80]  # одинаковые закупки — один раз
        if count[source] >= per_source or title_key in seen:
            continue
        seen.add(title_key)
        count[source] += 1
        taken.append(item)
        if len(taken) == n:
            break
    return taken


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--near", type=int, default=35, help="пограничных")
    ap.add_argument("--random", type=int, default=15, help="случайных из остального отсева")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--per-source", type=int, default=12)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", type=Path, default=Path("audit_sample.xlsx"))
    args = ap.parse_args()
    rng = random.Random(args.seed)

    since = date.today() - timedelta(days=args.days)
    with SessionLocal() as db:
        profiles = rs.default_profiles(db)
        rows = db.execute(
            select(
                Tender.id, Source.key, Tender.title, Tender.customer_name,
                Tender.procurement_method, Tender.okpd2_code, Tender.price,
                Tender.publish_date, Tender.source_url,
            )
            .join(Source, Source.id == Tender.source_id)
            .where(Tender.passed_relevance_filter.is_(False))
            .where(Source.key != "manual")
            .where(Tender.publish_date >= since)
        ).all()

        near, rest = [], []
        for row in rows:
            tid, source_key, title, customer, method, okpd2, price, pub, url = row
            tender = SimpleNamespace(
                title=title, customer_name=customer, procurement_method=method
            )
            tokens = rs.tokenize(rs.tender_text(tender))
            applicable = rs._applicable(profiles, source_key)
            hit = next(
                (p for p in applicable if rs.match_profile(tokens, _keyword_only(p)).matched),
                None,
            )
            if hit:
                real = rs.match_profile(tokens, hit, okpd2)
                # Исключение по «воде/газу/теплу» без слов про электричество в названии (не путать с «электронной формой») — задуманное сито, а не риск:
                # такие закупки специалисту не показываем (в 1-й версии они забили выборку).
                title_tokens = rs.tokenize(title)
                electric = any(t.startswith(("электрич", "электроэнерг", "электросч")) for t in title_tokens)
                by_design = real.reason.startswith("исключено") and not electric
                if by_design:
                    hit = None
            record = (row, source_key, hit)
            (near if hit else rest).append(record)

        chosen_near = _take(near, args.near, args.per_source, rng)
        chosen_rest = _take(rest, args.random, args.per_source, rng)

        wb = Workbook()
        ws = wb.active
        ws.title = "Отсеянные закупки"
        ws.append(HEADERS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8EEF7")
            cell.alignment = Alignment(wrap_text=True, vertical="center")

        i = 0
        for kind, chosen in (("пограничная", chosen_near), ("случайная", chosen_rest)):
            rng.shuffle(chosen)  # специалист не должен видеть «пограничные» блоком
        mixed = [(r, "пограничная") for r in chosen_near] + [
            (r, "случайная") for r in chosen_rest
        ]
        rng.shuffle(mixed)
        for (row, source_key, hit), kind in mixed:
            i += 1
            tid, _, title, customer, method, okpd2, price, pub, url = row
            reason = None
            if hit:
                profile_outcome = rs.match_profile(
                    rs.tokenize(rs.tender_text(
                        SimpleNamespace(title=title, customer_name=customer,
                                        procurement_method=method)
                    )),
                    hit, okpd2,
                )
                reason = f"профиль «{hit.name}»: {profile_outcome.reason}"
            ws.append([
                i, source_key, title, customer,
                float(price) if price is not None else None, pub, okpd2, url,
                None, None, kind, reason, str(tid),
            ])
        for idx, width in enumerate(WIDTHS, 1):
            ws.column_dimensions[ws.cell(1, idx).column_letter].width = width
        for row_cells in ws.iter_rows(min_row=2):
            for cell in row_cells:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        check = DataValidation(type="list", formula1='"Да,Нет"', allow_blank=True)
        ws.add_data_validation(check)
        check.add(f"I2:I{i + 1}")
        wb.save(args.out)

    print(
        f"Отсеяно за {args.days} дн.: {len(rows)}; из них пограничных (слова совпали): {len(near)}\n"
        f"В выгрузке: {len(chosen_near)} пограничных + {len(chosen_rest)} случайных → {args.out}"
    )


if __name__ == "__main__":
    main()
