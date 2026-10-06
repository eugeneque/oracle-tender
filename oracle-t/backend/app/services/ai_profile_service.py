"""AI-оценка по профилю: History / Task / Competencies (раздел 5.5.1 ТЗ, 03.09.2026).

Главная метрика тендера. Отвечает на вопрос «стоит ли МИРТЕК идти в эту закупку», а не «какой
прибор подходит под ТЗ» — на второй отвечает матрица соответствия (`compliance_service`).

Три принципа, из которых собран этот модуль:

1. **Каждое число прослеживаемо.** Модель не называет проценты вовсе (с 25.09.2026): она
   проставляет статусы пунктам чек-листа — критериям «Задачи» и требованиям к участнику в
   «Компетенциях», — а число считает код по весам (`TASK_CRITERIA`, `STATUS_VALUES`). К
   каждому пункту модель перечисляет номера требований и строк профиля, на которые
   опирается; номера превращаются в `*_evidence` со ссылками на реальные
   `requirements.id` и ключи полей профиля. Промпты и правила расчёта для согласования с
   тендерным отделом — в `AI_SCORE_PROMPTS.md`.
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
    CHECK_STATUS_LABELS,
    SEVERITY_ORDER,
    VERDICT_LABELS,
    CheckStatus,
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
    WinVerdict,
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
from app.services import ai_conclusion_service, company_profile_service
from app.services.audit import log_action
from app.services.ai_client import active_model, run_structured
from app.services.yandex_ai_client import drain_fallback_notes
from app.core.jobs import report_progress

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


class CriterionAnswer(pydantic.BaseModel):
    """Статус одного критерия «Задачи»."""

    code: str
    status: str
    comment: str
    requirement_numbers: list[int]
    profile_numbers: list[int]


class TaskAnswer(pydantic.BaseModel):
    criteria: list[CriterionAnswer]
    comment: str


class ParticipantRequirementAnswer(pydantic.BaseModel):
    """Одно требование к участнику и его статус против профиля — пункт «Компетенций»."""

    text: str
    requirement_number: int
    mandatory: bool
    status: str
    comment: str
    profile_numbers: list[int]


class CompetenciesAnswer(pydantic.BaseModel):
    requirements: list[ParticipantRequirementAnswer]
    comment: str


class WeakPointAnswer(pydantic.BaseModel):
    severity: str
    text: str


class ResumeAnswer(pydantic.BaseModel):
    summary: str
    weak_points: list[WeakPointAnswer]
    strategy_verdict: str
    strategy_price: str
    strategy_first_step: str


# --- чек-листы измерений (замечание 25.09.2026) ------------------------------------------
# До этой правки «Задачу» и «Компетенции» модель оценивала числом 0-100 по шкале с тремя
# опорными точками. Модели прилипали к опорам: одно измерение одного и того же тендера
# прыгало между 0 и 50 от запуска к запуску, а у Claude и YandexGPT — тем более, и итог
# сдвигался на 17-25 пунктов. Теперь модель отвечает на узкие вопросы — статус каждого
# пункта, — а число считает код по весам ниже. Веса и формулировки согласуются с
# тендерным отделом (AI_SCORE_PROMPTS.md), поэтому живут константами, а не внутри текста.

# Сколько даёт статус пункта. `not_applicable` в расчёт не входит вовсе.
STATUS_VALUES: dict[str, float] = {
    CheckStatus.MET.value: 1.0,
    CheckStatus.PARTIAL.value: 0.5,
    CheckStatus.UNKNOWN.value: 0.5,
    CheckStatus.NOT_MET.value: 0.0,
}


@dataclass(frozen=True)
class TaskCriterion:
    code: str
    title: str
    weight: int
    met: str
    partial: str
    not_met: str
    # Пустая строка — критерий применим всегда.
    not_applicable: str = ""


TASK_CRITERIA: tuple[TaskCriterion, ...] = (
    TaskCriterion(
        code="subject",
        title="Предмет закупки — профиль компании",
        weight=40,
        met=(
            "закупаются приборы учёта электроэнергии, системы учёта (АСКУЭ, АИИС КУЭ), "
            "УСПД и компоненты систем учёта, либо их монтаж, наладка, обслуживание, поверка"
        ),
        partial=(
            "учёт — только часть закупки, или предмет смежный: оборудование, которое "
            "компания поставляет не как основную продукцию (шкафы учёта, трансформаторы "
            "тока, каналообразующее оборудование)"
        ),
        not_met="предмет закупки к деятельности компании не относится",
    ),
    TaskCriterion(
        code="product_fit",
        title="Продукция и работы компании закрывают требования к товару и работам",
        weight=30,
        met=(
            "среди требований «к товару» и «к работам» нет таких, которые продукция или "
            "работы компании заведомо не выполняют"
        ),
        partial=(
            "отдельные требования по профилю не подтверждаются или потребуют доработки, "
            "субподрядчика, закупки у третьих лиц"
        ),
        not_met=(
            "есть критичное требование, которое компания выполнить не может (другой тип "
            "прибора, другой вид работ)"
        ),
        not_applicable="требований к товару и работам в списке нет",
    ),
    TaskCriterion(
        code="experience",
        title="Подтверждённый опыт аналогичных поставок или работ",
        weight=20,
        met=(
            "в профиле есть реализованные проекты того же вида (та же продукция или те же "
            "работы) — номер строки профиля обязателен"
        ),
        partial="в профиле есть проекты смежного вида, другого масштаба или другой отрасли",
        not_met="подтверждений аналогичного опыта в профиле нет",
    ),
    TaskCriterion(
        code="scope_purity",
        title="В закупке нет существенной непрофильной части",
        weight=10,
        met="вся закупка в профиле компании",
        partial=(
            "есть непрофильная часть, которую можно закрыть субподрядом или закупкой у "
            "третьих лиц"
        ),
        not_met="значительная доля закупки по объёму или цене непрофильна",
    ),
)
TASK_CRITERIA_BY_CODE = {criterion.code: criterion for criterion in TASK_CRITERIA}


def _task_criteria_block() -> str:
    blocks = []
    for number, criterion in enumerate(TASK_CRITERIA, start=1):
        lines = [
            f'{number}. code "{criterion.code}": {criterion.title}.',
            f"   met — {criterion.met};",
            f"   partial — {criterion.partial};",
            f"   not_met — {criterion.not_met}.",
        ]
        if criterion.not_applicable:
            lines[-1] = lines[-1][:-1] + ";"
            lines.append(f"   not_applicable — {criterion.not_applicable}.")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


_TASK_PROMPT = f"""Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Оцени измерение «Задача» (Task): насколько предмет закупки соответствует
опыту и продукции компании. Число не называй — проставь статус каждому критерию ниже,
процент посчитает система.

