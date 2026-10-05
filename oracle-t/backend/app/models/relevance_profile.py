"""Профили отбора — единственный механизм отбора закупок (с 05.10.2026).

Профиль — набор слов, исключений и кодов ОКПД2 с привязкой к площадкам. Два вида, один движок:

* **общие** (`is_default`) — их ведёт администратор; выбраны на странице тендеров по
  умолчанию и проставляют `tenders.passed_relevance_filter` (на ней держится очередь проверки
  моделью). В них перенесены бывшие группы системного профиля (миграция 0065);
* **личные** — заводит любой специалист; видны всем, менять может автор или администратор.

Что СОБИРАТЬ с площадок, профиль не решает — это `CollectionTerm`: охват сбора и отбор уже
собранного — разные вопросы, и личный фильтр специалиста не должен менять, что попадает в
систему.

Совпадения хранятся в `relevance_profile_matches`, а не считаются на лету при каждом запросе:
список отдаёт страницами и считает итог, а разбор текста 17 тысяч закупок на каждое открытие
страницы — это секунды. Таблица досчитывается при первом обращении после правки профиля и
добирает только изменившиеся закупки (`services/relevance_profile_service.py`).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RelevanceProfile(Base):
    __tablename__ = "relevance_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Автор. Профили видны всем — специалисты обмениваются удачными фильтрами, — а менять и
    # удалять может только автор или администратор. SET NULL: уход сотрудника не должен
    # уносить с собой фильтры, которыми пользуются остальные.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Синтаксис ключей тот же, что у групп системного профиля (`слово*`, `(a* b*)~N`).
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exclusion_keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Префиксы кодов ОКПД2: «26.51» включает всё вложенное.
    okpd2_codes: Mapped[list] = mapped_column(ARRAY(String(20)), nullable=False, default=list)
    match_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="any")
    # Как сочетаются слова и коды ОКПД2: `narrow` — нужно и то и другое (фильтр сужает),
    # `either` — достаточно одного (код товара ловит закупку с безликим заголовком «Поставка
    # оборудования»). Исключения действуют в обоих режимах.
    okpd2_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="narrow")
    # Общий профиль по умолчанию — ведёт администратор.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Выключенный общий профиль не выбирается по умолчанию и не участвует в отметке.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Площадки, на которые действует профиль (ключи `sources.key`); пусто — на все. Не входит
    # в «правила»: смена площадок не меняет совпадений профиля с закупками, а только то, чьи
    # закупки он сужает, — поэтому пересчёт совпадений она не вызывает.
    source_keys: Mapped[list] = mapped_column(ARRAY(String(50)), nullable=False, default=list)
    # Версия правил: растёт при каждой правке содержимого. Сравнивается с `matched_version` —
    # так устаревшие совпадения распознаются без сверки временных меток, которые сама
    # отметка пересчёта (UPDATE строки) и сдвигала бы.
    rules_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    matched_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RelevanceProfileMatch(Base):
    __tablename__ = "relevance_profile_matches"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("relevance_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )


class CollectionTerm(Base):
    """Фраза, которой система ищет закупки на площадках («что собираем»).

    Обычный текст, без звёздочек: у площадок нет ни стемминга, ни близости слов. Каждая фраза
    — отдельный обход выдачи на каждой площадке, поэтому их немного.
    """

    __tablename__ = "collection_terms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phrase: Mapped[str] = mapped_column(String(300), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
