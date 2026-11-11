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

Запуск: `python -m app.services.meter_market_smart run [--unknown | --all]`.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy import text as sql_text
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

Как считать интерфейсы. Поле «интерфейс» у модификации перечисляет только каналы удалённой
связи (GSM, RS-485, PLC, RF, LoRa, Ethernet и т. п.). Оптический порт в нём НЕ указывают никогда —
его отсутствие в поле не значит, что оптопорта нет. Оптический порт — это уже один интерфейс:
если оптопорт есть (так сказано в описании типа или ты знаешь это о линейке), модификация с одним
удалённым каналом (например, «GSM») имеет два интерфейса. Не отвечай "no" только потому, что в
поле «интерфейс» указан один канал. Если ниже приведён фрагмент «Описания типа», он важнее
твоих общих знаний: что в нём сказано про состав счётчика (оптический порт, каналы связи, журнал,
реле, профиль), то и считай общим для всех модификаций типа.

Тебе дан тип средства измерений и список его модификаций (в одном описании типа бывают и
интеллектуальные, и простейшие исполнения). По каждой модификации дай вердикт:
— "yes" — прибор отвечает требованиям раздела III;
— "no" — прибор простейший или заведомо не отвечает;
— "unknown" — прибор тебе незнаком и по данным судить нельзя.
Опирайся не только на переданные поля, но и на свои знания о линейках приборов: «НАРТИС-И100»,
«Энергомера СЕ208/СЕ308», «Меркурий 234/236», «МИРТЕК», «Милур», «Матрица» и т. п. — если ты
знаешь, что линейка выпускается как интеллектуальная (оптический порт плюс RS-485/PLC/GSM/LoRa,
журнал событий, профиль, реле), а модификация это подтверждает, отвечай "yes"; если знаешь, что
модификация без цифровых интерфейсов и журналов, — "no". "unknown" — только когда прибор тебе
незнаком. Не придумывай характеристик, которых не знаешь.
confidence — твоя уверенность: "high" (линейка и функции хорошо известны или явно следуют из
данных), "medium" (вывод по линейке и обозначению), "low" (скорее догадка).
В reason — одна короткая фраза по-русски, какие признаки определили вердикт.
Ориентируйся на обозначение модификации, фазность, интерфейсы и межповерочный интервал
(для прямого включения требуется не менее 16 лет, для трансформаторного — не менее 10).

Текст раздела III:
{REQUIREMENTS}"""


class ModificationVerdict(pydantic.BaseModel):
    id: int
    verdict: str  # yes | no | unknown
    confidence: str = "medium"  # high | medium | low
    reason: str = ""


class TypeAnswer(pydantic.BaseModel):
    verdicts: list[ModificationVerdict]


# Сколько знаков «Описания типа» передавать модели: раздел «Описание средства измерений»
# (состав, интерфейсы, функции) укладывается в 2–4 тыс. знаков.
DESCRIPTION_CHARS = 4000
_DESCRIPTION_HEAD = re.compile(r"описание\s+средства\s+измерени", re.IGNORECASE)


def description_excerpt(db: Session, mit_number: str) -> str | None:
    """Фрагмент «Описания типа» из Аршина (`si_types.description_type_text`) для модели.

    Замечание тестировщика 11.11.2026: КПЗ M2M-1/M2M-3 соответствуют ПП 890, а ИИ ответил «не
    соответствует» — видимо, по полю «интерфейс: GSM» решил, что интерфейс один. В описании типа
    прямо сказано: «оптический порт, основной канал связи: 2G (GPRS)…», — это два интерфейса.
    Модель видела только справочник, где оптопорт не указывают; теперь, если текст описания
    загружен, она видит и его."""

    text_value = db.execute(
        sql_text("SELECT description_type_text FROM si_types WHERE si_code = :c AND description_type_text <> '' LIMIT 1"),
        {"c": mit_number},
    ).scalar()
    if not text_value:
        return None
    head = _DESCRIPTION_HEAD.search(text_value)
    start = head.start() if head else 0
    return re.sub(r"[ \t]+", " ", text_value[start : start + DESCRIPTION_CHARS]).strip()


def _type_text(
    meter_type: MeterMarketType, modifications: list[MeterMarketModification], description: str | None = None
) -> str:
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
    if description:
        lines += ["", "Фрагмент «Описания типа» (ФГИС «Аршин»):", description]
    else:
        lines += ["", "«Описание типа» не загружено — суди по обозначению и знаниям о линейке."]
    return "\n".join(lines)


def classify_type(db: Session, meter_type: MeterMarketType, modifications: list[MeterMarketModification]) -> int:
    """Оценивает модификации одного типа; возвращает, сколько получили ответ."""

    answer = run_structured(
        db,
        system_prompt=SYSTEM_PROMPT,
        user_text=_type_text(meter_type, modifications, description_excerpt(db, meter_type.mit_number)),
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
        confidence = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(item.confidence.strip().lower(), "средняя")
        reason = (item.reason or "").strip() or ("Данных недостаточно" if verdict not in ("yes", "no") else "")
        mod.smart_890_note = (f"[уверенность: {confidence}] {reason}" if verdict in ("yes", "no") else reason)[:500] or None
        mod.smart_890_at = now
        done += 1
    db.flush()
    return done


def run(
    db: Session, *, mode: str = "new", limit_types: int | None = None, mit_numbers: list[str] | None = None
) -> dict[str, int]:
    """Оценивает типы отчёта. `mode`: `new` — только те, которых ИИ ещё не смотрел; `unknown` —
    ещё и те, где ИИ ответил «не определено» (после смены промпта); `all` — все, кроме ручных.
    `mit_numbers` — только эти типы (перепроверка по замечанию, без прохода по всему отчёту)."""

    query_types = select(MeterMarketType).where(MeterMarketType.in_report.is_(True))
    if mit_numbers:
        query_types = query_types.where(MeterMarketType.mit_number.in_(mit_numbers))
    types = db.scalars(query_types.order_by(MeterMarketType.mit_number)).all()
    stats = {"types": 0, "modifications": 0, "failed": 0}
    for meter_type in types:
        if limit_types is not None and stats["types"] >= limit_types:
            break
        query = select(MeterMarketModification).where(
            MeterMarketModification.type_id == meter_type.id,
            (MeterMarketModification.smart_890_source.is_(None)) | (MeterMarketModification.smart_890_source != "manual"),
        )
        if mode == "new":
            query = query.where(MeterMarketModification.smart_890_at.is_(None))
        elif mode == "unknown":
            query = query.where(
                MeterMarketModification.smart_890_at.is_(None) | MeterMarketModification.smart_890.is_(None)
            )
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

    usage = "python -m app.services.meter_market_smart run [--unknown | --all] [--types 79474-20,79481-20]"
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        raise SystemExit(usage)
    selected = None
    if "--types" in sys.argv:
        position = sys.argv.index("--types")
        if position + 1 >= len(sys.argv):
            raise SystemExit(usage)
        selected = [x.strip() for x in sys.argv[position + 1].split(",") if x.strip()]
    with SessionLocal() as session:
        mode = "all" if "--all" in sys.argv else "unknown" if "--unknown" in sys.argv else "new"
        print(run(session, mode=mode, mit_numbers=selected))
