"""ИИ-разметка неразмеченных написаний «Рынка ПУ» по структуре условного обозначения (12.11.2026).

Замечание аналитика (Тайпит, 61544-15 «НЕВА МТ 1»): «НЕВА МТ 114 AS E4PC» — 74 поверки строкой
«нет данных — написание не размечено», хотя всё нужное есть в «Описании типа»: на рисунке
«Структура условного обозначения» первая цифра после «МТ1» — тип корпуса (1 — крепление винтами,
шкаф; 2 — DIN-рейка), «AS» — только активная энергия (ПП 890 требует учёта активной и реактивной
энергии в двух направлениях — «2AR»), «E4» — интерфейс EIA 485. Неразмеченные позиции выпадают из
срезов «соответствует / не соответствует» и из блоков исполнения и интерфейсов — «серая зона», а
написаний сотни (ПУЛЬСАР, CE307, КВАНТ, НЕВА…) и по одной-две поверки: руками их не разметить.

Как работает. Шаблоны справочника аналитика (`ModificationMatcher`) по-прежнему первые — сначала
`remap_auto`. Что осталось — модели, пачкой на тип: фрагмент «Описания типа» с расшифровкой
обозначения (`si_types.description_type_text`), уже заведённые модификации и написания. По каждому
написанию модель либо указывает существующую модификацию (написание — это она), либо называет
новую — по значимым кодам обозначения, без токов и напряжений, — с фазой, исполнением,
интерфейсом и вердиктом ПП 890. Новая модификация заводится в справочник (её имя дальше работает
шаблоном для новых написаний того же исполнения), РПП ей проставит ближайший опрос ГИСП
(`meter_market_gisp.apply_gisp_rows` проходит по всем модификациям типов отчёта).

Привязка — `mapped_by = 'ai'`: ни `remap_auto(recheck=True)`, ни импорт её не пересматривают, а
аналитик видит в детализации, откуда она, и может переразметить вручную. Ответ с низкой
уверенностью не применяется — написание остаётся в «Разметить»: ложная привязка хуже честного
«не размечено».

Расшифровка кодов аналитика и перепроверка (13.11.2026, раздел 108). Аналитик (Тайпит): «надо, чтобы
Сова расшифровывала модификацию из Аршина по описанию типа и РЭ, а не сопоставляла с таблицей» —
в таблице несколько исполнений сведены в одну строку («НЕВА СТ221 G4/G3» — «GSM и RS»), хотя G3 —
модем NB-IoT. Описание типа этого не говорит («G — GSM/GPRS модем»), а руководство по эксплуатации —
да; такие знания аналитик пишет у типа в «Расшифровке кодов» (`meter_market_types.code_notes`), и
модель ставит их выше описания. Режим `recheck` отдаёт модели и уже привязанные написания (шаблоном,
импортом, ИИ — не вручную): если расшифровка написания расходится с атрибутами строки, к которой оно
привязано, написание уходит в отдельную модификацию по своему коду («НЕВА СТ221 G3»). Атрибуты
заведённой модификации ИИ правит, только когда написание — ровно она (то же имя-ключ) и либо у типа
есть расшифровка аналитика, либо интерфейса/исполнения в строке нет («н/д»); старое значение
остаётся в комментарии. Указанную по id модификацию с противоречащим интерфейсом модель не получает —
написание остаётся как было.

Запуск: `python -m app.services.meter_market_markup run [--types 61544-15,87229-22] [--min-count N] [--recheck]`;
после ночной загрузки поверок — само, для новых написаний (`core/scheduler.py`); перепроверка типа —
кнопка «Расшифровать заново» в справочнике.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from typing import Any

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.models.meter_market import DESIGN_LABELS, MappedBy, MeterMarketModification, MeterMarketType
from app.services.ai_client import run_structured
from app.services.ai_provider_service import AiNotConfiguredError, AiQuotaExceededError
from app.services.meter_market_dictionary import remap_auto
from app.services.meter_market_normalize import modification_key, normalize_interface

# Сколько знаков «Описания типа» передавать: расшифровка обозначения бывает на 3–4 листе
# (у НЕВА МТ 1 — с 4,5 тыс. знака), поэтому берётся окно вокруг неё, а не только начало.
DESCRIPTION_HEAD_CHARS = 2500
STRUCTURE_CHARS = 7000
CHUNK = 40
NO_INTERFACE = "без интерфейса"

_DESCRIPTION_HEAD = re.compile(r"описание\s+средства\s+измерени", re.IGNORECASE)
_STRUCTURE = re.compile(r"структур\w*\s+условного\s+обозначени|условн\w*\s+обозначени\w*\s+счетчик", re.IGNORECASE)

SYSTEM_PROMPT = """\
Ты — эксперт по приборам учёта электрической энергии и разметчик справочника модификаций.
Поверители во ФГИС «Аршин» пишут модификацию счётчика как придётся. Тебе дан тип средства
измерений (номер ГРСИ), фрагмент его «Описания типа» — главное в нём расшифровка структуры
условного обозначения, — уже заведённые модификации типа и список написаний из поверок.
Если дана «Расшифровка кодов от аналитика» (по руководству по эксплуатации), она ГЛАВНЕЕ
описания типа и твоих знаний: «G3 — NB-IoT» значит NB-IoT, даже если в описании «G — GSM».
Расшифровывай каждое написание по его собственным кодам, а не по похожей строке справочника.

