"""Детализация показателей «Рынка ПУ»: из чего сложилось число (замечания 09.11.2026).

Клик по любому итоговому числу отчёта открывает перечень позиций, вошедших в расчёт, и
позиций, исключённых из него, — с причиной, учтённым значением, применёнными правилами и
документом-основанием (запись поверки ФГИС «Аршин», описание типа ГРСИ, запись РПП ГИСП).

Позиция «Общей информации», «Исполнений» и «Групп компаний» — вариант написания модификации
(уровень, на котором поверки сводятся со справочником); позиция «Локализации» — исполнение
(уровень записи РПП). Учтённые значения считаются теми же функциями, что и сам отчёт
(`_keep`, `localization_ledger`), поэтому сумма «учтено» всегда равна числу, по которому
кликнули, — это проверяет тест.

Исключено из «Общей информации»: непригодные и аннулированные поверки (их нет в помесячном
агрегате) и типы, не включённые в отчёт (правило 6 ТЗ). Из «Локализации»: поверки до внесения
в РПП, после окончания срока записи, без баллов, другие «требования года», исполнения не в РПП
и неразмеченные написания.

Ручная проверка: расхождение до `TOLERANCE_PCT` процентов не считается ошибкой алгоритма —
поверки публикуются во ФГИС с опозданием, и подсчёт, снятый в другой день, честно отличается
(сверка 3 кв. 2026: 802 из 1 013 модификаций — в пределах 2 %, раздел 79 ARCHITECTURE.md).
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.meter_market import DESIGN_LABELS
from app.services import meter_market_report as report

TOLERANCE_PCT = 2.0
LATE_PUBLICATION = (
    "поверки публикуются во ФГИС с опозданием (в октябре 2026 пришло 92 тыс. поверок 3 квартала): "
    "подсчёт, снятый в другой день, отличается"
)
MAPPED_BY = {
    "import": "по шаблону из справочника аналитика",
    "auto": "автоматически — тот же ключ написания, что у размеченного",
    "manual": "вручную аналитиком",
}
REASON_UNFIT = "СИ признано непригодным — поверка не учитывается (правило 2 ТЗ)"
REASON_ANNULLED = "запись аннулирована во ФГИС"
REASON_NOT_IN_REPORT = "тип ГРСИ не включён в отчёт (правило 6 ТЗ)"
REASON_NOT_IN_RPP = "исполнение не внесено в РПП"
REASON_UNMAPPED_LOC = "написание не размечено — не отнесено ни к одному исполнению"


def _design(value: str | None) -> str:
    return report.NO_DATA if not value or value == "nd" else DESIGN_LABELS.get(value, report.NO_DATA)


def _position(row: dict[str, Any], names: dict[int, str], limits: list[tuple[int, float]]) -> dict[str, Any]:
    """Общие поля позиции: исходные данные, результат сопоставления, правила, документы."""

    unmapped = row.get("mod_id") is None
    smart_key, smart_status, smart_reason = report.smart_verdict(
        unmapped, row.get("smart"), row.get("smart_890_source"), row.get("smart_890_note"), row.get("smart_890_at")
    )
    score = row.get("score")
    year = report.requirement_year(score, limits) if row.get("in_rpp") else None
    rules = [f"Тип ГРСИ {row['mit_number']} {'включён' if row.get('in_report', True) else 'не включён'} в отчёт (правило 6 ТЗ)"]
    if unmapped:
        rules.append("Написание не сопоставлено с модификацией — исполнение, фаза и интерфейс: нет данных")
    else:
        how = MAPPED_BY.get(row.get("mapped_by") or "", "по справочнику")
        rules.append(f"Написание сопоставлено с модификацией «{row['mod_name']}» {how}")
    rules.append(
        "Производитель переопределён на модификации"
        if row.get("override_id")
        else "Производитель — владелец описания типа (правило 1 ТЗ)"
    )
    rules.append(f"ПП 890: {smart_status} — {smart_reason}")
    if unmapped:
        rules.append("РПП: нет данных — написание не размечено")
    elif row.get("in_rpp"):
        rules.append(
            f"РПП: {score:g} баллов — требования {year} г." if score is not None and year
            else f"РПП: {score:g} баллов — ниже порога любого года" if score is not None
            else "РПП: внесено, баллы не указаны"
        )
    else:
        rules.append("РПП: не внесено")
    documents = [{"label": f"Описание типа ГРСИ {row['mit_number']}" + (f" — {row['notation']}" if row.get("notation") else "")}]
    if row.get("rpp_record_number"):
        documents.append({"label": f"Запись РПП № {row['rpp_record_number']}"})
    return {
        "type_id": row["type_id"],
        "mit_number": row["mit_number"],
        "notation": row.get("notation"),
        "type_title": row.get("title"),
        "modification_id": row.get("mod_id"),
        "modification": row.get("mod_name"),
        "manufacturer": names.get(row.get("manufacturer_id"), "Производитель не указан"),
        "phase": report.NO_DATA if unmapped else (row.get("phase") or report.NO_DATA),
        "design": report.NO_DATA if unmapped else _design(row.get("design")),
        "interface": report.NO_DATA if unmapped else (row.get("interface") or report.NO_DATA),
        "smart": {"key": smart_key, "status": smart_status, "reason": smart_reason},
        "rpp": {
            "in_rpp": bool(row.get("in_rpp")) if not unmapped else None,
            "score": score,
            "requirement_year": year,
            "included_at": row.get("rpp_included_at"),
            "record_number": row.get("rpp_record_number"),
        },
        "rules": rules,
        "documents": documents,
    }


_ROW_SQL = (
    "SELECT v.id AS variant_id, v.raw, v.mapped_by, t.id AS type_id, t.mit_number, t.notation, t.title, "
    "t.in_report, m.id AS mod_id, m.name AS mod_name, m.phase, m.design, m.interface, m.in_rpp, m.score, "
    "m.rpp_included_at, m.rpp_record_number, m.smart_890 AS smart, m.smart_890_source, m.smart_890_note, "
    "m.smart_890_at, m.manufacturer_override_id AS override_id, (v.modification_id IS NULL) AS unmapped, "
    f"{report._MANUFACTURER_SQL} AS manufacturer_id, extract(year FROM mv.month)::int AS year, "
    "sum(mv.count) AS count "
    "FROM meter_verification_monthly mv "
    "JOIN meter_market_variants v ON v.id = mv.variant_id "
    "JOIN meter_market_types t ON t.id = v.type_id "
    "LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
)
_ROW_GROUP = " GROUP BY " + ", ".join(str(i) for i in range(1, 26))


def _summary(included: int, reasons: dict[tuple[str, bool], int]) -> list[dict[str, Any]]:
    return [
        {"reason": reason, "included": inc, "count": count}
        for (reason, inc), count in sorted(reasons.items(), key=lambda kv: (not kv[0][1], -kv[1]))
        if count
    ]


def explain_overview(db: Session, period: report.Period, filters: report.Filters, title: str = "") -> dict[str, Any]:
    """Детализация числа «Общей информации» (и ячеек «Исполнений», «Групп компаний»)."""

    names = report._manufacturer_names(db)
    limits = report.thresholds(db)
    period_sql, params = period.where()
    sql = _ROW_SQL + f"WHERE {period_sql}"
    if filters.manufacturer_id is not None:
        sql += f" AND {report._MANUFACTURER_SQL} = :mf"
        params["mf"] = filters.manufacturer_id
    rows = [dict(r) for r in db.execute(text(sql + _ROW_GROUP), params).mappings()]
    if filters.group is not None:
        groups, members = report._groups(db)
        for row in rows:
            row["group"] = report.group_label(row["manufacturer_id"], row["year"], names, groups, members)

    attribute_filters = any(v is not None for v in (filters.design, filters.phase, filters.interface, filters.smart))
    positions: dict[str, dict[str, Any]] = {}
    reasons: dict[tuple[str, bool], int] = defaultdict(int)
    for row in rows:
        count = int(row["count"])
        if row["in_report"]:
            if not report._keep(row, filters, names, ""):
                continue
            key = f"v{row['variant_id']}"
            if key not in positions:
                positions[key] = {
                    "key": key,
                    "variant_id": row["variant_id"],
                    "raw": row["raw"],
                    "mapped_by": row["mapped_by"],
                    **_position(row, names, limits),
                    "counted": 0,
                    "exclusions": defaultdict(int),
                    "basis": "пригодная неаннулированная поверка, тип в отчёте"
                    + ("; написание не размечено — атрибуты «нет данных»" if row["unmapped"] else ""),
                }
            positions[key]["counted"] += count
            reasons[(positions[key]["basis"], True)] += count
        elif not attribute_filters and (filters.group is None or row.get("group") == filters.group):
            # Тип не в отчёте: показываем одной позицией на тип — написаний у таких типов тысячи.
            key = f"t{row['type_id']}"
            if key not in positions:
                positions[key] = {
                    "key": key,
                    "variant_id": None,
                    "raw": None,
                    "mapped_by": None,
                    **_position({**row, "mod_id": None}, names, limits),
                    "counted": 0,
                    "exclusions": defaultdict(int),
                    "basis": REASON_NOT_IN_REPORT,
                }
            positions[key]["exclusions"][REASON_NOT_IN_REPORT] += count
            reasons[(REASON_NOT_IN_REPORT, False)] += count

    # Непригодные и аннулированные — по сырым поверкам тех же написаний за период.
    variant_ids = [p["variant_id"] for p in positions.values() if p["variant_id"] is not None]
    if variant_ids:
        raw_sql, raw_params = period.where("mv.verification_date")
        for variant_id, unfit, annulled in db.execute(
            text(
                "SELECT mv.variant_id, count(*) FILTER (WHERE NOT mv.applicable AND NOT mv.annulled), "
                "count(*) FILTER (WHERE mv.annulled) FROM meter_verifications mv "
                f"WHERE mv.variant_id = ANY(:ids) AND {raw_sql} GROUP BY 1"
            ),
            {"ids": variant_ids, **raw_params},
        ):
            position = positions[f"v{variant_id}"]
            for reason, value in ((REASON_UNFIT, unfit), (REASON_ANNULLED, annulled)):
                if value:
                    position["exclusions"][reason] += int(value)
                    reasons[(reason, False)] += int(value)

    return _result(
        title or "Поверенные счётчики ЭЭ",
        period.label,
        positions,
        reasons,
        {"period": period.label},
    )


def explain_localization(
    db: Session,
    edge: date,
    *,
    manufacturer_id: int | None = None,
    score: float | None = None,
    quarter: str | None = None,
    requirement: int | None = None,
    title: str = "",
) -> dict[str, Any]:
    """Детализация ячейки «Локализации»: производитель × баллы × квартал (любое — «все»)."""

    names = report._manufacturer_names(db)
    limits = report.thresholds(db)
    pieces, mods, _ = report.localization_ledger(db, edge, manufacturer_id=manufacturer_id)
    positions: dict[str, dict[str, Any]] = {}
    reasons: dict[tuple[str, bool], int] = defaultdict(int)

    def position(piece: report.LocPiece) -> dict[str, Any]:
        type_id = piece.unmapped_type_id
        key = f"u{type_id}" if type_id else f"m{piece.modification_id}"
        if key not in positions:
            positions[key] = {"key": key, "modification_id": piece.modification_id, "unmapped_type_id": type_id,
                              "counted": 0, "exclusions": defaultdict(int), "records": set(), "basis": ""}
        return positions[key]

    # Неразмеченные написания этих типов учтены бухгалтерией (общая запись РПП исполнений).
    inherited_types = sorted({p.unmapped_type_id for p in pieces if p.unmapped_type_id})

    for piece in pieces:
        if quarter and piece.quarter != quarter:
            continue
        verdict = piece.verdict
        if verdict.included:
            if score is not None and verdict.score != score:
                continue  # другая строка таблицы, а не исключение
            mismatch = report.requirement_reason(verdict.score, requirement, limits) if requirement else None
            item = position(piece)
            if verdict.record:
                item["records"].add(verdict.record.label() + (f", {verdict.score:g} баллов" if verdict.score is not None else ""))
            if mismatch:
                item["exclusions"][mismatch] += piece.count
                reasons[(mismatch, False)] += piece.count
            else:
                item["counted"] += piece.count
                item["basis"] = verdict.reason
                reasons[(verdict.reason, True)] += piece.count
        elif score is None:
            item = position(piece)
            if verdict.record:
                item["records"].add(verdict.record.label())
            item["exclusions"][verdict.reason] += piece.count
            reasons[(verdict.reason, False)] += piece.count

    # Строка производителя или итог: что не попало в локализацию совсем.
    start = date(report.LOCALIZATION_FROM_YEAR, 1, 1)
    q_start, q_end = start, edge
    if quarter:
        year, number = (int(x) for x in quarter.split("-"))
        q_start = date(year, (number - 1) * 3 + 1, 1)
        q_end = report._add_months(q_start, 3)
    if score is None:
        manufacturer_sql = f" AND {report._MANUFACTURER_SQL} = :mf" if manufacturer_id is not None else ""
        for mod_id, type_id, unmapped, count in db.execute(
            text(
                "SELECT v.modification_id, v.type_id, (v.modification_id IS NULL), sum(mv.count) "
                f"{report._BASE_FROM} WHERE mv.month >= :s AND mv.month < :e "
                f"AND ((v.modification_id IS NULL AND NOT v.type_id = ANY(:inherited)) OR NOT m.in_rpp)"
                f"{manufacturer_sql} GROUP BY 1, 2, 3"
            ),
            {"s": q_start, "e": q_end, "mf": manufacturer_id, "inherited": inherited_types},
        ):
            reason = REASON_UNMAPPED_LOC if unmapped else REASON_NOT_IN_RPP
            key = f"u{type_id}" if unmapped else f"m{mod_id}"
            item = positions.setdefault(
                key,
                {"key": key, "modification_id": mod_id, "unmapped_type_id": type_id if unmapped else None,
                 "counted": 0, "exclusions": defaultdict(int), "records": set(), "basis": ""},
            )
            item["exclusions"][reason] += int(count)
            reasons[(reason, False)] += int(count)

    # Сведения об исполнениях одним запросом.
    mod_ids = [p["modification_id"] for p in positions.values() if p.get("modification_id")]
    type_ids = [p["unmapped_type_id"] for p in positions.values() if p.get("unmapped_type_id")]
    info: dict[int, dict[str, Any]] = {}
    if mod_ids:
        for row in db.execute(
            text(
                "SELECT m.id AS mod_id, m.name AS mod_name, m.phase, m.design, m.interface, m.in_rpp, m.score, "
                "m.rpp_included_at, m.rpp_record_number, m.smart_890 AS smart, m.smart_890_source, "
                "m.smart_890_note, m.smart_890_at, m.manufacturer_override_id AS override_id, t.id AS type_id, "
                f"t.mit_number, t.notation, t.title, t.in_report, {report._MANUFACTURER_SQL} AS manufacturer_id, "
                "(SELECT string_agg(v.raw, ' · ' ORDER BY v.id) FROM meter_market_variants v "
                " WHERE v.modification_id = m.id) AS raw, "
                "(SELECT min(v.mapped_by) FROM meter_market_variants v WHERE v.modification_id = m.id) AS mapped_by "
                "FROM meter_market_modifications m JOIN meter_market_types t ON t.id = m.type_id "
                "WHERE m.id = ANY(:ids)"
            ),
            {"ids": mod_ids},
        ).mappings():
            info[row["mod_id"]] = dict(row)
    type_info: dict[int, dict[str, Any]] = {}
    if type_ids:
        for row in db.execute(
            text(
                "SELECT t.id AS type_id, t.mit_number, t.notation, t.title, t.manufacturer_id, "
                "(SELECT string_agg(v.raw, ' · ' ORDER BY v.id) FROM meter_market_variants v "
                " WHERE v.type_id = t.id AND v.modification_id IS NULL) AS raw "
                "FROM meter_market_types t WHERE t.id = ANY(:ids)"
            ),
            {"ids": type_ids},
        ).mappings():
            type_info[row["type_id"]] = dict(row)

    for item in positions.values():
        if item.get("unmapped_type_id"):
            source = {**type_info[item["unmapped_type_id"]], "mod_id": None}
        else:
            source = info[item["modification_id"]]
        records = sorted(item.pop("records"))
        item.update({k: v for k, v in _position(source, names, limits).items()})
        item["raw"] = source.get("raw")
        item["mapped_by"] = source.get("mapped_by")
        item["variant_id"] = None
        if records:
            item["documents"] = item["documents"][:1] + [{"label": r} for r in records]
        if item.get("unmapped_type_id") and item["counted"]:
            item["rules"].append("Исполнение по написанию не определить, но все исполнения типа внесены в РПП "
                                 "с одинаковыми баллами — поверки учтены по их общей записи")
        item["rules"].append("Точка отсчёта — дата внесения исполнения в РПП (правило 4 ТЗ); поверка "
                             "относится к записи РПП с баллами, действующей на её дату")
        if not item["basis"]:
            item["basis"] = next(iter(item["exclusions"]), "")

    label = report.quarter_label(*(int(x) for x in quarter.split("-"))) + " г." if quarter else (
        f"{report.LOCALIZATION_FROM_YEAR} г. – {report._edge_label(edge)}"
    )
    return _result(title or "Локализованные ПУ ЭЭ", label, positions, reasons, {"requirement": requirement})


def _result(
    title: str, period_label: str, positions: dict[str, dict[str, Any]], reasons: dict[tuple[str, bool], int],
    scope: dict[str, Any],
) -> dict[str, Any]:
    items = []
    for item in positions.values():
        exclusions = [{"reason": r, "count": c} for r, c in sorted(item["exclusions"].items(), key=lambda kv: -kv[1]) if c]
        if not item["counted"] and not exclusions:
            continue
        items.append({**item, "exclusions": exclusions, "excluded": sum(e["count"] for e in exclusions)})
    items.sort(key=lambda p: (-p["counted"], -p["excluded"]))
    included = sum(p["counted"] for p in items)
    return {
        "title": title,
        "period": period_label,
        "scope": scope,
        "value": included,
        "excluded": sum(p["excluded"] for p in items),
        "reasons": _summary(included, reasons),
        "positions": items,
        "tolerance_pct": TOLERANCE_PCT,
        "late_publication": LATE_PUBLICATION,
    }


# ── Выгрузка детализации в Excel ───────────────────────────────────────────────────────────

_HEADER = [
    "Производитель", "ГРСИ", "Тип СИ", "Модификация (результат сопоставления)", "Написание во ФГИС",
    "Тип ПУ", "Исполнение", "Интерфейс", "Учтено", "Исключено", "Основание включения / причина исключения",
    "ПП 890", "ПП 890: причина", "РПП, баллы", "Требования года", "Документ-основание", "Применённые правила",
]


def _line(p: dict[str, Any], counted: int, excluded: int, reason: str) -> list[Any]:
    return [
        p["manufacturer"], p["mit_number"], p.get("notation") or "", p.get("modification") or report.NO_DATA,
        p.get("raw") or "", p["phase"], p["design"], p["interface"], counted, excluded, reason,
        p["smart"]["status"], p["smart"]["reason"],
        p["rpp"]["score"] if p["rpp"]["score"] is not None else "",
        p["rpp"]["requirement_year"] or "",
        "; ".join(d["label"] for d in p["documents"]),
        "\n".join(p["rules"]),
    ]


def to_xlsx(result: dict[str, Any], subtitle: str) -> bytes:
    """Три листа: «Учтено», «Исключено» (позиция × причина), «Итог по причинам»."""

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    title = f"{result['title']}, {result['period']}"

    def sheet(ws, header: list[str], rows: list[list[Any]]) -> None:
        ws.append([title])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([subtitle])
        ws.append([])
        ws.append(header)
        for cell in ws[4]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append(row)
        ws.auto_filter.ref = f"A4:{get_column_letter(len(header))}{max(ws.max_row, 4)}"
        ws.freeze_panes = "A5"
        for index, name in enumerate(header, start=1):
            width = max([len(str(name))] + [min(80, len(str(r[index - 1]))) for r in rows[:2000]])
            ws.column_dimensions[get_column_letter(index)].width = min(60, max(10, width + 2))
        for row in ws.iter_rows(min_row=5):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=isinstance(cell.value, str) and "\n" in cell.value)

    positions = result["positions"]
    included = workbook.active
    included.title = "Учтено"
    sheet(included, _HEADER, [_line(p, p["counted"], 0, p["basis"]) for p in positions if p["counted"]])
    excluded = workbook.create_sheet("Исключено")
    sheet(
        excluded,
        _HEADER,
        [_line(p, 0, e["count"], e["reason"]) for p in positions for e in p["exclusions"]],
    )
    summary = workbook.create_sheet("Итог по причинам")
    sheet(
        summary,
        ["Причина", "В расчёте", "Количество"],
        [[r["reason"], "учтено" if r["included"] else "исключено", r["count"]] for r in result["reasons"]]
        + [["Итого учтено", "", result["value"]], ["Итого исключено", "", result["excluded"]]],
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def file_name(result: dict[str, Any]) -> str:
    return re.sub(r'[\\/:*?"<>|]', " ", f"Рынок ПУ — детализация — {result['title']} — {result['period']}.xlsx")
