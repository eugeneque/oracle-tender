"""Похожие тендеры: эмбеддинги и косинусная близость (раздел 5.5.1, 5.6 ТЗ, 03.09.2026).

Вектор считается один раз на тендер и складывается в `tender_embeddings`; вкладка «Похожие»
и измерение History сравнивают векторы между собой.

**Почему близость считается в Python, а не в БД.** ТЗ фиксирует `pgvector` (раздел 6.2), но
расширения нет ни в образе `postgres:15`, ни в локальной установке разработчика: миграция с
`CREATE EXTENSION vector` уронила бы и разработку, и тесты у всех разом. Поэтому вектор
лежит в JSONB, а косинус считается перебором — на нынешнем объёме (тысячи тендеров, вектор
на 256 чисел) это десятки миллисекунд. Переход на `pgvector` — задача уровня инфраструктуры
(сменить образ БД, мигрировать тип колонки, добавить ANN-индекс); интерфейс этого модуля при
этом не меняется, меняется только тело `find_similar`.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Requirement
from app.models.log import LogLevel
from app.models.market import SimilarTender, TenderEmbedding
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action
from app.services.yandex_ai_client import embed_text

# Сколько похожих тендеров сохраняется на один исходный. Больше десяти вкладка всё равно не
# показывает, а History смотрит на первые несколько.
TOP_SIMILAR = 10

# Ниже этого порога «похожесть» перестаёт быть похожестью: у любых двух закупок на
# приборы учёта косинус редко падает ниже 0.5, и без отсечки вкладка заполнялась бы
# случайными тендерами, создавая ложное впечатление найденных аналогов.
MIN_SIMILARITY = 0.75


@dataclass
class SimilarityOutcome:
    embedded: int = 0
    pairs_saved: int = 0
    messages: list[str] = field(default_factory=list)


def tender_text(db: Session, tender: Tender) -> str:
    """Текст тендера для вектора: наименование, предмет и извлечённые требования.

    Требования включены намеренно — без них похожими оказываются закупки с похожими
    названиями («Поставка приборов учёта») независимо от того, что именно закупается.
    """

    parts = [
        tender.title,
        tender.customer_name or "",
        tender.okpd2_code or "",
        tender.procurement_method or "",
    ]
    for normalized, original in db.execute(
        select(Requirement.normalized_text, Requirement.text)
        .where(Requirement.tender_id == tender.id)
        .limit(60)
    ).all():
        parts.append(normalized or original)
    return "\n".join(part for part in parts if part)


def cosine(left: list[float], right: list[float]) -> float:
    """Косинусная близость. Векторы разной длины (разные модели) сравнивать нельзя — 0."""

    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def ensure_embedding(db: Session, tender: Tender, *, force: bool = False) -> TenderEmbedding:
    """Считает вектор тендера, если его ещё нет. Обращение к модели платное — повторно не
    ходим, пока не попросили явно (`force`)."""

    existing = db.get(TenderEmbedding, tender.id)
    if existing is not None and not force:
        return existing

    vector, model_version = embed_text(db, tender_text(db, tender))
    if existing is None:
        existing = TenderEmbedding(tender_id=tender.id)
        db.add(existing)
    existing.embedding = vector
    existing.model_version = model_version
    db.commit()
    return existing


def find_similar(
    db: Session, tender: Tender, *, limit: int = TOP_SIMILAR
) -> list[tuple[uuid.UUID, float]]:
    """Ближайшие тендеры по косинусу — перебором по векторам той же модели.

    Векторы другой версии модели в сравнение не попадают: их числа лежат в другом
    пространстве, и близость к ним ничего не значит.
    """

    source = db.get(TenderEmbedding, tender.id)
    if source is None:
        return []

    rows = db.execute(
        select(TenderEmbedding.tender_id, TenderEmbedding.embedding).where(
            TenderEmbedding.tender_id != tender.id,
            TenderEmbedding.model_version == source.model_version,
        )
    ).all()

    scored = [
        (tender_id, cosine(source.embedding, vector))
        for tender_id, vector in rows
    ]
    scored = [(tender_id, score) for tender_id, score in scored if score >= MIN_SIMILARITY]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def refresh_similar(
    db: Session, tender: Tender, *, actor: User | None = None
) -> SimilarityOutcome:
    """Считает вектор тендера и перезаписывает его список похожих."""

    outcome = SimilarityOutcome()
    try:
        ensure_embedding(db, tender)
        outcome.embedded = 1
    except Exception as exc:  # noqa: BLE001 - сбой модели не должен ронять карточку
        logger.warning(f"Эмбеддинг тендера {tender.external_id} не посчитан: {exc}")
        outcome.messages.append(f"эмбеддинг не посчитан ({exc})")
        return outcome

    matches = find_similar(db, tender)
    # Полная замена, а не дополнение: корпус пополняется, и вчерашний список «похожих»
    # может уже не содержать ближайших. Дописывание превратило бы список в архив.
    for stale in db.scalars(select(SimilarTender).where(SimilarTender.tender_id == tender.id)):
        db.delete(stale)
    db.flush()

    for similar_id, score in matches:
        db.add(
            SimilarTender(
                tender_id=tender.id,
                similar_tender_id=similar_id,
                similarity_score=round(score, 5),
            )
        )
    outcome.pairs_saved = len(matches)
    db.commit()

    log_action(
        db,
        component="similarity",
        action=f"refresh_similar:{tender.external_id}",
        result="success" if matches else "empty",
        level=LogLevel.INFO,
        details=f"Похожих найдено: {len(matches)}"
        + ("; " + "; ".join(outcome.messages) if outcome.messages else ""),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return outcome
