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

Другие замеры (12.11.2026, все только читают базу):
* `single-pass` — один проход с руководством в запасе места вместо второго прохода: сколько
  обращений и символов экономит и не теряет ли строки каталога (включено в расчёт);
* `rules-check` — ячейки, о которых модель больше не спрашивается (`decided_by_rules`):
  итог по каждой не должен зависеть от ответа модели (включено в расчёт);
* `positions [--limit N]` — сколько запросов экономит объединение позиций лота с одинаковой
  карточкой (включено в расчёт).

Запуск: `python -m app.services.compliance_prefilter probe|single-pass|rules-check|positions`.
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


def _best_units(units: list, requirement: Requirement, top: int = 2) -> list[str]:
    """Лучшие куски карточки для требования — той же мерой, что `focus_context`."""

    import math

    from app.services.compliance_service import _parameter_of, _stems, parameter_hint

    query = _stems(requirement.normalized_text or requirement.text or "") | _stems(
        parameter_hint(_parameter_of(requirement)) or ""
    )
    frequency: dict[str, int] = {}
    for unit in units:
        for stem in unit.stems:
            frequency[stem] = frequency.get(stem, 0) + 1
    scored = []
    for unit in units:
        if unit.pinned:
            continue
        common = unit.stems & query
        if common:
            value = sum(math.log(1 + len(units) / frequency[stem]) for stem in common)
            scored.append((-value / math.sqrt(len(unit.text) + 40), unit.text))
    return [text for _, text in sorted(scored)[:top]]


def single_pass(db: Session) -> None:
    """Сколько обращений и символов карточки дал бы один проход с руководством вместо двух и
    не вытеснит ли руководство из карточки строку каталога, по которой модель уже ответила
    (12.11.2026, раздел 104). Ничего не меняет."""

    from app.services.compliance_service import (
        MANUAL_EXTRA_CHARS,
        REQUIREMENTS_PER_REQUEST,
        _focus_limit,
        build_manufacturer_context,
        focus_context,
        with_manual_extras,
    )
    from app.services.product_relevance import tender_named_text

    covered = manual_cells = covered_now = 0
    rows = db.execute(
        select(ComplianceMatrixEntry, Requirement)
        .join(Requirement, Requirement.id == ComplianceMatrixEntry.requirement_id)
        .where(Requirement.kind == RequirementKind.PRODUCT.value)
        .order_by(Requirement.created_at)
    ).all()
    by_pair: dict[tuple, list] = defaultdict(list)
    for entry, requirement in rows:
        by_pair[(entry.tender_id, entry.manufacturer_id)].append((entry, requirement))

    calls_now = calls_single = chars_now = chars_single = 0
    checked = lost = 0
    lost_examples: list[str] = []
    for (tender_id, manufacturer_id), pairs in by_pair.items():
        tender = db.get(Tender, tender_id)
        manufacturer = db.get(Manufacturer, manufacturer_id)
        requirements = [requirement for _, requirement in pairs]
        named = tender_named_text(db, tender)
        plain, _ = build_manufacturer_context(
            db, manufacturer, include_manual=False, requirements=requirements, named_text=named
        )
        full, _ = build_manufacturer_context(
            db, manufacturer, include_manual=True, requirements=requirements, named_text=named
        )
        if not plain.strip():
            continue
        unresolved = [
            requirement
            for entry, requirement in pairs
            if entry.source == "user_manual_fallback"
            or (entry.status == "no_data" and entry.source == "ai_semantic")
        ]
        plain_units = card_index(plain)[0]
        for offset in range(0, len(requirements), REQUIREMENTS_PER_REQUEST):
            batch = requirements[offset : offset + REQUIREMENTS_PER_REQUEST]
            limit = _focus_limit(db, batch)
            card_plain = focus_context(plain, batch, limit)
            card_full = with_manual_extras(card_plain, full, batch, MANUAL_EXTRA_CHARS)
            calls_now += 1
            calls_single += 1
            chars_now += len(card_plain)
            chars_single += len(card_full)
            if full == plain:
                continue
            # Ответ, найденный вторым проходом: попадает ли нужная строка руководства в запас.
            full_units = card_index(full)[0]
            manual_units = [unit for unit in full_units if unit.text.startswith("[manual]")]
            for entry, requirement in pairs:
                if entry.source != "user_manual_fallback" or requirement not in batch:
                    continue
                manual_cells += 1
                best = _best_units(manual_units, requirement, top=1)
                if best and best[0] in card_full:
                    covered += 1
            answered = {
                requirement.id
                for entry, requirement in pairs
                if entry.source in ("product_catalog", "si_type")
            }
            for requirement in batch:
                if requirement.id not in answered:
                    continue
                checked += 1
                for text in _best_units(plain_units, requirement):
                    if text in card_plain and text not in card_full:
                        lost += 1
                        if len(lost_examples) < 15:
                            lost_examples.append(
                                f"  {manufacturer.brand_name or manufacturer.legal_name} | "
                                f"{requirement.text[:70]} | потеряно: {text[:80]!r}"
                            )
                        break
        if full != plain and unresolved:
            manual_units = [unit for unit in card_index(full)[0] if unit.text.startswith("[manual]")]
            fallback_ids = {
                requirement.id for entry, requirement in pairs if entry.source == "user_manual_fallback"
            }
            for offset in range(0, len(unresolved), REQUIREMENTS_PER_REQUEST):
                batch = unresolved[offset : offset + REQUIREMENTS_PER_REQUEST]
                calls_now += 1
                card_now = focus_context(full, batch, _focus_limit(db, batch))
                chars_now += len(card_now)
                for requirement in batch:
                    if requirement.id in fallback_ids:
                        best = _best_units(manual_units, requirement, top=1)
                        if best and best[0] in card_now:
                            covered_now += 1

    print(f"Обращений к модели сейчас (два прохода): {calls_now}, одним проходом: {calls_single} "
          f"(−{100 * (calls_now - calls_single) / max(calls_now, 1):.0f}%)")
    print(f"Символов карточки сейчас: {chars_now:,}, одним проходом: {chars_single:,} "
          f"(−{100 * (chars_now - chars_single) / max(chars_now, 1):.0f}%)")
    print(f"Ячеек с ответом по каталогу/описанию типа проверено: {checked}; лучший факт вытеснен "
          f"руководством: {lost} ({100 * lost / max(checked, 1):.1f}%)")
    print(f"Ответов из руководства (второй проход): {manual_cells}; нужная строка руководства "
          f"попадает в запас {MANUAL_EXTRA_CHARS} символов: {covered} "
          f"({100 * covered / max(manual_cells, 1):.0f}%); во втором проходе сейчас — {covered_now} "
          f"({100 * covered_now / max(manual_cells, 1):.0f}%)")
    if lost_examples:
        print("\n".join(lost_examples))


