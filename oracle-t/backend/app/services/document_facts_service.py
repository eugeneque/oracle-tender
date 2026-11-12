"""Позиции, условия поставки и условия договора — одним запросом к модели (12.11.2026, раздел 104).

Для Bitrix24 полный разбор делал три отдельных запроса по одним и тем же документам:
таблица позиций (`tender_positions`), условия поставки (`delivery_terms_service`), условия
договора (`contract_conditions_service`). Фрагменты документов у них во многом общие —
договор и ТЗ, — и каждый запрос заново отправлял их модели.

Здесь фрагменты собираются один раз — объединением тех же признаков с теми же окнами строк,
— и модель отвечает на три задачи сразу, с теми же системными инструкциями и схемами ответа.
Сохранение — теми же функциями, что у отдельных запросов (`save_positions`, `save_answer`).

Безопасность — прежде экономии:
* общий запрос идёт, только если объединённые фрагменты помещаются в окно модели **целиком**:
  иначе каждая задача получила бы меньше текста, чем в своём отдельном запросе;
* сбой общего запроса (ответ не по схеме, обрыв по лимиту вывода, ошибка сети) — не потеря:
  `extract_all` возвращает `None`, и разбор делает три прежних запроса;
* закончились деньги или модель выключена — ошибка поднимается, как у отдельных запросов.
* задача без своих фрагментов (нет таблицы позиций, нет слов о поставке) не перезаписывает
  прежние данные — как у отдельного запроса, который при пустом тексте модель не вызывает.
"""

from __future__ import annotations

import pydantic
from loguru import logger
from sqlalchemy.orm import Session

from app.models.tender import Tender
from app.models.user import User
from app.services import contract_conditions_service as conditions
from app.services import delivery_terms_service as delivery
from app.services import tender_positions as positions
from app.services.ai_client import fit_chars, run_structured
from app.services.ai_provider_service import AiNotConfiguredError, AiQuotaExceededError

# Как у отдельных запросов: сколько текста хотелось бы на задачу; общий запрос берёт столько,
# сколько вмещает окно модели, и при нехватке уступает трём отдельным.
DESIRED_CHARS = 3 * 60_000


class BitrixFactsAnswer(pydantic.BaseModel):
    """Все поля обязательны: Yandex AI Studio не принимает схему с необязательными полями."""

    positions: list[positions.PositionAnswer]
    delivery: delivery.DeliveryAnswer
    conditions: conditions.ConditionsAnswer


SYSTEM_PROMPT = (
    "По одной и той же закупочной документации выполни три задачи и верни один JSON с полями "
    "«positions» (задача 1), «delivery» (задача 2) и «conditions» (задача 3). Каждую задачу "
    "решай по её инструкции так, как если бы она была единственной; если по задаче в "
    "документации ничего нет — верни для неё пустой ответ по её схеме.\n\n"
    f"=== ЗАДАЧА 1 — positions (массив позиций) ===\n{positions._SYSTEM_PROMPT}\n\n"
    f"=== ЗАДАЧА 2 — delivery ===\n{delivery._SYSTEM_PROMPT}\n\n"
    f"=== ЗАДАЧА 3 — conditions ===\n{conditions._SYSTEM_PROMPT}"
)


def _windows(lines: list[str], pattern, before: int, after: int, extra=None) -> set[int]:
    keep: set[int] = set()
    for index, line in enumerate(lines):
        if pattern.search(line) or (extra is not None and extra(line)):
            keep.update(range(max(0, index - before), min(len(lines), index + after)))
    return keep


def combined_text(db: Session, tender: Tender, limit: int) -> tuple[str, dict[str, bool], bool]:
    """Объединённые фрагменты трёх задач, есть ли фрагменты у каждой и поместились ли все.

    Окна строк — те же, что у отдельных запросов: вокруг признаков таблицы позиций
    (`tender_positions`), вокруг слов о поставке и об условиях договора (`fragments_text`)."""

    parts: list[str] = []
    length = 0
    present = {"positions": False, "delivery": False, "conditions": False}
    complete = True
    for document in delivery._ordered_documents(db, tender):  # noqa: SLF001 - общий порядок документов
        lines = (document.extracted_text or "").splitlines()
        spec = _windows(
            lines, positions._SPEC_RE, positions._CONTEXT_BEFORE, positions._CONTEXT_AFTER,
            extra=lambda line: " | " in line,
        )
        supply = _windows(lines, delivery._DELIVERY_RE, delivery._CONTEXT_BEFORE, delivery._CONTEXT_AFTER)
        terms = _windows(lines, conditions._CONDITIONS_RE, delivery._CONTEXT_BEFORE, delivery._CONTEXT_AFTER)
        present["positions"] |= bool(spec)
        present["delivery"] |= bool(supply)
        present["conditions"] |= bool(terms)
        keep = spec | supply | terms
        if not keep:
            continue
        chunks: list[str] = []
        previous = None
        for index in sorted(keep):
            if previous is not None and index != previous + 1:
                chunks.append("…")
            chunks.append(lines[index])
            previous = index
        body = "\n".join(chunks)
        header = f"\n=== {document.file_name} ===\n"
        if length + len(header) + len(body) > limit:
            complete = False
            break
        parts.append(header + body)
        length += len(header) + len(body)
    return "".join(parts), present, complete


def extract_all(db: Session, tender: Tender, *, actor: User | None = None) -> dict[str, int] | None:
    """Один запрос вместо трёх. `None` — общий запрос не подошёл (фрагменты не помещаются
    целиком, сбой ответа): вызывающий делает три отдельных запроса, как раньше."""

    limit = fit_chars(db, DESIRED_CHARS, reserved=len(SYSTEM_PROMPT) + 500)
    text, present, complete = combined_text(db, tender, limit)
    if not complete:
        return None
    if not text:
        return {"positions": 0, "points": 0, "stages": 0, "conditions": 0}
    try:
        answer = run_structured(
            db,
            system_prompt=SYSTEM_PROMPT,
            user_text=f"ЗАКУПКА: {tender.title}\n\nДОКУМЕНТАЦИЯ (фрагменты):\n{text}",
            response_model=BitrixFactsAnswer,
            temperature=0.0,
        )
    except (AiQuotaExceededError, AiNotConfiguredError):
        raise
    except Exception as exc:  # noqa: BLE001 - общий запрос не удался — три отдельных
        logger.warning(f"Общий запрос позиций и условий по {tender.external_id} не удался: {exc}")
        db.rollback()
        return None

    found = {"positions": 0, "points": 0, "stages": 0, "conditions": 0}
    if present["positions"]:
        found["positions"] = positions.save_positions(
            db, tender, positions.PositionsAnswer(positions=answer.positions), actor=actor
        )
    if present["delivery"]:
        found.update(delivery.save_answer(db, tender, answer.delivery, actor=actor))
    if present["conditions"]:
        found.update(conditions.save_answer(db, tender, answer.conditions, actor=actor))
    return found

