"""Заключение ИИ по закупке (28.09.2026).

До этой правки «Разбор ИИ» отвечал на вопрос «похожа ли закупка на наш профиль»: три
измерения, резюме, слабые места и стратегия из трёх строк. Тендерному отделу нужен другой
ответ — фундаментальный:

1. **Подходим ли мы** и **каким прибором** — модели МИРТЕК, названные поимённо;
2. если нет — **кто подходит** из конкурентов и каким прибором;
3. **стратегия входа** — подход, цена, конкретные шаги;
4. **риски** — детально, по категориям: цена, техника, допуски, договор, заказчик,
   конкуренция, сроки, документация, логистика — с мерой для каждого;
5. **метрики** — считает код, не модель: срок, НМЦК, матрица, история, ниша.

С заключением специалист соглашается или спорит. Его замечания (`ai_score_feedback`)
передаются в каждый следующий пересчёт этого тендера, и модель обязана ответить на них —
иначе «Обновить разбор» молча стирал бы поправку человека.

Два вызова, а не один: «кто подходит» и «стратегия с рисками» вместе — длинный JSON, который
YandexGPT рвёт по лимиту токенов на выходе (см. заголовок `ai_profile_service`).

Модель не выдумывает приборов: названия моделей сверяются с каталогом, и прибор, которого
в каталоге нет, из ответа выбрасывается. Производитель — так же, по справочнику.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import pydantic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_feedback import AiScoreFeedback, FeedbackKind
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceStatus,
    Criticality,
    Requirement,
    RequirementKind,
    WinPercentage,
)
from app.models.ai_profile import WeakPointSeverity
from app.models.company_participation import CompanyParticipation, ParticipationOutcome
from app.models.manufacturer import Manufacturer, Product
from app.models.market import NicheStatistics
from app.models.tender import Tender
from app.models.user import User
from app.services.ai_client import run_structured
from app.services.product_relevance import select_products_for_context

# --- словари ---------------------------------------------------------------------------


class Fit:
    FIT = "fit"
    FIT_WITH_CAVEATS = "fit_with_caveats"
    NOT_FIT = "not_fit"
    UNKNOWN = "unknown"


FIT_LABELS: dict[str, str] = {
    Fit.FIT: "Подходим",
    Fit.FIT_WITH_CAVEATS: "Подходим с оговорками",
    Fit.NOT_FIT: "Не подходим",
    Fit.UNKNOWN: "Не можем определить",
}

PRODUCT_STATUS_LABELS: dict[str, str] = {
    "fits": "подходит",
    "partial": "подходит частично",
    "not_fits": "не подходит",
    "unchecked": "не проверен по ТЗ",
}

RISK_CATEGORY_LABELS: dict[str, str] = {
    "price": "Цена",
    "technical": "Техника",
    "admission": "Допуски",
    "contract": "Договор",
    "customer": "Заказчик",
    "competition": "Конкуренция",
    "deadline": "Сроки",
    "documents": "Документация",
    "logistics": "Логистика",
    "other": "Прочее",
}

SEVERITIES = {item.value for item in WeakPointSeverity}

# Сколько моделей производителя показывать модели ИИ как кандидатов. У МИРТЕК — больше:
# именно среди них она называет «наш прибор».
OUR_CANDIDATES = 10
COMPETITOR_CANDIDATES = 3


# --- схемы ответа ------------------------------------------------------------------------
# Все поля обязательные, без значений по умолчанию — см. `ai_profile_service`.


class ProductAnswer(pydantic.BaseModel):
    model: str
    status: str
    note: str


class CompetitorAnswer(pydantic.BaseModel):
    manufacturer: str
    model: str
    status: str
    note: str


class FitAnswer(pydantic.BaseModel):
    fit: str
    participate: bool
    headline: str
    rationale: str
    our_products: list[ProductAnswer]
    competitors: list[CompetitorAnswer]
    feedback_response: str


class RiskAnswer(pydantic.BaseModel):
    category: str
    severity: str
    text: str
    mitigation: str


class PlanAnswer(pydantic.BaseModel):
    approach: str
    price: str
    steps: list[str]
    risks: list[RiskAnswer]


_FIT_PROMPT = """Ты — руководитель тендерного отдела МИРТЕК, производителя приборов учёта
электроэнергии. Вынеси заключение по закупке — такое, под которым ты готов подписаться и с
которым специалист либо согласится, либо поспорит.

Главный вопрос: проходим ли мы в эту закупку, и если да — каким прибором; если нет — кто из
производителей проходит и каким прибором.

Верни JSON:
- fit — строго одно из: "fit" (наш прибор проходит по ТЗ), "fit_with_caveats" (проходит, но
  есть невыполненные некритичные требования, требования без данных или ручные проверки),
  "not_fit" (ни один наш прибор не проходит: не выполнено критичное требование, предмет не
  наш, нет обязательного допуска), "unknown" (ТЗ не разобрано и сравнивать не с чем).
- participate — true, если тендерному отделу стоит готовить участие; false — нет.
- headline — одно предложение: вывод целиком, с названием прибора и главной причиной.
  Например: «Проходим счётчиком МИР С-05 — все критичные требования выполнены, риск в сроке
  поставки» или «Не проходим: у наших счётчиков нет реле 100 А, проходит Энергомера CE308».
  Не пересказывай статус общими словами («проходим с оговорками, есть требования»).
- rationale — 2-4 предложения: на каких фактах вывод держится — конкретные требования,
  проценты матрицы, модели, суммы, даты.
