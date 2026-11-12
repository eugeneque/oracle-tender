"""Разовая перепроверка после правила по коду ОКПД2 (12.11.2026, раздел 105 ARCHITECTURE.md).

    cd oracle-t/backend && .venv/bin/python recheck_meter_okpd2.py

Закупки с кодом счётчиков электроэнергии, которые модель раньше отклонила («недостаточно
информации» при названии «Поставка оборудования»), получают ответ нового правила. Закупки,
где правило не решает (стройка, другой ресурс), остаются с ответом модели. Решения
специалистов не затрагиваются.
"""

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.tender import Tender
from app.services import ai_relevance_service as service


def main() -> None:
    with SessionLocal() as db:
        tenders = db.scalars(
            select(Tender).where(
                Tender.ai_relevant.is_(False),
                Tender.okpd2_code.like(f"{service.METER_OKPD2_PREFIX}%"),
            )
        ).all()
        fixed = 0
        for tender in tenders:
            answer = service.rule_answer(tender)
            if answer is not None and answer.is_relevant:
                service._store(tender, answer)
                fixed += 1
                print(f"+ {' '.join((tender.title or '').split())[:100]}")
        # Возвращённые первой версией правила стройки (12.11.2026): правило их больше не
        # решает — отметка снимается, и закупку заново проверит модель.
        undone = 0
        by_code = db.scalars(
            select(Tender).where(
                Tender.ai_relevant.is_(True),
                Tender.ai_relevance_reason.like("Определено без модели: код ОКПД2%"),
            )
        ).all()
        for tender in by_code:
            if service.rule_answer(tender) is None:
                tender.ai_relevant = None
                tender.ai_relevance_reason = None
                tender.ai_relevance_confidence = None
                tender.ai_relevance_checked_at = None
                undone += 1
                print(f"- {' '.join((tender.title or '').split())[:100]}")
        db.commit()
        print(f"Отправлено модели на повторную проверку: {undone}")
    print(f"Отклонённых моделью с кодом счётчиков: {len(tenders)}, возвращено: {fixed}")


if __name__ == "__main__":
    main()
