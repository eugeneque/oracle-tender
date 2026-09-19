"""AI-оценка по профилю: History / Task / Competencies (раздел 5.5.1 ТЗ, 03.09.2026).

Главная метрика тендера. Отвечает на вопрос «стоит ли МИРТЕК идти в эту закупку», а не «какой
прибор подходит под ТЗ» — на второй отвечает матрица соответствия (`compliance_service`).

Три принципа, из которых собран этот модуль:

1. **Каждое число прослеживаемо.** Модель не называет проценты «из головы»: ей выдаются
   пронумерованные требования тендера и пронумерованные строки профиля компании, а в ответе
   она обязана перечислить номера, на которые опирается. Номера превращаются в `*_evidence`
   со ссылками на реальные `requirements.id` и ключи полей профиля — интерфейс по ним
   показывает, из чего сложилась цифра.
2. **«Нет данных» — не ноль.** History без записей в `company_participations` по этой
   закупке остаётся `null` и исключается из среднего, а не штрафует тендер (формула
   раздела 5.5.1).
3. **Длинные генерации разбиваются.** Task, Competencies и текстовый блок «Резюме /
   Слабые места / Стратегия» — три отдельных вызова: одним промптом длинный JSON рвётся по
   лимиту токенов на выходе (проверено на проекте, раздел 5.5.1 ТЗ).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import pydantic
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ai_profile import (
    SEVERITY_ORDER,
    VERDICT_LABELS,
    AiProfileScore,
    EvidenceType,
    Verdict,
    WeakPointSeverity,
)
from app.models.analysis import (
    Criticality,
    Requirement,
    RequirementKind,
    TenderOutcome,
    WinPercentage,
)
from app.models.company_participation import (
    OUTCOME_LABELS,
    WINS_ONLY_SOURCES,
    CompanyParticipation,
    ParticipationOutcome,
)
from app.models.company_profile import CompanyProfile
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer
from app.models.market import SimilarTender
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.user import User
from app.services import company_profile_service
from app.services.audit import log_action
from app.services.ai_client import run_structured

# Сколько требований уходит в промпт. Больше — не помещается вместе с профилем и рвёт ответ
# по лимиту токенов; требования при этом отбираются не подряд, а по критичности (см.
# `_select_requirements`), чтобы обрезать хвост из второстепенных, а не начало.
MAX_REQUIREMENTS_IN_PROMPT = 40

# Доля производителей тендера с заполненным каталогом, начиная с которой матрица
# соответствия (раздел 5.5.2) подключается как уточняющий вход к Task. К 09.09.2026 каталог
# закрыт на 3 из 13 производителей, порог не достигается, и матрица в оценку не идёт —
# ровно как решено в разделе 0.2 ТЗ. Когда каталог наполнится, вход включится сам.
MATRIX_COVERAGE_THRESHOLD = 0.8


class AiProfileError(RuntimeError):
    """Оценку посчитать нечем: пустой профиль компании или нет требований тендера.
    Эндпоинт показывает текст пользователю как есть."""


# --- схемы структурированного ответа -------------------------------------------------
# Все поля обязательные, без значений по умолчанию: Yandex AI Studio отклоняет схему с
# необязательным полем, а модель поле со значением по умолчанию просто не заполняет
# (раздел 5.5.1 ТЗ). «Не знаю» модель обязана выразить явно — пустым списком или пустой
# строкой по инструкции промпта.


class DimensionAnswer(pydantic.BaseModel):
    """Ответ модели по одному измерению."""

    score: int
    comment: str
    requirement_numbers: list[int]
    profile_numbers: list[int]


class WeakPointAnswer(pydantic.BaseModel):
    severity: str
    text: str


class ResumeAnswer(pydantic.BaseModel):
    summary: str
    weak_points: list[WeakPointAnswer]
    strategy_verdict: str
    strategy_price: str
    strategy_first_step: str


_TASK_PROMPT = """Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Оцени измерение «Задача» (Task): насколько предмет закупки соответствует
опыту и продукции компании.

Верни JSON:
- score: целое число 0-100. 100 — компания ровно этим и занимается, у неё есть похожие
  выполненные проекты и подходящая продукция; 50 — предмет смежный, часть работ непрофильна;
  0 — предмет закупки к деятельности компании отношения не имеет.
- comment: 1-3 предложения, почему именно такая цифра. Без общих слов, со ссылкой на суть
  закупки и конкретный опыт компании.
- requirement_numbers: номера требований закупки (из списка ТРЕБОВАНИЯ), которые сильнее
  всего повлияли на оценку. Пустой список, если ни одно требование не оказалось решающим.
- profile_numbers: номера строк профиля компании (из списка ПРОФИЛЬ КОМПАНИИ), на которых
  основан вывод. Пустой список, если профиль ничего не подтверждает.

Оценивай предмет закупки, а не оформление документации. Не выдумывай опыт, которого нет в
профиле: если подтверждения нет — это довод снизить оценку, а не повод предположить.
Отвечай по-русски."""

_COMPETENCIES_PROMPT = """Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Оцени измерение «Компетенции» (Competencies): хватает ли компании
формальных допусков, лицензий и стажа под требования закупки.

Верни JSON:
- score: целое число 0-100. 100 — все обязательные допуски и требуемый опыт у компании есть;
  50 — часть требований закрыта, часть под вопросом; 0 — обязательные допуски отсутствуют.
