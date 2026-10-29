"""Локализация модификаций рынка ПУ из реестра ГИСП (ПП 719) — раздел 79 ARCHITECTURE.md,
раздел 6.5 ТЗ.

Опрос ГИСП (`registry_polling`, раздел 77) раскладывает записи по продуктам каталога, а отчёту
о рынке нужна связь с модификациями из поверок: у них свои названия и свой справочник. После
каждого опроса строки реестра сопоставляются с модификациями.

Как устроены записи ГИСП (опрос 29.10.2026, 415 записей по 26.51.63.13x):
- исполнение записано на уровне корпуса и версии, а не полного кода поверки:
  «НАРТИС-И100-W112-2», «МИРТЕК-12-РУ-D17-2», «МИР С-05 (на DIN-рейку … S1)», тогда как в
  поверке модификация — «НАРТИС-И100-W112-2-A1R1-230-5-80A-ST-RS485-P1-HKLMOQ1V3-D». Поэтому
  ищется **самый длинный префикс** кода модификации, который стоит в наименовании продукции
  целиком (с границей после него — в том числе перед «/2»), так аналитик и ищет исполнение
  в XLSX реестра;
- у одного исполнения несколько записей во времени с ростом баллов (83 → 95 → 113). Все они
  сохраняются в `meter_market_rpp_records`: поверка относится к записи, действовавшей на её
  дату (экран локализации, `meter_market_report.localization`).

Совпадение только по обозначению семейства («МИР С-04» у типа «МИР С-04») — слабое: у ГИСП
есть записи вида «МИР С-04 (модификаций МИР С-04.10-…-KNQW-E-D, …)» со 115 баллами, и
приписать их баллы всем исполнениям семейства нельзя. По слабому совпадению берётся только
дата первого внесения, баллы остаются из справочника.

Кто важнее: ручное значение (`localization_source = manual`) не трогается вовсе. Модификации из
файла аналитика (`import`) ГИСП дописывает историю записей, только если аналитик отметила
«в РПП — да»: совпадение по префиксу семейства («МИР С-05») не повод объявить локализованным
исполнение, которое человек проверил и не нашёл; такие случаи считаются и уходят в итог опроса.
Модификацию без локализации ГИСП заполняет целиком (`gisp`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.adapters.admission_registries import RegistryRow

_HOMOGLYPHS = str.maketrans("авекмнорстхуё", "abekmhopctxye")
_SPLIT = re.compile(r"[\W_]+")
# Префикс короче этого (букв и цифр) не сопоставляется: «307» нашлось бы в половине реестра.
MIN_PREFIX = 4


def _fold(value: str) -> str:
    return value.casefold().translate(_HOMOGLYPHS).replace("0", "o")


def _pattern(parts: list[str]) -> re.Pattern[str]:
    body = r"[\W_]*".join(re.escape(p) for p in parts)
    return re.compile(rf"(?<![^\W_]){body}(?![^\W_])")


def name_pattern(name: str) -> re.Pattern[str] | None:
    """Обозначение целиком как шаблон: разделители — любые или никаких, по краям — граница."""

    parts = [p for p in _SPLIT.split(_fold(name)) if p]
    if sum(len(p) for p in parts) < MIN_PREFIX:
        return None
    return _pattern(parts)


def match_rows(name: str, prepared: list[tuple[RegistryRow, str]]) -> tuple[str, list[RegistryRow]]:
    """Записи ГИСП по самому длинному префиксу кода модификации, который в реестре есть."""

    parts = [p for p in _SPLIT.split(_fold(name)) if p]
    for size in range(len(parts), 0, -1):
        head = parts[:size]
        if sum(len(p) for p in head) < MIN_PREFIX:
            break
        pattern = _pattern(head)
        found = [row for row, folded in prepared if pattern.search(folded)]
        if found:
            return "-".join(head), found
    return "", []


def _family_key(value: str) -> str:
    return "".join(p for p in _SPLIT.split(_fold(value)) if p)


@dataclass
class GispStats:
    matched: int = 0
    updated: int = 0
    records: int = 0
    # Аналитик отметила «нет в РПП», а префикс нашёлся в ГИСП — на проверку человеку.
    not_marked: int = 0
    # Баллы аналитика расходятся с записью ГИСП, действующей сегодня.
    score_mismatch: int = 0
    # Совпало только обозначение семейства — взята лишь дата первого внесения.
    family_only: int = 0


def apply_gisp_rows(db: Session, rows: list[RegistryRow], *, today: date | None = None) -> GispStats:
    today = today or date.today()
    stats = GispStats()
    prepared = [(row, _fold(row.product_name or row.raw)) for row in rows if (row.product_name or row.raw) and row.issued_at]
    mods = db.execute(
        text(
            "SELECT m.id, m.name, m.in_rpp, m.score, m.localization_source, t.notation "
            "FROM meter_market_modifications m JOIN meter_market_types t ON t.id = m.type_id AND t.in_report"
        )
    ).mappings().all()
    for mod in mods:
        source = mod["localization_source"]
        if source == "manual":
            continue
        head, found = match_rows(mod["name"], prepared)
        if not found:
            continue
        stats.matched += 1
        if source == "import" and not mod["in_rpp"]:
            stats.not_marked += 1
            continue
        found.sort(key=lambda r: r.issued_at)
        families = {_family_key(part) for part in re.split(r"[,;]", mod["notation"] or "") if part.strip()}
        if _family_key(head) in families:
            stats.family_only += 1
            db.execute(
                text("DELETE FROM meter_market_rpp_records WHERE modification_id = :m AND source = 'gisp'"),
                {"m": mod["id"]},
            )
            if mod["in_rpp"]:
                db.execute(
                    text("UPDATE meter_market_modifications SET rpp_included_at = :d, updated_at = now() WHERE id = :id"),
                    {"d": found[0].issued_at, "id": mod["id"]},
                )
                stats.updated += 1
            continue
        db.execute(
            text("DELETE FROM meter_market_rpp_records WHERE modification_id = :m AND source = 'gisp'"),
            {"m": mod["id"]},
        )
        for row in found:
            db.execute(
                text(
                    "INSERT INTO meter_market_rpp_records (modification_id, record_number, product_name, issued_at, "
                    "valid_to, score, source) VALUES (:m, :n, :p, :i, :v, :s, 'gisp')"
                ),
                {
                    "m": mod["id"],
                    "n": (row.record_number or "")[:100] or None,
                    "p": row.product_name,
                    "i": row.issued_at,
                    "v": row.valid_to,
                    "s": row.score,
                },
            )
            stats.records += 1
        current = [r for r in found if r.issued_at <= today] or found
        latest = max(current, key=lambda r: (r.issued_at, r.score or 0))
        first = found[0]
        values = {"id": mod["id"], "date": first.issued_at, "number": (latest.record_number or "")[:100] or None}
        if source == "import":
            if mod["score"] is not None and latest.score is not None and float(mod["score"]) != float(latest.score):
                stats.score_mismatch += 1
            db.execute(
                text(
                    "UPDATE meter_market_modifications SET rpp_included_at = :date, rpp_record_number = :number, "
                    "updated_at = now() WHERE id = :id"
                ),
                values,
            )
        else:
            db.execute(
                text(
                    "UPDATE meter_market_modifications SET in_rpp = true, score = :score, rpp_included_at = :date, "
                    "rpp_record_number = :number, localization_source = 'gisp', updated_at = now() WHERE id = :id"
                ),
                {**values, "score": latest.score},
            )
        stats.updated += 1
    db.flush()
    return stats
