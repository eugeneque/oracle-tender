"""Матрица соответствия и «процент победителя» (раздел 5.5 ТЗ — Этап 6).

Три шага сопоставления из ТЗ реализованы как три источника данных о продукции, которые
подаются модели **с явной пометкой происхождения** каждого факта:

1. `si_type` — «Описание типа» ФГИС: метрологические и электрические характеристики,
   структура условного обозначения (из неё видно допустимую комплектацию — интерфейсы,
   реле, датчики);
2. `product_catalog` — характеристики моделей из каталога (Приложение C);
3. `user_manual_fallback` — руководство пользователя с сайта производителя; подтягивается
   только для требований, оставшихся без ответа после первых двух шагов.

Четвёртый источник — вне трёх шагов ТЗ, по замечанию тестировщика 16.09.2026:
`upper_software` — списки поддерживаемого оборудования ПО верхнего уровня (Пирамида,
Энфорс, Энергосфера, яЭнергетик, АльфаЦЕНТР, Некта, ЛЭРС). Подключается только когда в
требованиях закупки есть интеграция с таким ПО, и отвечает на вопрос, на который каталог
производителя ответить не может: интегрирован прибор или нет. Отсутствие в проверенном
списке — прямой факт «не соответствует», а не «нет данных» (см.
`app/services/upper_software_service.py`).

Пятый — `admission_registry`, тоже по замечанию 16.09.2026: записи о допуске модели в
реестре промпродукции (ПП 719), реестре ЗАК ПАО «Россети» и реестре российского ПО.
Подключается, когда в требованиях упомянут реестр; состояние записи (действует / истекла /
проверено, что нет) считает код по датам, модель лишь сопоставляет его с формулировкой
требования (см. `app/services/registry_records_service.py`).

Критерии оценки — сама методика вердиктов, весов и процента — описаны в `CRITERIA.md`
в корне проекта; этот модуль обязан ей соответствовать.

**Почему один запрос на производителя, а не на каждую пару «требование × производитель».**
У тендера бывает полсотни требований, производителей — тринадцать (раздел 4.3 ТЗ); попарно
это 650 обращений к модели на один тендер. Здесь модель за один вызов получает все
требования разом вместе с карточкой производителя и возвращает вердикт по каждому — 13
вызовов вместо 650, при том же объёме исходных данных в контексте.

Что делает код, а не модель: отбор кандидатных моделей прибора, сборка фактов по трём
источникам, отсев вердиктов по требованиям, которых не было в запросе, и весь расчёт
процента — формула раздела 5.5 ТЗ считается арифметикой, а не «оценивается» моделью.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceSource,
    ComplianceStatus,
    Criticality,
    Requirement,
    RequirementKind,
    WinPercentage,
    WinVerdict,
)
from app.models.log import LogLevel
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, SiType
from app.models.tender import Tender
from app.models.user import User
from app.services.audit import log_action
from app.seed.meter_parameters import (
    GISP_PP719_URL,
    PARAMETERS_BY_NO,
    RULE_ASTRA,
    RULE_MANUAL_CHECK,
    RULE_ONLY_MANUFACTURERS,
    THREE_POSITION_RELAY_BRANDS,
    detect_parameter,
    parameter_hint,
)
from app.services.meter_kind import kind_group, kind_labels, kinds_from_text, product_kinds
from app.services.product_relevance import select_products_for_context
from app.services.ai_client import run_structured

# Вес требования по критичности (раздел 5.5 ТЗ, п.1 методики).
CRITICALITY_WEIGHTS: dict[str, int] = {
    Criticality.CRITICAL.value: 3,
    Criticality.IMPORTANT.value: 2,
    Criticality.MINOR.value: 1,
}

# Вклад статуса в оценку (раздел 5.5 ТЗ, п.2). `no_data` не входит: такие требования
# исключаются из знаменателя, а не считаются невыполненными — иначе процент занижался бы
# из-за неполноты каталога, а не из-за свойств прибора.
STATUS_SCORES: dict[str, float] = {
    ComplianceStatus.MEETS.value: 1.0,
    ComplianceStatus.PARTIAL.value: 0.5,
    ComplianceStatus.NOT_MEETS.value: 0.0,
}

# Ниже этого порога уверенности вердикт помечается «требует проверки человеком»
# (раздел 5.5 ТЗ, п.4).
LOW_CONFIDENCE_THRESHOLD = 0.6

# Доля оценённых требований, ниже которой процент сопровождается предупреждением
# (см. `_calculate_percentage`).
LOW_COVERAGE_RATIO = 0.3

# Сколько «Описания типа» класть в контекст на один тип СИ. Документ — 20-30 страниц, а
# типов у производителя десятки; целиком они не поместятся, и самое ценное (структура
# условного обозначения с расшифровкой исполнений) находится в начале документа.
MAX_DESCRIPTION_CHARS = 6_000
MAX_SI_TYPES_IN_CONTEXT = 4

# Сколько моделей каталога класть в карточку производителя. Не «все»: у Энергомеры их
# 221, и весь каталог не поместится ни в один запрос. Какие именно шесть — решает
# `product_relevance` по требованиям закупки; раньше брались первые по алфавиту, то есть
# подходящий прибор в сравнение мог не попасть вовсе.
MAX_PRODUCTS_IN_CONTEXT = 6

# Сколько требований отдавать модели за один запрос.
#
# Не «все сразу»: на живом тендере с 83 требованиями ответ обрывался по лимиту токенов
# посреди JSON («EOF while parsing an object»), и вердикты по целому производителю
# терялись — из 13 производителей двое остались без матрицы. Вердикт с пояснением занимает
# ~250 символов, поэтому пачка по 20 укладывается в ответ с запасом.
#
# И не «по одному»: контекст (описание типа, характеристики каталога) заново уходит в
# каждый запрос, так что мелкие пачки кратно увеличивают и время, и расход токенов.
REQUIREMENTS_PER_REQUEST = 20

_SYSTEM_PROMPT = """Ты сопоставляешь требования закупки с характеристиками приборов учёта \
одного производителя и выносишь вердикт по каждому требованию.