По каждому написанию (id) ответь:
1. Если написание — это одна из уже заведённых модификаций (тот же корпус/модель, тот же вид
   энергии и интерфейс; их имена — шаблоны поиска, слова в любом порядке), укажи её
   modification_id и не заполняй name. Если модификация сводит несколько кодов («G4/G3»,
   «G3E/G4E»), а у кода написания другой интерфейс или вид энергии, чем у неё, — не указывай её,
   а дай name по коду написания («НЕВА СТ221 G3»). Если написание — ровно заведённая
   модификация, но её интерфейс не совпадает с расшифровкой или «н/д», дай name, равное её имени,
   и правильные атрибуты. «сейчас: …» у написания — текущая привязка, она может быть ошибочной.
   interface заполняй всегда — по расшифровке написания.
2. Иначе дай name — каноническое имя модификации: обозначение типа и значимые коды структуры
   обозначения, определяющие корпус/исполнение, вид измеряемой энергии, интерфейс и опции, —
   БЕЗ тока, напряжения и класса точности («НЕВА МТ 114 AS E4PC 5(60)A» → «НЕВА МТ 114 AS E4PC»).
   Написания одного исполнения с опечатками, кириллицей вместо латиницы, лишними пробелами
   («Е4 РС» = «E4PC») называй одинаково. Имена пиши так же, как у уже заведённых модификаций.
   Для нового имени заполни:
   - phase: "1ф" или "3ф" — по описанию типа (однофазный/трёхфазный), "" если нельзя понять;
   - design: "din" (на DIN-рейку / рейку ТН 35), "cabinet" (шкафное, крепление винтами /
     на щит), "split" (сплит-исполнение), "nd" — если по обозначению и описанию не понять;
   - interface: каналы удалённой связи по коду интерфейса в обозначении, как в справочнике
     аналитика: "RS" (RS-485 / EIA 485), "GSM", "PLC", "RF", "LoRaWAN", "NB-IoT", "Ethernet",
     "Wi-Fi", "M-Bus", "Bluetooth", "ZigBee", несколько — через " и " («GSM и RS»);
     "без интерфейса" — код прямо говорит, что удалённого интерфейса нет («O»);
     "н/д" — если кода интерфейса в написании нет и по описанию его не вывести.
     Оптический порт в interface не указывают;
   - smart_890: соответствует ли модификация требованиям к интеллектуальным ПУ (ПП РФ № 890,
     раздел III): "yes" / "no" / "unknown". Обязательные признаки: измерение активной и реактивной
     энергии в двух направлениях (у НЕВА — код «2AR»; «A», «AS», «2A», «AR» без «2» — только
     активная или одно направление → "no"), не менее двух интерфейсов связи (оптический + ещё
     один удалённый), журнал событий, профиль нагрузки, для прямого включения — встроенное реле
     и МПИ не менее 16 лет. Если обозначение прямо исключает признак — "no"; если всё
     подтверждается — "yes"; если по данным решить нельзя — "unknown".
3. confidence: "high" — расшифровка обозначения есть и написание разбирается однозначно;
   "medium" — по описанию и знаниям о линейке; "low" — догадка (такую разметку не применят).
4. reason — одна короткая фраза по-русски: какие коды что означают («114 — корпус для крепления
   винтами; AS — только активная; E4 — EIA 485»).