- our_products — наши приборы, которые подходят или ближе всего к ТЗ, лучший первым (до 4).
  model — название ТОЛЬКО из списка «НАШИ ПРИБОРЫ»; status — "fits", "partial", "not_fits"
  или "unchecked" (не проверен по ТЗ); note — чего не хватает или почему подходит, коротко.
  Пустой список, если предмет закупки — не приборы учёта.
- competitors — производители, которые проходят или проходят с оговорками, сильнейший
  первым (до 5); если мы не проходим — обязательно назови тех, кто проходит. manufacturer —
  название из раздела «ПРОИЗВОДИТЕЛИ»; model — модель из списка этого производителя или
  пустая строка; status — "fits", "partial" или "not_fits"; note — коротко, чем берёт или
  на чём отсекается.
- feedback_response — если есть раздел «ЗАМЕЧАНИЯ СПЕЦИАЛИСТОВ»: ответь на последнее
  замечание по существу — с чем согласен и что изменил в заключении, а где остаёшься при
  своём и почему (со ссылкой на факт). Без замечаний — пустая строка.

Порядок рассуждения — строго такой, и headline называет ПЕРВУЮ сработавшую причину:
1. Предмет закупки — приборы учёта электроэнергии или нет.
2. Раздел «ЗАТОЧКА ТЗ ПОД ТОВАРНЫЙ ЗНАК», если он есть. ТЗ под чужой знак и эквивалент не
   допускается — "not_fit", participate=false, headline: «Не проходим: ТЗ под приборы
   <производитель> (<модели>), эквивалент не допускается». Этот производитель — первым в
   competitors со статусом "fits", даже если матрица не нашла его точных исполнений в каталоге:
   ТЗ названо его моделями. Знак наш — это довод «за». Эквивалент допускается — сравнивай
   по характеристикам, как обычно.
3. Приборы — по матрице соответствия (правила ниже).
4. Требования к участнику (МСП, лицензии, опыт, реестры) — последними. Они не бывают главной
   причиной, если по пунктам 1-3 уже есть ответ. Статус компании утверждай только по данным
   входа; если хотя бы одно юрлицо группы требованию удовлетворяет (раздел «СТАТУС МСП ЮРЛИЦ
   ГРУППЫ»), это оговорка «подавать от <юрлицо>», а не «не подходим».

Правила:
- Итог матрицы соответствия («проходит / с оговорками / не проходит») — главный источник
  по приборам. Не противоречь ему без факта из входных данных. Если матрица по МИРТЕК
  построена, статус наших приборов ставь по ней ("fits" / "partial" / "not_fits"), а не
  "unchecked".
- Если матрица не построена, заключение "fit" невозможно: максимум "fit_with_caveats" с
  приборами в статусе "unchecked", или "unknown".
- Замечание специалиста — это знание человека, который читал документацию. Принимай его,
  если оно не опровергается фактами входных данных; если опровергается — объясни чем.
- Обязательного допуска (СРО, лицензия, запись в реестре) нет ни у одного юрлица группы —
  "not_fit" и participate=false; но и тогда причина в headline — после пунктов 1-3.
- Если замечание специалиста указывает на причину из пунктов 1-3 (например, «ТЗ под приборы
  другого производителя») и входные данные её не опровергают — заключение перестраивается
  вокруг неё, а не повторяет прежний вывод.
- Не выдумывай приборов, производителей и фактов, которых нет во входных данных.
Отвечай по-русски, без markdown."""


_PLAN_PROMPT = """Ты — руководитель тендерного отдела МИРТЕК, производителя приборов учёта
электроэнергии. Заключение по закупке уже вынесено (раздел «ЗАКЛЮЧЕНИЕ»). Составь план
входа и детальный разбор рисков.

Верни JSON:
- approach — 2-3 предложения: как заходим (каким прибором, в каком статусе, на что делаем
  ставку против конкурентов). Если не участвуем — почему и что сделать, чтобы в следующий
  раз пройти.
- price — ценовая тактика одной-двумя фразами: от НМЦК, медианного снижения в нише,
  наших прошлых снижений. Если данных о цене нет — так и напиши, и чем это опасно.
- steps — 3-6 конкретных шагов по порядку, каждый начинается с глагола и называет
  предмет: «Запросить у заказчика…», «Проверить запись МИР С-05 в реестре ГИСП…».
- risks — все существенные риски, 3-10 штук. Каждый — {category, severity, text,
  mitigation}. category строго одно из: "price" (НМЦК, демпинг, маржа, обеспечение),
  "technical" (характеристики, исполнения, несоответствия ТЗ), "admission" (ПП 719, реестры,
  сертификаты, допуски), "contract" (штрафы, оплата, гарантия, приёмка), "customer"
  (история с заказчиком, его привычки), "competition" (кто ещё проходит, их цены),
  "deadline" (срок подачи, срок поставки), "documents" (неполная или неразобранная
  документация, противоречия), "logistics" (регион, объём, график поставки), "other".
  severity строго одно из: "significant" (может стоить участия или денег), "moderate"
  (требует внимания), "minor". text — одно-два предложения с конкретикой из входных
  данных: номер или текст требования, модель, сумма, дата, название конкурента.
  mitigation — конкретное действие, чтобы снять риск.