На вход ты получаешь пронумерованный список требований и карточку производителя. Факты в \
карточке помечены источником:
- [si_type] — официальное «Описание типа» из Госреестра средств измерений;
- [catalog] — характеристики модели из каталога продукции;
- [manual] — руководство по эксплуатации с сайта производителя;
- [software] — официальный список поддерживаемого оборудования на сайте разработчика ПО \
верхнего уровня (АСКУЭ/ИСУ: «Пирамида», «Энфорс», «Энергосфера», «яЭнергетик», \
«АльфаЦЕНТР», «Некта», «ЛЭРС УЧЁТ»);
- [astra] — выписка из официального каталога совместимого ПО «Ready for Astra» (astra.ru) \
по производителю;
- [registry] — запись о допуске модели в реестре: реестр промышленной продукции (ПП РФ \
№ 719, ГИСП Минпромторга), заключение аттестационной комиссии ПАО «Россети» (ЗАК), реестр \
российского ПО. Состояние записи (действует / истекает / истекла / проверено, что записи \
нет) уже вычислено по датам.

Правила:
1. Ответь по КАЖДОМУ требованию ровно один раз, указав его номер в поле `number`.
2. `status` — строго одно из:
   - meets — в карточке есть факт, прямо подтверждающий выполнение требования;
   - partial — требование выполняется частично или только для части исполнений;
   - not_meets — в карточке есть факт, прямо противоречащий требованию;
   - no_data — в карточке НЕТ данных, чтобы судить.