def rules_check(db: Session) -> None:
    """Точность `decided_by_rules` на закупках базы: по каждой паре «закупка × производитель»
    правила кода прогоняются трижды — модель будто ответила «соответствует», «не
    соответствует» и «нет данных». Ячейка, о которой модель теперь не спрашивается, обязана
    получить одинаковый итог во всех трёх прогонах. Ничего не меняет."""

    from app.models.analysis import ComplianceStatus
    from app.services import compliance_service as cs
    from app.services.product_relevance import tender_named_text

    manufacturers = list(db.scalars(select(Manufacturer)))
    tender_ids = db.scalars(
        select(Requirement.tender_id).where(Requirement.kind == RequirementKind.PRODUCT.value).distinct()
    ).all()
    total = decided = unstable = 0
    examples: list[str] = []
    answers = (ComplianceStatus.MEETS.value, ComplianceStatus.NOT_MEETS.value, ComplianceStatus.NO_DATA.value)
    for tender_id in tender_ids:
        tender = db.get(Tender, tender_id)
        requirements = list(
            db.scalars(
                select(Requirement)
                .where(Requirement.tender_id == tender_id, Requirement.kind == RequirementKind.PRODUCT.value)
                .order_by(Requirement.created_at)
            )
        )
        reference = cs.find_reference(
            requirements, manufacturers, db=db, named_text=tender_named_text(db, tender)
        )
        for manufacturer in manufacturers:
            made_kinds = cs.manufacturer_kind_groups(db, manufacturer)
            marked = [
                index
                for index, requirement in enumerate(requirements, start=1)
                if cs.decided_by_rules(requirement, manufacturer, reference, manufacturers)
            ]
            total += len(requirements)
            decided += len(marked)
            if not marked:
                continue
            finals = []
            for answer in answers:
                verdicts = {
                    index: cs.RequirementVerdict(
                        number=index, status=answer, source="catalog", explanation="ответ модели", confidence=0.9
                    )
                    for index in range(1, len(requirements) + 1)
                }
                cs.apply_confidence_floor(requirements, verdicts)
                cs.apply_transformer_scope(requirements, verdicts)
                cs.apply_duration_check(requirements, verdicts)
                cs.apply_evidence_check(requirements, verdicts)
                cs.apply_expert_rules(manufacturer, requirements, verdicts, made_kinds=made_kinds)
                cs.apply_lot_variants(requirements, verdicts)
                cs.apply_reference_rules(manufacturer, requirements, verdicts, reference, manufacturers)
                finals.append({index: (verdicts[index].status, verdicts[index].source) for index in marked})
            for index in marked:
                if len({final[index] for final in finals}) > 1:
                    unstable += 1
                    if len(examples) < 10:
                        examples.append(
                            f"  {manufacturer.brand_name} | {requirements[index - 1].text[:80]} | "
                            f"{[final[index] for final in finals]}"
                        )
    print(f"Ячеек: {total}; модель больше не спрашивается: {decided} ({100 * decided / max(total, 1):.1f}%)")
    print(f"Из них итог зависит от ответа модели (должно быть 0): {unstable}")
    if examples:
        print("\n".join(examples))