Смотри на всё: цену и обеспечение, условия договора, срок подачи, прошлые исходы у этого
заказчика, конкурентов, полноту документации. Не пиши общих слов, которые подходят к любой
закупке: «проверить наличие компонентов», «провести анализ конкурентов», «следить за
сроками» без предмета — это брак. Если про риск нечего сказать конкретно, не включай его. Учитывай замечания специалистов, если они есть.
Не выдумывай фактов. Отвечай по-русски, без markdown."""


# --- сбор фактов -------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9a-zа-яё]", "", (text or "").lower().replace("ё", "е"))


def manufacturer_label(manufacturer: Manufacturer) -> str:
    return manufacturer.brand_name or manufacturer.legal_name


@dataclass
class ManufacturerFacts:
    manufacturer: Manufacturer
    products: list[Product]
    candidates: list[str]
    win: WinPercentage | None


@dataclass
class ConclusionFacts:
    text: str
    mirtek: ManufacturerFacts | None
    others: list[ManufacturerFacts] = field(default_factory=list)
    matrix_built: bool = False
    # Сколько требований к товару извлечено из ТЗ: без него «матрица не построена» не
    # отличить от «ТЗ не разобрано» — а причины и действия у них разные.
    product_requirements: int = 0
    # ТЗ под конкретный товарный знак (05.10.2026) — найдено кодом, см. `detect_brand_lock`.
    brand_lock: "BrandLock | None" = None


_VERDICT_TEXT = {
    "passes": "проходит",
    "caveats": "проходит с оговорками",
    "fails": "не проходит",
    "unknown": "не хватает данных",
}

_CRITICALITY_TEXT = {
    Criticality.CRITICAL.value: "критичное",
    Criticality.IMPORTANT.value: "важное",
    Criticality.MINOR.value: "второстепенное",
}


# --- заточка ТЗ под товарный знак ------------------------------------------------------------
#
# Случай 32616408289 (05.10.2026): в ТЗ шесть критичных требований «Наименование: Прибор учета
# МИР С-05.10-230-5(80)-PZ1В-KNQ-E-D…», а в документации — «заказчик закупает товар
# определённого товарного знака ввиду его несовместимости с другими». Матрица этого не видит:
# точных исполнений нет в каталоге ни у кого, и все производители, включая сам МИР, получали
# «не хватает данных». Модель, не имея факта, выводила заключение из требования МСП. Факт
# теперь находит код и подаёт модели первым — это главный ответ «почему мы не проходим».

# Фраза о закупке товара определённого товарного знака (ч. 6.1 ст. 3 223-ФЗ, ст. 33 44-ФЗ)
# и прямой запрет эквивалента.
_TRADEMARK_CLAUSE_RE = re.compile(
    r"[^.;]{0,160}(?:товар\w*\s+(?:определенн|определённ|конкретн)\w*\s+товарн\w*\s+знак\w*"
    r"|эквивалент\w*\s+не\s+допуска\w*|без\s+(?:права\s+)?(?:предоставлени\w+\s+)?эквивалент\w*"
    r"|несовместимост\w*\s+с\s+товарами)[^.;]{0,200}",
    re.IGNORECASE,
)
_EQUIVALENT_ALLOWED_RE = re.compile(r"или\s+эквивалент", re.IGNORECASE)
# Сколько текста документации просматривать: шапка извещения и ТЗ, а не весь проект договора.
_DOC_SCAN_LIMIT = 400_000


@dataclass
class BrandLock:
    """ТЗ называет модели одного производителя: кто он и чем это подтверждается."""

    manufacturer: Manufacturer
    is_ours: bool
    # Модели, как они названы в ТЗ (без повторов, до 8).
    models: list[str]
    critical_mentions: int
    total_mentions: int
    # Фраза из документации о товарном знаке / запрете эквивалента, если нашлась.
    clause: str | None
    # True — в ТЗ есть «или эквивалент»; False — эквивалент прямо запрещён или закупается товар
    # определённого знака; None — документация об этом молчит.
    equivalent_allowed: bool | None


def _brand_pattern(manufacturer: Manufacturer) -> re.Pattern[str] | None:
    """Марка, за которой идёт обозначение модели с цифрой: «МИР С-05», «CE308», «Нартис И300».

    Цифра в обозначении обязательна: «Матрица», «Мир», «Пульсар» — обычные слова, и без неё
    «матрица соответствия» или «в мире» считались бы упоминанием производителя. Короткие
    марки сверяются с учётом регистра по той же причине.
    """

    brand = (manufacturer.brand_name or "").split("(")[0].strip()
    if len(brand) < 2:
        return None
    flags = 0 if len(brand) <= 4 else re.IGNORECASE
    return re.compile(
        rf"(?<![\w-]){re.escape(brand)}[\s-]+(?=[\w.()/-]*\d)[\w.()/-]+", flags
    )


def _documents_text(db: Session, tender: Tender) -> str:
    from app.models.tender_document import TenderDocument

    ids = [tender.id] + ([tender.analysis_tender_id] if tender.analysis_tender_id else [])
    texts = db.scalars(
        select(TenderDocument.extracted_text).where(
            TenderDocument.tender_id.in_(ids), TenderDocument.extracted_text.is_not(None)
        )
    )
    return "\n".join(texts)[:_DOC_SCAN_LIMIT]


def detect_brand_lock(
    db: Session, tender: Tender, requirements: list[Requirement], manufacturers: list[Manufacturer]
) -> BrandLock | None:
    """Ищет производителя, чьи модели названы в требованиях к товару.

    Достаточно одного производителя с упоминаниями в требованиях: смешанное ТЗ («МИР С-05 или
    Энергомера CE208») тоже говорит о заточке, но под нескольких — тогда берётся тот, кого
    назвали чаще, а модели остальных в заключение не попадают как запрет.
    """

    best: BrandLock | None = None
    for manufacturer in manufacturers:
        pattern = _brand_pattern(manufacturer)
        if pattern is None:
            continue
        models: list[str] = []
        critical = total = 0
        for requirement in requirements:
            text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
            found = [match.group(0).strip(" .,;") for match in pattern.finditer(text)]
            if not found:
                continue
            total += 1
            if requirement.criticality == Criticality.CRITICAL.value:
                critical += 1
            for model in found:
                if model not in models:
                    models.append(model)
        if total == 0:
            continue
        # «МИР С-05.10-230-5(80)-» — обрезок полного обозначения, когда в ТЗ оно перенесено
        # через пробел; в список идёт только полное.
        models = [m for m in models if not any(o != m and o.startswith(m) for o in models)]
        if best is None or total > best.total_mentions:
            best = BrandLock(
                manufacturer=manufacturer,
                is_ours=bool(manufacturer.is_mirtek),
                models=models[:8],
                critical_mentions=critical,
                total_mentions=total,
                clause=None,
                equivalent_allowed=None,
            )
    if best is None:
        return None

    documents = _documents_text(db, tender)
    requirement_text = " ".join(f"{r.text or ''} {r.normalized_text or ''}" for r in requirements)
    clause = _TRADEMARK_CLAUSE_RE.search(documents) or _TRADEMARK_CLAUSE_RE.search(requirement_text)
    if clause:
        best.clause = re.sub(r"\s+", " ", clause.group(0)).strip()[:400]
        best.equivalent_allowed = False
    elif _EQUIVALENT_ALLOWED_RE.search(documents) or _EQUIVALENT_ALLOWED_RE.search(requirement_text):
        best.equivalent_allowed = True
    return best


def brand_lock_block(lock: BrandLock) -> str:
    who = manufacturer_label(lock.manufacturer)
    lines = ["ЗАТОЧКА ТЗ ПОД ТОВАРНЫЙ ЗНАК (найдено кодом по тексту ТЗ):"]
    lines.append(
        f"  ТЗ называет модели производителя «{who}» ({lock.manufacturer.legal_name}): "
        f"{'; '.join(lock.models)} — в {lock.total_mentions} требованиях к товару, "
        f"из них критичных {lock.critical_mentions}."
    )
    if lock.clause:
        lines.append(f"  В документации: «{lock.clause}».")
    if lock.equivalent_allowed is True:
        lines.append("  Эквивалент допускается («или эквивалент») — сравнивай по характеристикам.")
    elif lock.equivalent_allowed is False:
        lines.append("  Эквивалент не допускается: закупается товар именно этого товарного знака.")
    else:
        lines.append("  Про эквивалент документация молчит.")
    if lock.is_ours:
        lines.append("  Это наш товарный знак — ТЗ написано под приборы МИРТЕК.")
    elif lock.equivalent_allowed is False:
        lines.append(
            f"  Значит, поставить можно только приборы «{who}»: наши и других производителей не "
            "пройдут, сколько бы требований они ни выполняли по характеристикам."
        )
    else:
        lines.append(
            f"  Если эквивалент не допускается — поставить можно только приборы «{who}»; это нужно "
            "проверить по документации. Если предмет — работы или услуги с этими приборами "
            "(поверка, монтаж), а не их поставка, это не заточка под знак."
        )
    return "\n".join(lines)


# --- статус МСП по юрлицам группы ----------------------------------------------------------

_MSP_RE = re.compile(r"малого\s+и\s+среднего\s+предпринимательств|\bМСП\b", re.IGNORECASE)


def msp_block(db: Session, participant_requirements: list[Requirement]) -> str | None:
    """Закупка только для субъектов МСП — какие юрлица группы под это подходят.

    Без этого блока модель видела одну строку профиля «Реестр МСП: не входит» у основного
    юрлица и делала из неё главный вывод, хотя у группы есть микропредприятия, от имени
    которых участие возможно.
    """

    texts = [f"{r.text or ''} {r.normalized_text or ''}" for r in participant_requirements]
    if not any(_MSP_RE.search(text) for text in texts):
        return None
    from app.models.company_profile import CompanyProfile

    lines = ["СТАТУС МСП ЮРЛИЦ ГРУППЫ (в закупке есть требование к участнику о статусе МСП):"]
    profiles = list(db.scalars(select(CompanyProfile)))
    if not profiles:
        return None
    for profile in profiles:
        status = ((profile.rusprofile_data or {}).get("msp_status") or "нет данных").strip()
        lines.append(f"  - {profile.legal_name or 'юрлицо без названия'}: {status}")
    lines.append(
        "  Это требование к участнику, а не к прибору. Если хотя бы одно юрлицо группы — "
        "субъект МСП, участвовать можно от его имени: это оговорка, а не причина «не подходим»."
    )
    return "\n".join(lines)


def _product_requirements(db: Session, tender: Tender) -> list[Requirement]:
    return list(
        db.scalars(
            select(Requirement).where(
                Requirement.tender_id == tender.id,
                Requirement.kind == RequirementKind.PRODUCT.value,
            )
        )
    )


def collect_facts(db: Session, tender: Tender) -> ConclusionFacts:
    """Приборы и производители: кандидаты из каталога, итог матрицы, что у нас не так."""

    requirements = _product_requirements(db, tender)
    wins = {
        row.manufacturer_id: row
        for row in db.scalars(
            select(WinPercentage).where(
                WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True)
            )
        )
    }

    facts: list[ManufacturerFacts] = []
    for manufacturer in db.scalars(select(Manufacturer).order_by(Manufacturer.legal_name)):
        products = list(
            db.scalars(select(Product).where(Product.manufacturer_id == manufacturer.id))
        )
        limit = OUR_CANDIDATES if manufacturer.is_mirtek else COMPETITOR_CANDIDATES
        selected = (
            select_products_for_context(db, manufacturer, requirements, limit=limit)
            if products
            else []
        )
        facts.append(
            ManufacturerFacts(
                manufacturer=manufacturer,
                products=products,
                candidates=[item.product.model_name for item in selected],
                win=wins.get(manufacturer.id),
            )
        )

    mirtek = next((item for item in facts if item.manufacturer.is_mirtek), None)
    others = [item for item in facts if not item.manufacturer.is_mirtek]
    matrix_built = bool(wins)

    lines: list[str] = []
    lines.append("НАШИ ПРИБОРЫ (МИРТЕК):")
    if mirtek is None or not mirtek.products:
        lines.append("  каталог МИРТЕК пуст")
    else:
        lines.append(
            "  "
            + "; ".join(mirtek.candidates or [product.model_name for product in mirtek.products[:OUR_CANDIDATES]])
        )
        if mirtek.win is not None:
            win = mirtek.win
            lines.append(
                f"  Итог матрицы: {_VERDICT_TEXT.get(win.verdict or '', 'не определён')}, "
                f"{float(win.percentage):.0f}% (учтено {win.requirements_scored} из "
                f"{win.requirements_total} требований)"
            )
            if win.reason_summary:
                lines.append(f"  Пояснение матрицы: {win.reason_summary}")
            # Что у нас не так — по критичным и важным требованиям: из этого и строится
            # «проходим с оговорками» и список шагов.
            problems = db.execute(
                select(Requirement, ComplianceMatrixEntry)
                .join(ComplianceMatrixEntry, ComplianceMatrixEntry.requirement_id == Requirement.id)
                .where(
                    ComplianceMatrixEntry.tender_id == tender.id,
                    ComplianceMatrixEntry.manufacturer_id == mirtek.manufacturer.id,
                    ComplianceMatrixEntry.status != ComplianceStatus.MEETS.value,
                    Requirement.criticality.in_(
                        [Criticality.CRITICAL.value, Criticality.IMPORTANT.value]
                    ),
                )
                .limit(15)
            ).all()
            for requirement, entry in problems:
                lines.append(
                    f"  - [{_CRITICALITY_TEXT.get(requirement.criticality, '')}, {entry.status}"
                    f"{', проверить вручную' if entry.needs_human_review else ''}] "
                    f"{(requirement.normalized_text or requirement.text)[:200]}"
                    f"{f' — {entry.explanation[:250]}' if entry.explanation else ''}"
                )
        elif requirements:
            lines.append("  Матрица соответствия по МИРТЕК не построена.")
        else:
            lines.append(
                "  Требования к товару из документации не извлечены — приборы по ТЗ не "
                "проверялись, кандидаты подобраны без ТЗ."
            )

    lines.append("\nПРОИЗВОДИТЕЛИ (итог матрицы соответствия по ТЗ):")
    compared = [item for item in others if item.win is not None]
    compared.sort(key=lambda item: -float(item.win.percentage))  # type: ignore[union-attr]
    for item in compared:
        win = item.win
        assert win is not None
        lines.append(
            f"- {manufacturer_label(item.manufacturer)}: "
            f"{_VERDICT_TEXT.get(win.verdict or '', 'не определён')}, "
            f"{float(win.percentage):.0f}% (учтено {win.requirements_scored} из "
            f"{win.requirements_total})"
            f"{f'. {win.reason_summary[:300]}' if win.reason_summary else ''}"
            + (f". Модели: {'; '.join(item.candidates)}" if item.candidates else "")
        )
    rest = [item for item in others if item.win is None]
    if rest:
        lines.append(
            "- не сравнивались: "
            + ", ".join(manufacturer_label(item.manufacturer) for item in rest)
        )
    if not compared:
        lines.append("  матрица не построена — по конкурентам данных соответствия нет")

    # Заточка под товарный знак — в начало: это главный факт, когда он есть, и модель должна
    # увидеть его раньше процентов матрицы.
    lock = detect_brand_lock(db, tender, requirements, [item.manufacturer for item in facts])
    if lock is not None:
        lines.insert(0, brand_lock_block(lock) + "\n")

    participant_requirements = list(
        db.scalars(
            select(Requirement).where(
                Requirement.tender_id == tender.id,
                Requirement.kind == RequirementKind.PARTICIPANT.value,
            )
        )
    )
    msp = msp_block(db, participant_requirements)
    if msp:
        lines.append("\n" + msp)

    return ConclusionFacts(
        text="\n".join(lines),
        mirtek=mirtek,
        others=others,
        matrix_built=matrix_built,
        product_requirements=len(requirements),
        brand_lock=lock,
    )


def _niche(db: Session, tender: Tender) -> NicheStatistics | None:
    if not tender.okpd2_code:
        return None
    region_code = tender.region_delivery_code or tender.region_organizer_code
    query = select(NicheStatistics).where(NicheStatistics.okpd2_code == tender.okpd2_code)
    record = None
    if region_code:
        record = db.scalar(query.where(NicheStatistics.region_code == region_code))
    if record is None:
        record = db.scalar(query.where(NicheStatistics.region_code.is_(None)))
    return record


def _winner_names(items: list | None) -> list[str]:
    names: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name") or item.get("winner") or item.get("manufacturer")
            if name:
                names.append(str(name))
        elif item:
            names.append(str(item))
    return names[:5]


def market_block(
    db: Session, tender: Tender, participations: list[CompanyParticipation]
) -> str:
    """Ниша и наша история — вход для цены и рисков."""

    lines: list[str] = []
    niche = _niche(db, tender)
    if niche is not None:
        parts = []
        if niche.sample_size:
            parts.append(f"закупок в выборке {niche.sample_size}")
        if niche.avg_participants is not None:
            parts.append(f"участников в среднем {float(niche.avg_participants):.1f}")
        if niche.median_price_reduction_pct is not None:
            parts.append(f"медианное снижение цены {float(niche.median_price_reduction_pct):.1f}%")
        if niche.single_participant_share is not None:
            parts.append(f"доля закупок с одним участником {float(niche.single_participant_share):.0f}%")
        winners = _winner_names(niche.top_winners)
        if winners:
            parts.append("чаще выигрывают: " + ", ".join(winners))
        if parts:
            lines.append("НИША (ОКПД2 + регион): " + "; ".join(parts))

    if participations:
        lines.append("НАШИ УЧАСТИЯ В ПОХОЖИХ ЗАКУПКАХ И У ЭТОГО ЗАКАЗЧИКА:")
        for item in sorted(
            participations, key=lambda p: p.executed_at or datetime.min.date(), reverse=True
        )[:10]:
            details = [item.outcome]
            if item.price_drop_pct is not None:
                details.append(f"снижение {float(item.price_drop_pct):.1f}%")
            if item.competitors_count:
                details.append(f"конкурентов {item.competitors_count}")
            lines.append(
                f"- {(item.tender_title or '—')[:120]} · {item.customer_name or '—'} · "
                + ", ".join(details)
            )
    return "\n".join(lines)


def feedback_block(db: Session, tender: Tender) -> str | None:
    """Все несогласия специалистов по этой закупке — по порядку, последнее внизу."""

    rows = db.execute(
        select(AiScoreFeedback, User.full_name)
        .outerjoin(User, User.id == AiScoreFeedback.user_id)
        .where(
            AiScoreFeedback.tender_id == tender.id,
            AiScoreFeedback.kind == FeedbackKind.DISAGREE.value,
        )
        .order_by(AiScoreFeedback.created_at)
    ).all()
    if not rows:
        return None
    lines = ["ЗАМЕЧАНИЯ СПЕЦИАЛИСТОВ (специалист не согласен с прежним заключением):"]
    for feedback, author in rows:
        when = feedback.created_at.strftime("%d.%m.%Y %H:%M") if feedback.created_at else ""
        before = (feedback.before or {}).get("headline")
        lines.append(
            f"- {when}, {author or 'специалист'}"
            f"{f' (на заключение: «{before}»)' if before else ''}: {feedback.text}"
        )
    return "\n".join(lines)


# --- метрики (считает код) -----------------------------------------------------------------


def _days_left(deadline: datetime | None) -> int | None:
    if deadline is None:
        return None
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (deadline - now).days


def _plural(number: int, one: str, few: str, many: str) -> str:
    tail = number % 100
    if 11 <= tail <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(number % 10, many)


def build_metrics(
    db: Session,
    tender: Tender,
    facts: ConclusionFacts,
    participations: list[CompanyParticipation],
    *,
    history: Decimal | None,
    task: Decimal | None,
    competencies: Decimal | None,
    competencies_applicable: bool,
    overall: Decimal | None,
) -> list[dict]:
    """Плитки метрик: {key, label, value, hint, tone}. tone — good / warn / bad / neutral."""

    metrics: list[dict] = []

    days = _days_left(tender.application_end)
    if tender.application_end is not None:
        value = (
            "срок истёк"
            if days is not None and days < 0
            else f"{days} {_plural(days or 0, 'день', 'дня', 'дней')}"
        )
        metrics.append(
            {
                "key": "deadline",
                "label": "До подачи",
                "value": value,
                "hint": tender.application_end.strftime("%d.%m.%Y %H:%M"),
                "tone": "bad" if days is not None and days < 2 else "warn" if (days or 0) < 5 else "neutral",
            }
        )

    metrics.append(
        {
            "key": "price",
            "label": "НМЦК",
            "value": (
                f"{float(tender.price):,.0f} ₽".replace(",", " ")
                if tender.price
                else "не указана"
            ),
            "hint": None,
            "tone": "neutral" if tender.price else "warn",
        }
    )

    mirtek_win = facts.mirtek.win if facts.mirtek else None
    if mirtek_win is not None:
        tone = {"passes": "good", "caveats": "warn", "fails": "bad"}.get(
            mirtek_win.verdict or "", "neutral"
        )
        metrics.append(
            {
                "key": "matrix",
                "label": "Соответствие ТЗ",
                "value": f"{float(mirtek_win.percentage):.0f}%",
                "hint": (
                    f"учтено {mirtek_win.requirements_scored} из "
                    f"{mirtek_win.requirements_total} требований"
                ),
                "tone": tone,
            }
        )
    else:
        metrics.append(
            {
                "key": "matrix",
                "label": "Соответствие ТЗ",
                "value": "не проверено",
                "hint": "матрица соответствия не построена",
                "tone": "warn",
            }
        )

    compared = [item for item in facts.others if item.win is not None]
    # Расчёты матрицы до 25.09.2026 итога «проходит / нет» не имеют — для них проходящим
    # считается процент от 80, как у цветного бейджа списка, и подсказка это называет.
    legacy = any(item.win.verdict is None for item in compared)  # type: ignore[union-attr]
    passing = [
        item
        for item in compared
        if item.win.verdict in {"passes", "caveats"}  # type: ignore[union-attr]
        or (item.win.verdict is None and float(item.win.percentage) >= 80)  # type: ignore[union-attr]
    ]
    if compared:
        metrics.append(
            {
                "key": "competitors",
                "label": "Конкуренты проходят",
                "value": f"{len(passing)} из {len(compared)}",
                "hint": (
                    "по проценту матрицы ≥80% — пересчитайте матрицу для итога"
                    if legacy
                    else "по матрице соответствия"
                ),
                "tone": "neutral",
            }
        )

    decided = [
        item
        for item in participations
        if item.outcome
        in {
            ParticipationOutcome.WON.value,
            ParticipationOutcome.LOST.value,
            ParticipationOutcome.DISQUALIFIED.value,
        }
    ]
    if decided:
        wins = sum(1 for item in decided if item.outcome == ParticipationOutcome.WON.value)
        metrics.append(
            {
                "key": "history",
                "label": "Наши исходы",
                "value": f"{wins} {_plural(wins, 'победа', 'победы', 'побед')} из {len(decided)}",
                "hint": "похожие закупки и этот заказчик",
                "tone": "good" if wins * 2 >= len(decided) else "warn",
            }
        )
    elif history is not None:
        metrics.append(
            {
                "key": "history",
                "label": "История",
                "value": f"{float(history):.0f}%",
                "hint": None,
                "tone": "neutral",
            }
        )

    niche = _niche(db, tender)
    if niche is not None and niche.median_price_reduction_pct is not None:
        metrics.append(
            {
                "key": "niche_drop",
                "label": "Снижение в нише",
                "value": f"{float(niche.median_price_reduction_pct):.1f}%",
                "hint": (
                    f"участников в среднем {float(niche.avg_participants):.1f}"
                    if niche.avg_participants is not None
                    else "медиана"
                ),
                "tone": "neutral",
            }
        )

    metrics.append(
        {
            "key": "task",
            "label": "Предмет — наш профиль",
            "value": "—" if task is None else f"{float(task):.0f}%",
            "hint": "измерение «Задача»",
            "tone": "neutral",
        }
    )
    metrics.append(
        {
            "key": "competencies",
            "label": "Допуски участника",
            "value": (
                "не требуются"
                if not competencies_applicable
                else "—"
                if competencies is None
                else f"{float(competencies):.0f}%"
            ),
            "hint": "измерение «Компетенции»",
            "tone": "neutral",
        }
    )
    if overall is not None:
        metrics.append(
            {
                "key": "overall",
                "label": "AI-оценка профиля",
                "value": f"{float(overall):.0f}%",
                "hint": "среднее трёх измерений",
                "tone": "neutral",
            }
        )
    return metrics


# --- сверка ответа с каталогом --------------------------------------------------------------


def _match_product(name: str, products: list[Product]) -> Product | None:
    target = _norm(name)
    if len(target) < 3:
        return None
    exact = next((product for product in products if _norm(product.model_name) == target), None)
    if exact is not None:
        return exact
    # Модель пишет «МИР С-05» вместо «МИР С-05.10-230-5(80)» и наоборот — берём самое
    # длинное пересечение, но только по подстроке, не по «похожести».
    matches = [
        product
        for product in products
        if (norm := _norm(product.model_name)) and (target in norm or norm in target)
    ]
    matches.sort(key=lambda product: abs(len(_norm(product.model_name)) - len(target)))
    return matches[0] if matches else None


def _match_manufacturer(name: str, items: list[ManufacturerFacts]) -> ManufacturerFacts | None:
    target = _norm(name)
    if not target:
        return None
    for item in items:
        for candidate in (item.manufacturer.brand_name, item.manufacturer.legal_name):
            norm = _norm(candidate)
            if norm and (norm == target or norm in target or target in norm):
                return item
    return None


_PRODUCT_STATUSES = set(PRODUCT_STATUS_LABELS)


def _clean_products(answer: FitAnswer, facts: ConclusionFacts) -> list[dict]:
    if facts.mirtek is None:
        return []
    result: list[dict] = []
    seen: set[uuid.UUID] = set()
    for item in answer.our_products:
        product = _match_product(item.model, facts.mirtek.products)
        if product is None or product.id in seen:
            continue
        seen.add(product.id)
        status = item.status if item.status in _PRODUCT_STATUSES else "unchecked"
        # Без матрицы прибор по ТЗ никто не сверял — «подходит» было бы обещанием без факта.
        if not facts.matrix_built and status in {"fits", "partial"}:
            status = "unchecked"
        result.append(
            {
                "product_id": str(product.id),
                "model": product.model_name,
                "status": status,
                "status_label": PRODUCT_STATUS_LABELS[status],
                "note": item.note.strip(),
            }
        )
    return result[:4]


def _clean_competitors(answer: FitAnswer, facts: ConclusionFacts) -> list[dict]:
    result: list[dict] = []
    seen: set[uuid.UUID] = set()
    for item in answer.competitors:
        match = _match_manufacturer(item.manufacturer, facts.others)
        if match is None or match.manufacturer.id in seen:
            continue
        seen.add(match.manufacturer.id)
        product = _match_product(item.model, match.products) if item.model.strip() else None
        status = item.status if item.status in {"fits", "partial", "not_fits"} else "partial"
        win = match.win
        result.append(
            {
                "manufacturer_id": str(match.manufacturer.id),
                "manufacturer": manufacturer_label(match.manufacturer),
                "model": product.model_name if product else None,
                "product_id": str(product.id) if product else None,
                "status": status,
                "status_label": PRODUCT_STATUS_LABELS.get(status, status),
                "matrix_verdict": win.verdict if win else None,
                "matrix_percentage": float(win.percentage) if win else None,
                "note": item.note.strip(),
            }
        )
    return result[:5]


def _clean_risks(items: list[RiskAnswer]) -> list[dict]:
    order = {"significant": 0, "moderate": 1, "minor": 2}
    risks = [
        {
            "category": item.category if item.category in RISK_CATEGORY_LABELS else "other",
            "category_label": RISK_CATEGORY_LABELS.get(item.category, RISK_CATEGORY_LABELS["other"]),
            "severity": item.severity if item.severity in SEVERITIES else "moderate",
            "text": item.text.strip(),
            "mitigation": item.mitigation.strip(),
        }
        for item in items
        if item.text and item.text.strip()
    ]
    risks.sort(key=lambda risk: order.get(risk["severity"], 1))
    return risks[:10]


# --- расчёт --------------------------------------------------------------------------------


@dataclass
class ConclusionOutcome:
    conclusion: dict | None = None
    participate: bool | None = None
    messages: list[str] = field(default_factory=list)


def compute_conclusion(
    db: Session,
    tender: Tender,
    *,
    base_context: str,
    dimensions_text: str,
    facts: ConclusionFacts,
    market_text: str,
    feedback_text: str | None,
    metrics: list[dict],
) -> ConclusionOutcome:
    outcome = ConclusionOutcome()

    context = (
        f"{base_context}\n\n{facts.text}\n\n"
        + (f"{market_text}\n\n" if market_text else "")
        + f"ИЗМЕРЕНИЯ ПРОФИЛЯ:\n{dimensions_text}"
        + (f"\n\n{feedback_text}" if feedback_text else "")
    )

    fit_answer = run_structured(
        db,
        system_prompt=_FIT_PROMPT,
        user_text=context,
        response_model=FitAnswer,
        temperature=0.0,
    )
    fit = fit_answer.fit if fit_answer.fit in FIT_LABELS else Fit.UNKNOWN
    # То же правило, что в промпте, — кодом: модель иногда говорит «подходим» по одному
    # наименованию закупки, а проверять прибор было не с чем.
    if fit == Fit.FIT and not facts.matrix_built:
        fit = Fit.FIT_WITH_CAVEATS
    # ТЗ под чужой товарный знак без эквивалента — «не проходим» независимо от ответа модели:
    # характеристики тут ничего не решают.
    lock = facts.brand_lock
    if lock is not None and not lock.is_ours and lock.equivalent_allowed is False:
        fit = Fit.NOT_FIT

    conclusion: dict = {
        "fit": fit,
        "fit_label": FIT_LABELS[fit],
        "headline": fit_answer.headline.strip(),
        "rationale": fit_answer.rationale.strip(),
        "our_products": _clean_products(fit_answer, facts),
        "competitors": _clean_competitors(fit_answer, facts),
        "matrix_built": facts.matrix_built,
        "product_requirements": facts.product_requirements,
        "feedback_response": fit_answer.feedback_response.strip() or None,
        "strategy": None,
        "risks": [],
        "metrics": metrics,
    }
    outcome.participate = False if fit == Fit.NOT_FIT else bool(fit_answer.participate)

    try:
        plan = run_structured(
            db,
            system_prompt=_PLAN_PROMPT,
            user_text=(
                f"{context}\n\nЗАКЛЮЧЕНИЕ: {conclusion['fit_label']}. {conclusion['headline']} "
                f"{conclusion['rationale']}\nНаши приборы: "
                + (
                    "; ".join(
                        f"{item['model']} — {item['status_label']}"
                        for item in conclusion["our_products"]
                    )
                    or "не названы"
                )
                + "\nКто проходит: "
                + (
                    "; ".join(
                        f"{item['manufacturer']} {item['model'] or ''} — {item['status_label']}"
                        for item in conclusion["competitors"]
                    )
                    or "не названы"
                )
            ),
            response_model=PlanAnswer,
            temperature=0.2,
        )
        conclusion["strategy"] = {
            "approach": plan.approach.strip(),
            "price": plan.price.strip(),
            "steps": [step.strip() for step in plan.steps if step.strip()][:6],
        }
        conclusion["risks"] = _clean_risks(plan.risks)
    except Exception as exc:  # noqa: BLE001 - заключение без плана всё равно отвечает на главное
        outcome.messages.append(f"Стратегия и риски не составлены: {exc}")

    outcome.conclusion = conclusion
    return outcome


def snapshot(score) -> dict:
    """Компактный снимок заключения — для пары «до / после» в ответах специалистов."""

    conclusion = score.conclusion or {}
    return {
        "score_id": str(score.id),
        "verdict": score.verdict,
        "overall_score": float(score.overall_score) if score.overall_score is not None else None,
        "fit": conclusion.get("fit"),
        "fit_label": conclusion.get("fit_label"),
        "headline": conclusion.get("headline") or score.summary,
        "rationale": conclusion.get("rationale"),
        "our_products": conclusion.get("our_products") or [],
        "competitors": conclusion.get("competitors") or [],
        "strategy": conclusion.get("strategy"),
        "risks": conclusion.get("risks") or [],
        "ai_model": score.ai_model,
        "calculated_at": score.calculated_at.isoformat() if score.calculated_at else None,
    }