3. Не додумывай. Отсутствие упоминания — это no_data, а НЕ not_meets. Это важно: \
not_meets означает «прибор точно не подходит», и ставить его из-за неполноты данных нельзя.
4. `source` — откуда взят решающий факт: si_type, catalog, manual, software, registry или \
astra. Если данных не было, укажи пустую строку.
5. `explanation` — ОДНО короткое предложение по-русски (до 150 символов): какой факт из \
карточки привёл к вердикту; для no_data — чего именно не хватает. Не пересказывай карточку.
6. `confidence` — 0.0-1.0, насколько ты уверен. Точное совпадение числового параметра — \
близко к 1.0; вывод по смыслу — 0.5-0.7.
7. Требования об интеграции в ПО верхнего уровня оцениваются ТОЛЬКО по фактам [software]. \
Если названное в требовании ПО есть в карточке и приборы производителя в его списке есть — \
meets; если в карточке прямо сказано, что приборов производителя в списке этого ПО НЕТ — \
not_meets (список официальный и проверенный, это факт, а не отсутствие данных), \
confidence 0.7-0.85; поддержка только по протоколу без конкретной модели — partial. \
Если требуемое ПО не названо, а требуется интеграция «в ПО верхнего уровня» вообще — \
достаточно присутствия хотя бы в одном списке. Если фактов [software] по требуемому ПО \
в карточке нет — no_data.
8. Требования о наличии в реестре промышленной продукции (ПП 719, ГИСП, Минпромторг), о \
заключении аттестационной комиссии / аттестации ПАО «Россети» (ЗАК) и о реестре российского \
ПО оцениваются ТОЛЬКО по фактам [registry]. Запись «действует» или «истекает» — meets; \
«истекла» — not_meets (требуется действующая запись); «проверено: записи в реестре нет» — \
not_meets; для этих вердиктов confidence 0.9-1.0, source — registry. Если фактов [registry] \
по нужному реестру нет — no_data. Наличие в Госреестре СИ — не реестр допуска, оно \
оценивается по [si_type].
9. У требования может стоять подпись [параметр N: …; правило: …] — это параметр из \
перечня тендерного отдела «Параметры для приборов учёта» и указание, как его трактовать. \
Правило обязательно к исполнению.
10. Тип прибора. У моделей в карточке указан тип — один из одиннадцати видов (фазность × \
включение × крепление, либо высоковольтный прибор учёта). Требование к типу прибора, \
количеству фаз, номинальному напряжению, токам и способу крепления сверяй с моделью ТОГО \
ЖЕ типа, что требует закупка. Если у производителя в карточке есть только модели другого \
типа, это no_data (справочник может быть неполным), а не not_meets.
11. Габариты (параметр 3) сравнивай по сторонам: ширина — сторона b, длина — сторона a, \
высота — сторона c (глубина корпуса). Порядок чисел в ТЗ и у производителя может \
различаться — сопоставляй по смыслу сторон. «Не более» выполняется, если каждая сторона \
прибора не больше требуемой.
12. Графический ЖКИ (параметр 32): требование к индикаторному устройству (выносному \
дисплею) выполняют только сплит-исполнения и ВПУ; к встроенному индикатору — остальные.
13. Поддержка Astra Linux (параметр 39) оценивается ТОЛЬКО по фактам [astra] — выписке из \
официального каталога совместимого ПО «Ready for Astra». Если в выписке есть конфигуратор \
или иная программа производителя для работы с приборами учёта (настройка, считывание) — \
meets, source astra. Если найдено только постороннее ПО (другой фирмы с похожим названием, \
серверная СУБД) или в выписке прямо сказано, что ПО производителя в каталоге нет, — \
not_meets, confidence 0.6-0.7 (производитель мог заявить совместимость без сертификата). \
Если фактов [astra] нет — no_data.
"""


class RequirementVerdict(pydantic.BaseModel):
    """Поля обязательные, без значений по умолчанию.

    С дефолтами модель их не заполняла: на живом прогоне все 83 вердикта вернулись с пустым
    `explanation` и пустым `source`, из-за чего в матрице не было ни пояснений (а раздел 5.6
    ТЗ требует показывать их под каждой ячейкой), ни различения трёх шагов сопоставления —
    любой вердикт выглядел как `ai_semantic`. В JSON Schema поле со значением по умолчанию
    необязательно, и модель просто его пропускает."""

    number: int
    status: str
    source: str
    explanation: str
    confidence: float


class ComplianceResult(pydantic.BaseModel):
    verdicts: list[RequirementVerdict]


@dataclass
class ComplianceOutcome:
    """Итог расчёта по тендеру для показа пользователю."""

    manufacturers_processed: int = 0
    entries_saved: int = 0
    requirements_total: int = 0
    manual_fallback_used: int = 0
    needs_review: int = 0
    messages: list[str] = field(default_factory=list)


def _no_data_verdict(number: int, explanation: str = "", confidence: float = 1.0) -> RequirementVerdict:
    """Вердикт «нет данных», сформированный кодом (а не моделью) — например, когда карточка
    производителя пуста и спрашивать не о чем."""

    return RequirementVerdict(
        number=number,
        status=ComplianceStatus.NO_DATA.value,
        source="",
        explanation=explanation,
        confidence=confidence,
    )


_SOURCE_BY_TAG = {
    "si_type": ComplianceSource.SI_TYPE.value,
    "catalog": ComplianceSource.PRODUCT_CATALOG.value,
    "manual": ComplianceSource.USER_MANUAL_FALLBACK.value,
    "software": ComplianceSource.UPPER_SOFTWARE.value,
    "registry": ComplianceSource.ADMISSION_REGISTRY.value,
    "astra": ComplianceSource.ASTRA_CATALOG.value,
    "rule": ComplianceSource.EXPERT_RULE.value,
}
_STATUS_VALUES = {s.value for s in ComplianceStatus}


def build_manufacturer_context(
    db: Session,
    manufacturer: Manufacturer,
    *,
    include_manual: bool,
    requirements: list[Requirement] | None = None,
) -> tuple[str, uuid.UUID | None]:
    """Карточка производителя для модели: факты из трёх источников с пометками.

    Возвращает также идентификатор модели прибора, к которой относится основная часть
    характеристик, — он попадёт в матрицу как `product_id`. Строится по данным, а не по
    догадкам: если каталог пуст, карточка окажется пустой, и вызывающий код честно поставит
    `no_data`, не обращаясь к модели.

    `requirements` задают, какие модели показать: в карточку помещается лишь
    `MAX_PRODUCTS_IN_CONTEXT` штук, и выбирать их надо по требованиям закупки, а не по
    алфавиту (`app/services/product_relevance.py`). Без требований — прежний порядок по
    названию.
    """

    blocks: list[str] = []

    si_types = list(
        db.scalars(
            select(SiType)
            .where(SiType.manufacturer_id == manufacturer.id)
            .order_by(SiType.verified_by_user.desc(), SiType.created_at)
            .limit(MAX_SI_TYPES_IN_CONTEXT)
        )
    )
    for si_type in si_types:
        facts = [f"номер в Госреестре СИ {si_type.si_code}"]
        if si_type.notation:
            facts.append(f"обозначение типа {si_type.notation}")
        if si_type.type_name:
            facts.append(si_type.type_name)
        if si_type.mpi_months:
            facts.append(f"межповерочный интервал {si_type.mpi_months} мес.")
        blocks.append(f"[si_type] {'; '.join(facts)}")
        if si_type.allowed_modifications:
            blocks.append(
                f"[si_type] Структура условного обозначения (допустимые исполнения):\n"
                f"{si_type.allowed_modifications[:MAX_DESCRIPTION_CHARS]}"
            )
        if si_type.description_type_text:
            blocks.append(
                f"[si_type] Выдержка из «Описания типа» {si_type.si_code}:\n"
                f"{si_type.description_type_text[:MAX_DESCRIPTION_CHARS]}"
            )

    selected = select_products_for_context(
        db, manufacturer, requirements or [], limit=MAX_PRODUCTS_IN_CONTEXT
    )
    primary_product_id = selected[0].product.id if selected else None

    for product, characteristics in ((item.product, item.characteristics) for item in selected):
        if not characteristics:
            continue
        lines = []
        kinds = product_kinds(product, characteristics)
        if kinds:
            lines.append(f"[catalog] {product.model_name}: тип прибора — {'; '.join(kind_labels(kinds))}")
        for characteristic in characteristics:
            if not characteristic.value:
                continue
            # Руководство — третий шаг сопоставления, и подключается только когда первые два
            # не дали ответа (раздел 5.5 ТЗ, п.3).
            is_manual = characteristic.source == "user_manual"
            if is_manual and not include_manual:
                continue
            tag = "manual" if is_manual else "catalog"
            lines.append(
                f"[{tag}] {product.model_name}: {characteristic.group_name} — "
                f"{characteristic.field_name}: {characteristic.value}"
            )
        blocks.extend(lines)

    # Списки ПО верхнего уровня — только когда в требованиях есть интеграция с таким ПО.
    # Иначе карточка раздувается фактами, по которым нет вопроса, а для производителя без
    # каталога появился бы контекст из одних «в списке нет» и модель звалась бы впустую.
    if requirements:
        from app.services.upper_software_service import software_facts

        blocks.extend(software_facts(db, manufacturer, requirements))

    # Записи о допуске — по той же логике: только когда закупка спрашивает о реестре.
    if requirements:
        from app.services.registry_records_service import registry_facts

        blocks.extend(registry_facts(db, manufacturer, requirements))

    # Каталог «Ready for Astra» — только когда закупка спрашивает об Astra Linux.
    if requirements and any(_parameter_of(item) == 39 for item in requirements):
        blocks.extend(astra_facts(manufacturer))

    return _cap_context(blocks), primary_product_id


# Предел длины карточки производителя. Окно YandexGPT — 32 768 токенов на запрос; проход с
# руководствами у Пульсара давал 87 тыс. символов и отказ «number of input tokens must be no
# more than 32768» на всех трёх попытках (25.09.2026). Хвост отрезается по целым строкам:
# в начале карточки — описание типа и каталог, главные источники.
MAX_CONTEXT_CHARS = 70_000


def _cap_context(blocks: list[str]) -> str:
    text = "\n".join(blocks)
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_CONTEXT_CHARS)
    return text[: cut if cut > 0 else MAX_CONTEXT_CHARS]


def _parameter_of(requirement: Requirement) -> int | None:
    """Параметр файла «Параметры для ПУ»: проставленный при извлечении, а для старых
    требований — узнанный по формулировке (только параметры с правилами кода)."""

    return requirement.parameter_no or detect_parameter(
        f"{requirement.text or ''} {requirement.normalized_text or ''}"
    )


def astra_facts(manufacturer: Manufacturer) -> list[str]:
    """Строки `[astra]`: что производитель имеет в каталоге «Ready for Astra». Каталог
    недоступен — строк нет, и требование честно останется «нет данных»."""

    from app.adapters.astra_compatible import PAGE_URL, load_catalog

    catalog = load_catalog()
    if catalog is None:
        return []
    brand = manufacturer.brand_name or manufacturer.legal_name
    found = catalog.find(brand)
    if not found:
        return [
            f"[astra] В каталоге совместимого ПО «Ready for Astra» ({PAGE_URL}, "
            f"{len(catalog.items)} позиций) ПО производителя «{brand}» НЕ найдено"
        ]
    return [
        f"[astra] Каталог «Ready for Astra» ({PAGE_URL}): {item.describe()}" for item in found
    ]



def _requirements_block(requirements: list[Requirement]) -> str:
    lines = []
    for index, requirement in enumerate(requirements, start=1):
        text = requirement.normalized_text or requirement.text
        hint = parameter_hint(_parameter_of(requirement))
        lines.append(f"{index}. {text}" + (f" {hint}" if hint else ""))
    return "\n".join(lines)


def evaluate_tender(
    db: Session,
    tender: Tender,
    *,
    actor: User | None,
    use_manual_fallback: bool = True,
) -> ComplianceOutcome:
    """Строит матрицу соответствия и считает проценты победителя по всем производителям."""

    outcome = ComplianceOutcome()
    all_requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.tender_id == tender.id)
            .order_by(Requirement.created_at)
        )
    )
    # Матрица сравнивает модели приборов с требованиями, поэтому в неё идут только
    # требования к товару (18.09.2026). Требования к работам и к участнику («персонал с
    # IV группой допуска») сравнивать с каталогом бессмысленно — они дали бы «нет данных»
    # по каждому производителю и обнулили бы процент.
    requirements = [
        item for item in all_requirements if item.kind == RequirementKind.PRODUCT.value
    ]
    outcome.requirements_total = len(requirements)
    if not all_requirements:
        outcome.messages.append(
            "У тендера нет извлечённых требований — сначала выполните анализ документации"
        )
        _log(db, tender, outcome, actor, level=LogLevel.WARNING)
        return outcome
    if not requirements:
        outcome.messages.append(
            f"Матрица не строится: все {len(all_requirements)} требований — к работам, "
            "услугам или участнику, товара в закупке нет; соответствие по ним оценивает "
            "AI-оценка по профилю"
        )
        _log(db, tender, outcome, actor)
        return outcome

    manufacturers = list(
        db.scalars(select(Manufacturer).order_by(Manufacturer.is_mirtek.desc(), Manufacturer.legal_name))
    )

    for manufacturer in manufacturers:
        verdicts = _evaluate_manufacturer(
            db, tender, manufacturer, requirements, outcome, use_manual_fallback=use_manual_fallback
        )
        _save_entries(db, tender, manufacturer, requirements, verdicts, outcome)
        _calculate_percentage(db, tender, manufacturer, requirements, verdicts)
        outcome.manufacturers_processed += 1

    db.commit()
    _request_missing_catalog_data(db, manufacturers, actor=actor)
    _log(db, tender, outcome, actor)
    return outcome


def _request_missing_catalog_data(
    db: Session, manufacturers: list[Manufacturer], *, actor: User | None
) -> None:
    """Триггер 2 из п.1.1 задания: производители, по которым сопоставлять было нечем,
    ставятся в очередь на поиск в ФГИС вне общего расписания.

    Ставится ПОСЛЕ расчёта, а не вместо него, и намеренно ничего не меняет в вердиктах:
    сопоставление обязано честно вернуть `no_data` по тем данным, что есть сейчас, — иначе
    пользователь ждал бы обращения к внешнему реестру внутри своего запроса. Смысл в том,
    чтобы к следующему тендеру по этому производителю данные в справочнике уже были, а не
    в том, чтобы «дозаполнить на лету».

    Отсутствие данных определяется по справочнику (нет ни одного типа СИ), а не по вердиктам
    модели: `no_data` по конкретному требованию — это чаще всего «в описании типа нет такого
    параметра», и ходить из-за него в реестр бессмысленно.
    """

    from app.services import fgis_catalog_sync

    for manufacturer in manufacturers:
        has_si_types = db.scalar(
            select(SiType.id).where(SiType.manufacturer_id == manufacturer.id).limit(1)
        )
        if has_si_types is not None:
            continue
        try:
            fgis_catalog_sync.enqueue_missing_model(
                db,
                model_name=manufacturer.brand_name or manufacturer.legal_name,
                manufacturer=manufacturer,
                actor_id=actor.id if actor else None,
            )
        except Exception as exc:  # noqa: BLE001 - постановка в очередь не должна ронять расчёт
            db.rollback()
            logger.warning(
                f"Не удалось поставить «{manufacturer.legal_name}» в очередь поиска в ФГИС: {exc}"
            )


def _evaluate_manufacturer(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    outcome: ComplianceOutcome,
    *,
    use_manual_fallback: bool,
) -> dict[int, RequirementVerdict]:
    """Вердикты по всем требованиям для одного производителя.

    Сначала — два первых источника (описание типа и каталог). Если после этого часть
    требований осталась без данных, а у производителя есть материалы из руководства, делается
    второй проход **только по этим требованиям** — именно так третий шаг описан в разделе
    5.5 ТЗ (fallback, а не обязательный источник)."""

    context, product_id = build_manufacturer_context(
        db, manufacturer, include_manual=False, requirements=requirements
    )
    if not context.strip():
        # Данных о производителе нет вовсе — честный no_data без обращения к модели.
        return {
            index: _no_data_verdict(index, "В каталоге нет данных по этому производителю")
            for index in range(1, len(requirements) + 1)
        }

    verdicts = _ask_in_batches(db, manufacturer, requirements, context, outcome)

    unresolved = [
        index
        for index in range(1, len(requirements) + 1)
        if verdicts.get(index, _no_data_verdict(index)).status == ComplianceStatus.NO_DATA.value
    ]
    if use_manual_fallback and unresolved:
        manual_context, _ = build_manufacturer_context(
            db, manufacturer, include_manual=True, requirements=requirements
        )
        if manual_context.strip() and manual_context != context:
            subset = [requirements[index - 1] for index in unresolved]
            fallback = _ask_in_batches(db, manufacturer, subset, manual_context, outcome)
            for position, index in enumerate(unresolved, start=1):
                verdict = fallback.get(position)
                if verdict is None or verdict.status == ComplianceStatus.NO_DATA.value:
                    continue
                verdict.number = index
                # Даже если модель не отметила источник, вердикт получен на проходе с
                # руководством — фиксируем это, чтобы в карточке было видно происхождение.
                verdict.source = verdict.source or "manual"
                verdicts[index] = verdict
                outcome.manual_fallback_used += 1

    for index in range(1, len(requirements) + 1):
        verdicts.setdefault(
            index, _no_data_verdict(index, "Модель не вернула вердикт по этому требованию", 0.0)
        )
    # Типы каталога считаются, только когда есть «не соответствует» по типу/фазности:
    # это запрос по всем моделям производителя, а нужен он редко.
    needs_kinds = any(
        _parameter_of(requirement) in TYPE_PARAMETERS
        and verdicts[index].status == ComplianceStatus.NOT_MEETS.value
        for index, requirement in enumerate(requirements, start=1)
    )
    apply_expert_rules(
        manufacturer,
        requirements,
        verdicts,
        made_kinds=manufacturer_kind_groups(db, manufacturer) if needs_kinds else None,
    )
    return verdicts


def _apply_astra_rule(
    manufacturer: Manufacturer, index: int, verdicts: dict[int, RequirementVerdict]
) -> None:
    """Astra Linux (параметр 39): если ПО производителя в каталоге «Ready for Astra» нет,
    вердикт — «не соответствует», что бы ни ответила модель. На живом прогоне 25.09.2026
    модель поставила «соответствует» Ленэлектро, которого в каталоге нет. Если ПО найдено,
    решает модель: только она отличит конфигуратор от чужой программы с похожим названием."""

    from app.adapters.astra_compatible import load_catalog

    catalog = load_catalog()
    if catalog is None:
        return
    if catalog.find(manufacturer.brand_name or manufacturer.legal_name):
        return
    verdicts[index] = RequirementVerdict(
        number=index,
        status=ComplianceStatus.NOT_MEETS.value,
        source="astra",
        explanation=(
            "ПО производителя нет в каталоге совместимого ПО «Ready for Astra»; совместимость "
            "могла быть заявлена без сертификата — уточнить у производителя"
        ),
        confidence=0.65,
    )


# Параметры «тип прибора» и «количество фаз» — по ним модель чаще всего сравнивает позицию
# спецификации с моделью другого типа, попавшей в карточку.
TYPE_PARAMETERS = frozenset({1, 2})


def manufacturer_kind_groups(db: Session, manufacturer: Manufacturer) -> set[str]:
    """Группы типов (фазность × включение, либо ВПУ), в которых у производителя есть хотя
    бы одна модель в каталоге."""

    products = list(db.scalars(select(Product).where(Product.manufacturer_id == manufacturer.id)))
    if not products:
        return set()
    by_product: dict[uuid.UUID, list[ProductCharacteristic]] = {}
    for item in db.scalars(
        select(ProductCharacteristic).where(
            ProductCharacteristic.product_id.in_([product.id for product in products])
        )
    ):
        by_product.setdefault(item.product_id, []).append(item)
    return {
        kind_group(code)
        for product in products
        for code in product_kinds(product, by_product.get(product.id, []))
    }


def _brand_matches(manufacturer: Manufacturer, brands: tuple[str, ...]) -> bool:
    names = f"{manufacturer.brand_name or ''} {manufacturer.legal_name or ''}".lower()
    return any(brand in names for brand in brands)


def apply_expert_rules(
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    verdicts: dict[int, RequirementVerdict],
    *,
    made_kinds: set[str] | None = None,
) -> None:
    """Правила из комментариев файла «Параметры для ПУ», которые выполняет код поверх
    вердикта модели. Модель знает правило из подписи требования, но решение, от которого
    зависит «проходит / не проходит», не должно зависеть от того, прочитала ли она его.

    Трёхпозиционный переключатель реле (параметр 26): «соответствуют только три
    производителя (Энергомера, Тайпит, Пульсар)». У остальных — «не соответствует».
    У этих троих вердикт модели по фактам сохраняется; если фактов не нашлось —
    «соответствует» по правилу тендерного отдела."""

    for index, requirement in enumerate(requirements, start=1):
        parameter = _parameter_of(requirement)
        current = verdicts.get(index)
        # Тип прибора / фазность: «не соответствует», когда у производителя в каталоге ЕСТЬ
        # прибор требуемого типа, — это сравнение с моделью другого типа, попавшей в карточку,
        # а не свойство производителя (CRITERIA.md, п. 3.6). Остаётся «нет данных».
        if (
            parameter in TYPE_PARAMETERS
            and made_kinds
            and current is not None
            and current.status == ComplianceStatus.NOT_MEETS.value
        ):
            wanted = {
                kind_group(code)
                for code in kinds_from_text(f"{requirement.text or ''} {requirement.normalized_text or ''}")
            }
            if wanted & made_kinds:
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.NO_DATA.value,
                    source="rule",
                    explanation=(
                        "У производителя есть приборы этого типа "
                        f"({'; '.join(kind_labels(sorted(wanted & made_kinds)))}), но в "
                        "сравнение попала модель другого типа — проверить исполнение"
                    )[:300],
                    confidence=0.5,
                )
                continue
        item = PARAMETERS_BY_NO.get(parameter or 0)
        if item is not None and item.rule == RULE_ASTRA:
            _apply_astra_rule(manufacturer, index, verdicts)
            continue
        if item is None or item.rule != RULE_ONLY_MANUFACTURERS:
            continue
        if not _brand_matches(manufacturer, THREE_POSITION_RELAY_BRANDS):
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.NOT_MEETS.value,
                source="rule",
                explanation=(
                    "Трёхпозиционный переключатель реле есть только у Энергомеры, Тайпит "
                    "и Пульсара (правило тендерного отдела)"
                ),
                confidence=0.9,
            )
        elif current is None or current.status == ComplianceStatus.NO_DATA.value:
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.MEETS.value,
                source="rule",
                explanation=(
                    "Производитель входит в тройку с трёхпозиционным переключателем реле "
                    "(правило тендерного отдела); исполнение модели проверить"
                ),
                confidence=0.75,
            )


def _ask_in_batches(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    context: str,
    outcome: ComplianceOutcome,
) -> dict[int, RequirementVerdict]:
    """Спрашивает модель пачками по `REQUIREMENTS_PER_REQUEST` и склеивает вердикты,
    пересчитывая локальные номера пачки в сквозные.

    Неудача одной пачки не отменяет остальные: требования из неё останутся без вердикта
    (и получат `no_data` выше по стеку), но соседние пачки сохранятся — это лучше, чем
    терять всего производителя из-за одного оборванного ответа."""

    verdicts: dict[int, RequirementVerdict] = {}
    for offset in range(0, len(requirements), REQUIREMENTS_PER_REQUEST):
        batch = requirements[offset : offset + REQUIREMENTS_PER_REQUEST]
        batch_verdicts = _ask_model(db, manufacturer, batch, context, outcome)
        for local_number, verdict in batch_verdicts.items():
            global_number = offset + local_number
            verdict.number = global_number
            verdicts[global_number] = verdict
    return verdicts


def _ask_model(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    context: str,
    outcome: ComplianceOutcome,
) -> dict[int, RequirementVerdict]:
    user_text = (
        f"ТРЕБОВАНИЯ ЗАКУПКИ:\n{_requirements_block(requirements)}\n\n"
        f"КАРТОЧКА ПРОИЗВОДИТЕЛЯ «{manufacturer.brand_name or manufacturer.legal_name}»:\n{context}"
    )
    try:
        result = run_structured(
            db,
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            response_model=ComplianceResult,
        )
    except Exception as exc:  # noqa: BLE001 - сбой по одному производителю не рушит весь расчёт
        logger.warning(f"Сопоставление для «{manufacturer.legal_name}» не удалось: {exc}")
        outcome.messages.append(f"{manufacturer.legal_name}: сопоставление не выполнено ({exc})")
        return {}

    verdicts: dict[int, RequirementVerdict] = {}
    for verdict in result.verdicts:
        # Модель иногда возвращает номера, которых не было в запросе, — такие вердикты
        # относятся неизвестно к чему и молча портили бы матрицу.
        if not 1 <= verdict.number <= len(requirements):
            continue
        if verdict.status not in _STATUS_VALUES:
            verdict.status = ComplianceStatus.NO_DATA.value
        verdicts[verdict.number] = verdict
    return verdicts


def _save_entries(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    verdicts: dict[int, RequirementVerdict],
    outcome: ComplianceOutcome,
) -> None:
    existing = {
        entry.requirement_id: entry
        for entry in db.scalars(
            select(ComplianceMatrixEntry).where(
                ComplianceMatrixEntry.tender_id == tender.id,
                ComplianceMatrixEntry.manufacturer_id == manufacturer.id,
            )
        )
    }

    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts[index]
        confidence = max(0.0, min(1.0, float(verdict.confidence)))
        needs_review = (
            confidence < LOW_CONFIDENCE_THRESHOLD
            and verdict.status != ComplianceStatus.NO_DATA.value
        )
        explanation = verdict.explanation or ""
        # ПП 719 (параметр 35): «подтягивать с комментарием, чтобы пользователь проверил —
        # много нюансов» (баллы локализации, год соответствия, исполнения). Любой вердикт
        # по нему уходит на проверку человеком, со ссылкой на реестр.
        item = PARAMETERS_BY_NO.get(_parameter_of(requirement) or 0)
        if item is not None and item.rule == RULE_MANUAL_CHECK:
            needs_review = True
            explanation = (
                f"{explanation} Проверьте запись вручную в реестре ГИСП ({GISP_PP719_URL}): "
                "баллы и соответствие требованиям текущего года."
            ).strip()
        if needs_review:
            outcome.needs_review += 1

        entry = existing.get(requirement.id)
        if entry is None:
            entry = ComplianceMatrixEntry(
                tender_id=tender.id,
                requirement_id=requirement.id,
                manufacturer_id=manufacturer.id,
            )
            db.add(entry)

        entry.product_id = None
        entry.status = verdict.status
        entry.explanation = explanation or None
        entry.source = _SOURCE_BY_TAG.get(verdict.source, ComplianceSource.AI_SEMANTIC.value)
        entry.confidence = Decimal(f"{confidence:.3f}")
        entry.needs_human_review = needs_review
        outcome.entries_saved += 1


def calculate_percentage(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> tuple[Decimal, int]:
    """Формула раздела 5.5 ТЗ: `Σ(вес × статус) / Σ(вес по требованиям с определённым
    статусом) × 100`.

    Требования со статусом `no_data` исключаются из знаменателя — отсюда второе возвращаемое
    значение — сколько требований реально участвовало в оценке. Без него 100% по одному
    определённому требованию из пятидесяти выглядит как полное соответствие.
    """

    numerator = 0.0
    denominator = 0
    scored = 0
    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts.get(index)
        if verdict is None:
            continue
        score = STATUS_SCORES.get(verdict.status)
        if score is None:
            continue  # no_data — вне знаменателя
        weight = CRITICALITY_WEIGHTS.get(requirement.criticality, 1)
        numerator += weight * score
        denominator += weight
        scored += 1

    if denominator == 0:
        return Decimal("0.00"), 0
    return Decimal(f"{numerator / denominator * 100:.2f}"), scored


def _calculate_percentage(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    verdicts: dict[int, RequirementVerdict],
) -> None:
    percentage, scored = calculate_percentage(requirements, verdicts)

    failed_critical = [
        requirements[index - 1]
        for index in range(1, len(requirements) + 1)
        if requirements[index - 1].criticality == Criticality.CRITICAL.value
        and verdicts.get(index) is not None
        and verdicts[index].status == ComplianceStatus.NOT_MEETS.value
    ]
    total = len(requirements)
    if scored == 0:
        reason = "Оценка невозможна: по всем требованиям нет данных в каталоге"
    elif failed_critical:
        reason = "Не выполнены критичные требования: " + "; ".join(
            (requirement.normalized_text or requirement.text)[:120] for requirement in failed_critical[:3]
        )
    else:
        reason = f"Оценка по {scored} из {total} требований с определённым статусом"

    # Процент считается только по требованиям с известным вердиктом (раздел 5.5 ТЗ), поэтому
    # производитель с пустым каталогом может показать 100% по трём случайно совпавшим
    # требованиям и обойти того, у кого честно оценены сорок. Само число менять нельзя — это
    # методика ТЗ, — но пользователь должен видеть, что за ним стоит.
    if 0 < scored < total * LOW_COVERAGE_RATIO:
        reason = (
            f"Оценка ненадёжна: данных хватило лишь на {scored} из {total} требований. " + reason
        )

    verdict, verdict_reason = decide_verdict(requirements, verdicts)
    # «Не проходит» уже названо выше перечнем критичных требований; оговорки и нехватку
    # данных добавляем, иначе итог в списке производителей было бы нечем объяснить.
    if verdict in (WinVerdict.CAVEATS.value, WinVerdict.UNKNOWN.value):
        reason = f"{reason}. {verdict_reason}"

    # Пересчёт не правит прежнюю строку, а гасит её и добавляет новую (решение 03.09.2026,
    # раздел 7 ТЗ): каталог пополняется, процент от этого меняется, и без истории пересчётов
    # объяснить скачок с 46% до 88% нечем. Погашение идёт до вставки — «текущая» запись по
    # паре (тендер, производитель) может быть только одна, это частичный уникальный индекс.
    previous = db.scalar(
        select(WinPercentage).where(
            WinPercentage.tender_id == tender.id,
            WinPercentage.manufacturer_id == manufacturer.id,
            WinPercentage.is_current.is_(True),
        )
    )
    if previous is not None:
        previous.is_current = False
        db.flush()

    db.add(
        WinPercentage(
            tender_id=tender.id,
            manufacturer_id=manufacturer.id,
            percentage=percentage,
            reason_summary=reason,
            requirements_total=len(requirements),
            requirements_scored=scored,
            verdict=verdict,
            is_current=True,
        )
    )
    db.flush()


# Какая доля критичных требований без данных делает итог неопределённым.
UNKNOWN_CRITICAL_RATIO = 0.5


def decide_verdict(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> tuple[str, str]:
    """Проходит ли прибор производителя (раздел 5а CRITERIA.md) и почему — одной фразой.

    * не проходит — хотя бы одно критичное требование не выполнено: заявку отклонят,
      сколько бы остального ни совпало;
    * не хватает данных — ничего не оценено, либо без данных больше половины критичных;
    * с оговорками — критичные не нарушены, но есть невыполненные важные/второстепенные,
      частичное выполнение критичного или критичное без данных;
    * проходит — всё остальное."""

    def text_of(requirement: Requirement) -> str:
        return (requirement.normalized_text or requirement.text or "")[:90]

    failed_critical: list[str] = []
    failed_other: list[str] = []
    unclear_critical: list[str] = []
    critical_total = 0
    scored = 0
    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts.get(index)
        status = verdict.status if verdict else ComplianceStatus.NO_DATA.value
        critical = requirement.criticality == Criticality.CRITICAL.value
        critical_total += critical
        if status != ComplianceStatus.NO_DATA.value:
            scored += 1
        if status == ComplianceStatus.NOT_MEETS.value:
            (failed_critical if critical else failed_other).append(text_of(requirement))
        elif critical and status in (ComplianceStatus.NO_DATA.value, ComplianceStatus.PARTIAL.value):
            unclear_critical.append(text_of(requirement))

    if failed_critical:
        return WinVerdict.FAILS.value, "Не проходит: " + "; ".join(failed_critical[:3])
    unknown_critical = sum(
        1
        for index, requirement in enumerate(requirements, start=1)
        if requirement.criticality == Criticality.CRITICAL.value
        and (verdicts.get(index) is None or verdicts[index].status == ComplianceStatus.NO_DATA.value)
    )
    if scored == 0 or (critical_total and unknown_critical > critical_total * UNKNOWN_CRITICAL_RATIO):
        return WinVerdict.UNKNOWN.value, (
            f"Не хватает данных: без ответа {unknown_critical} из {critical_total} критичных требований"
            if critical_total
            else "Не хватает данных"
        )
    if failed_other or unclear_critical:
        parts = []
        if failed_other:
            parts.append("не выполнено: " + "; ".join(failed_other[:2]))
        if unclear_critical:
            parts.append("проверить: " + "; ".join(unclear_critical[:2]))
        return WinVerdict.CAVEATS.value, "С оговорками — " + "; ".join(parts)
    return WinVerdict.PASSES.value, "Проходит"


def _log(
    db: Session,
    tender: Tender,
    outcome: ComplianceOutcome,
    actor: User | None,
    *,
    level: LogLevel = LogLevel.INFO,
) -> None:
    details = (
        f"Производителей обработано: {outcome.manufacturers_processed}; ячеек матрицы: "
        f"{outcome.entries_saved}; требований: {outcome.requirements_total}; "
        f"через руководство: {outcome.manual_fallback_used}; "
        f"требуют проверки человеком: {outcome.needs_review}"
    )
    if outcome.messages:
        details += "; " + "; ".join(outcome.messages)

    log_action(
        db,
        component="compliance",
        action=f"evaluate_tender:{tender.external_id}",
        result="success" if outcome.entries_saved else "empty",
        level=level,
        details=details,
        user_id=actor.id if actor else None,
    )
    db.commit()