КРИТЕРИИ И ЗНАЧЕНИЕ СТАТУСОВ:
{_task_criteria_block()}

Верни JSON:
- criteria: ровно по одному объекту на каждый критерий из списка, в том же порядке:
  - code: код критерия из списка;
  - status: строго одно из "met", "partial", "not_met", "not_applicable"
    ("not_applicable" — только там, где оно описано у критерия);
  - comment: одно предложение — почему такой статус, со ссылкой на суть закупки или
    конкретный опыт компании;
  - requirement_numbers: номера требований закупки (из списка ТРЕБОВАНИЯ), на которые
    опирается статус. Пустой список, если требования ни при чём;
  - profile_numbers: номера строк профиля компании (из списка ПРОФИЛЬ КОМПАНИИ), на
    которых основан статус. Пустой список, если профиль ничего не подтверждает.
- comment: 1-2 предложения — общий вывод по измерению.

Оценивай предмет закупки, а не оформление документации. Не выдумывай опыт, которого нет в
профиле: если подтверждения нет — это "partial" или "not_met", а не "met".
Отвечай по-русски."""

_COMPETENCIES_PROMPT = """Ты — руководитель тендерного отдела производителя приборов учёта
электроэнергии. Оцени измерение «Компетенции» (Competencies): хватает ли компании
формальных допусков, лицензий и стажа под требования закупки. Число не называй — выпиши
требования к участнику и проставь каждому статус, процент посчитает система.

