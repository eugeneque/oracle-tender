"""Разовый пересчёт отбора после смены логики движка (06.11.2026, раздел 86 ARCHITECTURE.md).

    cd oracle-t/backend && .venv/bin/python recalc_relevance.py

Отметки `passed_relevance_filter` и совпадения профилей сами по себе пересчитываются только при
правке профиля, поэтому после изменения движка старые закупки остались бы с прежним решением.
Повышение `rules_version` заставляет пересчитать совпадения всех профилей при следующем обращении.
Этапы и решения специалистов не затрагиваются.
"""

from sqlalchemy import update

from app.db.session import SessionLocal
from app.models.relevance_profile import RelevanceProfile
from app.services import relevance_service


def main() -> None:
    with SessionLocal() as db:
        db.execute(update(RelevanceProfile).values(rules_version=RelevanceProfile.rules_version + 1))
        db.commit()
        stats = relevance_service.backfill(db)
    print(f"Обработано: {stats['processed']}, прошли: {stats['passed']}, отсеяно: {stats['rejected']}")


if __name__ == "__main__":
    main()
