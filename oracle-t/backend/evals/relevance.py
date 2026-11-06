"""Эталон и метрики отбора (слои A/B плана): профили и модель против решения специалиста.

    python -m evals.relevance --export golden/relevance.jsonl      # заморозить эталон
    python -m evals.relevance --golden golden/relevance.jsonl      # метрики по сохранённым отметкам
    python -m evals.relevance --golden golden/relevance.jsonl --replay   # + движок на текущих профилях
    python -m evals.relevance --golden ... --errors fn             # примеры ложных пропусков

ИИ не вызывается. Положительный класс — «релевантна» (решение человека: stage ≠ rejected).
Hold-out: `split` считается из хэша id закупки, поэтому стабилен между выгрузками.
"""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.source import Source
from app.models.tender import Tender
from app.services import relevance_service
from evals.metrics import by_group, confusion, scores

HUMAN_YES = {"under_review", "application_submitted", "won", "lost"}
HOLDOUT_SHARE = 0.3


def _split(tender_id: str) -> str:
    bucket = int(hashlib.sha1(tender_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "holdout" if bucket < HOLDOUT_SHARE else "train"


def export(path: Path) -> None:
    """Только закупки, по которым человек принял решение: «релевантна» или «неактуально»."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db, path.open("w", encoding="utf-8") as out:
        rows = db.execute(
            select(Tender, Source.key)
            .join(Source, Source.id == Tender.source_id, isouter=True)
            .where(Tender.stage.in_(HUMAN_YES | {"rejected"}))
            .where(Tender.relevance_marked_at.is_not(None))
        )
        n = 0
        for tender, source_key in rows:
            tid = str(tender.id)
            out.write(json.dumps({
                "id": tid,
                "split": _split(tid),
                "source": source_key,
                "title": tender.title,
                "customer_name": tender.customer_name,
                "procurement_method": tender.procurement_method,
                "okpd2_code": tender.okpd2_code,
                "actual": tender.stage in HUMAN_YES,
                "stage": tender.stage,
                "passed_filter": tender.passed_relevance_filter,
                "ai_relevant": tender.ai_relevant,
                "ai_reason": tender.ai_relevance_reason,
            }, ensure_ascii=False) + "\n")
            n += 1
    print(f"Выгружено: {n} → {path}")


class _Row:
    """Минимальная замена Tender для движка профилей (читает только эти поля)."""

    def __init__(self, row: dict):
        self.title = row["title"]
        self.customer_name = row["customer_name"]
        self.procurement_method = row["procurement_method"]
        self.okpd2_code = row["okpd2_code"]


def replay(rows: list[dict]) -> None:
    with SessionLocal() as db:
        profiles = relevance_service.default_profiles(db)
        for row in rows:
            outcome = relevance_service._decide(profiles, _Row(row), row["source"])
            row["replay_passed"] = None if outcome is None else outcome.passed
            row["replay_reason"] = None if outcome is None else outcome.reason


def report(rows: list[dict], split: str | None, has_replay: bool) -> None:
    if split:
        rows = [r for r in rows if r["split"] == split]
    print(f"Примеров: {len(rows)}" + (f" (split={split})" if split else ""))
    print(f"Релевантных по решению человека: {sum(r['actual'] for r in rows)}")
    systems = [("passed_filter", "Профили (сохранённая отметка)"),
               ("ai_relevant", "Модель (ai_relevant)")]
    if has_replay:
        systems.append(("replay_passed", "Профили (пересчёт сейчас)"))
    for field, title in systems:
        known = [r for r in rows if r[field] is not None]
        s = scores(confusion((bool(r[field]), r["actual"]) for r in known))
        print(f"\n== {title}: проверено {len(known)} из {len(rows)}")
        print(f"   TP={s['tp']} FP={s['fp']} FN={s['fn']} TN={s['tn']}  "
              f"precision={s['precision']} recall={s['recall']} F1={s['f1']}"
              + ("" if s["reliable"] else "  (мало данных)"))
        for name, g in by_group(rows, "source", field).items():
            flag = "" if g["reliable"] else " *мало"
            print(f"   {name:20} n={g['n']:4} P={g['precision']} R={g['recall']}{flag}")


def errors(rows: list[dict], kind: str, field: str, limit: int = 25) -> None:
    want_actual = kind == "fn"  # fn: человек «релевантна», система «нет»
    bad = [r for r in rows if r.get(field) is not None
           and bool(r[field]) != want_actual and r["actual"] == want_actual]
    print(f"\n{kind.upper()} по «{field}»: {len(bad)} (показано {min(limit, len(bad))})")
    for r in bad[:limit]:
        print(f"- [{r['source']}] {r['title'][:110]}\n    ОКПД2={r['okpd2_code']} "
              f"причина: {r.get('replay_reason') or r.get('ai_reason') or '—'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path)
    ap.add_argument("--golden", type=Path)
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--split", choices=["train", "holdout"])
    ap.add_argument("--errors", choices=["fn", "fp"])
    ap.add_argument("--field", default="passed_filter",
                    help="passed_filter | ai_relevant | replay_passed (для --errors)")
    args = ap.parse_args()
    if args.export:
        export(args.export)
        return
    if not args.golden:
        ap.error("нужен --export или --golden")
    rows = [json.loads(line) for line in args.golden.read_text(encoding="utf-8").splitlines()]
    if args.replay:
        replay(rows)
    report(rows, args.split, args.replay)
    if args.errors:
        errors(rows, args.errors, "replay_passed" if args.replay and args.field == "passed_filter"
               else args.field)


if __name__ == "__main__":
    main()
