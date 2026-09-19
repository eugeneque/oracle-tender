"""Подготовка данных к интеграции с Bitrix24 (Этап 13, раздел 5.10 ТЗ).

Реальных вызовов к Bitrix24 здесь нет и не предполагается — ТЗ прямо выносит их во вторую
очередь. Задача этого модуля другая: превратить тендер во внутреннем виде в **лид** в том
виде, в каком его понимает CRM, и отдать это двумя способами — файлом для ручного импорта и
через API для будущей интеграции.

**Почему лид, а не сделка.** Тендер на момент попадания к нам — ещё не договорённость, а
возможность: заказчик о нас не знает, участие не подтверждено. В терминах Bitrix24 это
именно лид, который менеджер квалифицирует и лишь потом превращает в сделку. Сложись иначе,
воронка забилась бы сделками, по которым никто не работает.

**Маппинг полей.** Стандартные поля лида (`TITLE`, `COMPANY_TITLE`, `OPPORTUNITY`,
`CURRENCY_ID`, `COMMENTS`, `SOURCE_ID`, `ASSIGNED_BY_NAME`) заполняются напрямую. Всё, чему
в стандартной карточке лида места нет — номер закупки, ОКПД2, площадка, сроки, процент
победителя, ссылка — уходит в поля `UF_CRM_*`: в Bitrix24 их заводит администратор портала
под конкретный портал, поэтому имена здесь заданы как соглашение и вынесены в одну таблицу
`LEAD_FIELDS`, чтобы поменять их можно было в одном месте, не трогая ни экспорт, ни API.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Requirement, WinPercentage
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer
from app.models.region import Region, RegionResponsible
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action
from app.services.tender_service import TenderFilters, apply_tender_filters

# (ключ поля в Bitrix24, заголовок колонки в CSV). Порядок — порядок колонок файла.
LEAD_FIELDS: list[tuple[str, str]] = [
    ("TITLE", "Название лида"),
    ("COMPANY_TITLE", "Компания (заказчик)"),
    ("OPPORTUNITY", "Сумма"),
    ("CURRENCY_ID", "Валюта"),
    ("SOURCE_ID", "Источник"),
    ("SOURCE_DESCRIPTION", "Описание источника"),
    ("ASSIGNED_BY_NAME", "Ответственный"),
    ("STATUS_ID", "Стадия"),
    ("COMMENTS", "Комментарий"),
    ("UF_CRM_TENDER_NUMBER", "Номер закупки"),
    ("UF_CRM_TENDER_PLATFORM", "Площадка"),
    ("UF_CRM_TENDER_URL", "Ссылка на закупку"),
    ("UF_CRM_TENDER_OKPD2", "ОКПД2"),
    ("UF_CRM_TENDER_TYPE", "Тип конкурса"),
    ("UF_CRM_TENDER_REGION", "Регион заказчика"),
    ("UF_CRM_TENDER_DELIVERY_REGION", "Регион поставки"),
    ("UF_CRM_TENDER_PUBLISH_DATE", "Дата размещения"),
    ("UF_CRM_TENDER_DEADLINE", "Окончание приёма заявок"),
    ("UF_CRM_TENDER_WIN_PERCENTAGE", "Процент победителя МИРТЕК"),
    ("UF_CRM_TENDER_REQUIREMENTS", "Требований извлечено"),
]

# Стадия лида по нашему статусу релевантности. «Новый» и «в работе» — стандартные стадии
# любого портала Bitrix24; отклонённые тендеры выгружаются как «мусорные», а не пропускаются:
# менеджер должен видеть, что закупку смотрели и сознательно отклонили.
_STATUS_TO_LEAD_STAGE = {
    "new": "NEW",
    "confirmed": "IN_PROCESS",
    "rejected": "JUNK",
    "irrelevant": "JUNK",
}


def _format_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%d.%m.%Y")


def _format_amount(value: Decimal | None) -> str:
    # Точка как десятичный разделитель: Bitrix24 при импорте ожидает число, а не строку
    # в русском формате — «1 234,56» он прочитает как текст и сумма в лид не попадёт.
    return f"{value:.2f}" if value is not None else ""


def _build_comment(tender: Tender, requirements_count: int, win_percentage: Decimal | None) -> str:
    """Текст комментария лида — то, что менеджер увидит, не заходя в нашу систему."""

    lines: list[str] = []
    if tender.ai_comment:
        lines.append(tender.ai_comment)
    if win_percentage is not None:
        lines.append(
            f"Процент победителя МИРТЕК: {float(win_percentage):.1f}% "
            "(взвешенная оценка соответствия характеристик, без учёта цены)."
        )
    if requirements_count:
        lines.append(f"Извлечено требований из документации: {requirements_count}.")
    if tender.source_url:
        lines.append(f"Карточка закупки: {tender.source_url}")
    return "\n".join(lines)


def _region_names(db: Session, tender: Tender) -> tuple[str, str, str]:
    """Названия регионов и ответственный по региону заказчика.

    Регион поставки, если он не определён отдельно, подставляется из региона организатора —
    так же, как в выгрузке Excel: в базе пустое значение означает «не определено», и
    затирать его дублем нельзя, а вот в выгрузке дубль полезен.
    """

    organizer = ""
    delivery = ""
    responsible = ""

    if tender.region_organizer_code:
        region = db.get(Region, tender.region_organizer_code)
        organizer = region.name if region else ""
        assignment = db.get(RegionResponsible, tender.region_organizer_code)
        if assignment and assignment.responsible_name:
            responsible = assignment.responsible_name

    if tender.region_delivery_code:
        region = db.get(Region, tender.region_delivery_code)
        delivery = region.name if region else ""

    return organizer, delivery or organizer, responsible


def build_lead(db: Session, tender: Tender) -> dict[str, Any]:
    """Один тендер в виде лида Bitrix24."""

    win_percentage = db.execute(
        select(WinPercentage.percentage)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            WinPercentage.tender_id == tender.id,
            Manufacturer.is_mirtek.is_(True),
            WinPercentage.is_current.is_(True),
        )
    ).scalar_one_or_none()

    requirements_count = (
        db.execute(
            select(Requirement.id).where(Requirement.tender_id == tender.id)
        ).scalars().all()
    )
    requirements_total = len(requirements_count)

    organizer_region, delivery_region, responsible = _region_names(db, tender)
    platform = tender.source.name if tender.source else ""

    return {
        # Номер закупки в названии — чтобы лид можно было найти поиском по нему, а менеджер
        # понимал, о какой закупке речь, ещё в списке.
        "TITLE": f"Тендер {tender.external_id}: {tender.title}"[:250],
        "COMPANY_TITLE": tender.customer_name or tender.organizer_name or "",
        "OPPORTUNITY": _format_amount(tender.price),
        "CURRENCY_ID": tender.currency or "RUB",
        "SOURCE_ID": "OTHER",
        "SOURCE_DESCRIPTION": f"Sova Scanner / {platform}" if platform else "Sova Scanner",
        "ASSIGNED_BY_NAME": responsible,
        "STATUS_ID": _STATUS_TO_LEAD_STAGE.get(tender.relevance_status, "NEW"),
        "COMMENTS": _build_comment(tender, requirements_total, win_percentage),
        "UF_CRM_TENDER_NUMBER": tender.external_id,
        "UF_CRM_TENDER_PLATFORM": platform,
        "UF_CRM_TENDER_URL": tender.source_url or "",
        "UF_CRM_TENDER_OKPD2": tender.okpd2_code or "",
        "UF_CRM_TENDER_TYPE": tender.tender_type or "",
        "UF_CRM_TENDER_REGION": organizer_region,
        "UF_CRM_TENDER_DELIVERY_REGION": delivery_region,
        "UF_CRM_TENDER_PUBLISH_DATE": _format_date(tender.publish_date),
        "UF_CRM_TENDER_DEADLINE": _format_date(tender.application_end),
        "UF_CRM_TENDER_WIN_PERCENTAGE": (
            f"{float(win_percentage):.1f}" if win_percentage is not None else ""
        ),
        "UF_CRM_TENDER_REQUIREMENTS": str(requirements_total),
    }


def build_leads(
    db: Session, filters: TenderFilters, *, limit: int | None = None
) -> list[dict[str, Any]]:
    query = apply_tender_filters(select(Tender), filters).order_by(
        Tender.publish_date.desc().nullslast(), Tender.created_at.desc()
    )
    if limit is not None:
        query = query.limit(limit)

    return [build_lead(db, tender) for tender in db.scalars(query)]


def build_leads_csv(
    db: Session, filters: TenderFilters, *, limit: int | None = None, actor: User | None = None
) -> tuple[bytes, int]:
    """CSV для импорта в Bitrix24. Возвращает (содержимое файла, число строк).

    Разделитель — точка с запятой, кодировка — UTF-8 с BOM: именно так Excel в русской
    локали открывает файл без «кракозябр» и без ручного мастера импорта, а импорт Bitrix24
    принимает оба разделителя. Заголовки — русские названия, вторая строка — технические
    имена полей: при импорте Bitrix24 просит сопоставить колонки, и с техническими именами
    рядом это делается за один проход.
    """

    leads = build_leads(db, filters, limit=limit)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([title for _key, title in LEAD_FIELDS])
    writer.writerow([key for key, _title in LEAD_FIELDS])
    for lead in leads:
        writer.writerow([lead.get(key, "") for key, _title in LEAD_FIELDS])

    content = "﻿" + buffer.getvalue()

    if actor is not None:
        log_action(
            db,
            component="export",
            action="export_bitrix_leads",
            result="success",
            level=LogLevel.INFO,
            details=f"Выгружено лидов: {len(leads)}",
            user_id=actor.id,
        )
        db.commit()

    return content.encode("utf-8"), len(leads)


def build_file_name() -> str:
    return f"sova-scanner_bitrix24_leads_{datetime.now(timezone.utc):%Y-%m-%d}.csv"
