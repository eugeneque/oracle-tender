"""Профиль компании-заявителя (раздел 7 ТЗ, решение 03.09.2026).

Без этой записи измерения Task и Competencies из раздела 5.5.1 считать не из чего: они
сравнивают требования тендера не с каталогом приборов, а с самой компанией — её допусками,
стажем и уже выполненными проектами. Заполняется вручную в разделе «Настройки».

Запись одна на компанию (МИРТЕК), но таблица не singleton-строкой с фиксированным id, а
привязкой к `manufacturers`: производителей в системе 13, и профиль по смыслу принадлежит
конкретному из них — «нашему».
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyProfile(Base):
    __tablename__ = "company_profile"
    __table_args__ = (
        UniqueConstraint("manufacturer_id", name="uq_company_profile_manufacturer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manufacturers.id", ondelete="CASCADE"), nullable=False
    )
    # Юридические данные (раздел 7 ТЗ). Заполняются автопоиском по ЕГРЮЛ с подтверждением
    # человеком — какое поле откуда взялось, помнит `field_sources`. ИНН здесь не только
    # реквизит для заявки: по нему синхронизируется история участий (`company_participations`),
    # то есть без него не считается измерение History.
    legal_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inn: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Отдельным полем, а не внутри реквизитов: в заявке требуется пара ИНН/КПП, и у
    # обособленных подразделений КПП свой при общем ИНН.
    kpp: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ogrn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    legal_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {поле: {source: 'auto_search'|'manual', verified_by_user: bool}} — тот же приём, что у
    # `product_characteristics.source`: автоподставленное значение остаётся видимо
    # автоподставленным, пока человек его не подтвердил, и в evidence подаётся с этой меткой.
    field_sources: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    years_of_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{name, number, issued_at, valid_until, issuer}] — свободная форма, потому что состав
    # реквизитов у СРО, лицензии ФСБ и сертификата ISO разный, а сравнивать их модель будет
    # по наименованию, а не по колонкам.
    licenses: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # [{work_type, customer, volume, year, description}] — вход измерения Task.
    past_projects: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Реквизиты и бланк нужны вкладке «Заявка» (раздел 5.6 ТЗ). Она делается в последнюю
    # очередь, но поля заводятся сразу: профиль заполняют один раз и вручную, и просить
    # заказчика вернуться к форме второй раз — лишняя работа для него.
    bank_requisites: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    letterhead_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def is_filled(self) -> bool:
        """Достаточно ли профиля, чтобы считать Task/Competencies.

        Пустой профиль — не повод считать оценку нулём: это повод честно сказать «нечем
        считать» (раздел 5.5.1 ТЗ, то же правило, что и для History без данных).
        """

        return bool(self.years_of_experience or self.licenses or self.past_projects)

    def has_inn(self) -> bool:
        """Есть ли ИНН — без него не с чем идти в синхронизацию истории участий."""

        return bool(self.inn and self.inn.strip())
