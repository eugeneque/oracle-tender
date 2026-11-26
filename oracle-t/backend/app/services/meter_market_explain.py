"""Детализация показателей «Рынка ПУ»: из чего сложилось число (замечания 09.11.2026).

Клик по любому итоговому числу отчёта открывает перечень позиций, вошедших в расчёт, и
позиций, исключённых из него, — с причиной, учтённым значением, применёнными правилами и
документом-основанием (запись поверки ФГИС «Аршин», описание типа ГРСИ, запись РПП ГИСП).

Позиция «Общей информации», «Исполнений» и «Групп компаний» — вариант написания модификации
(уровень, на котором поверки сводятся со справочником); позиция «Локализации» — исполнение
(уровень записи РПП). Учтённые значения считаются теми же функциями, что и сам отчёт
(`_keep`, `localization_ledger`), поэтому сумма «учтено» равна числу, по которому кликнули. Исключение —
правило локализации Энергомеры: в детализации дополнительно отсекаются написания без точного
SPDs, а их количество видно отдельной причиной на листе «Исключено» (раздел 130).

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
    "ai": "ИИ по структуре условного обозначения из описания типа",
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
    interface_rule = _interface_rule(row, unmapped)
    if interface_rule:
        rules.append(interface_rule)
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
        "variant_smart_source": row.get("variant_smart_source"),
        "variant_interface": {
            "source": row.get("variant_interface_source"),
            "note": row.get("interface_note"),
            "review": row.get("interface_review"),
            "suggested": row.get("interface_suggested"),
            "modification": row.get("mod_interface"),
        },
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


def _interface_rule(row: dict[str, Any], unmapped: bool) -> str | None:
    """Откуда взят интерфейс позиции (раздел 122): решение аналитика, код в написании или модификация."""

    if unmapped or "variant_interface_source" not in row:
        return None
    if row.get("variant_interface_source") == "manual":
        return f"Интерфейс: {row.get('interface') or report.NO_DATA} — решение аналитика по этому написанию"
    if row.get("interface_review"):
        return (
            f"Интерфейс: на согласовании ({row['interface_review']}); предложение Совы — "
            f"{row.get('interface_suggested') or 'нет'}, до решения — интерфейс модификации «{row.get('mod_interface') or report.NO_DATA}»"
        )
    if row.get("variant_interface_source") == "rule":
        return f"Интерфейс: {row.get('interface')} — {row.get('interface_note')}"
    return "Интерфейс — из модификации справочника (у типа нет таблицы кодов интерфейсов)"


_ROW_SQL = (
    "SELECT v.id AS variant_id, v.raw, v.mapped_by, t.id AS type_id, t.mit_number, t.notation, t.title, "
    f"t.in_report, m.id AS mod_id, m.name AS mod_name, m.phase, m.design, {report.INTERFACE_SQL} AS interface, m.in_rpp, m.score, "
    f"m.rpp_included_at, m.rpp_record_number, {report.SMART_SQL} AS smart, {report.SMART_SOURCE_SQL} AS smart_890_source, "
    f"{report.SMART_NOTE_SQL} AS smart_890_note, "
    f"{report.SMART_AT_SQL} AS smart_890_at, m.manufacturer_override_id AS override_id, (v.modification_id IS NULL) AS unmapped, "
    f"{report._MANUFACTURER_SQL} AS manufacturer_id, extract(year FROM mv.month)::int AS year, "
    "v.smart_890_source AS variant_smart_source, v.interface_source AS variant_interface_source, "
    "v.interface_note, v.interface_review, v.interface_suggested, m.interface AS mod_interface, sum(mv.count) AS count "
    "FROM meter_verification_monthly mv "
    "JOIN meter_market_variants v ON v.id = mv.variant_id "
    "JOIN meter_market_types t ON t.id = v.type_id "
    "LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
)
_ROW_GROUP = " GROUP BY " + ", ".join(str(i) for i in range(1, 32))

# Сведения о написании без агрегата поверок — для детализации «Локализации» по написаниям (24.11.2026).
_VARIANT_INFO_SQL = (
    "SELECT v.id AS variant_id, v.raw, v.mapped_by, t.id AS type_id, t.mit_number, t.notation, t.title, "
    f"t.in_report, m.id AS mod_id, m.name AS mod_name, m.phase, m.design, {report.INTERFACE_SQL} AS interface, m.in_rpp, m.score, "
    f"m.rpp_included_at, m.rpp_record_number, {report.SMART_SQL} AS smart, {report.SMART_SOURCE_SQL} AS smart_890_source, "
    f"{report.SMART_NOTE_SQL} AS smart_890_note, {report.SMART_AT_SQL} AS smart_890_at, "
    f"m.manufacturer_override_id AS override_id, {report._MANUFACTURER_SQL} AS manufacturer_id, "
    "v.smart_890_source AS variant_smart_source, v.interface_source AS variant_interface_source, "
    "v.interface_note, v.interface_review, v.interface_suggested, m.interface AS mod_interface "
    "FROM meter_market_variants v JOIN meter_market_types t ON t.id = v.type_id "
    "LEFT JOIN meter_market_modifications m ON m.id = v.modification_id WHERE v.id = ANY(:ids)"
)


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
            if names.get(row["manufacturer_id"]) == "Энергомера" and "SPDs" not in row["raw"]:
                # Тот же признак локализации действует и в привычной детализации «Общей информации».
                positions[key]["exclusions"][report.REASON_NO_SPDS] += count
                reasons[(report.REASON_NO_SPDS, False)] += count
            else:
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
    """Детализация ячейки «Локализации»: производитель × баллы × квартал (любое — «все»).

    Строка — написание из ФГИС, а не модификация целиком (выбор аналитика 24.11.2026): ПП 890 и интерфейс
    теперь хранятся у написания (разделы 120, 122), и метка строки должна совпадать с основным отчётом.
    Неразмеченные написания типа с общей записью РПП — по-прежнему одной строкой на тип."""

    names = report._manufacturer_names(db)
    limits = report.thresholds(db)
    pieces, mods, _ = report.localization_ledger(db, edge, manufacturer_id=manufacturer_id, by_variant=True)
    positions: dict[str, dict[str, Any]] = {}
    reasons: dict[tuple[str, bool], int] = defaultdict(int)

    def position(piece: report.LocPiece) -> dict[str, Any]:
        type_id = piece.unmapped_type_id
        key = f"u{type_id}" if type_id else f"v{piece.variant_id}"
        if key not in positions:
            positions[key] = {"key": key, "modification_id": piece.modification_id, "unmapped_type_id": type_id,
                              "variant_id": piece.variant_id, "counted": 0, "exclusions": defaultdict(int),
                              "records": set(), "record_rows": {}, "applied_scores": set(),
                              "applied_years": set(), "basis": ""}
        return positions[key]

    def remember_verdict(item: dict[str, Any], piece: report.LocPiece, reason: str, included: bool) -> None:
        verdict = piece.verdict
        if verdict.score is not None:
            item["applied_scores"].add(verdict.score)
            year = report.requirement_year(verdict.score, limits)
            if year:
                item["applied_years"].add(year)
        if not verdict.record:
            return
        record = verdict.record
        item["records"].add(record.label() + (
            f", {verdict.score:g} баллов" if verdict.score is not None else ""
        ))
        usage = item["record_rows"].setdefault(record, {"counted": 0, "excluded": 0, "reasons": set()})
        usage["counted" if included else "excluded"] += piece.count
        if reason:
            usage["reasons"].add(reason)

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
            if mismatch:
                item["exclusions"][mismatch] += piece.count
                reasons[(mismatch, False)] += piece.count
                remember_verdict(item, piece, mismatch, False)
            else:
                item["counted"] += piece.count
                item["basis"] = verdict.reason
                reasons[(verdict.reason, True)] += piece.count
                remember_verdict(item, piece, verdict.reason, True)
        elif score is None:
            item = position(piece)
            item["exclusions"][verdict.reason] += piece.count
            reasons[(verdict.reason, False)] += piece.count
            remember_verdict(item, piece, verdict.reason, False)

    # Строка производителя или итог: что не попало в локализацию совсем.
    start = date(report.LOCALIZATION_FROM_YEAR, 1, 1)
    q_start, q_end = start, edge
    if quarter:
        year, number = (int(x) for x in quarter.split("-"))
        q_start = date(year, (number - 1) * 3 + 1, 1)
        q_end = report._add_months(q_start, 3)
    if score is None:
        manufacturer_sql = f" AND {report._MANUFACTURER_SQL} = :mf" if manufacturer_id is not None else ""
        for variant_id, mod_id, type_id, unmapped, count in db.execute(
            text(
                "SELECT CASE WHEN v.modification_id IS NULL THEN NULL ELSE v.id END, v.modification_id, v.type_id, "
                "(v.modification_id IS NULL), sum(mv.count) "
                f"{report._BASE_FROM} WHERE mv.month >= :s AND mv.month < :e "
                f"AND ((v.modification_id IS NULL AND NOT v.type_id = ANY(:inherited)) OR NOT m.in_rpp)"
                f"{manufacturer_sql} GROUP BY 1, 2, 3, 4"
            ),
            {"s": q_start, "e": q_end, "mf": manufacturer_id, "inherited": inherited_types},
        ):
            reason = REASON_UNMAPPED_LOC if unmapped else REASON_NOT_IN_RPP
            key = f"u{type_id}" if unmapped else f"v{variant_id}"
            item = positions.setdefault(
                key,
                {"key": key, "modification_id": mod_id, "unmapped_type_id": type_id if unmapped else None,
                 "variant_id": None if unmapped else variant_id, "counted": 0, "exclusions": defaultdict(int),
                 "records": set(), "record_rows": {}, "applied_scores": set(), "applied_years": set(),
                 "basis": ""},
            )
            item["exclusions"][reason] += int(count)
            reasons[(reason, False)] += int(count)

    # Сведения о написаниях одним запросом.
    variant_ids = [p["variant_id"] for p in positions.values() if p.get("variant_id")]
    type_ids = [p["unmapped_type_id"] for p in positions.values() if p.get("unmapped_type_id")]
    info: dict[int, dict[str, Any]] = {}
    if variant_ids:
        for row in db.execute(text(_VARIANT_INFO_SQL), {"ids": variant_ids}).mappings():
            info[row["variant_id"]] = dict(row)
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
            source = info[item["variant_id"]]
        records = sorted(item.pop("records"))
        item["applied_scores"] = sorted(item["applied_scores"])
        item["applied_years"] = sorted(item["applied_years"])
        item["rpp_records"] = [
            {
                "number": record.number,
                "issued": record.issued,
                "valid_to": record.valid_to,
                "score": record.score,
                "counted": usage["counted"],
                "excluded": usage["excluded"],
                "reasons": sorted(usage["reasons"]),
            }
            for record, usage in sorted(item.pop("record_rows").items(), key=lambda pair: pair[0].issued)
        ]
        item.update({k: v for k, v in _position(source, names, limits).items()})
        item["raw"] = source.get("raw")
        item["mapped_by"] = source.get("mapped_by")
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
    result = _result(title or "Локализованные ПУ ЭЭ", label, positions, reasons, {"requirement": requirement})
    result["rpp_reference"] = True
    return result


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
    "Комментарии",
]

_COUNTRY_COMMENTS = tuple(
    (re.compile(rf"(?<![A-Za-z]){code}(?![A-Za-z])|(?<=SPds){code}", re.IGNORECASE), comment)
    for code, comment in (("GE", "для Грузии"), ("UZ", "для Узбекистана"), ("KZ", "для Казахстана"))
)


def _export_comment(p: dict[str, Any]) -> str:
    """Страна — отдельным кодом в написании Энергомеры; регистр у поверителей непостоянный."""

    if p["manufacturer"] != "Энергомера":
        return ""
    raw = p.get("raw") or ""
    return "; ".join(comment for pattern, comment in _COUNTRY_COMMENTS if pattern.search(raw))


def _line(p: dict[str, Any], counted: int, excluded: int, reason: str) -> list[Any]:
    if "applied_scores" in p:
        scores = p["applied_scores"]
        years = p["applied_years"]
        score = scores[0] if len(scores) == 1 else "; ".join(f"{value:g}" for value in scores)
        requirement = years[0] if len(years) == 1 else "; ".join(map(str, years))
    else:
        score = p["rpp"]["score"] if p["rpp"]["score"] is not None else ""
        requirement = p["rpp"]["requirement_year"] or ""
    return [
        p["manufacturer"], p["mit_number"], p.get("notation") or "", p.get("modification") or report.NO_DATA,
        p.get("raw") or "", p["phase"], p["design"], p["interface"], counted, excluded, reason,
        p["smart"]["status"], p["smart"]["reason"], score, requirement,
        "; ".join(d["label"] for d in p["documents"]),
        "\n".join(p["rules"]),
        _export_comment(p),
    ]


def _rpp_reference_rows(positions: list[dict[str, Any]]) -> list[list[Any]]:
    """Одна строка на запись исполнения, даже если в отчёте у него несколько написаний."""

    rows: dict[tuple, dict[str, Any]] = {}
    for position in positions:
        for record in position.get("rpp_records", []):
            key = (
                position["type_id"], position["modification_id"], record["number"], record["issued"],
                record["valid_to"], record["score"],
            )
            item = rows.setdefault(key, {"position": position, "record": record, "counted": 0, "excluded": 0,
                                         "reasons": set()})
            item["counted"] += record["counted"]
            item["excluded"] += record["excluded"]
            item["reasons"].update(record["reasons"])
    result = []
    for item in sorted(
        rows.values(),
        key=lambda r: (r["position"]["manufacturer"], r["position"].get("modification") or "", r["record"]["issued"]),
    ):
        position, record = item["position"], item["record"]
        usage = []
        if item["counted"]:
            usage.append(f"учтено: {item['counted']}")
        if item["excluded"]:
            usage.append(f"исключено: {item['excluded']}")
        usage.extend(sorted(item["reasons"]))
        result.append([
            position["manufacturer"], position["mit_number"], position.get("notation") or "",
            position.get("modification") or report.NO_DATA, record["number"] or "не указан",
            "не указана" if record["issued"] == date.min else record["issued"].strftime("%d.%m.%Y"),
            record["valid_to"].strftime("%d.%m.%Y") if record["valid_to"] else "не ограничен",
            record["score"] if record["score"] is not None else "не указаны", "; ".join(usage),
        ])
    return result


def to_xlsx(result: dict[str, Any], subtitle: str) -> bytes:
    """Листы расчёта и, для локализации, справка о периодах и баллах записей РПП."""

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
    if result.get("rpp_reference"):
        reference = workbook.create_sheet("Записи РПП")
        sheet(
            reference,
            ["Производитель", "ГРСИ", "Тип СИ", "Модификация (результат сопоставления)",
             "Номер записи РПП", "Действует с", "Действует по", "Баллы", "Использование в отчёте"],
            _rpp_reference_rows(positions),
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def file_name(result: dict[str, Any]) -> str:
    return re.sub(r'[\\/:*?"<>|]', " ", f"Рынок ПУ — детализация — {result['title']} — {result['period']}.xlsx")