ЧТО СЧИТАТЬ ТРЕБОВАНИЕМ К УЧАСТНИКУ: лицензии и допуски (СРО, ФСБ, ФСТЭК и т.п.),
сертификаты систем менеджмента, нахождение продукции или ПО в реестрах, если это условие
допуска, стаж и опыт исполнения аналогичных договоров, кадры, производственные мощности,
сервисные центры, статус участника (например, МСП), если это условие допуска.
Источники — список ТРЕБОВАНИЯ (пометка «к участнику») и разделы карточки закупки.

НЕ ВКЛЮЧАЙ: типовые декларации участника (не в реестре недобросовестных поставщиков, не в
стадии ликвидации или банкротства, нет недоимок, нет судимости у руководителя, нет
конфликта интересов, не офшор) — они подтверждаются декларацией в заявке; обеспечение
заявки и договора; требования к оформлению заявки; технические характеристики товара —
их оценивает другой раздел. Одинаковые требования из разных источников объединяй.

Верни JSON:
- requirements: список требований к участнику, не более 12. Каждое:
  - text: требование своими словами, коротко;
  - requirement_number: номер из списка ТРЕБОВАНИЯ, или 0, если требование взято из
    разделов карточки закупки;
  - mandatory: true — без выполнения заявку отклонят; false — требование даёт баллы при
    оценке заявок или носит рекомендательный характер;
  - status: строго одно из:
    "met" — профиль прямо подтверждает (в profile_numbers обязателен номер строки);
    "not_met" — профиль прямо противоречит: стаж или дата регистрации меньше требуемой,
      допуск указан с истёкшим сроком, требуется статус, которого у компании нет;
    "unknown" — в профиле нет сведений ни за, ни против;
  - comment: одно предложение — чем подтверждено или чего не хватает;
  - profile_numbers: номера строк профиля компании, на которых основан статус.
  Пустой список, если формальных требований к участнику в закупке нет.
- comment: 1-2 предложения — какие требования закрыты, какие нет.

