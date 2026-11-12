"""«В карточке об этом ни слова» — вердикт «нет данных» без обращения к модели (12.11.2026, раздел 104).

Разбор расхода ИИ за 14.10–12.11.2026: матрица соответствия — 89% всех токенов, и на живой
базе две трети её ячеек — «нет данных» от модели. То есть больше половины денег уходило на
ответ «в карточке производителя про это ничего нет». Часть таких ячеек видна заранее: ни одно
содержательное слово требования не встречается в карточке (с руководством) ни разу.

Модуль сначала **меряет**, а не решает: `probe` прогоняет правило по уже посчитанным матрицам и
показывает, какие вердикты модель вынесла в ячейках, которые правило отдало бы коду. Правило
включается в расчёт (`no_facts`) только если среди таких ячеек модель почти всегда говорила
«нет данных» — иначе код превращал бы найденное моделью «соответствует» в «нет данных» и
занижал процент.

**Замер 12.11.2026 на локальной базе (20 закупок, 9 269 ячеек): правило НЕ включено.** Оно
сработало бы лишь на 5,1% ячеек, и в 18% из них модель нашла ответ: «Ширина», «Вес» против
«габариты», «масса» в карточке; «Номинальный ток: 5 А», где «ток» и «5» есть почти в каждой
строке каталога. Остальные «нет данных» модель выносит, прочитав карточку, в которой слова
требования есть, — по словам их не отличить. Модуль оставлен для повторного замера на рабочей
базе, где закупок и производителей больше.

Запуск: `python -m app.services.compliance_prefilter probe [--examples 20]`.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ComplianceMatrixEntry, Requirement, RequirementKind
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender

# Слово карточки, которое есть больше чем в этой доле её кусков, ничего не говорит о
# конкретном требовании («счетч», «электр», «230») — совпадение по нему не считается.
COMMON_SHARE = 0.25
# Короткие общие основы, которые встречаются почти в любом требовании и любой карточке.
_GENERIC = frozenset(
    "прибор учета учет счетчи электр энерги энергия модель издели устрой оборуд должен "
    "соответ требов характе значен номина наличие".split()
)


def informative_overlap(requirement: Requirement, units: list, frequency: dict[str, int]) -> set[str]:
    """Содержательные основы требования, которые есть в карточке."""

    from app.services.compliance_service import _parameter_of, _stems, parameter_hint

    query = _stems(requirement.normalized_text or requirement.text or "") | _stems(
        parameter_hint(_parameter_of(requirement)) or ""
    )
    limit = max(1, int(len(units) * COMMON_SHARE))
    return {
        stem
        for stem in query
        if stem in frequency and frequency[stem] <= limit and stem[:6] not in _GENERIC
    }


def card_index(context: str) -> tuple[list, dict[str, int]]:
    from app.services.compliance_service import _context_units

    units = _context_units(context)
    frequency: dict[str, int] = {}
    for unit in units:
        for stem in unit.stems:
            frequency[stem] = frequency.get(stem, 0) + 1
    return units, frequency


def probe(db: Session, examples: int = 20) -> None:
    """Сверка правила с вердиктами, уже лежащими в матрицах (ничего не меняет)."""

    from app.services.compliance_service import build_manufacturer_context
    from app.services.product_relevance import tender_named_text

    entries = db.execute(
        select(ComplianceMatrixEntry, Requirement)
        .join(Requirement, Requirement.id == ComplianceMatrixEntry.requirement_id)
        .where(Requirement.kind == RequirementKind.PRODUCT.value)
    ).all()
    by_pair: dict[tuple, list] = defaultdict(list)
    for entry, requirement in entries:
        by_pair[(entry.tender_id, entry.manufacturer_id)].append((entry, requirement))

    table: Counter = Counter()  # (сработало правило, статус, источник) → ячеек
    misses: list[str] = []
    named_cache: dict = {}
    reqs_cache: dict = {}
    for (tender_id, manufacturer_id), rows in by_pair.items():
        tender = db.get(Tender, tender_id)
        manufacturer = db.get(Manufacturer, manufacturer_id)
        if tender_id not in reqs_cache:
            reqs_cache[tender_id] = list(
                db.scalars(
                    select(Requirement).where(
                        Requirement.tender_id == tender_id,
                        Requirement.kind == RequirementKind.PRODUCT.value,
                    )
                )
            )
            named_cache[tender_id] = tender_named_text(db, tender)
        context, _ = build_manufacturer_context(
            db,
            manufacturer,
            include_manual=True,
            requirements=reqs_cache[tender_id],
            named_text=named_cache[tender_id],
        )
        if not context.strip():
            continue
        units, frequency = card_index(context)
        for entry, requirement in rows:
            fires = not informative_overlap(requirement, units, frequency)
            table[(fires, entry.status, entry.source)] += 1
            if fires and entry.status != "no_data" and len(misses) < examples:
                misses.append(
                    f"  {manufacturer.brand_name or manufacturer.legal_name} | {entry.status} "
                    f"({entry.source}) | {requirement.text[:110]}"
                )

    total = sum(table.values())
    fired = {key: value for key, value in table.items() if key[0]}
    fired_total = sum(fired.values())
    fired_no_data = sum(value for key, value in fired.items() if key[1] == "no_data")
    print(f"Ячеек матрицы (требования к товару): {total}")
    print(
        f"Правило «ни слова в карточке» сработало бы: {fired_total} "
        f"({100 * fired_total / max(total, 1):.1f}%)"
    )
    print(
        f"  из них модель тоже сказала «нет данных»: {fired_no_data} "
        f"({100 * fired_no_data / max(fired_total, 1):.1f}%)"
    )
    print("  расхождения по статусу и источнику:")
    for (_, status, source), count in sorted(fired.items(), key=lambda item: -item[1]):
        if status != "no_data":
            print(f"    {status:10} {source:22} {count}")
    if misses:
        print("Примеры расхождений:")
        print("\n".join(misses))


def main(argv: list[str]) -> int:
    from app.db.session import SessionLocal

    if not argv or argv[0] != "probe":
        print(__doc__)
        return 1
    examples = int(argv[argv.index("--examples") + 1]) if "--examples" in argv else 20
    db = SessionLocal()
    try:
        probe(db, examples=examples)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
