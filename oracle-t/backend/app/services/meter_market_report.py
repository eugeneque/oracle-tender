"""Отчёт «Рынок ПУ» по поверкам ФГИС «Аршин» (раздел 79 ARCHITECTURE.md, раздел 8 ТЗ).

Четыре экрана повторяют страницы Power BI аналитика: общая информация и интерфейсы,
исполнения по годам, группы компаний по годам, локализация по кварталам. Все цифры — из
помесячного агрегата `meter_verification_monthly` (пригодные неаннулированные поверки по
варианту написания), соединённого со справочником: производитель — владелец описания типа
(или переопределение на модификации), атрибуты — у модификации. Только типы «в отчёте»
(правило 6 ТЗ).

Агрегат за период — несколько тысяч строк «производитель × исполнение × 1ф/3ф × интерфейс»,
поэтому перекрёстные фильтры (клик по производителю сужает остальные блоки, как в Power BI)
считаются в Python над этими строками, а не отдельным запросом на каждый блок.

Неразмеченные варианты не пропадают: их поверки видны строкой «нет данных» (раздел 6.3 ТЗ),
а их количество отдаётся отдельно — на экране видно, что итог неполный.

Статусов у признака три — «Соответствует», «Не соответствует», «Нет данных» (замечание
тестировщика 09.11.2026: «не определено» и «не размечено» отдельными значениями путали) — и у
«Нет данных» всегда есть причина: её показывает детализация (`meter_market_explain`).
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.meter_market import DESIGN_LABELS

# «Не размечено» и «н/д» в отчёте — одно значение «нет данных» (замечание 09.11.2026): причина
# (написание не размечено или признак пуст в справочнике) — в детализации показателя.
NO_DATA = "нет данных"
UNMAPPED = NO_DATA
FIRST_YEAR = 2021

_MANUFACTURER_SQL = "coalesce(m.manufacturer_override_id, t.manufacturer_id)"
_BASE_FROM = (
    "FROM meter_verification_monthly mv "
    "JOIN meter_market_variants v ON v.id = mv.variant_id "
    "JOIN meter_market_types t ON t.id = v.type_id AND t.in_report "
    "LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
)


# ── Период ─────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Period:
    """Полуинтервал месяцев `[start, end)` и подпись для заголовка."""

    start: date
    end: date
    label: str
    # Несколько кварталов/месяцев подряд не обязательно (замечание аналитика 06.11.2026):
    # `start`/`end` — охват, `ranges` — сами полуинтервалы. Пусто — один `[start, end)`.
    ranges: tuple[tuple[date, date], ...] = ()

    def where(self, column: str = "mv.month", prefix: str = "p") -> tuple[str, dict[str, Any]]:
        """Условие по периоду и его параметры; `column` — месяц или дата поверки."""

        ranges = self.ranges or ((self.start, self.end),)
        parts, params = [], {}
        for i, (a, b) in enumerate(ranges):
            parts.append(f"({column} >= :{prefix}s{i} AND {column} < :{prefix}e{i})")
            params[f"{prefix}s{i}"], params[f"{prefix}e{i}"] = a, b
        return "(" + " OR ".join(parts) + ")", params


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def quarter_label(year: int, quarter: int) -> str:
    return f"{quarter} кв. {year}"


def data_edge(db: Session) -> date | None:
    """Конец последнего полного квартала по дате последнего загруженного пакета.

    Отчёт аналитика — поквартальный, а изменения ФГИС приходят каждый день: без этой границы
    «2026 год» к концу октября показывал бы три квартала и кусок четвёртого."""

    package = db.execute(
        text("SELECT package_id FROM meter_market_loads WHERE status = 'done' ORDER BY package_id DESC LIMIT 1")
    ).scalar()
    if not package:
        return None
    loaded = datetime.strptime(package, "%Y%m%d").date()
    return date(loaded.year, (loaded.month - 1) // 3 * 3 + 1, 1)


def _last_loaded(db: Session) -> date | None:
    package = db.execute(
        text("SELECT package_id FROM meter_market_loads WHERE status = 'done' ORDER BY package_id DESC LIMIT 1")
    ).scalar()
    return datetime.strptime(package, "%Y%m%d").date() if package else None


def parse_period(value: str | None, edge: date | None) -> Period:
    """`year:2026`, `ytd:2026`, `quarter:2026-3`, `month:2026-09`, `all`. Без значения — год
    последнего полного квартала с начала года («2026 г., 1–3 кв.»)."""

    edge = edge or date.today().replace(day=1)
    last = _add_months(edge, -1)  # последний полный месяц
    if not value:
        value = f"ytd:{last.year}"
    kind, _, arg = value.partition(":")
    try:
        if kind == "all":
            return Period(date(FIRST_YEAR, 1, 1), edge, f"{FIRST_YEAR} г. – {_edge_label(edge)}")
        if kind in ("year", "ytd"):
            year = int(arg)
            start, end = date(year, 1, 1), date(year + 1, 1, 1)
            if end > edge:
                end = edge
                return Period(start, end, f"{year} г. ({_quarters_label(start, end)})")
            return Period(start, end, f"{year} г.")
        if kind in ("quarters", "months"):
            return _multi_period(kind, arg, edge)
        if kind == "quarter":
            year, quarter = (int(x) for x in arg.split("-"))
            start = date(year, (quarter - 1) * 3 + 1, 1)
            return Period(start, _add_months(start, 3), f"{quarter_label(year, quarter)} г.")
        if kind == "month":
            year, month = (int(x) for x in arg.split("-"))
            start = date(year, month, 1)
            return Period(start, _add_months(start, 1), f"{start:%m.%Y}")
    except ValueError as exc:
        raise ValueError(f"Непонятный период «{value}»") from exc
    raise ValueError(f"Непонятный период «{value}»")


def _multi_period(kind: str, arg: str, edge: date) -> Period:
    """`quarters:2026-1,2026-3`, `months:2026-01,2026-03` — несколько кварталов или месяцев."""

    starts: set[date] = set()
    for item in arg.split(","):
        year, number = (int(x) for x in item.strip().split("-"))
        if kind == "quarters":
            if not 1 <= number <= 4:
                raise ValueError("Квартал — от 1 до 4")
            starts.add(date(year, (number - 1) * 3 + 1, 1))
        else:
            starts.add(date(year, number, 1))
    if not starts:
        raise ValueError("Не выбран ни один период")
    step = 3 if kind == "quarters" else 1
    ordered = sorted(starts)
    ranges: list[tuple[date, date]] = []
    for start in ordered:  # соседние периоды склеиваем: меньше условий в запросе
        end = _add_months(start, step)
        if ranges and ranges[-1][1] == start:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    if kind == "quarters":
        label = ", ".join(quarter_label(d.year, (d.month - 1) // 3 + 1) for d in ordered) + " г."
    else:
        label = ", ".join(f"{d:%m.%Y}" for d in ordered)
    if len(ordered) > 6:
        label = f"{len(ordered)} {'кварталов' if kind == 'quarters' else 'мес.'}: {label[:40]}…"
    return Period(ordered[0], ranges[-1][1], label, tuple(ranges))


def _edge_label(edge: date) -> str:
    last = _add_months(edge, -1)
    quarter = (last.month - 1) // 3 + 1
    return f"{quarter_label(last.year, quarter)} г." if last.month % 3 == 0 else f"{last:%m.%Y}"


def _quarters_label(start: date, end: date) -> str:
    last = _add_months(end, -1)
    q_to = (last.month - 1) // 3 + 1
    return f"1–{q_to} кв." if q_to > 1 else "1 кв."


# ── Справочные данные отчёта ───────────────────────────────────────────────────────────────


def _manufacturer_names(db: Session) -> dict[int, str]:
    return dict(db.execute(text("SELECT id, name FROM meter_market_manufacturers")).all())


def thresholds(db: Session) -> list[tuple[int, float]]:
    return [
        (int(y), float(s))
        for y, s in db.execute(text("SELECT year, min_score FROM meter_market_score_thresholds ORDER BY year")).all()
    ]


def requirement_year(score: float | None, limits: list[tuple[int, float]]) -> int | None:
    """«Соответствие требованиям года» — наибольший год, чей порог не выше баллов (правило 3 ТЗ)."""

    if score is None:
        return None
    met = [year for year, minimum in limits if score >= minimum]
    return max(met) if met else None


def status(db: Session) -> dict[str, Any]:
    last = db.execute(
        text(
            "SELECT package_id, kind, finished_at, message FROM meter_market_loads "
            "WHERE status = 'done' ORDER BY package_id DESC LIMIT 1"
        )
    ).mappings().first()
    running = db.execute(
        text(
            "SELECT package_id, kind, status, checkpoint, started_at, message FROM meter_market_loads "
            "WHERE status <> 'done' ORDER BY id DESC LIMIT 1"
        )
    ).mappings().first()
    edge = data_edge(db)
    return {
        "last_package": dict(last) if last else None,
        "pending_package": dict(running) if running else None,
        "edge": edge,
        "edge_label": _edge_label(edge) if edge else None,
        # Месяц последнего пакета: за него поверки уже есть, хотя он ещё неполный (замечание
        # аналитика 06.11.2026: «выбрать октябрь не получается»).
        "loaded_month": date(loaded.year, loaded.month, 1) if (loaded := _last_loaded(db)) else None,
        "new_types": db.execute(text("SELECT count(*) FROM meter_market_types WHERE review_status = 'new'")).scalar(),
        "unmapped_variants": db.execute(
            text(
                "SELECT count(*) FROM meter_market_variants v JOIN meter_market_types t ON t.id = v.type_id "
                "WHERE t.in_report AND v.modification_id IS NULL"
            )
        ).scalar(),
        "thresholds": [{"year": y, "min_score": s} for y, s in thresholds(db)],
    }


# ── Экран 1: общая информация и интерфейсы ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Filters:
    manufacturer_id: int | None = None
    design: str | None = None
    phase: str | None = None
    interface: str | None = None
    smart: str | None = None  # yes | no | nodata — соответствие ПП 890 (06.11.2026, 09.11.2026)
    # Детализация «Группы компаний»: группа (или компания вне групп) на год поверки.
    group: str | None = None


def _cells(db: Session, period: Period, extra_group: str = "", extra_select: str = "") -> list[dict[str, Any]]:
    period_sql, period_params = period.where()
    rows = db.execute(
        text(
            f"SELECT {_MANUFACTURER_SQL} AS manufacturer_id, m.design, m.phase, m.interface, "
            f"(v.modification_id IS NULL) AS unmapped, m.smart_890 AS smart{extra_select}, sum(mv.count) AS count "
            f"{_BASE_FROM} WHERE {period_sql} "
            f"GROUP BY 1, 2, 3, 4, 5, 6{extra_group}"
        ),
        period_params,
    ).mappings()
    return [dict(r) for r in rows]


def _design_label(cell: dict[str, Any]) -> str:
    if cell["unmapped"]:
        return UNMAPPED
    design = cell["design"] or "nd"
    return NO_DATA if design == "nd" else DESIGN_LABELS.get(design, NO_DATA)


def _phase_label(cell: dict[str, Any]) -> str:
    return UNMAPPED if cell["unmapped"] else (cell["phase"] or NO_DATA)


def _interface_label(cell: dict[str, Any]) -> str:
    return UNMAPPED if cell["unmapped"] else (cell["interface"] or NO_DATA)


def smart_label(cell: dict[str, Any]) -> str:
    """yes / no / nodata. Третьего «не определено» нет (замечание 09.11.2026): пустой признак —
    это «нет данных», а почему — пишет `meter_market_explain.smart_verdict`."""

    if cell["unmapped"] or cell["smart"] is None:
        return "nodata"
    return "yes" if cell["smart"] else "no"


SMART_STATUS = {"yes": "Соответствует", "no": "Не соответствует", "nodata": "Нет данных"}


def smart_verdict(
    unmapped: bool, smart: bool | None, source: str | None, note: str | None, checked_at: Any
) -> tuple[str, str, str]:
    """Статус ПП 890 и причина его присвоения: (yes|no|nodata, подпись, причина).

    Причина нужна всегда (замечание 09.11.2026): «Нет данных» без объяснения выглядит как
    ошибка алгоритма, а это либо неразмеченное написание, либо ИИ ещё не проверял, либо в
    описании типа не хватило сведений."""

    note = (note or "").strip()
    if unmapped:
        return "nodata", SMART_STATUS["nodata"], "написание модификации не размечено — признаки прибора неизвестны"
    if smart is None:
        if checked_at is None and source is None:
            return "nodata", SMART_STATUS["nodata"], "проверка по разделу III ПП 890 ещё не выполнялась"
        return "nodata", SMART_STATUS["nodata"], f"ИИ не хватило сведений в описании типа{': ' + note if note else ''}"
    key = "yes" if smart else "no"
    who = "решение аналитика в справочнике" if source == "manual" else "вывод ИИ по разделу III ПП 890"
    return key, SMART_STATUS[key], f"{who}{': ' + note if note else ''}"


def _keep(cell: dict[str, Any], filters: Filters, names: dict[int, str], skip: str) -> bool:
    if skip != "manufacturer" and filters.manufacturer_id is not None and cell["manufacturer_id"] != filters.manufacturer_id:
        return False
    if skip != "design" and filters.design is not None and _design_label(cell) != filters.design:
        return False
    if skip != "phase" and filters.phase is not None and _phase_label(cell) != filters.phase:
        return False
    if skip != "interface" and filters.interface is not None and _interface_label(cell) != filters.interface:
        return False
    if filters.smart is not None and smart_label(cell) != filters.smart:
        return False
    if filters.group is not None and cell.get("group") != filters.group:
        return False
    return True


def _breakdown(items: dict[Any, int]) -> list[dict[str, Any]]:
    total = sum(items.values())
    return [
        {"key": key, "count": count, "share": round(count * 100 / total, 2) if total else 0.0}
        for key, count in sorted(items.items(), key=lambda kv: -kv[1])
    ]


def overview(db: Session, period: Period, filters: Filters) -> dict[str, Any]:
    names = _manufacturer_names(db)
    cells = _cells(db, period)

    def block(skip: str, label) -> dict[Any, int]:
        acc: dict[Any, int] = defaultdict(int)
        for cell in cells:
            if _keep(cell, filters, names, skip):
                acc[label(cell)] += int(cell["count"])
        return acc

    manufacturers = _breakdown(block("manufacturer", lambda c: c["manufacturer_id"]))
    for row in manufacturers:
        row["name"] = names.get(row["key"], "Производитель не указан")
    total_all = sum(int(c["count"]) for c in cells if _keep(c, filters, names, ""))
    unmapped = sum(int(c["count"]) for c in cells if c["unmapped"] and _keep(c, filters, names, ""))
    return {
        "period": {"start": period.start, "end": period.end, "label": period.label},
        "total": total_all,
        "unmapped": unmapped,
        "manufacturers": manufacturers,
        "designs": _breakdown(block("design", _design_label)),
        "phases": _breakdown(block("phase", _phase_label)),
        "interfaces": _breakdown(block("interface", _interface_label)),
        # Сколько поверок в каждом статусе ПП 890 при остальных фильтрах: «Нет данных» видно
        # числом, а не прячется в разнице «всего − соответствуют − не соответствуют».
        "smart_counts": {
            key: sum(
                int(c["count"]) for c in cells if smart_label(c) == key and _keep(c, _without_smart(filters), names, "")
            )
            for key in ("yes", "no", "nodata")
        },
    }


def _without_smart(filters: Filters) -> Filters:
    return Filters(filters.manufacturer_id, filters.design, filters.phase, filters.interface, None, filters.group)


# ── Экраны 2 и 3: по годам ─────────────────────────────────────────────────────────────────


def _years(edge: date) -> list[int]:
    return list(range(FIRST_YEAR, _add_months(edge, -1).year + 1))


def designs_by_year(db: Session, edge: date, phase: str | None = None) -> dict[str, Any]:
    """Производитель → исполнения, столбцы — годы (экран 2). Фильтр — 1ф/3ф."""

    names = _manufacturer_names(db)
    period = Period(date(FIRST_YEAR, 1, 1), edge, f"{FIRST_YEAR} г. – {_edge_label(edge)}")
    cells = _cells(db, period, ", 7", ", extract(year FROM mv.month)::int AS year")
    years = _years(edge)
    table: dict[int, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for cell in cells:
        if phase and _phase_label(cell) != phase:
            continue
        table[cell["manufacturer_id"]][_design_label(cell)][cell["year"]] += int(cell["count"])
    rows = []
    for manufacturer_id, designs in table.items():
        by_year = {y: sum(d.get(y, 0) for d in designs.values()) for y in years}
        rows.append(
            {
                "manufacturer_id": manufacturer_id,
                "name": names.get(manufacturer_id, "Производитель не указан"),
                "years": by_year,
                "total": sum(by_year.values()),
                "designs": sorted(
                    (
                        {"design": design, "years": {y: values.get(y, 0) for y in years}, "total": sum(values.values())}
                        for design, values in designs.items()
                    ),
                    key=lambda r: -r["total"],
                ),
            }
        )
    rows.sort(key=lambda r: -r["total"])
    totals = {y: sum(r["years"][y] for r in rows) for y in years}
    return {"label": period.label, "years": years, "rows": rows, "totals": totals, "total": sum(totals.values())}


def _groups(db: Session) -> tuple[dict[int, str], list[tuple[int, int, int | None, int | None]]]:
    groups = dict(db.execute(text("SELECT id, name FROM meter_market_groups")).all())
    members = [
        tuple(r)
        for r in db.execute(text("SELECT group_id, manufacturer_id, year_from, year_to FROM meter_market_group_members")).all()
    ]
    return groups, members


def group_for(manufacturer_id: int | None, year: int, members: list[tuple[int, int, int | None, int | None]]) -> int | None:
    for group_id, member_id, year_from, year_to in members:
        if member_id == manufacturer_id and (year_from is None or year >= year_from) and (year_to is None or year <= year_to):
            return group_id
    return None


def group_label(
    manufacturer_id: int | None,
    year: int,
    names: dict[int, str],
    groups: dict[int, str],
    members: list[tuple[int, int, int | None, int | None]],
) -> str:
    """Строка экрана «Группы компаний»: группа на год поверки или сама компания вне групп."""

    group_id = group_for(manufacturer_id, year, members)
    return groups[group_id] if group_id is not None else names.get(manufacturer_id, "Производитель не указан")


def groups_by_year(db: Session, edge: date) -> dict[str, Any]:
    """Группа → входящие компании, столбцы — годы и «Всего» (экран 3). Состав группы — на год
    поверки: АтомЦифроСбыт в 2022–2025 в «Энергомере», с 2026 — в «Заводе НАРТИС»."""

    names = _manufacturer_names(db)
    groups, members = _groups(db)
    period = Period(date(FIRST_YEAR, 1, 1), edge, f"{FIRST_YEAR} г. – {_edge_label(edge)}")
    years = _years(edge)
    rows = db.execute(
        text(
            f"SELECT {_MANUFACTURER_SQL} AS manufacturer_id, extract(year FROM mv.month)::int AS year, sum(mv.count) "
            f"{_BASE_FROM} WHERE mv.month >= :start AND mv.month < :end GROUP BY 1, 2"
        ),
        {"start": period.start, "end": period.end},
    ).all()
    table: dict[str, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    ids: dict[str, int | None] = {}
    for manufacturer_id, year, count in rows:
        name = names.get(manufacturer_id, "Производитель не указан")
        ids[name] = manufacturer_id
        table[group_label(manufacturer_id, year, names, groups, members)][name][year] += int(count)
    result = []
    for group, companies in table.items():
        by_year = {y: sum(c.get(y, 0) for c in companies.values()) for y in years}
        result.append(
            {
                "name": group,
                "years": by_year,
                "total": sum(by_year.values()),
                "companies": sorted(
                    (
                        {
                            "name": company,
                            "manufacturer_id": ids.get(company),
                            "years": {y: v.get(y, 0) for y in years},
                            "total": sum(v.values()),
                        }
                        for company, v in companies.items()
                    ),
                    key=lambda r: -r["total"],
                ),
            }
        )
    result.sort(key=lambda r: -r["total"])
    totals = {y: sum(r["years"][y] for r in result) for y in years}
    return {"label": period.label, "years": years, "rows": result, "totals": totals, "total": sum(totals.values())}


# ── Экран 4: локализация ───────────────────────────────────────────────────────────────────

LOCALIZATION_FROM_YEAR = 2025


@dataclass(frozen=True)
class RppRecord:
    """Запись РПП исполнения: с какого дня, по какой (включительно), баллы, номер."""

    issued: date
    valid_to: date | None
    score: float | None
    number: str | None = None

    def label(self) -> str:
        parts = [f"№ {self.number}" if self.number else "запись РПП"]
        if self.issued != date.min:
            parts.append(f"от {self.issued:%d.%m.%Y}")
        if self.valid_to:
            parts.append(f"до {self.valid_to:%d.%m.%Y}")
        return " ".join(parts)


@dataclass(frozen=True)
class LocVerdict:
    """Решение по поверке на дату: учтена ли, с какими баллами и почему."""

    included: bool
    score: float | None
    reason: str
    record: RppRecord | None


REASON_IN_RPP = "в РПП на дату поверки"
REASON_UNDATED = "в РПП, дата внесения неизвестна — учтено с начала периода"


def rpp_verdict(records: list[RppRecord], day: date) -> LocVerdict:
    """Какая запись РПП действует на дату поверки (правило 4 ТЗ, проверка 09.11.2026).

    Действует самая поздняя из внесённых к этой дате записей с баллами, чей срок не истёк. Так
    раньше не считалось: бралась последняя внесённая запись, даже без баллов и с истёкшим
    сроком. В ГИСП у НАРТИС-И100-W113-2 есть запись 22.09–07.10.2025 без баллов — из-за неё все
    поверки после 22.09.2025 молча выпадали из локализации, хотя запись с 83 баллами ещё
    действовала. Теперь запись без баллов не перекрывает предыдущую, а поверки после окончания
    срока последней записи исключаются с причиной «срок истёк»."""

    issued = [r for r in records if r.issued <= day]
    if not issued:
        first = min(records, key=lambda r: r.issued)
        return LocVerdict(False, None, f"поверка до внесения в РПП ({first.issued:%d.%m.%Y})", first)
    valid = [r for r in issued if r.valid_to is None or day <= r.valid_to]
    scored = [r for r in valid if r.score is not None]
    if scored:
        record = max(scored, key=lambda r: r.issued)
        reason = REASON_UNDATED if record.issued == date.min else REASON_IN_RPP
        return LocVerdict(True, float(record.score), reason, record)
    if valid:
        return LocVerdict(False, None, "в действующей записи РПП нет баллов", max(valid, key=lambda r: r.issued))
    last = max(issued, key=lambda r: r.valid_to or date.max)
    return LocVerdict(False, None, f"срок записи РПП истёк {last.valid_to:%d.%m.%Y}", last)


@dataclass
class LocPiece:
    """Часть поверок исполнения за месяц с одним решением — строка «бухгалтерии» локализации."""

    modification_id: int
    manufacturer_id: int | None
    month: date
    count: int
    verdict: LocVerdict

    @property
    def quarter(self) -> str:
        return f"{self.month.year}-{(self.month.month - 1) // 3 + 1}"


def localization_quarters(edge: date) -> list[str]:
    quarters: list[str] = []
    cursor = date(LOCALIZATION_FROM_YEAR, 1, 1)
    while cursor < edge:
        quarters.append(f"{cursor.year}-{(cursor.month - 1) // 3 + 1}")
        cursor = _add_months(cursor, 3)
    return quarters


def localization_ledger(
    db: Session, edge: date, *, manufacturer_id: int | None = None
) -> tuple[list[LocPiece], dict[int, dict[str, Any]], set[int]]:
    """Все поверки исполнений «в РПП» с 2025 года, разложенные по решениям: учтена (с какими
    баллами и по какой записи) или исключена (почему). Таблица экрана «Локализация» и её
    детализация считаются из одной бухгалтерии — число в ячейке и сумма «учтено» в
    детализации совпадают по построению.

    Возвращает части, сведения об исполнениях и исполнения без даты внесения."""

    start = date(LOCALIZATION_FROM_YEAR, 1, 1)
    manufacturer_sql = f" AND {_MANUFACTURER_SQL} = :mf" if manufacturer_id is not None else ""
    mods = db.execute(
        text(
            f"SELECT m.id, {_MANUFACTURER_SQL} AS manufacturer_id, m.score, m.rpp_included_at, m.rpp_record_number "
            "FROM meter_market_modifications m JOIN meter_market_types t ON t.id = m.type_id AND t.in_report "
            f"WHERE m.in_rpp{manufacturer_sql}"
        ),
        {"mf": manufacturer_id},
    ).mappings().all()
    history: dict[int, list[RppRecord]] = defaultdict(list)
    for mod_id, issued, valid_to, score, number in db.execute(
        text(
            "SELECT modification_id, issued_at, valid_to, score, record_number FROM meter_market_rpp_records "
            "ORDER BY issued_at"
        )
    ):
        history[mod_id].append(RppRecord(issued, valid_to, score, number))

    records: dict[int, list[RppRecord]] = {}
    undated_mods: set[int] = set()
    for mod in mods:
        if history.get(mod["id"]):
            records[mod["id"]] = history[mod["id"]]
        else:
            # Без истории ГИСП — баллы модификации (даже пустые: исключение будет с причиной).
            if mod["rpp_included_at"] is None:
                undated_mods.add(mod["id"])
            records[mod["id"]] = [
                RppRecord(mod["rpp_included_at"] or date.min, None, mod["score"], mod["rpp_record_number"])
            ]
    by_id = {mod["id"]: dict(mod) for mod in mods}
    if not records:
        return [], by_id, undated_mods

    monthly = db.execute(
        text(
            "SELECT v.modification_id, mv.month, sum(mv.count) FROM meter_verification_monthly mv "
            "JOIN meter_market_variants v ON v.id = mv.variant_id "
            "WHERE v.modification_id = ANY(:ids) AND mv.month >= :start AND mv.month < :end GROUP BY 1, 2"
        ),
        {"ids": list(records), "start": start, "end": edge},
    ).all()

    pieces: list[LocPiece] = []
    for mod_id, month, count in monthly:
        month_end = _add_months(month, 1)
        manufacturer = by_id[mod_id]["manufacturer_id"]
        bounds = {r.issued for r in records[mod_id]} | {
            r.valid_to + timedelta(days=1) for r in records[mod_id] if r.valid_to
        }
        if not any(month < b < month_end for b in bounds):
            pieces.append(LocPiece(mod_id, manufacturer, month, int(count), rpp_verdict(records[mod_id], month)))
            continue
        # Запись сменилась (или истекла) внутри месяца — делим поверки месяца по дням.
        days = db.execute(
            text(
                "SELECT mv.verification_date, count(*) FROM meter_verifications mv "
                "JOIN meter_market_variants v ON v.id = mv.variant_id WHERE v.modification_id = :m "
                "AND mv.applicable AND NOT mv.annulled AND mv.verification_date >= :s AND mv.verification_date < :e "
                "GROUP BY 1"
            ),
            {"m": mod_id, "s": month, "e": month_end},
        ).all()
        merged: dict[LocVerdict, int] = defaultdict(int)
        for day, day_count in days:
            merged[rpp_verdict(records[mod_id], day)] += int(day_count)
        pieces.extend(LocPiece(mod_id, manufacturer, month, n, verdict) for verdict, n in merged.items())
    return pieces, by_id, undated_mods


def requirement_reason(score: float, requirement: int, limits: list[tuple[int, float]]) -> str | None:
    """Причина исключения по фильтру «Требования года»; None — проходит."""

    year = requirement_year(score, limits)
    if year == requirement:
        return None
    return f"{score:g} баллов — требования {year} г., выбраны {requirement} г." if year else (
        f"{score:g} баллов — ниже порога любого года"
    )


def localization(db: Session, edge: date, requirement: int | None = None) -> dict[str, Any]:
    """Поверенные локализованные ПУ: производитель → баллы, столбцы — кварталы с 2025 года.

    Точка отсчёта — дата внесения исполнения в РПП (правило 4 ТЗ): поверки раньше неё не
    считаются. Если у исполнения есть история записей ГИСП (`meter_market_rpp_records`: 83 →
    95 → 113 баллов), поверка относится к записи, действовавшей на дату поверки, — и к её
    «требованиям года» (`rpp_verdict`). Без истории — баллы модификации с даты
    `rpp_included_at`; если и даты нет (в файле аналитика её нет), поверки считаются с начала
    периода, а их количество отдаётся отдельно (`undated`) — расхождение видно, а не спрятано.
    Месяц, внутри которого меняется запись, делится точно — по сырым поверкам."""

    names = _manufacturer_names(db)
    limits = thresholds(db)
    quarters = localization_quarters(edge)
    pieces, _, undated_mods = localization_ledger(db, edge)
    empty = {"quarters": quarters, "rows": [], "totals": {q: 0 for q in quarters}, "total": 0, "undated": 0,
             "requirement": requirement, "requirement_years": sorted({y for y, _ in limits})}

    table: dict[int, dict[float, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    undated = 0
    for piece in pieces:
        verdict = piece.verdict
        if not verdict.included or piece.count <= 0:
            continue
        if requirement is not None and requirement_reason(verdict.score, requirement, limits):
            continue
        if piece.modification_id in undated_mods:
            undated += piece.count
        table[piece.manufacturer_id][verdict.score][piece.quarter] += piece.count

    rows = []
    for manufacturer_id, scores in table.items():
        by_q = {q: sum(s.get(q, 0) for s in scores.values()) for q in quarters}
        rows.append(
            {
                "manufacturer_id": manufacturer_id,
                "name": names.get(manufacturer_id, "Производитель не указан"),
                "quarters": by_q,
                "total": sum(by_q.values()),
                "scores": [
                    {
                        "score": score,
                        "requirement_year": requirement_year(score, limits),
                        "quarters": {q: values.get(q, 0) for q in quarters},
                        "total": sum(values.values()),
                    }
                    for score, values in sorted(scores.items())
                ],
            }
        )
    rows.sort(key=lambda r: -r["total"])
    totals = {q: sum(r["quarters"][q] for r in rows) for q in quarters}
    return {**empty, "rows": rows, "totals": totals, "total": sum(totals.values()), "undated": undated}


# ── Переход вглубь: производитель → тип → модификация → варианты ───────────────────────────


def drill(
    db: Session,
    period: Period,
    *,
    manufacturer_id: int | None = None,
    type_id: int | None = None,
    modification_id: int | None = None,
    unmapped_type_id: int | None = None,
) -> dict[str, Any]:
    period_where, params = period.where()
    if modification_id is not None or unmapped_type_id is not None:
        if modification_id is not None:
            where, params["id"] = "v.modification_id = :id", modification_id
        else:
            where, params["id"] = "v.type_id = :id AND v.modification_id IS NULL", unmapped_type_id
        rows = db.execute(
            text(
                "SELECT v.id, v.raw, v.mapped_by, coalesce(sum(mv.count) FILTER (WHERE " + period_where + "), 0) AS count "
                "FROM meter_market_variants v LEFT JOIN meter_verification_monthly mv ON mv.variant_id = v.id "
                f"WHERE {where} GROUP BY 1, 2, 3 ORDER BY 4 DESC"
            ),
            params,
        ).mappings()
        return {"level": "variants", "rows": [dict(r) for r in rows]}
    if type_id is not None:
        rows = db.execute(
            text(
                "SELECT m.id, coalesce(m.name, :unmapped) AS name, m.phase, m.design, m.interface, sum(mv.count) AS count "
                f"{_BASE_FROM} WHERE t.id = :t AND {period_where} GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC"
            ),
            {**params, "t": type_id, "unmapped": "нет данных — написания не размечены"},
        ).mappings()
        return {"level": "modifications", "type_id": type_id, "rows": [dict(r) for r in rows]}
    if manufacturer_id is not None:
        rows = db.execute(
            text(
                "SELECT t.id, t.mit_number, t.notation, t.title, sum(mv.count) AS count "
                f"{_BASE_FROM} WHERE {_MANUFACTURER_SQL} = :m AND {period_where} GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC"
            ),
            {**params, "m": manufacturer_id},
        ).mappings()
        return {"level": "types", "rows": [dict(r) for r in rows]}
    raise ValueError("Не указано, что раскрывать")


def variant_samples(db: Session, variant_id: int, period: Period, limit: int = 10) -> list[dict[str, Any]]:
    """Примеры поверок варианта — чтобы любое расхождение можно было показать записями ФГИС."""

    sample_sql, sample_params = period.where("mv.verification_date")
    rows = db.execute(
        text(
            "SELECT mv.global_id, o.title AS org, mv.mi_number, mv.verification_date, mv.applicable, mv.annulled "
            "FROM meter_verifications mv LEFT JOIN meter_market_orgs o ON o.id = mv.org_id "
            f"WHERE mv.variant_id = :v AND {sample_sql} "
            "ORDER BY mv.verification_date DESC LIMIT :l"
        ),
        {"v": variant_id, "l": limit, **sample_params},
    ).mappings()
    return [{**dict(r), "url": fgis_record_url(r["global_id"])} for r in rows]


def modification_samples(db: Session, modification_id: int, period: Period, limit: int = 10) -> list[dict[str, Any]]:
    """Примеры поверок исполнения (все его написания) — документ-основание в детализации."""

    sample_sql, sample_params = period.where("mv.verification_date")
    rows = db.execute(
        text(
            "SELECT mv.global_id, o.title AS org, mv.mi_number, mv.verification_date, mv.applicable, mv.annulled, "
            "v.raw FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "LEFT JOIN meter_market_orgs o ON o.id = mv.org_id "
            f"WHERE v.modification_id = :m AND {sample_sql} ORDER BY mv.verification_date DESC LIMIT :l"
        ),
        {"m": modification_id, "l": limit, **sample_params},
    ).mappings()
    return [{**dict(r), "url": fgis_record_url(r["global_id"])} for r in rows]


def fgis_record_url(global_id: int) -> str:
    """Карточка поверки во ФГИС «Аршин»: `global_id` открытых данных — номер записи ВРИ."""

    return f"https://fgis.gost.ru/fundmetrology/cm/results/1-{global_id}"


def powerbi_rows(db: Session, period: Period) -> list[list[Any]]:
    """Плоская таблица для Power BI (замечание аналитика 06.11.2026): строка = модификация ×
    месяц. Баллы и «в РПП» — по модификации справочника (без истории записей РПП)."""

    names = _manufacturer_names(db)
    limits = thresholds(db)
    period_sql, period_params = period.where()
    rows = db.execute(
        text(
            f"SELECT {_MANUFACTURER_SQL} AS manufacturer_id, t.mit_number, "
            "m.design, m.phase, m.interface, "
            "mv.month, m.in_rpp, m.score, m.smart_890, (v.modification_id IS NULL) AS unmapped, "
            "m.smart_890_source, m.smart_890_note, m.smart_890_at, "
            "sum(mv.count) AS count "
            f"{_BASE_FROM} WHERE {period_sql} "
            "GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 "
            "ORDER BY 2, 6, 1"
        ),
        period_params,
    ).mappings()
    result: list[list[Any]] = []
    for r in rows:
        unmapped = r["unmapped"]
        year = requirement_year(r["score"], limits)
        _, smart_status, smart_reason = smart_verdict(
            unmapped, r["smart_890"], r["smart_890_source"], r["smart_890_note"], r["smart_890_at"]
        )
        cell = {"unmapped": unmapped, "design": r["design"], "phase": r["phase"], "interface": r["interface"]}
        result.append(
            [
                names.get(r["manufacturer_id"], "Производитель не указан"),
                r["mit_number"],
                _phase_label(cell),
                _design_label(cell),
                r["month"].strftime("%m.%Y"),
                int(r["count"]),
                _interface_label(cell),
                NO_DATA if unmapped else ("да" if r["in_rpp"] else "нет"),
                year if year is not None else "",
                r["score"] if r["score"] is not None else "",
                smart_status,
                smart_reason,
            ]
        )
    return result


POWERBI_HEADER = [
    "Производитель",
    "Регистрационный номер типа СИ",
    "Тип ПУ",
    "Исполнение",
    "Период",
    "Количество",
    "Интерфейс",
    "В Реестре российской промышленной продукции",
    "Соответствие требованиям года",
    "Кол-во баллов",
    "Соответствие ПП 890",
    "ПП 890: причина",
]


# ── Выгрузка в Excel ───────────────────────────────────────────────────────────────────────


def to_xlsx(title: str, subtitle: str, header: list[str], rows: list[list[Any]]) -> bytes:
    """Таблица экрана в Excel: шапка с названием и источником, автофильтр, закрепление шапки,
    числа — числами (их пересчитывают и сортируют, как и выгрузку тендеров, раздел 16)."""

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = re.sub(r"[\\/*?:\[\]]", "", title)[:31] or "Отчёт"
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=13)
    sheet.append([subtitle])
    sheet.append([])
    sheet.append(header)
    for cell in sheet[4]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    sheet.auto_filter.ref = f"A4:{get_column_letter(len(header))}{sheet.max_row}"
    sheet.freeze_panes = "A5"
    for index, name in enumerate(header, start=1):
        width = max([len(str(name))] + [len(str(r[index - 1])) for r in rows if index - 1 < len(r)])
        sheet.column_dimensions[get_column_letter(index)].width = min(60, max(10, width + 2))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