Не придумывай кодов, которых нет в описании типа. Если написание — только обозначение типа без
кодов («НЕВА МТ 1», «Нет модификации»), а у типа несколько исполнений — confidence "low"."""


class SpellingMarkup(pydantic.BaseModel):
    id: int
    modification_id: int | None = None
    name: str = ""
    phase: str = ""
    design: str = ""
    interface: str = ""
    smart_890: str = "unknown"
    confidence: str = "medium"
    reason: str = ""


class MarkupAnswer(pydantic.BaseModel):
    items: list[SpellingMarkup]


def designation_excerpt(db: Session, mit_number: str) -> str | None:
    """Начало раздела «Описание средства измерений» и окно вокруг «структуры условного
    обозначения» — там расшифровка кодов, без которой написание не разобрать."""

    value = db.execute(
        sql_text("SELECT description_type_text FROM si_types WHERE si_code = :c AND description_type_text <> '' LIMIT 1"),
        {"c": mit_number},
    ).scalar()
    if not value:
        return None
    head = _DESCRIPTION_HEAD.search(value)
    start = head.start() if head else 0
    parts = [value[start : start + DESCRIPTION_HEAD_CHARS]]
    structure = _STRUCTURE.search(value, start)
    if structure:
        begin = max(structure.start() - 300, start + DESCRIPTION_HEAD_CHARS)
        if begin < structure.start() + STRUCTURE_CHARS:
            parts.append(value[begin : structure.start() + STRUCTURE_CHARS])
    else:
        parts.append(value[start + DESCRIPTION_HEAD_CHARS : start + DESCRIPTION_HEAD_CHARS + STRUCTURE_CHARS])
    return re.sub(r"[ \t]+", " ", "\n…\n".join(p for p in parts if p)).strip()


def _user_text(
    meter_type: MeterMarketType,
    modifications: list[MeterMarketModification],
    spellings: list[dict[str, Any]],
    description: str | None,
) -> str:
    lines = [
        f"Тип СИ: {meter_type.mit_number}",
        f"Расшифровка кодов от аналитика (главнее описания типа): {meter_type.code_notes or '—'}",
        f"Обозначение: {meter_type.notation or '—'}",
        f"Наименование: {meter_type.title or '—'}",
        f"МПИ типа, лет: {meter_type.mpi_years if meter_type.mpi_years is not None else '—'}",
        "",
        "Заведённые модификации:",
    ]
    for mod in modifications:
        lines.append(
            f"- id={mod.id}; {mod.name}; фаза: {mod.phase or 'н/д'}; исполнение: {mod.design}; "
            f"интерфейс: {mod.interface or 'н/д'}"
        )
    if not modifications:
        lines.append("— нет")
    lines += ["", "Написания из поверок (id; написание; поверок):"]
    current = {m.id: m.name for m in modifications}
    lines += [
        f"- id={s['id']}; {s['raw']}; {s['count']}"
        + (f"; сейчас: {current[s['current']]}" if s.get("current") in current else "")
        for s in spellings
    ]
    lines += ["", "Фрагмент «Описания типа» (ФГИС «Аршин»):", description or "не загружено — суди по обозначению и знаниям о линейке."]
    return "\n".join(lines)


def _design(value: str) -> str:
    value = (value or "").strip().lower()
    return value if value in DESIGN_LABELS else "nd"


def _interface(value: str) -> str:
    value = (value or "").strip()
    if not value or value.lower() in ("н/д", "нд", "нет данных", "unknown"):
        return "н/д"
    if value.lower() in ("нет", "o", "без интерфейса", "без интерфейса удалённого доступа"):
        return NO_INTERFACE
    return normalize_interface(value) or "н/д"


def _known(value: str | None) -> bool:
    return bool(value) and value not in ("н/д", "nd")


def _correct_existing(
    meter_type: MeterMarketType, mod: MeterMarketModification, item: SpellingMarkup, now: datetime
) -> bool:
    """Написание — ровно эта модификация, а расшифровка даёт другой интерфейс/исполнение. Правит
    строку, только если у типа есть расшифровка кодов аналитика или в строке «н/д»: иначе слово
    модели против слова аналитика — оставляем аналитика."""

    if item.confidence.strip().lower() != "high":
        return False
    changes = []
    interface = _interface(item.interface)
    if _known(interface) and interface != mod.interface and (meter_type.code_notes or not _known(mod.interface)):
        changes.append(f"интерфейс «{mod.interface or 'н/д'}» → «{interface}»")
        mod.interface = interface
    design = _design(item.design)
    if design != "nd" and mod.design == "nd":
        changes.append(f"исполнение «н/д» → «{DESIGN_LABELS.get(design, design)}»")
        mod.design = design
    if not changes:
        return False
    note = f"ИИ по расшифровке кодов ({now:%d.%m.%Y}): {', '.join(changes)}; {(item.reason or '').strip()}"
    mod.comment = (f"{mod.comment}\n{note}" if mod.comment else note)[:1000]
    return True


def apply_answer(
    db: Session,
    meter_type: MeterMarketType,
    spellings: list[dict[str, Any]],
    answer: MarkupAnswer,
    *,
    recheck: bool = False,
) -> dict[str, int]:
    """Привязывает написания по ответу модели; новые модификации заводит в справочник. `recheck` —
    уже привязанные (не вручную) написания переносятся, если расшифровка дала другую модификацию."""

    stats = {"mapped": 0, "created": 0, "skipped": 0, "corrected": 0}
    by_id = {s["id"]: s for s in spellings}
    mods = db.scalars(select(MeterMarketModification).where(MeterMarketModification.type_id == meter_type.id)).all()
    by_key = {modification_key(m.name): m for m in mods}
    own_ids = {m.id for m in mods}
    now = datetime.now(timezone.utc)
    for item in answer.items:
        spelling = by_id.pop(item.id, None)
        if spelling is None:
            continue  # модель выдумала id или повторила написание
        if item.confidence.strip().lower() == "low":
            stats["skipped"] += 1
            continue
        target: MeterMarketModification | None = None
        if item.modification_id is not None and item.modification_id in own_ids:
            target = db.get(MeterMarketModification, item.modification_id)
            decoded = _interface(item.interface)
            if target is not None and _known(decoded) and _known(target.interface) and decoded != target.interface:
                # Модель указала строку, чей интерфейс противоречит её же расшифровке, — скорее
                # всего, сведённую «G4/G3»: такой привязке не верим.
                stats["skipped"] += 1
                continue
        elif item.name.strip():
            name = re.sub(r"\s+", " ", item.name).strip()[:500]
            target = by_key.get(modification_key(name))
            if target is not None and _correct_existing(meter_type, target, item, now):
                stats["corrected"] += 1
            if target is None:
                verdict = item.smart_890.strip().lower()
                confidence = {"high": "высокая", "medium": "средняя"}.get(item.confidence.strip().lower(), "средняя")
                reason = (item.reason or "").strip()
                phase = item.phase.strip().replace("ф", "") if item.phase else ""
                target = MeterMarketModification(
                    type_id=meter_type.id,
                    name=name,
                    phase=f"{phase}ф" if phase in ("1", "3") else None,
                    design=_design(item.design),
                    interface=_interface(item.interface),
                    mpi_years=meter_type.mpi_years,
                    comment=f"Заведено ИИ по структуре условного обозначения ({now:%d.%m.%Y}): {reason}"[:1000],
                    smart_890={"yes": True, "no": False}.get(verdict),
                    smart_890_source="ai",
                    smart_890_note=(f"[уверенность: {confidence}] {reason}" if verdict in ("yes", "no") else reason)[:500]
                    or None,
                    smart_890_at=now,
                )
                db.add(target)
                db.flush()
                by_key[modification_key(name)] = target
                own_ids.add(target.id)
                stats["created"] += 1
        if target is None:
            stats["skipped"] += 1
            continue
        if target.id == spelling.get("current"):
            continue  # перепроверка подтвердила привязку
        # Все неразмеченные написания типа с тем же ключом — туда же (опечатки одного исполнения);
        # при перепроверке — и привязанные не вручную.
        scope = "mapped_by IS DISTINCT FROM 'manual'" if recheck else "modification_id IS NULL"
        result = db.execute(
            sql_text(
                "UPDATE meter_market_variants SET modification_id = :m, mapped_by = :by "
                f"WHERE type_id = :t AND {scope} AND (id = :v OR norm_key = :k)"
            ),
            {"m": target.id, "by": MappedBy.AI.value, "t": meter_type.id, "v": spelling["id"], "k": spelling["norm_key"]},
        )
        stats["mapped"] += result.rowcount or 0
    db.flush()
    return stats


def _pending(
    db: Session, type_id: int, min_count: int, since: datetime | None = None, *, recheck: bool = False
) -> list[dict[str, Any]]:
    """Неразмеченные написания типа — по одному на ключ, самые массовые первыми. `since` — только
    появившиеся после этой отметки: ночное задание не переспрашивает модель о написаниях, по
    которым она уже не уверена (они ждут человека в «Разметить»). `recheck` — и привязанные не
    вручную (`current` — их текущая модификация)."""

    scope = "{v}.mapped_by IS DISTINCT FROM 'manual'" if recheck else "{v}.modification_id IS NULL"
    rows = db.execute(
        sql_text(
            "SELECT DISTINCT ON (v.norm_key) v.id, v.raw, v.norm_key, v.modification_id AS current, x.c AS count "
            "FROM meter_market_variants v "
            "JOIN (SELECT v2.norm_key, coalesce(sum(mv.count), 0) AS c FROM meter_market_variants v2 "
            "      LEFT JOIN meter_verification_monthly mv ON mv.variant_id = v2.id "
            f"      WHERE v2.type_id = :t AND {scope.format(v='v2')} GROUP BY 1) x ON x.norm_key = v.norm_key "
            f"WHERE v.type_id = :t AND {scope.format(v='v')} AND x.c >= :n "
            + ("AND v.created_at >= :s " if since else "")
            + "ORDER BY v.norm_key, v.id"
        ),
        {"t": type_id, "n": min_count, "s": since},
    ).mappings().all()
    return sorted((dict(r) for r in rows), key=lambda r: -int(r["count"]))


def markup_type(
    db: Session,
    meter_type: MeterMarketType,
    *,
    min_count: int = 1,
    since: datetime | None = None,
    recheck: bool = False,
) -> dict[str, int]:
    stats = {"mapped": 0, "created": 0, "skipped": 0, "corrected": 0, "failed": 0}
    stats["mapped"] += remap_auto(db, meter_type.id)
    db.commit()
    spellings = _pending(db, meter_type.id, min_count, since, recheck=recheck)
    if not spellings:
        return stats
    description = designation_excerpt(db, meter_type.mit_number)
    for start in range(0, len(spellings), CHUNK):
        chunk = spellings[start : start + CHUNK]
        mods = db.scalars(
            select(MeterMarketModification)
            .where(MeterMarketModification.type_id == meter_type.id)
            .order_by(MeterMarketModification.name)
        ).all()
        try:
            answer = run_structured(
                db,
                system_prompt=SYSTEM_PROMPT,
                user_text=_user_text(meter_type, list(mods), chunk, description),
                response_model=MarkupAnswer,
                temperature=0.0,
            )
            for key, value in apply_answer(db, meter_type, chunk, answer, recheck=recheck).items():
                stats[key] += value
            db.commit()
        except (AiNotConfiguredError, AiQuotaExceededError):
            # Ключа нет, отклонён или лимит исчерпан — следующие пачки упадут так же; ночное
            # задание не должно сотню раз писать одну и ту же ошибку в журнал ИИ.
            db.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - сбой одной пачки не должен ронять остальные
            db.rollback()
            stats["failed"] += 1
            logger.warning(f"Разметка ИИ: тип {meter_type.mit_number} не размечен: {exc}")
    return stats


def run(
    db: Session,
    *,
    mit_numbers: list[str] | None = None,
    min_count: int = 1,
    since: datetime | None = None,
    recheck: bool = False,
) -> dict[str, int]:
    """Размечает неразмеченные написания типов отчёта (или только `mit_numbers`; `since` — только
    новые написания, так зовёт ночное задание; `recheck` — расшифровать заново и привязанные)."""

    query = select(MeterMarketType).where(MeterMarketType.in_report.is_(True))
    if mit_numbers:
        query = query.where(MeterMarketType.mit_number.in_(mit_numbers))
    pending_types = set(
        db.execute(
            sql_text(
                "SELECT DISTINCT type_id FROM meter_market_variants WHERE "
                + ("mapped_by IS DISTINCT FROM 'manual'" if recheck else "modification_id IS NULL")
                + (" AND created_at >= :s" if since else "")
            ),
            {"s": since},
        ).scalars()
    )
    total = {"types": 0, "mapped": 0, "created": 0, "skipped": 0, "corrected": 0, "failed": 0}
    for meter_type in db.scalars(query.order_by(MeterMarketType.mit_number)).all():
        if meter_type.id not in pending_types:
            continue
        total["types"] += 1
        try:
            done = markup_type(db, meter_type, min_count=min_count, since=since, recheck=recheck)
        except (AiNotConfiguredError, AiQuotaExceededError) as exc:
            logger.warning(f"Разметка ИИ остановлена: {exc}")
            total["failed"] += 1
            total["stopped"] = 1
            break
        for key, value in done.items():
            total[key] += value
    return total


if __name__ == "__main__":
    from app.db.session import SessionLocal

    usage = "python -m app.services.meter_market_markup run [--types 61544-15,87229-22] [--min-count N] [--recheck]"
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        raise SystemExit(usage)

    def _arg(flag: str) -> str | None:
        if flag not in sys.argv:
            return None
        position = sys.argv.index(flag)
        if position + 1 >= len(sys.argv):
            raise SystemExit(usage)
        return sys.argv[position + 1]

    selected = [x.strip() for x in (_arg("--types") or "").split(",") if x.strip()] or None
    with SessionLocal() as session:
        print(
            run(
                session,
                mit_numbers=selected,
                min_count=int(_arg("--min-count") or 1),
                recheck="--recheck" in sys.argv,
            )
        )