def positions(db: Session, limit_tenders: int | None = None) -> None:
    """Сколько запросов к модели дают позиции лота сейчас (группа — своя карточка) и сколько
    после объединения позиций с одинаковой карточкой (`_ask_plans`). Считает только
    карточки, к модели не обращается и ничего не меняет."""

    import math

    from app.services.compliance_service import (
        REQUIREMENTS_PER_REQUEST,
        build_manufacturer_context,
        lot_groups,
    )
    from app.services.product_relevance import tender_named_text

    manufacturers = list(db.scalars(select(Manufacturer)))
    tender_ids = db.scalars(
        select(Requirement.tender_id)
        .where(Requirement.kind == RequirementKind.PRODUCT.value, Requirement.position.is_not(None))
        .distinct()
    ).all()
    if limit_tenders:
        tender_ids = tender_ids[:limit_tenders]
    before = after = groups_total = 0
    for number, tender_id in enumerate(tender_ids, start=1):
        print(f"  закупка {number} из {len(tender_ids)}…", file=sys.stderr, flush=True)
        tender = db.get(Tender, tender_id)
        requirements = list(
            db.scalars(
                select(Requirement)
                .where(Requirement.tender_id == tender_id, Requirement.kind == RequirementKind.PRODUCT.value)
                .order_by(Requirement.created_at)
            )
        )
        groups = lot_groups([item.position for item in requirements])
        if len(groups) < 2:
            continue
        named = tender_named_text(db, tender)
        for manufacturer in manufacturers:
            sizes: dict[str, int] = {}
            for label, indices in groups:
                subset = [requirements[index - 1] for index in indices]
                context, _ = build_manufacturer_context(
                    db, manufacturer, include_manual=False,
                    requirements=subset if label else requirements,
                    named_text=named, only_wanted_kinds=label is not None,
                )
                if not context.strip():
                    continue
                groups_total += 1
                before += math.ceil(len(subset) / REQUIREMENTS_PER_REQUEST)
                sizes[context] = sizes.get(context, 0) + len(subset)
            after += sum(math.ceil(size / REQUIREMENTS_PER_REQUEST) for size in sizes.values())
    print(f"Закупок с позициями: {len(tender_ids)}; групп «позиция × производитель»: {groups_total}")
    print(f"Запросов матрицы по ним сейчас: {before}, с объединением одинаковых карточек: {after} "
          f"(−{100 * (before - after) / max(before, 1):.0f}%)")


def main(argv: list[str]) -> int:
    from app.db.session import SessionLocal

    if not argv or argv[0] not in ("probe", "single-pass", "rules-check", "positions"):
        print(__doc__)
        return 1
    examples = int(argv[argv.index("--examples") + 1]) if "--examples" in argv else 20
    db = SessionLocal()
    try:
        if argv[0] == "single-pass":
            single_pass(db)
        elif argv[0] == "rules-check":
            rules_check(db)
        elif argv[0] == "positions":
            limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
            positions(db, limit_tenders=limit)
        else:
            probe(db, examples=examples)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