- comment: 1-3 предложения: какие требования закрыты, какие нет.
- requirement_numbers: номера требований закупки, относящихся к допускам, лицензиям, стажу
  и опыту участника, которые повлияли на оценку.
- profile_numbers: номера строк профиля компании, подтверждающих (или не подтверждающих)
  соответствие.

Считай только формальные требования к участнику — технические характеристики приборов
оценивает другой раздел. Отсутствие сведений в профиле — это «не подтверждено», а не
«соответствует». Отвечай по-русски."""

_RESUME_PROMPT = """Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Тебе даны сведения о закупке, уже посчитанные измерения AI-оценки и уже
вынесенное решение «смотреть / не смотреть». Сформулируй итоговый блок для карточки.
Решение не пересматривай — объясни его и подскажи, что делать дальше.

Верни JSON:
- summary: 2-4 предложения — суть закупки, главная сложность, ценовой ориентир, если он
  следует из данных. Не повторяй цифры измерений дословно.
- weak_points: список слабых мест. Каждое — {severity, text}. severity строго одно из:
  "significant" (может стоить участия или денег), "moderate" (требует внимания),
  "minor" (мелочь). text — одно предложение по делу. Пустой список, если слабых мест нет.
- strategy_verdict: одна строка — что делаем и почему.
- strategy_price: одна строка — ценовой ориентир или ставка. Если данных о цене нет, так и
  напиши: "данных о цене недостаточно".
- strategy_first_step: одна строка — одно конкретное следующее действие.

Не выдумывай фактов, которых нет во входных данных. Отвечай по-русски."""


class DecisionAnswer(pydantic.BaseModel):
    summary: str
    participate: bool


_DECISION_PROMPT = """Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Тебе даны три уже посчитанных измерения AI-оценки закупки — «История»
(участвовала ли компания в похожих закупках и с каким итогом), «Задача» (насколько предмет
закупки соответствует опыту и продукции) и «Компетенции» (хватает ли формальных допусков и
стажа) — с комментариями и обоснованиями.

Нужен один ответ: стоит ли тендерному отделу вообще тратить время на эту закупку.

Верни JSON:
- summary: 2-4 предложения — служебная сводка по трём измерениям для самого решения:
  что за них, что против, что перевешивает. Пользователю она не показывается.
- participate: true — закупку стоит смотреть и готовить участие; false — не стоит.

