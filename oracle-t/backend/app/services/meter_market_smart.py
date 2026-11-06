"""Соответствие модификаций ПУ постановлению 890 — «интеллектуальный ПУ» (06.11.2026).

Аналитик: соответствие ПП РФ 890 — это не локализация. Счётчик должен быть интеллектуальным,
то есть обеспечивать функции раздела III постановления («Перечень функций приборов учёта,
которые могут быть присоединены к интеллектуальной системе учёта, и требования к ним»).
В одном описании типа выпускаются и интеллектуальные ПУ, и простейшие — поэтому признак у
**модификации**, а не у типа. Определяет его ИИ по тексту раздела III (`seed/pp890_section_iii.txt`);
решение аналитика (`smart_890_source = 'manual'`) модель не перезаписывает.

Один вызов — один тип со всеми его неоценёнными модификациями: ~100 типов вместо тысячи
вызовов. Модель не знает того, чего нет в данных (в справочнике — обозначение типа, имя
модификации, фаза, интерфейс, МПИ), и обязана отвечать «не определено», когда по этим данным
решить нельзя: ложное «да» в отчёте хуже, чем честное «н/д».

Запуск: `python -m app.services.meter_market_smart run [--all]`.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meter_market import MeterMarketModification, MeterMarketType
from app.services.ai_client import run_structured

REQUIREMENTS = (Path(__file__).resolve().parent.parent / "seed" / "pp890_section_iii.txt").read_text(encoding="utf-8")

SYSTEM_PROMPT = f"""\
Ты — эксперт по приборам учёта электрической энергии. Нужно определить, является ли прибор
«интеллектуальным» в смысле постановления Правительства РФ № 890: удовлетворяет ли он
требованиям раздела III (текст ниже) к приборам учёта, которые могут быть присоединены к
интеллектуальной системе учёта.

Признаки интеллектуального ПУ по этому разделу: два интерфейса связи (оптический и ещё один;
для трансформаторного включения — RS-485 или Ethernet), журнал событий, электронные пломбы,
профиль нагрузки, тарифное расписание, защищённый обмен с системой учёта, встроенный
коммутационный аппарат для управления нагрузкой (для прямого включения) и т. п. Простейшие
счётчики — без цифровых интерфейсов, журналов и профилей — не соответствуют.

Тебе дан тип средства измерений и список его модификаций (в одном описании типа бывают и
интеллектуальные, и простейшие исполнения). По каждой модификации дай вердикт:
— "yes" — по обозначению и данным прибор отвечает требованиям раздела III;
— "no" — прибор простейший или заведомо не отвечает;
— "unknown" — данных недостаточно. Не угадывай: лучше "unknown", чем ошибочное "yes".
Ориентируйся на обозначение модификации, фазность, интерфейсы и межповерочный интервал
(для прямого включения требуется не менее 16 лет, для трансформаторного — не менее 10).
В reason — одна короткая фраза по-русски, какие признаки определили вердикт.

Текст раздела III:
{REQUIREMENTS}"""


class ModificationVerdict(pydantic.BaseModel):
    id: int
    verdict: str  # yes | no | unknown
    reason: str = ""


class TypeAnswer(pydantic.BaseModel):
    verdicts: list[ModificationVerdict]


def _type_text(meter_type: MeterMarketType, modifications: list[MeterMarketModification]) -> str:
    lines = [
        f"Тип СИ: {meter_type.mit_number}",
        f"Обозначение: {meter_type.notation or '—'}",
        f"Наименование: {meter_type.title or '—'}",
        f"МПИ типа, лет: {meter_type.mpi_years if meter_type.mpi_years is not None else '—'}",
        "",
        "Модификации:",
    ]
    for mod in modifications:
        lines.append(
            f"- id={mod.id}; {mod.name}; фаза: {mod.phase or 'н/д'}; интерфейс: {mod.interface or 'н/д'}; "
            f"МПИ, лет: {mod.mpi_years if mod.mpi_years is not None else 'н/д'}"
        )
    return "\n".join(lines)


def classify_type(db: Session, meter_type: MeterMarketType, modifications: list[MeterMarketModification]) -> int:
    """Оценивает модификации одного типа; возвращает, сколько получили ответ."""

    answer = run_structured(
        db,
        system_prompt=SYSTEM_PROMPT,
        user_text=_type_text(meter_type, modifications),
        response_model=TypeAnswer,
        temperature=0.0,
    )
    by_id = {m.id: m for m in modifications}
    now = datetime.now(timezone.utc)
    done = 0
    for item in answer.verdicts:
        mod = by_id.get(item.id)
        if mod is None or mod.smart_890_source == "manual":
            continue  # модель выдумала id или решение принадлежит аналитику
        verdict = item.verdict.strip().lower()
        mod.smart_890 = {"yes": True, "no": False}.get(verdict)  # unknown → None
        mod.smart_890_source = "ai"
        mod.smart_890_note = (item.reason or "")[:500] or ("Данных недостаточно" if verdict not in ("yes", "no") else None)
        mod.smart_890_at = now
        done += 1
    db.flush()
    return done


def run(db: Session, *, only_new: bool = True, limit_types: int | None = None) -> dict[str, int]:
    """Оценивает типы отчёта. `only_new` — только модификации, которых ИИ ещё не смотрел."""

    types = db.scalars(
        select(MeterMarketType).where(MeterMarketType.in_report.is_(True)).order_by(MeterMarketType.mit_number)
    ).all()
    stats = {"types": 0, "modifications": 0, "failed": 0}
    for meter_type in types:
        if limit_types is not None and stats["types"] >= limit_types:
            break
        query = select(MeterMarketModification).where(
            MeterMarketModification.type_id == meter_type.id,
            (MeterMarketModification.smart_890_source.is_(None)) | (MeterMarketModification.smart_890_source != "manual"),
        )
        if only_new:
            query = query.where(MeterMarketModification.smart_890_at.is_(None))
        modifications = db.scalars(query.order_by(MeterMarketModification.id)).all()
        if not modifications:
            continue
        stats["types"] += 1
        # Крупные типы — пачками: длинный JSON YandexGPT рвёт по лимиту токенов на выходе.
        for start in range(0, len(modifications), 25):
            chunk = modifications[start : start + 25]
            try:
                stats["modifications"] += classify_type(db, meter_type, chunk)
                db.commit()
            except Exception as exc:  # noqa: BLE001 - сбой одного типа не должен ронять остальные
                db.rollback()
                stats["failed"] += 1
                logger.warning(f"ПП 890: тип {meter_type.mit_number} не оценён: {exc}")
    return stats


if __name__ == "__main__":
    from app.db.session import SessionLocal

    if len(sys.argv) < 2 or sys.argv[1] != "run":
        raise SystemExit("python -m app.services.meter_market_smart run [--all]")
    with SessionLocal() as session:
        print(run(session, only_new="--all" not in sys.argv))
