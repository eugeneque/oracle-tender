"""Аналитика: агрегаты по тендерам и сводка состояния системы (раздел 5.6 ТЗ, «Аналитика»).

Пять требуемых разрезов (количество и суммы, динамика по месяцам, распределение по
регионам, топ заказчиков, средний процент победителя) считаются в БД, а не на фронте:
выборка — тысячи тендеров, и тянуть их в браузер ради пяти чисел бессмысленно.

Отдельно от предметной аналитики здесь же собирается «состояние приложения» — источники,
документы, каталог, журнал. Формально ТЗ этого не требует, но дашборд без него отвечает
только на вопрос «что в тендерах» и молчит о том, работает ли сбор данных вообще; а именно
это — первый вопрос при взгляде на систему утром.

Все агрегаты уважают те же фильтры, что и список тендеров (`TenderFilters`), — иначе
цифры на дашборде не сходились бы с тем, что пользователь только что отфильтровал в списке.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pydantic
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai_profile import AiProfileScore
from app.models.analysis import ComplianceMatrixEntry, Requirement, WinPercentage
from app.models.log import Log, LogLevel
from app.models.manufacturer import Manufacturer, Product
from app.models.region import FederalDistrict, Region
from app.models.source import Source
from app.models.tender import Tender
from app.models.tender_document import ParseStatus, TenderDocument
from app.models.user import User
from app.services.audit import log_action
from app.services.tender_service import TenderFilters, apply_tender_filters
from app.services.ai_client import AiNotConfiguredError, run_structured

# Сколько строк отдавать в «длинных» разрезах. Топ-10 умещается в виджет и покрывает
# практический вопрос «кто основные заказчики»; полный список — это отдельный отчёт.
TOP_LIMIT = 10

# Глубина ряда динамики по умолчанию. Год — минимальный горизонт, на котором видна
# сезонность закупок (бюджетный цикл), и при этом ряд остаётся читаемым на графике.
DEFAULT_MONTHS = 12


def _filtered_tenders(filters: TenderFilters):
    """Базовый SELECT по тендерам с наложенными фильтрами — общий для всех агрегатов."""

    return apply_tender_filters(select(Tender.id).select_from(Tender), filters).subquery()


def get_summary(db: Session, filters: TenderFilters) -> dict:
    """Карточки верхнего уровня: сколько тендеров, на какую сумму, сколько в работе.

    `sum` считается по `price`, а он nullable (часть площадок не публикует НМЦК до
    вскрытия), поэтому рядом отдаётся `with_price` — без него средний чек молча врёт.
    """

    scope = _filtered_tenders(filters)
    base = select(Tender).where(Tender.id.in_(select(scope.c.id)))

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_amount = db.scalar(
        select(func.coalesce(func.sum(Tender.price), 0)).where(Tender.id.in_(select(scope.c.id)))
    ) or Decimal("0")
    with_price = db.scalar(
        select(func.count()).where(
            Tender.id.in_(select(scope.c.id)), Tender.price.is_not(None)
        )
    ) or 0

    now = datetime.now(timezone.utc)
    deadline_soon = db.scalar(
        select(func.count()).where(
            Tender.id.in_(select(scope.c.id)),
            Tender.application_end.is_not(None),
            Tender.application_end >= now,
            Tender.application_end <= now + timedelta(days=5),
        )
    ) or 0

    analysed = db.scalar(
        select(func.count(func.distinct(Requirement.tender_id))).where(
            Requirement.tender_id.in_(select(scope.c.id))
        )
    ) or 0

    by_status = dict(
        db.execute(
            select(Tender.status, func.count())
            .where(Tender.id.in_(select(scope.c.id)))
            .group_by(Tender.status)
        ).all()
    )
    by_relevance = dict(
        db.execute(
            select(Tender.relevance_status, func.count())
            .where(Tender.id.in_(select(scope.c.id)))
            .group_by(Tender.relevance_status)
        ).all()
    )
    by_type = dict(
        db.execute(
            select(Tender.tender_type, func.count())
            .where(Tender.id.in_(select(scope.c.id)), Tender.tender_type.is_not(None))
            .group_by(Tender.tender_type)
        ).all()
    )

    avg_win = db.scalar(
        select(func.avg(WinPercentage.percentage))
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            WinPercentage.tender_id.in_(select(scope.c.id)),
            Manufacturer.is_mirtek.is_(True),
            WinPercentage.is_current.is_(True),
        )
    )

    # Средняя AI-оценка по профилю (раздел 5.6 ТЗ, «Аналитика», редакция 03.09.2026) —
    # рядом со средним процентом соответствия, а не вместо него: это разные вопросы
    # («стоит ли идти» и «подходит ли прибор»), и заказчик смотрит на оба.
    avg_ai_score = db.scalar(
        select(func.avg(AiProfileScore.overall_score)).where(
            AiProfileScore.tender_id.in_(select(scope.c.id)),
            AiProfileScore.is_current.is_(True),
        )
    )

    return {
        "total": total,
        "total_amount": total_amount,
        "with_price": with_price,
        "average_amount": (total_amount / with_price) if with_price else Decimal("0"),
        "deadline_soon": deadline_soon,
        "analysed": analysed,
        "average_win_percentage": avg_win,
        "average_ai_score": avg_ai_score,
        "by_status": {(key or "unknown"): value for key, value in by_status.items()},
        "by_relevance": by_relevance,
        "by_type": by_type,
    }


def get_monthly_series(db: Session, filters: TenderFilters, *, months: int = DEFAULT_MONTHS) -> list[dict]:
    """Динамика по месяцам: количество, сумма и средний процент победителя.

    Группировка идёт по `publish_date`, а не по `created_at`: интерес представляет месяц
    размещения закупки (так же, как в поле «Месяц» Приложения D), а не момент, когда наш
    сборщик до неё добрался. Месяцы без тендеров возвращаются нулями — иначе на графике
    линия перепрыгивала бы через пустой период, создавая иллюзию непрерывного роста.
    """

    scope = _filtered_tenders(filters)
    month = func.date_trunc("month", Tender.publish_date)

    rows = db.execute(
        select(
            month.label("month"),
            func.count(Tender.id),
            func.coalesce(func.sum(Tender.price), 0),
        )
        .where(Tender.id.in_(select(scope.c.id)), Tender.publish_date.is_not(None))
        .group_by(month)
        .order_by(month)
    ).all()
    counted = {row[0].date().replace(day=1): (row[1], row[2]) for row in rows}

    win_rows = db.execute(
        select(month.label("month"), func.avg(WinPercentage.percentage))
        .select_from(Tender)
        .join(WinPercentage, WinPercentage.tender_id == Tender.id)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            Tender.id.in_(select(scope.c.id)),
            Tender.publish_date.is_not(None),
            Manufacturer.is_mirtek.is_(True),
            WinPercentage.is_current.is_(True),
        )
        .group_by(month)
    ).all()
    wins = {row[0].date().replace(day=1): row[1] for row in win_rows}

    score_rows = db.execute(
        select(month.label("month"), func.avg(AiProfileScore.overall_score))
        .select_from(Tender)
        .join(AiProfileScore, AiProfileScore.tender_id == Tender.id)
        .where(
            Tender.id.in_(select(scope.c.id)),
            Tender.publish_date.is_not(None),
            AiProfileScore.is_current.is_(True),
        )
        .group_by(month)
    ).all()
    scores = {row[0].date().replace(day=1): row[1] for row in score_rows}

    # Ряд строится от последнего месяца с данными, а не от «сегодня»: на тестовых и
    # демонстрационных базах свежих закупок может не быть вовсе, и график из пустых
    # месяцев выглядел бы как неработающая система.
    anchor = max(counted) if counted else date.today().replace(day=1)
    series: list[dict] = []
    for offset in range(months - 1, -1, -1):
        year = anchor.year
        month_number = anchor.month - offset
        while month_number <= 0:
            month_number += 12
            year -= 1
        point = date(year, month_number, 1)
        count, amount = counted.get(point, (0, Decimal("0")))
        series.append(
            {
                "month": point,
                "count": count,
                "amount": amount,
                "average_win_percentage": wins.get(point),
                "average_ai_score": scores.get(point),
            }
        )
    return series


def get_by_region(db: Session, filters: TenderFilters) -> list[dict]:
    """Распределение по регионам заказчика с раскрытием федерального округа.

    Тендеры без определённого региона собираются в отдельную строку с `code=None`, а не
    отбрасываются: сейчас это подавляющее большинство записей (регион не определяется —
    см. дефект REQ-5.4-12), и молча спрятать их значило бы нарисовать красивую картинку
    по одному проценту данных.
    """

    scope = _filtered_tenders(filters)
    rows = db.execute(
        select(
            Tender.region_organizer_code,
            Region.name,
            FederalDistrict.name,
            func.count(Tender.id),
            func.coalesce(func.sum(Tender.price), 0),
        )
        .select_from(Tender)
        .outerjoin(Region, Region.code == Tender.region_organizer_code)
        .outerjoin(FederalDistrict, FederalDistrict.code == Region.federal_district_code)
        .where(Tender.id.in_(select(scope.c.id)))
        .group_by(Tender.region_organizer_code, Region.name, FederalDistrict.name)
        .order_by(func.count(Tender.id).desc())
    ).all()

    return [
        {
            "code": code,
            "name": name or "Регион не определён",
            "federal_district": district,
            "count": count,
            "amount": amount,
        }
        for code, name, district, count, amount in rows
    ]


def get_top_customers(db: Session, filters: TenderFilters, *, limit: int = TOP_LIMIT) -> list[dict]:
    """Топ заказчиков по количеству закупок (сумма — вторым ключом сортировки)."""

    scope = _filtered_tenders(filters)
    rows = db.execute(
        select(
            Tender.customer_name,
            func.count(Tender.id),
            func.coalesce(func.sum(Tender.price), 0),
        )
        .where(Tender.id.in_(select(scope.c.id)), Tender.customer_name.is_not(None))
        .group_by(Tender.customer_name)
        .order_by(func.count(Tender.id).desc(), func.coalesce(func.sum(Tender.price), 0).desc())
        .limit(limit)
    ).all()
    return [{"name": name, "count": count, "amount": amount} for name, count, amount in rows]


def get_win_breakdown(db: Session, filters: TenderFilters) -> dict:
    """Средний процент победителя МИРТЕК в трёх разрезах ТЗ: период, регион, тип конкурса.

    Разрез «по периодам» здесь не дублируется — он уже есть в ряде `get_monthly_series`,
    который рисуется на графике; тут только регион и тип.
    """

    scope = _filtered_tenders(filters)
    mirtek_win = (
        select(WinPercentage.tender_id, WinPercentage.percentage)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(Manufacturer.is_mirtek.is_(True), WinPercentage.is_current.is_(True))
        .subquery()
    )

    by_region = db.execute(
        select(
            func.coalesce(Region.name, "Регион не определён"),
            func.avg(mirtek_win.c.percentage),
            func.count(mirtek_win.c.tender_id),
        )
        .select_from(Tender)
        .join(mirtek_win, mirtek_win.c.tender_id == Tender.id)
        .outerjoin(Region, Region.code == Tender.region_organizer_code)
        .where(Tender.id.in_(select(scope.c.id)))
        .group_by(func.coalesce(Region.name, "Регион не определён"))
        .order_by(func.avg(mirtek_win.c.percentage).desc())
        .limit(TOP_LIMIT)
    ).all()

    by_type = db.execute(
        select(
            Tender.tender_type,
            func.avg(mirtek_win.c.percentage),
            func.count(mirtek_win.c.tender_id),
        )
        .select_from(Tender)
        .join(mirtek_win, mirtek_win.c.tender_id == Tender.id)
        .where(Tender.id.in_(select(scope.c.id)), Tender.tender_type.is_not(None))
        .group_by(Tender.tender_type)
        .order_by(func.avg(mirtek_win.c.percentage).desc())
    ).all()

    return {
        "by_region": [
            {"label": label, "percentage": value, "count": count}
            for label, value, count in by_region
        ],
        "by_type": [
            {"label": label, "percentage": value, "count": count}
            for label, value, count in by_type
        ],
    }


# --------------------------------------------------------------------------------------
# Виджеты состояния приложения
#
# Не предметная аналитика по тендерам, а ответ на вопрос «система вообще работает?».
# Каждый виджет самодостаточен: заголовок, крупное значение, подпись и признак состояния
# (ok / warn / danger) — фронт по нему красит плитку и не занимается интерпретацией цифр,
# иначе пороги пришлось бы держать в двух местах.
# --------------------------------------------------------------------------------------

# Сколько часов без успешного опроса считать признаком неполадки. Расписание — дважды в
# сутки (раздел 5.1 ТЗ), поэтому 24 часа тишины означают пропуск минимум двух прогонов.
STALE_POLL_HOURS = 24


@dataclass
class Widget:
    key: str
    title: str
    value: str
    caption: str
    tone: str  # ok | warn | danger | neutral
    progress: float | None = None  # 0..1, если у виджета есть шкала заполнения


def _tone(ratio: float, *, warn: float, danger: float) -> str:
    if ratio <= danger:
        return "danger"
    if ratio <= warn:
        return "warn"
    return "ok"


def get_widgets(db: Session) -> list[dict]:
    """Плитки состояния системы — источники, документы, каталог, ИИ-анализ, журнал.

    Считается по всей базе, без фильтров списка: это здоровье системы, а не срез данных.
    """

    widgets: list[Widget] = []

    # --- Источники: сколько реально опрашивается из заведённых.
    sources_total = db.scalar(select(func.count()).select_from(Source)) or 0
    sources_ready = db.scalar(
        select(func.count()).where(Source.adapter_key.is_not(None))
    ) or 0
    stale_after = datetime.now(timezone.utc) - timedelta(hours=STALE_POLL_HOURS)
    sources_fresh = db.scalar(
        select(func.count()).where(
            Source.adapter_key.is_not(None), Source.last_polled_at >= stale_after
        )
    ) or 0
    ratio = sources_ready / sources_total if sources_total else 0.0
    widgets.append(
        Widget(
            key="sources",
            title="Источники",
            value=f"{sources_ready} / {sources_total}",
            caption=(
                f"адаптер реализован · опрошено за сутки: {sources_fresh}"
                if sources_ready
                else "ни один адаптер не подключён"
            ),
            tone=_tone(ratio, warn=0.9, danger=0.6),
            progress=ratio,
        )
    )

    # --- Документы: доля с извлечённым текстом. Именно текст, а не статус разбора:
    # неподдержанные форматы (.doc, .xls, .zip) сохраняются со статусом success и пустым
    # текстом, и по статусу проблема не видна — а в анализ такой документ уходит пустым.
    docs_total = db.scalar(select(func.count()).select_from(TenderDocument)) or 0
    docs_with_text = db.scalar(
        select(func.count()).where(
            TenderDocument.extracted_text.is_not(None), TenderDocument.extracted_text != ""
        )
    ) or 0
    docs_failed = db.scalar(
        select(func.count()).where(TenderDocument.parse_status == ParseStatus.ERROR.value)
    ) or 0
    ratio = docs_with_text / docs_total if docs_total else 0.0
    widgets.append(
        Widget(
            key="documents",
            title="Документы с текстом",
            value=f"{docs_with_text} / {docs_total}",
            caption=f"не удалось загрузить или разобрать: {docs_failed}",
            tone=_tone(ratio, warn=0.8, danger=0.5),
            progress=ratio,
        )
    )

    # --- Каталог продукции: у скольких производителей вообще есть модели. Это тот самый
    # показатель, от которого зависит осмысленность матрицы соответствия (раздел 5.5 ТЗ):
    # у производителя без моделей все вердикты будут «нет данных».
    manufacturers_total = db.scalar(select(func.count()).select_from(Manufacturer)) or 0
    manufacturers_filled = db.scalar(
        select(func.count(func.distinct(Product.manufacturer_id))).select_from(Product)
    ) or 0
    ratio = manufacturers_filled / manufacturers_total if manufacturers_total else 0.0
    widgets.append(
        Widget(
            key="catalog",
            title="Каталог заполнен",
            value=f"{manufacturers_filled} / {manufacturers_total}",
            caption="производителей с моделями в каталоге",
            tone=_tone(ratio, warn=0.8, danger=0.5),
            progress=ratio,
        )
    )

    # --- Покрытие анализом: сколько собранных тендеров прошло ИИ-разбор.
    tenders_total = db.scalar(select(func.count()).select_from(Tender)) or 0
    tenders_analysed = db.scalar(
        select(func.count(func.distinct(Requirement.tender_id))).select_from(Requirement)
    ) or 0
    ratio = tenders_analysed / tenders_total if tenders_total else 0.0
    widgets.append(
        Widget(
            key="analysis",
            title="Проанализировано",
            value=f"{tenders_analysed} / {tenders_total}",
            caption="тендеров с извлечёнными требованиями",
            tone=_tone(ratio, warn=0.5, danger=0.1),
            progress=ratio,
        )
    )

    # --- Матрица соответствия: доля ячеек с определённым вердиктом. Ровно этот процент
    # показывает, насколько оценке вообще можно верить.
    cells_total = db.scalar(select(func.count()).select_from(ComplianceMatrixEntry)) or 0
    cells_known = db.scalar(
        select(func.count()).where(ComplianceMatrixEntry.status != "no_data")
    ) or 0
    needs_review = db.scalar(
        select(func.count()).where(ComplianceMatrixEntry.needs_human_review.is_(True))
    ) or 0
    ratio = cells_known / cells_total if cells_total else 0.0
    widgets.append(
        Widget(
            key="compliance",
            title="Матрица определена",
            value=f"{round(ratio * 100)} %",
            caption=(
                f"{cells_known} из {cells_total} ячеек · требуют проверки: {needs_review}"
                if cells_total
                else "матрица ещё не рассчитывалась"
            ),
            tone=_tone(ratio, warn=0.5, danger=0.2),
            progress=ratio,
        )
    )

    # --- Журнал: ошибки за сутки. Здесь шкалы нет — важен сам факт и их количество.
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    errors_24h = db.scalar(
        select(func.count()).where(
            Log.timestamp >= since,
            Log.level.in_([LogLevel.ERROR, LogLevel.CRITICAL]),
        )
    ) or 0
    warnings_24h = db.scalar(
        select(func.count()).where(Log.timestamp >= since, Log.level == LogLevel.WARNING)
    ) or 0
    widgets.append(
        Widget(
            key="errors",
            title="Ошибки за сутки",
            value=str(errors_24h),
            caption=f"предупреждений: {warnings_24h}",
            tone="ok" if errors_24h == 0 else ("warn" if errors_24h < 5 else "danger"),
        )
    )

    return [
        {
            "key": w.key,
            "title": w.title,
            "value": w.value,
            "caption": w.caption,
            "tone": w.tone,
            "progress": w.progress,
        }
        for w in widgets
    ]


# --------------------------------------------------------------------------------------
# ИИ-сводка по всем разделам
# --------------------------------------------------------------------------------------

_SUMMARY_SYSTEM_PROMPT = """Ты — аналитик системы мониторинга тендеров на поставку приборов \
учёта. Тебе дают сводку числовых показателей по всем разделам системы за один момент \
времени. Твоя задача — коротко объяснить руководителю, что происходит.