Правила: обязательные допуски, которых у компании нет, — это false, каким бы профильным ни
был предмет. Непрофильный предмет закупки — false. Отсутствие истории само по себе не
причина для false, если задача и компетенции подтверждены. Сомнение при подтверждённых
задаче и компетенциях трактуй в пользу true — окончательное решение примет человек.
Не выдумывай фактов, которых нет во входных данных. Отвечай по-русски."""


@dataclass
class ProfileScoreOutcome:
    """Что получилось у расчёта — для журнала и ответа эндпоинта."""

    score: AiProfileScore | None = None
    messages: list[str] = field(default_factory=list)


# --- сбор входных данных ---------------------------------------------------------------


def _select_requirements(db: Session, tender: Tender) -> list[Requirement]:
    """Требования тендера, обрезанные до размера промпта — критичные первыми.

    Порядок важен: если резать список по времени извлечения, в промпт попадёт начало
    документации (обычно общие фразы), а критичные требования из технических приложений
    отвалятся — ровно тот случай, который на созвоне 02.09 назвали «система не погружается
    вовнутрь тендера».
    """

    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.created_at)
        )
    )
    weight = {
        Criticality.CRITICAL.value: 0,
        Criticality.IMPORTANT.value: 1,
        Criticality.MINOR.value: 2,
    }
    requirements.sort(key=lambda item: weight.get(item.criticality, 1))
    return requirements[:MAX_REQUIREMENTS_IN_PROMPT]


_KIND_LABELS = {
    RequirementKind.PRODUCT.value: "к товару",
    RequirementKind.SERVICE.value: "к работам",
    RequirementKind.PARTICIPANT.value: "к участнику",
}


def _requirements_block(requirements: list[Requirement]) -> str:
    # Вид требования — в скобках рядом с критичностью: измерение «Компетенции» ищет
    # требования к участнику, «Задача» — к товару и работам, и без пометки модель
    # угадывала бы, что есть что.
    if not requirements:
        return "(требования из документации ещё не извлечены)"
    return "\n".join(
        f"{number}. [{requirement.criticality}, {_KIND_LABELS.get(requirement.kind, 'к товару')}] "
        f"{(requirement.normalized_text or requirement.text)[:400]}"
        for number, requirement in enumerate(requirements, start=1)
    )


def _tender_block(db: Session, tender: Tender) -> str:
    """Краткая карточка закупки для промпта: то, что определяет предмет и условия."""

    lines = [
        f"Наименование: {tender.title}",
        f"Заказчик: {tender.customer_name or '—'}",
        f"Способ закупки: {tender.procurement_method or '—'}",
        f"НМЦК: {tender.price if tender.price is not None else '—'} {tender.currency}",
        f"ОКПД2: {tender.okpd2_code or '—'}",
        f"Тип конкурса: {tender.tender_type or '—'}",
        f"Срок подачи заявок: {tender.application_end or '—'}",
    ]

    # Разделы вкладки «Дополнительно» (раздел 5.6 ТЗ) — уже извлечённые моделью условия
    # контракта, допуски и требования к участнику. Для Competencies это главный вход:
    # формальные требования к участнику редко попадают в перечень технических требований,
    # и без этого блока измерение считалось бы по одному наименованию закупки.
    from app.services.tender_insights import EXTRA_SECTIONS

    card = db.get(TenderCard, tender.id)
    stored = ((card.payload or {}).get("extra_sections") if card else None) or {}
    sections = stored.get("sections") or {}
    for key, title in EXTRA_SECTIONS.items():
        points = sections.get(key) or []
        if points:
            body = "\n".join(f"- {point}" for point in points)
            lines.append(f"\n== {title} ==\n{body[:1500]}")
    return "\n".join(lines)


def _matrix_hint(db: Session, tender: Tender) -> str | None:
    """Процент соответствия характеристик как уточняющий вход к Task (раздел 5.5.2 ТЗ).

    Подключается только когда каталог продукции представителен — не менее
    `MATRIX_COVERAGE_THRESHOLD` производителей с заполненными характеристиками (решение
    раздела 0.2 ТЗ). До этого момента возвращается None: матрица, посчитанная по трём
    производителям из тринадцати, сместила бы оценку в пользу тех, чей каталог успели
    завести, а не тех, кто действительно подходит.

    Подсказка уходит в промпт текстом, а не примешивается к числу арифметически: веса
    смешивания в ТЗ не зафиксированы и пересматриваются на созвоне 09.09.
    """

    manufacturers = list(db.scalars(select(Manufacturer)))
    if not manufacturers:
        return None

    rows = db.execute(
        select(WinPercentage.manufacturer_id, WinPercentage.percentage, Manufacturer.is_mirtek)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(WinPercentage.tender_id == tender.id, WinPercentage.is_current.is_(True))
    ).all()
    if not rows:
        return None

    coverage = len(rows) / len(manufacturers)
    if coverage < MATRIX_COVERAGE_THRESHOLD:
        return None

    mirtek_percentage = next(
        (percentage for _, percentage, is_mirtek in rows if is_mirtek), None
    )
    if mirtek_percentage is None:
        return None
    return (
        f"Процент соответствия характеристик приборов МИРТЕК требованиям закупки: "
        f"{float(mirtek_percentage):.0f}% (посчитан по каталогу, покрытие "
        f"{coverage * 100:.0f}% производителей)."
    )


# --- измерения --------------------------------------------------------------------------


def _evidence(
    answer: DimensionAnswer,
    requirements: list[Requirement],
    profile: CompanyProfile,
) -> list[dict]:
    """Превращает номера из ответа модели в ссылки на реальные объекты.

    Номера вне диапазона отбрасываются молча: модель иногда ссылается на требование, которого
    в промпте не было, и такая «опора» хуже, чем её отсутствие — по ней ничего не проверить.
    """

    evidence: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for number in answer.requirement_numbers:
        if not 1 <= number <= len(requirements):
            continue
        requirement = requirements[number - 1]
        key = (EvidenceType.REQUIREMENT.value, str(requirement.id))
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "type": EvidenceType.REQUIREMENT.value,
                "ref_id": str(requirement.id),
                "note": (requirement.normalized_text or requirement.text)[:300],
            }
        )

    profile_fields = company_profile_service.profile_fields(profile)
    for number in answer.profile_numbers:
        if not 1 <= number <= len(profile_fields):
            continue
        field_key, text = profile_fields[number - 1]
        key = (EvidenceType.COMPANY_PROFILE_FIELD.value, field_key)
        if key in seen:
            continue
        seen.add(key)
        evidence.append(
            {
                "type": EvidenceType.COMPANY_PROFILE_FIELD.value,
                "ref_id": field_key,
                "note": text[:300],
            }
        )
    return evidence


def _clamp_score(value: int) -> Decimal:
    return Decimal(str(max(0, min(100, int(value)))))


def _ask_dimension(
    db: Session,
    *,
    system_prompt: str,
    user_text: str,
) -> DimensionAnswer:
    return run_structured(
        db,
        system_prompt=system_prompt,
        user_text=user_text,
        response_model=DimensionAnswer,
        temperature=0.1,
    )


# Организационно-правовые формы и служебные слова, которые не отличают одного заказчика от
# другого. «ПАО "Россети Северный Кавказ"» на rusprofile и «ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО
# "РОССЕТИ СЕВЕРНЫЙ КАВКАЗ"» в ЕИС — один заказчик, и сравнивать надо ядро названия.
_LEGAL_FORM_WORDS = {
    "публичное", "акционерное", "общество", "с", "ограниченной", "ответственностью",
    "открытое", "закрытое", "непубличное", "государственное", "муниципальное", "унитарное",
    "предприятие", "бюджетное", "казённое", "казенное", "учреждение", "федеральное",
    "автономное", "пао", "ао", "оао", "зао", "ооо", "гуп", "муп", "фгуп", "фгбу", "гбу",
    "мбу", "ип", "нао", "тд", "торговый", "дом", "филиал", "компания",
}
# Слова предмета закупки, которые встречаются почти в каждой и потому ничего не говорят о
# сходстве: «поставка», «нужд», «услуги»…
_SUBJECT_STOP_WORDS = {
    "поставка", "поставки", "поставку", "выполнение", "оказание", "услуг", "услуги", "работ",
    "работы", "нужд", "нужды", "для", "год", "году", "года", "филиал", "филиала", "филиалов",
    "объект", "объектов", "объекта", "закупка", "право", "заключение", "договора", "договор",
    "том", "числе", "также", "или", "иных", "прочих", "комплектующих",
}


def _customer_core(name: str | None) -> str:
    """Ядро названия заказчика: без кавычек, организационно-правовой формы и лишних пробелов."""

    if not name:
        return ""
    words = re.findall(r"[а-яёa-z0-9]+", name.lower().replace("ё", "е"))
    core = [word for word in words if word not in _LEGAL_FORM_WORDS and len(word) > 1]
    return " ".join(core)


def _subject_stems(text: str | None) -> set[str]:
    """Грубые основы значимых слов предмета закупки: первые 6 букв слова длиннее трёх.

    Полноценная морфология здесь не нужна: «счетчиков»/«счетчики»/«счетчик» сходятся на
    «счетчи», и этого хватает, чтобы отличить закупку счётчиков от закупки кабеля.
    """

    if not text:
        return set()
    words = re.findall(r"[а-яёa-z0-9]+", text.lower().replace("ё", "е"))
    return {word[:6] for word in words if len(word) > 3 and word not in _SUBJECT_STOP_WORDS}


def _subject_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / min(len(a), len(b))


# Порог сходства предмета закупки: доля общих основ от меньшего набора. Ниже — общие слова
# вроде «электрической энергии» есть и у закупки счётчиков, и у закупки трансформаторов.
_SUBJECT_SIMILARITY_THRESHOLD = 0.5
_SUBJECT_MIN_SHARED = 2


def _relevant_participations(
    db: Session, tender: Tender, similar_ids: list[uuid.UUID]
) -> tuple[list[CompanyParticipation], list[str]]:
    """Участия, которые вправе говорить об этой закупке, и человекочитаемое «почему они».

    Три признака родства — из формулировки раздела 5.5.1 ТЗ («похожие закупки у похожих
    заказчиков»):

    * закупка та же по смыслу — участие привязано к тендеру из списка похожих;
    * заказчик тот же — сравнивается ядро названия без организационно-правовой формы и
      кавычек (18.09.2026: история с rusprofile хранит короткие имена «ПАО "…"», а
      тендеры из ЕИС — полные, и подстрока их не сводила);
    * предмет закупки похож по словам — для истории, которая пришла по ИНН за годы до
      появления системы и ни к какому нашему тендеру не привязана (18.09.2026).

    Записи, попавшие по нескольким признакам, не удваиваются.
    """

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    if mirtek is None:
        return [], []

    found: dict[uuid.UUID, CompanyParticipation] = {}
    reasons: list[str] = []

    if similar_ids:
        by_tender = list(
            db.scalars(
                select(CompanyParticipation).where(
                    CompanyParticipation.manufacturer_id == mirtek.id,
                    CompanyParticipation.tender_id.in_(similar_ids),
                )
            )
        )
        if by_tender:
            reasons.append(f"по похожим закупкам — {len(by_tender)}")
        found.update({item.id: item for item in by_tender})

    # Истории у компании сотни записей, не миллионы: нормализовать имена и сравнить предметы
    # в Python проще и надёжнее, чем изобретать это в SQL.
    all_items = list(
        db.scalars(
            select(CompanyParticipation).where(CompanyParticipation.manufacturer_id == mirtek.id)
        )
    )

    customer_core = _customer_core(tender.customer_name)
    if len(customer_core) >= 4:
        by_customer = [
            item
            for item in all_items
            if item.id not in found
            and (core := _customer_core(item.customer_name))
            and len(core) >= 4
            and (core in customer_core or customer_core in core)
        ]
        if by_customer:
            reasons.append(f"по тому же заказчику — {len(by_customer)}")
        found.update({item.id: item for item in by_customer})

    tender_stems = _subject_stems(tender.title)
    if tender_stems:
        by_subject = []
        for item in all_items:
            if item.id in found:
                continue
            stems = _subject_stems(item.tender_title)
            if (
                len(tender_stems & stems) >= _SUBJECT_MIN_SHARED
                and _subject_similarity(tender_stems, stems) >= _SUBJECT_SIMILARITY_THRESHOLD
            ):
                by_subject.append(item)
        if by_subject:
            reasons.append(f"по схожему предмету закупки — {len(by_subject)}")
        found.update({item.id: item for item in by_subject})

    return list(found.values()), reasons


def _all_decided_participations(db: Session) -> list[CompanyParticipation]:
    """Вся история компании с известным исходом — запасной уровень измерения History."""

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    if mirtek is None:
        return []
    return list(
        db.scalars(
            select(CompanyParticipation).where(
                CompanyParticipation.manufacturer_id == mirtek.id,
                CompanyParticipation.outcome.in_(
                    [
                        ParticipationOutcome.WON.value,
                        ParticipationOutcome.LOST.value,
                        ParticipationOutcome.DISQUALIFIED.value,
                    ]
                ),
            )
        )
    )


def _participation_evidence(items: list[CompanyParticipation]) -> list[dict]:
    """Ссылки на конкретные участия — то, по чему человек проверяет цифру History."""

    return [
        {
            "type": EvidenceType.COMPANY_PARTICIPATION.value,
            "ref_id": str(item.id),
            "note": (
                f"{OUTCOME_LABELS.get(item.outcome, item.outcome)}: "
                f"{item.tender_title or item.external_tender_id or 'закупка без названия'}"
                + (f" ({item.customer_name})" if item.customer_name else "")
            ),
        }
        for item in items
    ]


def _history_from_outcomes(
    db: Session, similar_ids: list[uuid.UUID]
) -> tuple[Decimal | None, str | None, list[dict]]:
    """Запасной путь: по-тендерные исходы `tender_outcomes` (раздел 5.5.2 ТЗ).

    Он не про нас, а про рынок — «кто выигрывал такие закупки», — и подключается только
    когда собственной истории участия по этой нише нет вовсе. Пока по-тендерного источника
    исходов не появилось, таблица пуста, и путь молча ничего не даёт.
    """

    if not similar_ids:
        return None, None, []
    outcomes = db.execute(
        select(TenderOutcome.tender_id, TenderOutcome.winner_manufacturer_name).where(
            TenderOutcome.tender_id.in_(similar_ids)
        )
    ).all()
    if not outcomes:
        return None, None, []

    mirtek = db.scalar(select(Manufacturer).where(Manufacturer.is_mirtek.is_(True)))
    brand = (mirtek.brand_name or mirtek.legal_name).lower() if mirtek else ""
    wins = sum(1 for _, winner in outcomes if brand and winner and brand in winner.lower())
    score = Decimal(f"{wins / len(outcomes) * 100:.2f}")
    evidence = [
        {
            "type": EvidenceType.SIMILAR_TENDER.value,
            "ref_id": str(tender_id),
            "note": f"победитель: {winner or 'не указан'}",
        }
        for tender_id, winner in outcomes
    ]
    comment = (
        f"Собственных участий по похожим закупкам не найдено; по {len(outcomes)} похожим "
        f"закупкам с известным победителем компания выигрывала {wins} раз."
    )
    return score, comment, evidence


def _history_dimension(
    db: Session, tender: Tender
) -> tuple[Decimal | None, str, list[dict]]:
    """Измерение History (раздел 5.5.1 ТЗ, уточнение 03.09.2026).

    Считается по РЕАЛЬНОЙ истории участия МИРТЕК (`company_participations`, выгружается
    из реестра контрактов ЕИС по ИНН), а не по агрегату ниши: агрегат живёт в
    `niche_statistics` и питает вкладку «Расчёт», а History отвечает на вопрос «выигрывали
    ли МЫ такие же тендеры у таких же заказчиков».

    Итог — доля побед среди участий с ИЗВЕСТНЫМ исходом. Записи «исход неизвестен» в
    знаменатель не идут: неполнота данных не должна выглядеть как череда поражений. Снятия
    с торгов (`disqualified`) в знаменателе остаются — контракт мы всё-таки не получили, —
    но выносятся в комментарий отдельной цифрой, потому что лечатся они не ценой, а
    оформлением заявки.

    Когда своей истории по этой закупке нет, а `null` неизбежен, честное «недостаточно
    данных» лучше нуля: ноль читается как «проверили и побед нет», то есть клевещет на
    компанию.
    """

    similar_ids = [
        row[0]
        for row in db.execute(
            select(SimilarTender.similar_tender_id)
            .where(SimilarTender.tender_id == tender.id)
            .order_by(SimilarTender.similarity_score.desc())
            .limit(20)
        ).all()
    ]

    participations, reasons = _relevant_participations(db, tender, similar_ids)
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
        # Выборка целиком из источника, который знает только о победах (реестр контрактов
        # ЕИС), долю побед дать не может: она выйдет 100% не потому, что мы не проигрывали,
        # а потому, что проигрыши туда не попадают. Это зеркальное отражение правила «ноль
        # ≠ нет данных» — цифра, посчитанная по заведомо однобокой выборке, врёт так же.
        # Сами победы при этом не пропадают: они уходят в комментарий и в evidence, где их
        # видит человек.
        if all(item.source in WINS_ONLY_SOURCES for item in decided) and all(
            item.outcome == ParticipationOutcome.WON.value for item in decided
        ):
            evidence = _participation_evidence(decided)
            return (
                None,
                f"Подтверждённых побед по этой закупке: {len(decided)}"
                + (f" ({', '.join(reasons)})" if reasons else "")
                + ". Доля побед не считается: данные взяты из реестра контрактов ЕИС, где "
                "есть только заключённые контракты, а проигрышей нет по устройству. "
                "Измерение исключено из итоговой оценки — добавьте проигранные закупки "
                "вручную в разделе «Настройки → Моя компания», чтобы оно заработало.",
                evidence,
            )

        wins = sum(1 for item in decided if item.outcome == ParticipationOutcome.WON.value)
        disqualified = sum(
            1 for item in decided if item.outcome == ParticipationOutcome.DISQUALIFIED.value
        )
        score = Decimal(f"{wins / len(decided) * 100:.2f}")
        evidence = _participation_evidence(decided)
        unknown_count = len(participations) - len(decided)
        comment_parts = [
            f"Найдено участий, относящихся к этой закупке: {len(participations)}"
            + (f" ({', '.join(reasons)})" if reasons else "")
            + f"; из них с известным исходом — {len(decided)}, побед — {wins}."
        ]
        if disqualified:
            comment_parts.append(
                f"Снятий с торгов по формальным основаниям — {disqualified}: это риск "
                f"оформления заявки, а не цены."
            )
        if unknown_count:
            comment_parts.append(
                f"Ещё {unknown_count} участий с неизвестным исходом в расчёт не взяты."
            )
        return score, " ".join(comment_parts), evidence

    fallback_score, fallback_comment, fallback_evidence = _history_from_outcomes(db, similar_ids)
    if fallback_score is not None:
        return fallback_score, fallback_comment or "", fallback_evidence

    if participations:
        return (
            None,
            f"Участия по этой закупке найдены ({len(participations)}), но ни у одного не "
            "известен исход — измерение исключено из итоговой оценки.",
            [],
        )

    # Третий уровень (18.09.2026): прямых совпадений нет, но история компании в целом есть —
    # с rusprofile она приходит вместе с проигрышами. Общая доля побед — более грубая мера,
    # чем «выигрывали ли такие же закупки», но всё же мера, а не «нет данных»; комментарий
    # прямо говорит, по чему она посчитана. Правило «только победы — не выборка» действует
    # и здесь.
    general = _all_decided_participations(db)
    if general and not (
        all(item.source in WINS_ONLY_SOURCES for item in general)
        and all(item.outcome == ParticipationOutcome.WON.value for item in general)
    ):
        wins = sum(1 for item in general if item.outcome == ParticipationOutcome.WON.value)
        score = Decimal(f"{wins / len(general) * 100:.2f}")
        return (
            score,
            f"Участий по похожим закупкам или у этого заказчика не найдено; оценка дана по "
            f"всей истории участий компании: {len(general)} участий с известным исходом, "
            f"побед — {wins}. Это общая доля побед, а не по нише закупки.",
            _participation_evidence(general),
        )
    if not similar_ids:
        return (
            None,
            "Недостаточно данных: похожие закупки ещё не рассчитаны, а истории участия по "
            "этому заказчику нет. Измерение исключено из итоговой оценки.",
            [],
        )
    return (
        None,
        f"Похожих закупок найдено {len(similar_ids)}, но собственных участий по ним нет — "
        "измерение исключено из итоговой оценки. Синхронизируйте историю участий в разделе "
        "«Настройки → Моя компания».",
        [],
    )


def _overall(
    history: Decimal | None, task: Decimal | None, competencies: Decimal | None
) -> Decimal | None:
    """Формула раздела 5.5.1 ТЗ: среднее по доступным измерениям.

    History не «считается нулём», а исключается из расчёта. Если недоступны и Task, и
    Competencies, итог тоже `null` — оценка не посчитана, и показать её числом нельзя.
    """

    values = [value for value in (history, task, competencies) if value is not None]
    if not values:
        return None
    return Decimal(f"{sum(float(value) for value in values) / len(values):.2f}")


def _normalize_weak_points(items: list[WeakPointAnswer]) -> list[dict]:
    allowed = {item.value for item in WeakPointSeverity}
    normalized = [
        {
            "severity": (
                item.severity if item.severity in allowed else WeakPointSeverity.MODERATE.value
            ),
            "text": item.text.strip(),
        }
        for item in items
        if item.text and item.text.strip()
    ]
    normalized.sort(key=lambda item: SEVERITY_ORDER.get(item["severity"], 1))
    return normalized


def _decision_context(
    tender: Tender,
    *,
    history: tuple[Decimal | None, str | None, list | None],
    task: tuple[Decimal | None, str | None, list],
    competencies: tuple[Decimal | None, str | None, list],
) -> str:
    """Три измерения с комментариями и обоснованиями — всё, на чём строится решение.
    Само обоснование (`*_evidence`) даётся текстом, а не номерами: решению нужны факты,
    а не ссылки на них."""

    def block(name: str, item: tuple) -> str:
        score, comment, evidence = item
        lines = [f"{name}: {'нет данных' if score is None else f'{int(score)}%'}"]
        if comment:
            lines.append(f"  Комментарий: {comment}")
        for fact in (evidence or [])[:8]:
            text = (fact.get("note") or "").strip() if isinstance(fact, dict) else str(fact)
            if text:
                lines.append(f"  - {text}")
        return "\n".join(lines)

    return (
        f"ЗАКУПКА: {tender.title}\n"
        f"Заказчик: {tender.customer_name or 'не указан'}\n\n"
        "ИЗМЕРЕНИЯ:\n"
        + block("История", history)
        + "\n"
        + block("Задача", task)
        + "\n"
        + block("Компетенции", competencies)
    )


def _decision_label(decision: bool | None) -> str:
    if decision is None:
        return "не выносилось"
    return "стоит смотреть" if decision else "не стоит смотреть"


def _verdict(decision: bool | None, overall: Decimal | None) -> str:
    """Вердикт «идти / с оговорками / не идти» — выводится, а не спрашивается у модели.

    До 18.09.2026 вердикт выбирала модель в промпте «Резюме», без оглядки на процент и на
    решение «смотреть / не смотреть» из отдельного вызова. На карточке это давало красный
    крест, 34% и «ИДТИ С ОГОВОРКАМИ» одновременно — три ответа на один вопрос. Теперь
    источник один: решение (`ai_decision`) главнее всего, а между «идти» и «с оговорками»
    выбирает порог процента — тот же, что у цветного бейджа списка (раздел 5.6 ТЗ):
    ≥80 — зелёный, 50-80 — жёлтый, ниже — красный.
    """

    if decision is False:
        return Verdict.NO_GO.value
    if overall is None:
        return Verdict.GO_WITH_RESERVATIONS.value
    if overall >= 80:
        return Verdict.GO.value
    if overall >= 50:
        return Verdict.GO_WITH_RESERVATIONS.value
    # Решение «смотреть» при низком проценте — оговорки, а не «не идти»: решение
    # выносилось с учётом того, что человек примет окончательное; отказ ниже не рисуем.
    if decision is True:
        return Verdict.GO_WITH_RESERVATIONS.value
    return Verdict.NO_GO.value


def compute_profile_score(
    db: Session, tender: Tender, *, actor: User | None = None
) -> ProfileScoreOutcome:
    """Считает AI-оценку по профилю и сохраняет её новой текущей версией."""

    outcome = ProfileScoreOutcome()
    profile = company_profile_service.require_filled(db)
    requirements = _select_requirements(db, tender)
    if not requirements:
        outcome.messages.append(
            "Требования из документации не извлечены — оценка считается только по карточке "
            "закупки, точность ниже"
        )

    tender_block = _tender_block(db, tender)
    requirements_block = _requirements_block(requirements)
    profile_block = company_profile_service.profile_block(profile)
    matrix_hint = _matrix_hint(db, tender)

    base_context = (
        f"ЗАКУПКА:\n{tender_block}\n\n"
        f"ТРЕБОВАНИЯ:\n{requirements_block}\n\n"
        f"ПРОФИЛЬ КОМПАНИИ:\n{profile_block}"
    )
    task_context = base_context + (f"\n\nДОПОЛНИТЕЛЬНО:\n{matrix_hint}" if matrix_hint else "")

    task_score: Decimal | None = None
    task_comment: str | None = None
    task_evidence: list[dict] = []
    try:
        answer = _ask_dimension(db, system_prompt=_TASK_PROMPT, user_text=task_context)
        task_score = _clamp_score(answer.score)
        task_comment = answer.comment.strip()
        task_evidence = _evidence(answer, requirements, profile)
    except Exception as exc:  # noqa: BLE001 - сбой одного измерения не отменяет остальные
        logger.warning(f"Измерение Task для {tender.external_id} не посчитано: {exc}")
        outcome.messages.append(f"Измерение «Задача» не посчитано: {exc}")

    competencies_score: Decimal | None = None
    competencies_comment: str | None = None
    competencies_evidence: list[dict] = []
    try:
        answer = _ask_dimension(
            db, system_prompt=_COMPETENCIES_PROMPT, user_text=base_context
        )
        competencies_score = _clamp_score(answer.score)
        competencies_comment = answer.comment.strip()
        competencies_evidence = _evidence(answer, requirements, profile)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Измерение Competencies для {tender.external_id} не посчитано: {exc}")
        outcome.messages.append(f"Измерение «Компетенции» не посчитано: {exc}")

    history_score, history_comment, history_evidence = _history_dimension(db, tender)
    overall = _overall(history_score, task_score, competencies_score)

    # Решение «смотреть / не смотреть» (замечание 17.09.2026) — отдельным вызовом по трём
    # измерениям, а не порогом по проценту: процент усредняет, а решение должно ломаться об
    # одно «нет допуска» независимо от остальных двух. Без Задачи и Компетенций решать
    # нечего — остаётся `None`.
    decision: bool | None = None
    decision_summary: str | None = None
    if task_score is not None or competencies_score is not None:
        try:
            answer = run_structured(
                db,
                system_prompt=_DECISION_PROMPT,
                user_text=_decision_context(
                    tender,
                    history=(history_score, history_comment, history_evidence),
                    task=(task_score, task_comment, task_evidence),
                    competencies=(competencies_score, competencies_comment, competencies_evidence),
                ),
                response_model=DecisionAnswer,
                temperature=0.1,
            )
            decision = bool(answer.participate)
            decision_summary = answer.summary.strip() or None
        except Exception as exc:  # noqa: BLE001 - без решения оценка всё равно полезна
            logger.warning(f"Решение по тендеру {tender.external_id} не вынесено: {exc}")
            outcome.messages.append(f"Решение «смотреть / не смотреть» не вынесено: {exc}")

    summary: str | None = None
    verdict = _verdict(decision, overall)
    weak_points: list[dict] = []
    strategy: dict | None = None
    try:
        resume_context = (
            f"{base_context}\n\n"
            f"ИЗМЕРЕНИЯ:\n"
            f"История: {'нет данных' if history_score is None else f'{history_score}%'}\n"
            f"Задача: {'не посчитано' if task_score is None else f'{task_score}%'}"
            f"{f' — {task_comment}' if task_comment else ''}\n"
            f"Компетенции: "
            f"{'не посчитано' if competencies_score is None else f'{competencies_score}%'}"
            f"{f' — {competencies_comment}' if competencies_comment else ''}\n"
            f"Итог: {'не посчитан' if overall is None else f'{overall}%'}\n"
            f"РЕШЕНИЕ: {_decision_label(decision)} — {VERDICT_LABELS.get(verdict, verdict)}"
            f"{f'. {decision_summary}' if decision_summary else ''}"
        )
        resume = run_structured(
            db,
            system_prompt=_RESUME_PROMPT,
            user_text=resume_context,
            response_model=ResumeAnswer,
            temperature=0.2,
        )
        summary = resume.summary.strip()
        weak_points = _normalize_weak_points(resume.weak_points)
        strategy = {
            "verdict": resume.strategy_verdict.strip(),
            "price": resume.strategy_price.strip(),
            "first_step": resume.strategy_first_step.strip(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Резюме по тендеру {tender.external_id} не составлено: {exc}")
        outcome.messages.append(f"Блок «Резюме» не составлен: {exc}")

    record = _store(
        db,
        tender,
        history=(history_score, history_comment, history_evidence),
        task=(task_score, task_comment, task_evidence),
        competencies=(competencies_score, competencies_comment, competencies_evidence),
        overall=overall,
        summary=summary,
        verdict=verdict,
        weak_points=weak_points,
        strategy=strategy,
        profile=profile,
        decision=(decision, decision_summary),
    )
    outcome.score = record

    log_action(
        db,
        component="ai_profile",
        action=f"compute_profile_score:{tender.external_id}",
        result="success" if overall is not None else "partial",
        level=LogLevel.INFO if overall is not None else LogLevel.WARNING,
        details=(
            f"Итог: {overall if overall is not None else '—'}; задача: "
            f"{task_score if task_score is not None else '—'}; компетенции: "
            f"{competencies_score if competencies_score is not None else '—'}; "
            f"слабых мест: {len(weak_points)}"
            + ("; " + "; ".join(outcome.messages) if outcome.messages else "")
        ),
        user_id=actor.id if actor else None,
    )
    db.commit()
    return outcome


def _store(
    db: Session,
    tender: Tender,
    *,
    history: tuple[Decimal | None, str | None, list],
    task: tuple[Decimal | None, str | None, list],
    competencies: tuple[Decimal | None, str | None, list],
    overall: Decimal | None,
    summary: str | None,
    verdict: str,
    weak_points: list[dict],
    strategy: dict | None,
    profile: CompanyProfile,
    decision: tuple[bool | None, str | None] = (None, None),
) -> AiProfileScore:
    """Сохраняет новую текущую оценку, погасив предыдущую.

    Прежняя строка не удаляется и не переписывается: пересчёт после правки профиля меняет
    цифры, и без истории объяснить это изменение нечем (раздел 7 ТЗ).
    """

    previous = db.scalar(
        select(AiProfileScore).where(
            AiProfileScore.tender_id == tender.id, AiProfileScore.is_current.is_(True)
        )
    )
    if previous is not None:
        previous.is_current = False
        db.flush()

    record = AiProfileScore(
        tender_id=tender.id,
        history_score=history[0],
        history_comment=history[1],
        history_evidence=history[2],
        task_score=task[0],
        task_comment=task[1],
        task_evidence=task[2],
        competencies_score=competencies[0],
        competencies_comment=competencies[1],
        competencies_evidence=competencies[2],
        overall_score=overall,
        summary=summary,
        verdict=verdict,
        weak_points=weak_points,
        recommended_strategy=strategy,
        decision=decision[0],
        decision_summary=decision[1],
        company_profile_snapshot=company_profile_service.snapshot(profile),
        is_current=True,
    )
    db.add(record)
    db.flush()
    return record


def get_current(db: Session, tender_id: uuid.UUID) -> AiProfileScore | None:
    return db.scalar(
        select(AiProfileScore).where(
            AiProfileScore.tender_id == tender_id, AiProfileScore.is_current.is_(True)
        )
    )


def list_history(db: Session, tender_id: uuid.UUID) -> list[AiProfileScore]:
    """Все пересчёты по тендеру, свежие первыми — карточка показывает, как менялась оценка."""

    return list(
        db.scalars(
            select(AiProfileScore)
            .where(AiProfileScore.tender_id == tender_id)
            .order_by(AiProfileScore.calculated_at.desc())
        )
    )


# --- представление для API ------------------------------------------------------------


def serialize(db: Session, score: AiProfileScore) -> dict:
    """Оценка в виде, готовом для карточки: с русскими подписями вердикта и значимости.

    Подписи проставляются здесь, а не на фронте: перечень значений и их русские названия
    должны жить в одном месте — иначе новый вердикт появится в API и молча не появится в
    интерфейсе.
    """

    from app.models.ai_profile import SEVERITY_LABELS

    weak_points = [
        {
            "severity": item.get("severity", WeakPointSeverity.MODERATE.value),
            "severity_label": SEVERITY_LABELS.get(
                item.get("severity", ""), SEVERITY_LABELS[WeakPointSeverity.MODERATE.value]
            ),
            "text": item.get("text", ""),
        }
        for item in (score.weak_points or [])
    ]

    similar_ids = [
        uuid.UUID(item["ref_id"])
        for item in (score.history_evidence or [])
        if item.get("type") == EvidenceType.SIMILAR_TENDER.value and item.get("ref_id")
    ]
    # Участия — вторая опора History (уточнение 03.09.2026). Карточка показывает их числом
    # рядом с похожими тендерами: иначе блок «на чём основана История» выглядел бы пустым
    # там, где данные как раз есть.
    participation_ids = [
        uuid.UUID(item["ref_id"])
        for item in (score.history_evidence or [])
        if item.get("type") == EvidenceType.COMPANY_PARTICIPATION.value and item.get("ref_id")
    ]

    return {
        "id": score.id,
        "tender_id": score.tender_id,
        "history_score": score.history_score,
        "history_comment": score.history_comment,
        "history_evidence": score.history_evidence or [],
        "task_score": score.task_score,
        "task_comment": score.task_comment,
        "task_evidence": score.task_evidence or [],
        "competencies_score": score.competencies_score,
        "competencies_comment": score.competencies_comment,
        "competencies_evidence": score.competencies_evidence or [],
        "overall_score": score.overall_score,
        "summary": score.summary,
        "verdict": score.verdict,
        "verdict_label": VERDICT_LABELS.get(score.verdict or ""),
        # Сводка решения (`decision_summary`) наружу не отдаётся — она служебная.
        "decision": score.decision,
        "weak_points": weak_points,
        "recommended_strategy": score.recommended_strategy,
        "similar_tender_ids": similar_ids,
        "participation_ids": participation_ids,
        "company_profile_snapshot": score.company_profile_snapshot,
        "calculated_at": score.calculated_at,
    }
