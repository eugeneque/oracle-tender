"""Сверка отчёта «Рынок ПУ» с квартальным файлом аналитика (раздел 79 ARCHITECTURE.md,
раздел 9 ТЗ).

Файл аналитика — строки «тип ГРСИ × сырое написание модификации → количество за квартал».
Сова считает то же по своим поверкам и сравнивает: итог, производителей, каждую строку. Любое
расхождение должно показываться конкретными записями ФГИС, поэтому к крупным расхождениям
прикладываются примеры поверок.

Запуск: `python -m app.services.meter_market_reconcile "<xlsx>" 2026-3 [ГГГГММДД] > сверка.md`.

Третий параметр — «состояние ФГИС на дату». Поверки публикуют с опозданием: за октябрь 2026
пришло 92 тыс. поверок 3 квартала, и файл аналитика (снятый в начале октября) с базой, догруженной
до конца месяца, честно не сойдётся. Если в `storage/fgis_poverki` лежат суточные файлы изменений
после этой даты, то, что пришло в них, вычитается (а аннулированное — возвращается) по каждой
модификации — так получается отчёт на дату файла аналитика.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.meter_market_import import read_rows
from app.services.meter_market_normalize import manufacturer_display_name, modification_key
from app.services.meter_market_report import Filters, Period, _add_months, overview


def _quarter(value: str) -> Period:
    year, quarter = (int(x) for x in value.split("-"))
    start = date(year, (quarter - 1) * 3 + 1, 1)
    return Period(start, _add_months(start, 3), f"{quarter} кв. {year} г.")


def sova_counts(db: Session, period: Period) -> dict[tuple[str, str], dict[str, int]]:
    """(ГРСИ, сырое написание) → пригодные неаннулированные, непригодные, аннулированные."""

    rows = db.execute(
        text(
            "SELECT t.mit_number, v.raw, "
            "count(*) FILTER (WHERE mv.applicable AND NOT mv.annulled), "
            "count(*) FILTER (WHERE NOT mv.applicable AND NOT mv.annulled), "
            "count(*) FILTER (WHERE mv.annulled) "
            "FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "JOIN meter_market_types t ON t.id = v.type_id "
            "WHERE mv.verification_date >= :s AND mv.verification_date < :e GROUP BY 1, 2"
        ),
        {"s": period.start, "e": period.end},
    ).all()
    return {(n, r): {"ok": ok, "unfit": unfit, "annulled": ann} for n, r, ok, unfit, ann in rows}


def samples(db: Session, mit_number: str, raw: str, period: Period, limit: int = 3) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT mv.global_id, o.title, mv.mi_number, mv.verification_date, mv.applicable, mv.annulled "
            "FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "JOIN meter_market_types t ON t.id = v.type_id LEFT JOIN meter_market_orgs o ON o.id = mv.org_id "
            "WHERE t.mit_number = :n AND v.raw = :r AND mv.verification_date >= :s AND mv.verification_date < :e "
            "ORDER BY mv.verification_date LIMIT :l"
        ),
        {"n": mit_number, "r": raw, "s": period.start, "e": period.end, "l": limit},
    ).mappings()
    return [dict(r) for r in rows]


def _mod_counts(db: Session, period: Period) -> dict[int | None, dict[str, int]]:
    """Модификация → пригодные/непригодные/аннулированные поверки за период (типы «в отчёте»)."""

    rows = db.execute(
        text(
            "SELECT v.modification_id, "
            "count(*) FILTER (WHERE mv.applicable AND NOT mv.annulled), "
            "count(*) FILTER (WHERE NOT mv.applicable AND NOT mv.annulled), "
            "count(*) FILTER (WHERE mv.annulled) "
            "FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report "
            "WHERE mv.verification_date >= :s AND mv.verification_date < :e GROUP BY 1"
        ),
        {"s": period.start, "e": period.end},
    ).all()
    return {m: {"ok": ok, "unfit": unfit, "annulled": ann} for m, ok, unfit, ann in rows}


def published_after(db: Session, period: Period, as_of: str) -> dict[int | None, int]:
    """Чистый вклад суточных файлов после `as_of` в поверки периода — по модификациям."""

    import csv
    import io
    import zipfile

    from app.services.meter_market_loader import _IDX, storage_dir

    variants = {
        (number, raw): mod_id
        for number, raw, mod_id in db.execute(
            text(
                "SELECT t.mit_number, v.raw, v.modification_id FROM meter_market_variants v "
                "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report"
            )
        )
    }
    start, end = period.start.isoformat(), period.end.isoformat()
    delta: dict[int | None, int] = defaultdict(int)
    for path in sorted(storage_dir().glob("poverki.delta.*--*.csv.zip")):
        if path.name.split("--")[1][:8] <= as_of:
            continue
        with zipfile.ZipFile(path) as zf:
            stream = io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="utf-8", newline="")
            for row in csv.reader(stream, delimiter=";"):
                if len(row) < 16 or row[_IDX["applicability"]] != "1":
                    continue
                key = (row[_IDX["mit_number"]].strip(), row[_IDX["mi_modification"]])
                if key not in variants or not (start <= row[_IDX["verification_date"]][:10] < end):
                    continue
                delta[variants[key]] += -1 if row[_IDX["ver_date_end"]].strip() else 1
    return delta


def reconcile(db: Session, source: Any, quarter: str, as_of: str | None = None) -> str:
    """Строка файла аналитика — это модификация справочника (её название — шаблон поиска, а не
    сырое написание), поэтому сравнение идёт по модификации: сумма строк файла, которые импорт
    свёл в неё, против поверок всех написаний, привязанных к ней в Сове."""

    period = _quarter(quarter)
    file_rows = read_rows(source)
    mod_of = {
        (number, raw): mod_id
        for number, raw, mod_id in db.execute(
            text(
                "SELECT t.mit_number, v.raw, v.modification_id FROM meter_market_variants v "
                "JOIN meter_market_types t ON t.id = v.type_id WHERE v.mapped_by = 'import'"
            )
        )
    }
    mods = {
        mod_id: (number, name, manufacturer)
        for mod_id, number, name, manufacturer in db.execute(
            text(
                "SELECT m.id, t.mit_number, m.name, mf.name FROM meter_market_modifications m "
                "JOIN meter_market_types t ON t.id = m.type_id "
                "LEFT JOIN meter_market_manufacturers mf ON mf.id = coalesce(m.manufacturer_override_id, t.manufacturer_id)"
            )
        )
    }
    counts = _mod_counts(db, period)
    report = overview(db, period, Filters())
    if as_of:
        for mod_id, value in published_after(db, period, as_of).items():
            counts.setdefault(mod_id, {"ok": 0, "unfit": 0, "annulled": 0})["ok"] -= value

    file_by_mod: dict[int | None, int] = defaultdict(int)
    seen: set[tuple[str, str]] = set()
    for row in file_rows:
        key = (row.mit_number, row.raw)
        if key in seen:  # дубль строки в файле (одна с количеством, другая без) — импорт свёл в одну
            file_by_mod[mod_of.get(key)] += row.count or 0
            continue
        seen.add(key)
        file_by_mod[mod_of.get(key)] += row.count or 0
    file_total = sum(file_by_mod.values())
    sova_total = sum(v["ok"] for v in counts.values())
    unmapped = counts.get(None, {"ok": 0})["ok"]
    out: list[str] = [
        f"# Сверка «Рынок ПУ» с файлом аналитика — {period.label}",
        "",
        f"Состояние ФГИС на {as_of[6:]}.{as_of[4:6]}.{as_of[:4]} (суточные файлы после этой даты вычтены)."
        if as_of
        else "Состояние ФГИС — по последнему загруженному пакету.",
        "",
        "| Что | Поверок |",
        "|---|---:|",
        f"| Файл аналитика (сумма «Количество») | {_n(file_total)} |",
        f"| Сова: типы «в отчёте», все написания | {_n(sova_total)} |",
        f"| — из них привязано к модификациям | {_n(sova_total - unmapped)} |",
        f"| — не размечено | {_n(unmapped)} |",
        f"| Сова: итог экрана «Общая информация» (на последний пакет) | {_n(report['total'])} |",
        "",
    ]

    by_manufacturer: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for mod_id, count in file_by_mod.items():
        name = mods.get(mod_id, ("", "", "не сведено"))[2] or "не указан"
        by_manufacturer[name][0] += count
    for mod_id, value in counts.items():
        if mod_id is None:
            continue
        name = mods.get(mod_id, ("", "", "не указан"))[2] or "не указан"
        by_manufacturer[name][1] += value["ok"]
    unmapped_by_manufacturer = dict(
        db.execute(
            text(
                "SELECT mf.name, count(*) FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
                "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report "
                "LEFT JOIN meter_market_manufacturers mf ON mf.id = t.manufacturer_id "
                "WHERE v.modification_id IS NULL AND mv.applicable AND NOT mv.annulled "
                "AND mv.verification_date >= :s AND mv.verification_date < :e GROUP BY 1"
            ),
            {"s": period.start, "e": period.end},
        ).all()
    )
    out += [
        "## По производителям",
        "",
        "| Производитель | Файл | Сова, размечено | Разница | Сова, не размечено |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, (file_count, sova) in sorted(by_manufacturer.items(), key=lambda kv: -kv[1][0]):
        out.append(
            f"| {name} | {_n(file_count)} | {_n(sova)} | {sova - file_count:+,} | {_n(unmapped_by_manufacturer.get(name, 0))} |".replace(",", " ")
        )
    out.append("")

    diffs = []
    for mod_id in set(file_by_mod) | {m for m in counts if m is not None}:
        if mod_id is None:
            continue
        file_count = file_by_mod.get(mod_id, 0)
        got = counts.get(mod_id, {"ok": 0, "unfit": 0, "annulled": 0})
        diffs.append((mod_id, file_count, got))
    exact = sum(1 for _, f, g in diffs if f == g["ok"])
    within = sum(1 for _, f, g in diffs if abs(f - g["ok"]) <= max(5, 0.02 * f))
    diffs.sort(key=lambda d: -abs(d[2]["ok"] - d[1]))
    out += [
        f"## Модификации: точно совпали {exact} из {len(diffs)}, в пределах 2 % — {within}",
        "",
        "| ГРСИ | Модификация | Файл | Сова | Разница | Непригодных | Аннулировано |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for mod_id, file_count, got in diffs[:40]:
        number, name, _ = mods.get(mod_id, ("?", "?", ""))
        out.append(
            f"| {number} | `{name}` | {_n(file_count)} | {_n(got['ok'])} | {got['ok'] - file_count:+,} | {got['unfit']} | {got['annulled']} |".replace(",", " ")
        )
    out.append("")

    top_unmapped = db.execute(
        text(
            "SELECT t.mit_number, v.raw, count(*) FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report WHERE v.modification_id IS NULL "
            "AND mv.applicable AND NOT mv.annulled AND mv.verification_date >= :s AND mv.verification_date < :e "
            "GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25"
        ),
        {"s": period.start, "e": period.end},
    ).all()
    out += ["## Неразмеченные написания с наибольшим числом поверок", "", "| ГРСИ | Написание | Поверок |", "|---|---|---:|"]
    out += [f"| {n} | `{raw!r}` | {_n(c)} |" for n, raw, c in top_unmapped]
    out.append("")
    return "\n".join(out)


def _n(value: int) -> str:
    return f"{value:,}".replace(",", " ")


if __name__ == "__main__":
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        print(reconcile(session, sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    finally:
        session.close()