Правила:
1. Пиши по-русски, деловым тоном, без воды и без обращений.
2. `headline` — ОДНО предложение (до 140 символов): главное, что видно из цифр.
3. `highlights` — 3-5 пунктов о состоянии дел. Каждый пункт — одно предложение, с \
конкретным числом из сводки. Не пересказывай сводку целиком, выбирай значимое.
4. `risks` — 2-4 пункта о том, что мешает работе или искажает оценку. Если рисков не \
видно, верни пустой список; не выдумывай.
5. `actions` — 2-4 конкретных следующих шага, каждый начинается с глагола.
6. Опирайся ТОЛЬКО на переданные числа. Не додумывай факты, которых в сводке нет.
7. Если показатель равен нулю или данных мало — так и скажи, это тоже вывод."""


class AiSummary(pydantic.BaseModel):
    """Поля обязательные и без значений по умолчанию — по тем же причинам, что и в
    `compliance_service.RequirementVerdict`: необязательное поле JSON Schema модель просто
    пропускает, и блок приходит пустым."""

    headline: str
    highlights: list[str]
    risks: list[str]
    actions: list[str]


def build_summary_context(db: Session, filters: TenderFilters) -> str:
    """Числовая выжимка по всем разделам — то, что уходит модели.

    Собирается кодом, а не моделью: цифры должны совпадать с тем, что нарисовано на
    дашборде, а не пересчитываться заново с риском разойтись.
    """

    summary = get_summary(db, filters)
    widgets = get_widgets(db)
    regions = get_by_region(db, filters)[:5]
    customers = get_top_customers(db, filters, limit=5)
    series = get_monthly_series(db, filters)

    lines = ["РАЗДЕЛ «ТЕНДЕРЫ»:"]
    lines.append(f"- всего в выборке: {summary['total']}")
    lines.append(
        f"- суммарная НМЦК: {summary['total_amount']:.0f} руб. "
        f"(цена указана у {summary['with_price']} тендеров)"
    )
    lines.append(f"- проанализировано ИИ: {summary['analysed']}")
    lines.append(f"- до окончания приёма заявок менее 5 дней: {summary['deadline_soon']}")
    if summary["average_ai_score"] is not None:
        lines.append(
            f"- средняя AI-оценка по профилю: {float(summary['average_ai_score']):.1f}%"
        )
    else:
        lines.append("- AI-оценка по профилю ещё не считалась ни по одному тендеру")
    if summary["average_win_percentage"] is not None:
        lines.append(
            f"- средний процент соответствия характеристик МИРТЕК: "
            f"{float(summary['average_win_percentage']):.1f}%"
        )
    else:
        lines.append("- процент соответствия характеристик ещё не рассчитан ни по одному тендеру")
    if summary["by_status"]:
        lines.append("- по статусам: " + ", ".join(f"{k}={v}" for k, v in summary["by_status"].items()))
    if summary["by_type"]:
        lines.append("- по типам конкурса: " + ", ".join(f"{k}={v}" for k, v in summary["by_type"].items()))

    recent = [point for point in series if point["count"]][-6:]
    if recent:
        lines.append("ДИНАМИКА ПО МЕСЯЦАМ (месяц: количество / сумма):")
        for point in recent:
            lines.append(f"- {point['month']:%Y-%m}: {point['count']} / {point['amount']:.0f} руб.")

    if regions:
        lines.append("ТОП РЕГИОНОВ:")
        for row in regions:
            lines.append(f"- {row['name']}: {row['count']} тендеров")

    if customers:
        lines.append("ТОП ЗАКАЗЧИКОВ:")
        for row in customers:
            lines.append(f"- {row['name']}: {row['count']} тендеров")

    lines.append("СОСТОЯНИЕ СИСТЕМЫ:")
    for widget in widgets:
        lines.append(f"- {widget['title']}: {widget['value']} ({widget['caption']})")

    return "\n".join(lines)


def generate_ai_summary(db: Session, filters: TenderFilters, actor: User) -> dict:
    """Текстовая сводка по всей системе силами YandexGPT (раздел 5.4 ТЗ — тот же клиент).

    Сбой модели не должен ломать дашборд: аналитика остаётся на экране, а блок сводки
    честно сообщает, что текст получить не удалось.
    """

    context = build_summary_context(db, filters)
    generated_at = datetime.now(timezone.utc)

    try:
        result = run_structured(
            db,
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            user_text=context,
            response_model=AiSummary,
            temperature=0.3,
        )
    except AiNotConfiguredError as exc:
        log_action(
            db,
            component="analytics",
            action="ai_summary",
            result="not_configured",
            level=LogLevel.WARNING,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        return {
            "generated_at": generated_at,
            "headline": None,
            "highlights": [],
            "risks": [],
            "actions": [],
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - недоступность модели не должна ронять дашборд
        logger.warning(f"ИИ-сводка по дашборду не сформирована: {exc}")
        log_action(
            db,
            component="analytics",
            action="ai_summary",
            result="error",
            level=LogLevel.ERROR,
            details=str(exc),
            user_id=actor.id,
        )
        db.commit()
        return {
            "generated_at": generated_at,
            "headline": None,
            "highlights": [],
            "risks": [],
            "actions": [],
            "error": f"Не удалось получить сводку от модели: {exc}",
        }

    log_action(
        db,
        component="analytics",
        action="ai_summary",
        result="success",
        level=LogLevel.INFO,
        details=(
            f"Пунктов: наблюдений {len(result.highlights)}, рисков {len(result.risks)}, "
            f"действий {len(result.actions)}"
        ),
        user_id=actor.id,
    )
    db.commit()

    return {
        "generated_at": generated_at,
        "headline": result.headline,
        "highlights": result.highlights,
        "risks": result.risks,
        "actions": result.actions,
        "error": None,
    }