Отсутствие сведений в профиле — это "unknown", а не "met". Не выдумывай допусков, которых
нет в профиле. Отвечай по-русски."""

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
    RequirementKind.SUPPLY.value: "условия поставки",
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
    requirement_numbers: list[int],
    profile_numbers: list[int],
    requirements: list[Requirement],
    profile: CompanyProfile,
) -> list[dict]:
    """Превращает номера из ответа модели в ссылки на реальные объекты.

    Номера вне диапазона отбрасываются молча: модель иногда ссылается на требование, которого
    в промпте не было, и такая «опора» хуже, чем её отсутствие — по ней ничего не проверить.
    """

    evidence: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for number in requirement_numbers:
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
    for number in profile_numbers:
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


def _valid_profile_numbers(numbers: list[int], profile: CompanyProfile) -> list[int]:
    total = len(company_profile_service.profile_fields(profile))
    return [number for number in numbers if 1 <= number <= total]


def _checklist_item(
    title: str,
    status: str,
    comment: str,
    *,
    weight: int | None = None,
    mandatory: bool | None = None,
) -> dict:
    return {
        "title": title,
        "status": status,
        "status_label": CHECK_STATUS_LABELS.get(status, status),
        "comment": comment,
        "weight": weight,
        "mandatory": mandatory,
    }


def _score_task(
    answer: TaskAnswer, requirements: list[Requirement], profile: CompanyProfile
) -> tuple[Decimal | None, list[dict], list[dict]]:
    """Число «Задачи» по статусам критериев: взвешенная доля, `not_applicable` вне расчёта.

    Правила поверх ответа модели:
    - критерий, который модель пропустила или пометила неизвестным статусом, считается
      «частично» — нейтральная середина, а не штраф и не подарок; в комментарии это видно;
    - «опыт выполнено» без ссылки на строку профиля понижается до «частично»: подтверждение,
      которое нельзя проверить, не подтверждение;
    - непрофильный предмет закупки (`subject` = not_met) обнуляет измерение целиком — иначе
      остальные критерии натянули бы ему до 60%.
    """

    answers = {item.code: item for item in answer.criteria}
    checklist: list[dict] = []
    requirement_numbers: list[int] = []
    profile_numbers: list[int] = []
    earned = 0.0
    total = 0

    for criterion in TASK_CRITERIA:
        item = answers.get(criterion.code)
        allowed = {CheckStatus.MET.value, CheckStatus.PARTIAL.value, CheckStatus.NOT_MET.value}
        if criterion.not_applicable:
            allowed.add(CheckStatus.NOT_APPLICABLE.value)

        if item is None:
            status, comment = CheckStatus.PARTIAL.value, "Модель критерий не оценила — учтён как «частично»."
        else:
            status = item.status.strip().lower()
            comment = item.comment.strip()
            if status not in allowed:
                comment = f"{comment} (статус «{item.status}» не распознан — учтён как «частично»)".strip()
                status = CheckStatus.PARTIAL.value
            valid_profile = _valid_profile_numbers(item.profile_numbers, profile)
            if (
                criterion.code == "experience"
                and status == CheckStatus.MET.value
                and not valid_profile
            ):
                status = CheckStatus.PARTIAL.value
                comment = f"{comment} (без ссылки на профиль — понижено до «частично»)".strip()
            requirement_numbers.extend(item.requirement_numbers)
            profile_numbers.extend(valid_profile)

        checklist.append(_checklist_item(criterion.title, status, comment, weight=criterion.weight))
        if status == CheckStatus.NOT_APPLICABLE.value:
            continue
        earned += criterion.weight * STATUS_VALUES[status]
        total += criterion.weight

    evidence = _evidence(requirement_numbers, profile_numbers, requirements, profile)
    subject = checklist[0]["status"]
    if subject == CheckStatus.NOT_MET.value:
        return Decimal("0"), checklist, evidence
    if not total:
        return None, checklist, evidence
    return Decimal(str(round(earned / total * 100))), checklist, evidence


def _score_competencies(
    answer: CompetenciesAnswer, requirements: list[Requirement], profile: CompanyProfile
) -> tuple[Decimal | None, list[dict], list[dict]]:
    """Число «Компетенций» по списку требований к участнику.

    - Требований к участнику нет — измерение «не применимо» (`None`) и исключается из
      итога: раньше модель в этом случае ставила то 0 («ничего не подтверждено»), то 50,
      то 100 («требовать нечего»), и это был главный источник расхождений между моделями.
    - Невыполненное обязательное требование обнуляет измерение: без него заявку отклонят.
    - Иначе — средняя по пунктам: выполнено 1, не подтверждено 0,5, не выполнено 0.
    - «Выполнено» без ссылки на строку профиля понижается до «не подтверждено».
    """

    checklist: list[dict] = []
    requirement_numbers: list[int] = []
    profile_numbers: list[int] = []
    values: list[float] = []
    mandatory_failed = False
    allowed = {CheckStatus.MET.value, CheckStatus.NOT_MET.value, CheckStatus.UNKNOWN.value}

    for item in answer.requirements:
        text = item.text.strip()
        if not text:
            continue
        status = item.status.strip().lower()
        comment = item.comment.strip()
        if status not in allowed:
            comment = f"{comment} (статус «{item.status}» не распознан — учтён как «не подтверждено»)".strip()
            status = CheckStatus.UNKNOWN.value
        valid_profile = _valid_profile_numbers(item.profile_numbers, profile)
        if status == CheckStatus.MET.value and not valid_profile:
            status = CheckStatus.UNKNOWN.value
            comment = f"{comment} (без ссылки на профиль — понижено до «не подтверждено»)".strip()
        if item.requirement_number:
            requirement_numbers.append(item.requirement_number)
        profile_numbers.extend(valid_profile)
        if item.mandatory and status == CheckStatus.NOT_MET.value:
            mandatory_failed = True
        values.append(STATUS_VALUES[status])
        checklist.append(_checklist_item(text, status, comment, mandatory=bool(item.mandatory)))

    evidence = _evidence(requirement_numbers, profile_numbers, requirements, profile)
    if not checklist:
        return None, checklist, evidence
    if mandatory_failed:
        return Decimal("0"), checklist, evidence
    return Decimal(str(round(sum(values) / len(values) * 100))), checklist, evidence


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

    similar_ids = _similar_ids(db, tender)

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


# Потолки, когда наш прибор не проходит (06.10.2026). Закупка 32616436166: заключение «НЕ
# ИДТИ — ТЗ требует фирменное ПО Энергомеры», а рядом бейдж «AI 67%» — среднее истории,
# задачи и компетенций, которое о провале по критичному требованию не знало.
# «Задача» — может ли компания выполнить предмет закупки своими приборами: при «не проходит»
# в матрице это красная зона. Итог при заключении «не подходим» — тоже красная зона, ниже
# порога «с оговорками» (50).
FAILED_MATRIX_TASK_CAP = 20
NOT_FIT_OVERALL_CAP = 30


def _apply_matrix_gate(
    db: Session,
    tender: Tender,
    task_score: Decimal | None,
    task_comment: str | None,
    task_checklist: list[dict] | None,
) -> tuple[Decimal | None, str | None]:
    """«Задача» не выше `FAILED_MATRIX_TASK_CAP`, если в матрице соответствия приборы МИРТЕК
    не проходят по критичному требованию. Причина дописывается в комментарий и чек-лист."""

    row = db.execute(
        select(WinPercentage.verdict, WinPercentage.reason_summary)
        .join(Manufacturer, Manufacturer.id == WinPercentage.manufacturer_id)
        .where(
            WinPercentage.tender_id == tender.id,
            WinPercentage.is_current.is_(True),
            Manufacturer.is_mirtek.is_(True),
        )
    ).first()
    if row is None or row.verdict != WinVerdict.FAILS.value or task_score is None:
        return task_score, task_comment
    if task_score <= FAILED_MATRIX_TASK_CAP:
        return task_score, task_comment
    reason = (row.reason_summary or "не выполнены критичные требования")[:300]
    if task_checklist is not None:
        task_checklist.append(
            _checklist_item(
                "Приборы МИРТЕК проходят по критичным требованиям ТЗ",
                CheckStatus.NOT_MET.value,
                reason,
                mandatory=True,
            )
        )
    note = (
        f"Оценка ограничена {FAILED_MATRIX_TASK_CAP}%: по матрице соответствия приборы МИРТЕК "
        f"не проходят — {reason}"
    )
    return Decimal(FAILED_MATRIX_TASK_CAP), f"{task_comment} {note}".strip() if task_comment else note


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
    task: tuple,
    competencies: tuple,
) -> str:
    """Три измерения с комментариями и обоснованиями — всё, на чём строится решение.
    Само обоснование (`*_evidence`) даётся текстом, а не номерами: решению нужны факты,
    а не ссылки на них."""

    def block(name: str, item: tuple) -> str:
        score, comment, evidence, *rest = item
        checklist = rest[0] if rest else None
        lines = [f"{name}: {'нет данных' if score is None else f'{int(score)}%'}"]
        if comment:
            lines.append(f"  Комментарий: {comment}")
        # Пункты чек-листа — главный вход решения: правило «нет обязательного допуска —
        # false» модель может применить, только видя, какой пункт не выполнен.
        for point in checklist or []:
            mark = " (обязательное)" if point.get("mandatory") else ""
            lines.append(f"  * {point['title']}{mark}: {point['status_label']}. {point['comment']}")
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


def _legacy_resume(
    db: Session,
    tender: Tender,
    outcome: ProfileScoreOutcome,
    *,
    base_context: str,
    history: tuple,
    task: tuple,
    competencies: tuple,
    overall: Decimal | None,
) -> tuple[bool | None, str | None, str | None, str, list[dict], dict | None]:
    """Решение и «Резюме» прежним путём (до 28.09.2026) — когда заключение не составилось."""

    history_score, history_comment, history_evidence = history
    task_score, task_comment, task_evidence, task_checklist = task
    (
        competencies_score,
        competencies_comment,
        competencies_evidence,
        competencies_checklist,
    ) = competencies

    # Решение «смотреть / не смотреть» (замечание 17.09.2026) — отдельным вызовом по трём
    # измерениям, а не порогом по проценту: процент усредняет, а решение должно ломаться об
    # одно «нет допуска» независимо от остальных двух. Без Задачи и Компетенций решать
    # нечего — остаётся `None`.
    decision: bool | None = None
    decision_summary: str | None = None
    if task_checklist is not None or competencies_checklist is not None:
        try:
            answer = run_structured(
                db,
                system_prompt=_DECISION_PROMPT,
                user_text=_decision_context(
                    tender,
                    history=(history_score, history_comment, history_evidence),
                    task=(task_score, task_comment, task_evidence, task_checklist),
                    competencies=(
                        competencies_score,
                        competencies_comment,
                        competencies_evidence,
                        competencies_checklist,
                    ),
                ),
                response_model=DecisionAnswer,
                temperature=0.0,
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
            f"{('не применимо' if competencies_checklist == [] else 'не посчитано') if competencies_score is None else f'{competencies_score}%'}"
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
    return decision, decision_summary, summary, verdict, weak_points, strategy


def _verdict_from_conclusion(conclusion: dict, decision: bool | None) -> str:
    """Вердикт из заключения: «не подходим» и отказ от участия — НЕ ИДТИ, «подходим» — ИДТИ,
    остальное — С ОГОВОРКАМИ. Процент профиля больше не решает: он отвечает на вопрос «похожа
    ли закупка на нас», а не «проходит ли наш прибор»."""

    fit = conclusion.get("fit")
    if decision is False or fit == "not_fit":
        return Verdict.NO_GO.value
    if fit == "fit":
        return Verdict.GO.value
    return Verdict.GO_WITH_RESERVATIONS.value


def _similar_ids(db: Session, tender: Tender) -> list[uuid.UUID]:
    return [
        row[0]
        for row in db.execute(
            select(SimilarTender.similar_tender_id)
            .where(SimilarTender.tender_id == tender.id)
            .order_by(SimilarTender.similarity_score.desc())
            .limit(20)
        ).all()
    ]


def compute_profile_score(
    db: Session, tender: Tender, *, actor: User | None = None
) -> ProfileScoreOutcome:
    """Считает AI-оценку по профилю и сохраняет её новой текущей версией."""

    outcome = ProfileScoreOutcome()
    # Заметки о запасных путях (отказ YandexGPT по фильтру) — только этого расчёта.
    drain_fallback_notes()
    profile = company_profile_service.require_filled(db)
    requirements = _select_requirements(db, tender)
    if not requirements:
        outcome.messages.append(
            "Требования из документации не извлечены — оценка считается только по карточке "
            "закупки, точность ниже"
        )

    # Модель фиксируется до первого вызова: выбор персональный, и пересчёт должен показать,
    # какая модель его сделала (25.09.2026).
    model = active_model(db)
    tender_block = _tender_block(db, tender)
    requirements_block = _requirements_block(requirements)
    profile_block = company_profile_service.profile_block(profile)
    matrix_hint = _matrix_hint(db, tender)

    # Замечания специалистов (28.09.2026) идут во все вызовы, а не только в заключение:
    # «в закупке есть требование о допуске СРО» меняет «Компетенции», а не только вывод.
    feedback_text = ai_conclusion_service.feedback_block(db, tender)

    base_context = (
        f"ЗАКУПКА:\n{tender_block}\n\n"
        f"ТРЕБОВАНИЯ:\n{requirements_block}\n\n"
        f"ПРОФИЛЬ КОМПАНИИ:\n{profile_block}"
        + (
            f"\n\n{feedback_text}\nЭто знание человека, читавшего документацию: учитывай "
            "его, если оно не опровергается фактами выше."
            if feedback_text
            else ""
        )
    )
    task_context = base_context + (f"\n\nДОПОЛНИТЕЛЬНО:\n{matrix_hint}" if matrix_hint else "")

    task_score: Decimal | None = None
    task_comment: str | None = None
    task_evidence: list[dict] = []
    task_checklist: list[dict] | None = None
    report_progress("AI-оценка: измерения «Задача» и «Компетенции»…")
    try:
        task_answer = run_structured(
            db,
            system_prompt=_TASK_PROMPT,
            user_text=task_context,
            response_model=TaskAnswer,
            temperature=0.0,
        )
        task_score, task_checklist, task_evidence = _score_task(task_answer, requirements, profile)
        task_comment = task_answer.comment.strip()
    except Exception as exc:  # noqa: BLE001 - сбой одного измерения не отменяет остальные
        logger.warning(f"Измерение Task для {tender.external_id} не посчитано: {exc}")
        outcome.messages.append(f"Измерение «Задача» не посчитано: {exc}")

    competencies_score: Decimal | None = None
    competencies_comment: str | None = None
    competencies_evidence: list[dict] = []
    competencies_checklist: list[dict] | None = None
    try:
        competencies_answer = run_structured(
            db,
            system_prompt=_COMPETENCIES_PROMPT,
            user_text=base_context,
            response_model=CompetenciesAnswer,
            temperature=0.0,
        )
        competencies_score, competencies_checklist, competencies_evidence = _score_competencies(
            competencies_answer, requirements, profile
        )
        competencies_comment = competencies_answer.comment.strip()
        if not competencies_checklist:
            competencies_comment = (
                "Формальных требований к участнику (допуски, лицензии, стаж, опыт) в закупке "
                "не найдено — измерение не применимо и исключено из итоговой оценки."
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Измерение Competencies для {tender.external_id} не посчитано: {exc}")
        outcome.messages.append(f"Измерение «Компетенции» не посчитано: {exc}")

    # Ни одно модельное измерение не посчитано (нет денег на счёте, модель недоступна) —
    # новую версию не сохраняем. Иначе итог сложился бы из одной «Истории», и сбой модели
    # вытеснил бы прежнюю нормальную оценку записью «80%, ИДТИ» (найдено 25.09.2026 на
    # RouterAI с нулевым балансом).
    if task_checklist is None and competencies_checklist is None:
        raise AiProfileError(
            "Оценка не посчитана, прежняя оставлена без изменений: " + "; ".join(outcome.messages)
        )

    task_score, task_comment = _apply_matrix_gate(db, tender, task_score, task_comment, task_checklist)
    history_score, history_comment, history_evidence = _history_dimension(db, tender)
    overall = _overall(history_score, task_score, competencies_score)

    # Заключение (28.09.2026): подходим ли мы и каким прибором, кто проходит, стратегия,
    # риски, метрики. Оно же выносит решение «смотреть / не смотреть» и задаёт вердикт.
    # Прежние вызовы «Решение» и «Резюме» остались запасным путём — если заключение не
    # составилось, карточка получит хотя бы их.
    similar_ids = _similar_ids(db, tender)
    participations, _ = _relevant_participations(db, tender, similar_ids)
    facts = ai_conclusion_service.collect_facts(db, tender)
    metrics = ai_conclusion_service.build_metrics(
        db,
        tender,
        facts,
        participations,
        history=history_score,
        task=task_score,
        competencies=competencies_score,
        competencies_applicable=bool(competencies_checklist),
        overall=overall,
    )
    dimensions_text = _decision_context(
        tender,
        history=(history_score, history_comment, history_evidence),
        task=(task_score, task_comment, task_evidence, task_checklist),
        competencies=(
            competencies_score,
            competencies_comment,
            competencies_evidence,
            competencies_checklist,
        ),
    )

    conclusion: dict | None = None
    decision: bool | None = None
    decision_summary: str | None = None
    summary: str | None = None
    weak_points: list[dict] = []
    strategy: dict | None = None
    report_progress("Заключение ИИ: подходим ли мы, каким прибором, кто проходит…")
    try:
        result = ai_conclusion_service.compute_conclusion(
            db,
            tender,
            base_context=base_context,
            dimensions_text=dimensions_text,
            facts=facts,
            market_text=ai_conclusion_service.market_block(db, tender, participations),
            feedback_text=feedback_text,
            metrics=metrics,
        )
        conclusion = result.conclusion
        decision = result.participate
        outcome.messages.extend(result.messages)
    except Exception as exc:  # noqa: BLE001 - запасной путь ниже
        logger.warning(f"Заключение по тендеру {tender.external_id} не составлено: {exc}")
        outcome.messages.append(f"Заключение не составлено: {exc}")

    if conclusion is not None and conclusion.get("fit") == "not_fit" and overall is not None:
        if overall > NOT_FIT_OVERALL_CAP:
            outcome.messages.append(
                f"итоговая оценка {float(overall):.0f}% ограничена {NOT_FIT_OVERALL_CAP}%: "
                "заключение — «не подходим»"
            )
            overall = Decimal(NOT_FIT_OVERALL_CAP)

    if conclusion is not None:
        verdict = _verdict_from_conclusion(conclusion, decision)
        summary = " ".join(
            part for part in (conclusion.get("headline"), conclusion.get("rationale")) if part
        ) or None
        # Прежние поля заполняются из заключения: их читают список, уведомления и Bitrix.
        weak_points = [
            {"severity": risk["severity"], "text": risk["text"]}
            for risk in conclusion.get("risks") or []
        ]
        plan = conclusion.get("strategy") or {}
        if plan:
            strategy = {
                "verdict": conclusion.get("headline") or "",
                "price": plan.get("price") or "",
                "first_step": (plan.get("steps") or [""])[0],
            }
    else:
        decision, decision_summary, summary, verdict, weak_points, strategy = _legacy_resume(
            db,
            tender,
            outcome,
            base_context=base_context,
            history=(history_score, history_comment, history_evidence),
            task=(task_score, task_comment, task_evidence, task_checklist),
            competencies=(
                competencies_score,
                competencies_comment,
                competencies_evidence,
                competencies_checklist,
            ),
            overall=overall,
        )

    record = _store(
        db,
        tender,
        history=(history_score, history_comment, history_evidence),
        task=(task_score, task_comment, task_evidence),
        competencies=(competencies_score, competencies_comment, competencies_evidence),
        checklists=(task_checklist, competencies_checklist),
        model=model,
        overall=overall,
        summary=summary,
        verdict=verdict,
        weak_points=weak_points,
        strategy=strategy,
        profile=profile,
        decision=(decision, decision_summary),
        conclusion=conclusion,
    )
    outcome.score = record
    outcome.messages.extend(drain_fallback_notes())

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
            f"слабых мест: {len(weak_points)}; модель: {model[1] or model[0]}"
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
    checklists: tuple[list | None, list | None] = (None, None),
    model: tuple[str | None, str | None] = (None, None),
    verdict: str,
    weak_points: list[dict],
    strategy: dict | None,
    profile: CompanyProfile,
    decision: tuple[bool | None, str | None] = (None, None),
    conclusion: dict | None = None,
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
        task_checklist=checklists[0],
        competencies_checklist=checklists[1],
        ai_provider=model[0],
        ai_model=model[1],
        overall_score=overall,
        summary=summary,
        verdict=verdict,
        weak_points=weak_points,
        recommended_strategy=strategy,
        decision=decision[0],
        decision_summary=decision[1],
        conclusion=conclusion,
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
    from app.services.ai_provider_service import PROVIDER_LABELS

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
        "task_checklist": score.task_checklist,
        "competencies_checklist": score.competencies_checklist,
        "ai_provider": score.ai_provider,
        "ai_provider_label": PROVIDER_LABELS.get(score.ai_provider or ""),
        "ai_model": score.ai_model,
        "overall_score": score.overall_score,
        "summary": score.summary,
        "verdict": score.verdict,
        "verdict_label": VERDICT_LABELS.get(score.verdict or ""),
        # Сводка решения (`decision_summary`) наружу не отдаётся — она служебная.
        "decision": score.decision,
        "conclusion": score.conclusion,
        "weak_points": weak_points,
        "recommended_strategy": score.recommended_strategy,
        "similar_tender_ids": similar_ids,
        "participation_ids": participation_ids,
        "company_profile_snapshot": score.company_profile_snapshot,
        "calculated_at": score.calculated_at,
    }
