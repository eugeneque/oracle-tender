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

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.jobs import report_progress
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
from app.models.manufacturer import Manufacturer, Product, ProductCharacteristic, ProductStatus, SiType
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
from app.services.lot_position import lot_groups, position_key
from app.services.meter_kind import kind_group, kind_labels, kinds_from_text, product_kinds
from app.services.compliance_numeric import (
    CatalogModel,
    ENABLED as NUMERIC_ENABLED,
    lost_numbers,
    prejudge,
    requirement_checks,
    wording_for_code,
)
from app.services.product_relevance import select_products_for_context, tender_named_text
from app.services.tz_reference import (
    TzReference,
    find_reference,
    is_model_requirement,
    is_registry_requirement,
    is_vendor_exclusive,
    match_manufacturer,
    names_reference_model,
)
from app.services.ai_client import input_budget_chars, run_structured
from app.services.ai_provider_service import AiNotConfiguredError, AiQuotaExceededError

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

# Уверенность вердикта, вынесенного кодом по значению из каталога: выше порога проверки
# человеком, но ниже 1 — значение в каталоге само извлечено автоматически.
PREJUDGED_CONFIDENCE = 0.9

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
нет) уже вычислено по датам;
- [tz] — по какой модели этого производителя составлено ТЗ закупки (найдено кодом по \
тексту документации).

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
astra. Если данных не было, укажи пустую строку. Факт [tz] сам по себе требование не \
подтверждает — он говорит, с какой моделью сравнивать.
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
14. Обозначение исполнения. Модели в карточке часто названы полным обозначением \
исполнения («CE207 R7.849.2.OG.QUVLF GS01 SPDS», «МИР С-05.10-230-5(80)-PZ»), а в [si_type] \
есть «Структура условного обозначения» с таблицами кодов. Расшифруй обозначение модели по \
этим таблицам и используй расшифровку как факт: у Энергомеры «R7» — корпус на рейку, \
«8» — класс 1/2, «4» — 230 В, «9» — 5(80) А, «2» — два датчика тока, «OG» — оптопорт и \
GSM, «QUVLF» — реле управления нагрузкой, показатели качества, электронные пломбы, \
подсветка, датчик магнитного поля. Вердикт по расшифровке — meets, source si_type, \
confidence 0.8-0.9; в explanation назови код («G — GSM в обозначении»). Если код в \
таблицах не найден — не угадывай.
15. Наличие в Госреестре СИ, утверждение типа: если в карточке есть номер в Госреестре \
(«номер в Госреестре СИ 72632-18», «Номер в Госреестре: …») для модели требуемого типа \
— meets, source si_type. Соответствие ГОСТ 31818.11, 31819.21/23 подтверждается тем же \
описанием типа, если стандарт в нём назван.
16. Программа обслуживания (конфигуратор) производителя, названная в [si_type] или \
[manual] («обслуживание с помощью ПО «Admin Tools»»), подтверждает требование о работе с \
этой программой — meets.
17. Если в запросе указана ПОЗИЦИЯ СПЕЦИФИКАЦИИ, все требования относятся к этому изделию \
закупки, а карточка подобрана под него. Другие позиции той же закупки к этому запросу не \
относятся.
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
    # Пропущены при продолжении: итог уже посчитан по актуальным требованиям.
    manufacturers_skipped: int = 0
    entries_saved: int = 0
    requirements_total: int = 0
    manual_fallback_used: int = 0
    # Требования, решённые кодом без модели (`prejudge_numeric`).
    prejudged: int = 0
    needs_review: int = 0
    # Ячейки, по которым модель не вернула вердикт (сбой, отказ фильтра, запасная модель
    # недоступна), и у скольких производителей они есть. Разбор при этом «успешен», а процент
    # считается по меньшему числу требований — без счётчика это видно только в «Ошибках ИИ»
    # (15.10.2026: пересчёт 18 закупок прошёл «успешно» при 112 пакетах без ответа).
    unanswered: int = 0
    manufacturers_unanswered: int = 0
    messages: list[str] = field(default_factory=list)

    def unanswered_text(self) -> str | None:
        if not self.unanswered:
            return None
        return (
            f"без ответа модели: {self.unanswered} ячеек у {self.manufacturers_unanswered} "
            "производителей — пересчитать, когда модель будет доступна"
        )


# Пояснение к «нет данных», когда модель не ответила (сбой запроса, обрыв ответа): это не
# «в каталоге нет данных», и эталон ТЗ такие ячейки не заполняет (`apply_reference_rules`).
MODEL_NO_VERDICT = "Модель не вернула вердикт по этому требованию"


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
    "tz": ComplianceSource.TZ_REFERENCE.value,
}
_STATUS_VALUES = {s.value for s in ComplianceStatus}


def build_manufacturer_context(
    db: Session,
    manufacturer: Manufacturer,
    *,
    include_manual: bool,
    requirements: list[Requirement] | None = None,
    named_text: str = "",
    only_wanted_kinds: bool = False,
) -> tuple[str, uuid.UUID | None]:
    """Карточка производителя для модели: факты из трёх источников с пометками.

    Возвращает также идентификатор модели прибора, к которой относится основная часть
    характеристик, — он попадёт в матрицу как `product_id`. Строится по данным, а не по
    догадкам: если каталог пуст, карточка окажется пустой, и вызывающий код честно поставит
    `no_data`, не обращаясь к модели.

    `requirements` задают, какие модели показать: в карточку помещается лишь
    `MAX_PRODUCTS_IN_CONTEXT` штук, и выбирать их надо по требованиям закупки, а не по
    алфавиту (`app/services/product_relevance.py`). Без требований — прежний порядок по
    названию. `named_text` — документация закупки, где ищется прямо названная модель.

    Типы СИ — те, к которым относятся отобранные модели (`_si_types_for`).

    `only_wanted_kinds` — карточка для позиции спецификации: только модели её типа, если
    такие у производителя есть (раздел 61 ARCHITECTURE.md).
    """

    blocks: list[str] = []

    selected = select_products_for_context(
        db,
        manufacturer,
        requirements or [],
        limit=MAX_PRODUCTS_IN_CONTEXT,
        named_text=named_text,
        only_wanted_kinds=only_wanted_kinds,
        # Свои снятые с производства модели не сравниваются (22.10.2026): поставить их
        # нельзя, а «соответствует» по ним уводило заключение к прибору, которого нет.
        active_only=manufacturer.is_mirtek,
    )
    primary_product_id = selected[0].product.id if selected else None

    for si_type in _si_types_for(db, manufacturer, [item.product for item in selected]):
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
                f"{description_excerpt(si_type.description_type_text)}"
            )

    if manufacturer.is_mirtek:
        # «Описание типа» перечисляет все исполнения, в том числе снятые: модель должна знать,
        # что их предлагать нельзя.
        retired = list(
            db.scalars(
                select(Product.model_name).where(
                    Product.manufacturer_id == manufacturer.id,
                    Product.status != ProductStatus.ACTIVE.value,
                )
            )
        )
        if retired:
            blocks.append(
                "[catalog] Сняты с производства — не считать подходящими, даже если исполнение "
                f"есть в описании типа: {'; '.join(sorted(retired))[:1500]}"
            )

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


# Где в «Описании типа» начинается расшифровка условного обозначения и сколько начала
# документа оставлять перед ней.
_DESIGNATION_MARKER = re.compile(r"структур\w*\s+условного\s+обозначения", re.IGNORECASE)
DESCRIPTION_HEAD_CHARS = 2_500
# Сколько текста брать до маркера: перед структурой обозначения обычно абзац о программе
# обслуживания («Admin Tools» у Энергомеры) — частое требование ТЗ.
DESCRIPTION_LEAD_CHARS = 400


def description_excerpt(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Выдержка из «Описания типа» не длиннее `limit`: начало документа и расшифровка
    условного обозначения.

    Первые 6 000 знаков подряд теряли главное: у СЕ207 (72632-18) таблицы «G — GSM»,
    «Q — реле управления нагрузкой», «R2 — внешняя антенна» стоят на 9 400-м знаке, после
    назначения, описания конструкции и пломбировки. Модель видела исполнение
    «CE207 R7.849.2.OG.QUVLF» и не могла его прочитать — «нет данных» по модему и реле у
    прибора, под который написано ТЗ."""

    if len(text) <= limit:
        return text
    marker = _DESIGNATION_MARKER.search(text, DESCRIPTION_HEAD_CHARS)
    if marker is None:
        return text[:limit]
    head = text[:DESCRIPTION_HEAD_CHARS]
    start = max(DESCRIPTION_HEAD_CHARS, marker.start() - DESCRIPTION_LEAD_CHARS)
    tail = text[start : start + limit - DESCRIPTION_HEAD_CHARS]
    return f"{head}\n[…]\n{tail}"


def _si_types_for(db: Session, manufacturer: Manufacturer, products: list[Product]) -> list[SiType]:
    """Типы СИ для карточки: сначала типы отобранных моделей в порядке их отбора, остаток
    мест — прочие типы производителя прежним порядком.

    Раньше брались только «прочие»: четыре первых по дате записи из 155 у Энергомеры —
    тестовая «12345-19», ЦЭ6827М, поверочная установка СУ203. Описание типа СЕ207, которое
    расшифровывает исполнение «…OG.QUVLF» как GSM-модем и реле нагрузки, в карточку не
    попадало, и на закупке под CE207 Энергомера получала «нет данных» по модему и реле.
    Прочие типы добираются только когда у отобранных моделей типа нет вовсе: чужое описание
    типа на 6 000 знаков модели не помогает, а место в карточке занимает."""

    linked: list[uuid.UUID] = []
    for product in products:
        if product.si_type_id and product.si_type_id not in linked:
            linked.append(product.si_type_id)
    linked = linked[:MAX_SI_TYPES_IN_CONTEXT]
    if linked:
        by_id = {
            item.id: item for item in db.scalars(select(SiType).where(SiType.id.in_(linked)))
        }
        return [by_id[item] for item in linked if item in by_id]
    return list(
        db.scalars(
            select(SiType)
            .where(SiType.manufacturer_id == manufacturer.id)
            .order_by(SiType.verified_by_user.desc(), SiType.created_at)
            .limit(MAX_SI_TYPES_IN_CONTEXT)
        )
    )


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


# --- карточка под пачку требований (14.10.2026) --------------------------------------------
#
# Карточка производителя по живой базе — в среднем 37 тыс. символов, у каждого десятого —
# все 70 тыс.; с руководствами — 67 тыс. в среднем. Она уходила целиком в каждую пачку из 20
# требований, хотя пачке о габаритах и реле не нужны строки каталога про температуру и
# межповерочный интервал. Это главная статья расхода токенов, и с ней матрица не помещается в
# окно 32 тыс. (ускоритель с локальной моделью). Теперь каждой пачке отдаётся своя выборка:
# всё, что про эти требования, плюс то, что нужно всегда. Карточка короче предела уходит как
# есть — отбор не трогает то, что и так помещается.

# Сколько карточки отдавать модели на одну пачку. 24 тыс. символов (~10 тыс. токенов): втрое
# меньше прежнего потолка, но с запасом против потери нужного факта — строк каталога у шести
# моделей бывает две-три сотни, и уже требованию «GSM-модем» с «классом точности» и «реле»
# релевантны десятки из них. Меньше — только если не помещается в окно модели.
FOCUS_TARGET_CHARS = 24_000
# Блок длиннее этого (выдержка из «Описания типа», структура условного обозначения) режется
# на куски, и в карточку попадают только нужные; заголовок блока идёт с любым из них.
_SPLIT_BLOCK_CHARS = 1_500
_PIECE_CHARS = 700
# Начало блока карточки — строка с пометкой источника.
_BLOCK_START = re.compile(r"^\[(si_type|catalog|manual|software|registry|astra|tz)\]")
# Факты, без которых вердикт не вынести, — в карточке всегда: по ним правила 7, 8, 13
# системного промпта решают «нет в списке — не соответствует», и потеря строки превратила бы
# проверенное «нет» в «нет данных». Их немного: они уже отобраны по требованиям закупки.
_PINNED_TAGS = ("[tz]", "[software]", "[registry]", "[astra]")
_WORD = re.compile(r"[0-9a-zа-я]+")
# Слитные обозначения дают и целое слово, и части: «RS485» совпадёт и с «RS485», и с
# «RS-485»; «300x170x75» — с «300 мм».
_WORD_PART = re.compile(r"[0-9]+|[a-zа-я]+")
_STOP_WORDS = frozenset(
    "для при или без над под как что это его она они быть должен должна должно должны "
    "наличие наличии менее более чем также либо который которая которые всех всего".split()
)


@dataclass
class _ContextUnit:
    """Кусок карточки: целый блок или часть длинного. `header` — номер единицы-заголовка
    своего блока (для частей длинного), без которой кусок в карточку не попадает."""

    text: str
    pinned: bool = False
    header: int | None = None
    stems: frozenset[str] = frozenset()


def _stems(text: str) -> frozenset[str]:
    """Основы слов для сравнения требования с карточкой: русские слова обрезаются до шести
    букв («интерфейсом» и «интерфейсы» совпадут), латиница и числа — целиком (`gsm`, `485`,
    `230`). Грубо, но без словарей и предсказуемо — отбор лишь решает, что положить в
    карточку, а не выносит вердикт."""

    result = set()
    for token in _WORD.findall(text.lower().replace("ё", "е")):
        parts = _WORD_PART.findall(token)
        for word in parts if len(parts) > 1 else [token]:
            if word in _STOP_WORDS:
                continue
            if word.isdigit() or word.isascii():
                if len(word) >= 2:
                    result.add(word)
                continue
            if len(word) >= 3:
                result.add(word[:6])
        if len(parts) > 1 and token.isascii():
            result.add(token)
    return frozenset(result)


def _context_units(context: str) -> list[_ContextUnit]:
    blocks: list[list[str]] = []
    for line in context.split("\n"):
        if not blocks or _BLOCK_START.match(line):
            blocks.append([line])
        else:
            blocks[-1].append(line)

    units: list[_ContextUnit] = []
    for lines in blocks:
        text = "\n".join(lines)
        first = lines[0]
        # Короткие факты типа СИ (номер в Госреестре, МПИ) и тип прибора модели — по ним
        # правила 10 и 15 сверяют тип и Госреестр, а места они почти не занимают.
        pinned = (
            first.startswith(_PINNED_TAGS)
            or (len(lines) == 1 and first.startswith("[si_type]"))
            or ": тип прибора — " in first
        )
        if len(text) <= _SPLIT_BLOCK_CHARS:
            units.append(_ContextUnit(text=text, pinned=pinned, stems=_stems(text)))
            continue
        header_index = len(units)
        units.append(_ContextUnit(text=first, pinned=pinned, stems=_stems(first)))
        piece: list[str] = []
        piece_length = 0
        for line in lines[1:]:
            if piece and piece_length + len(line) > _PIECE_CHARS:
                piece_text = "\n".join(piece)
                units.append(
                    _ContextUnit(text=piece_text, pinned=pinned, header=header_index, stems=_stems(piece_text))
                )
                piece, piece_length = [], 0
            piece.append(line)
            piece_length += len(line) + 1
        if piece:
            piece_text = "\n".join(piece)
            units.append(
                _ContextUnit(text=piece_text, pinned=pinned, header=header_index, stems=_stems(piece_text))
            )
    return units


def focus_context(context: str, requirements: list[Requirement], limit: int) -> str:
    """Карточка производителя не длиннее `limit`, собранная под эти требования.

    Порядок: закреплённые факты → куски, нужные требованиям, **по кругу**: сначала лучший
    кусок для каждого требования, затем второй и так далее → остальное, пока есть место.
    По кругу, а не по общему счёту: при общем счёте длинные выдержки «Описания типа», где
    встречаются слова половины требований, вытесняли короткие строки каталога, и требование
    о массе теряло строку «Масса — 1,5 кг» (проверено на 488 прошлых вердиктах, 14.10.2026).
    Лучший кусок для требования — с наибольшим числом общих с ним слов на длину; редкие
    слова весят больше (`gsm` в карточке в двух строках, «счётчик» — в каждой).

    Выбранное выводится в исходном порядке карточки, пропуск внутри длинного блока
    помечается «[…]» — модель видит, что выдержка неполная."""

    if len(context) <= limit:
        return context
    units = _context_units(context)
    queries = [
        _stems(requirement.normalized_text or requirement.text or "")
        | _stems(parameter_hint(_parameter_of(requirement)) or "")
        for requirement in requirements
    ]
    frequency: dict[str, int] = {}
    for unit in units:
        for stem in unit.stems:
            frequency[stem] = frequency.get(stem, 0) + 1

    def weight(stem: str) -> float:
        return math.log(1 + len(units) / frequency[stem])

    chosen: set[int] = set()
    used = 0

    def take(index: int) -> None:
        nonlocal used
        header = units[index].header
        need = [index] if header is None else [header, index]
        cost = sum(len(units[item].text) + 1 for item in need if item not in chosen)
        if used + cost <= limit:
            chosen.update(need)
            used += cost

    for index, unit in enumerate(units):
        if unit.pinned:
            take(index)

    rankings: list[list[int]] = []
    for query in queries:
        scored = []
        for index, unit in enumerate(units):
            if unit.pinned:
                continue
            common = unit.stems & query
            if common:
                value = sum(weight(stem) for stem in common) / math.sqrt(len(unit.text) + 40)
                scored.append((-value, index))
        rankings.append([index for _, index in sorted(scored)])
    depth = 0
    while any(depth < len(ranking) for ranking in rankings) and used < limit:
        for ranking in rankings:
            if depth < len(ranking):
                take(ranking[depth])
        depth += 1
    for index, unit in enumerate(units):
        if index not in chosen:
            take(index)

    lines: list[str] = []
    previous: int | None = None
    for index in sorted(chosen):
        if units[index].header is not None and previous != index - 1:
            lines.append("[…]")
        lines.append(units[index].text)
        previous = index
    return "\n".join(lines)


def _focus_limit(db: Session, requirements: list[Requirement]) -> int:
    """Предел карточки на пачку: `FOCUS_TARGET_CHARS` или меньше, если окно модели (за
    вычетом промпта, требований и потолка ответа) не вмещает и этого."""

    reserved = len(_SYSTEM_PROMPT) + len(_requirements_block(requirements)) + 200
    return max(4_000, min(FOCUS_TARGET_CHARS, input_budget_chars(db) - reserved))


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
        if lost_numbers(requirement.text, requirement.normalized_text):
            # Сокращение потеряло часть условий — модель видит и формулировку ТЗ
            # (22.10.2026: «не менее 100А» пропало, и D17 с 80 А «соответствовал»).
            text = f"{text} (в ТЗ: {requirement.text[:300]})"
        hint = parameter_hint(_parameter_of(requirement))
        lines.append(f"{index}. {text}" + (f" {hint}" if hint else ""))
    return "\n".join(lines)


def evaluate_tender(
    db: Session,
    tender: Tender,
    *,
    actor: User | None,
    use_manual_fallback: bool = True,
    skip_fresh_since: datetime | None = None,
) -> ComplianceOutcome:
    """Строит матрицу соответствия и считает проценты победителя по всем производителям.

    `skip_fresh_since` — продолжение, а не пересчёт: производитель, чей текущий итог
    посчитан позже этого момента и позже последнего извлечения требований, пропускается.
    Достройка брешей передаёт время извлечения требований (считать надо только тех, кого
    нет или кто устарел), задача «Расчёт соответствия» — момент своей постановки в очередь
    (после перезапуска сервера не повторять уже посчитанное ею же)."""

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

    named_text = tender_named_text(db, tender)
    reference = find_reference(requirements, manufacturers, db=db, named_text=named_text)
    if reference is not None:
        who = reference.manufacturer.brand_name or reference.manufacturer.legal_name
        outcome.messages.append(f"ТЗ составлено по модели {who}: {reference.label}")
    fresh = _fresh_manufacturers(db, tender, requirements, skip_fresh_since)
    pending = [item for item in manufacturers if item.id not in fresh]
    outcome.manufacturers_skipped = len(manufacturers) - len(pending)
    total = len(pending)

    def finish(manufacturer: Manufacturer, verdicts: dict[int, RequirementVerdict]) -> None:
        unanswered = count_unanswered(verdicts)
        if unanswered:
            outcome.unanswered += unanswered
            outcome.manufacturers_unanswered += 1
        apply_reference_rules(manufacturer, requirements, verdicts, reference, manufacturers)
        _save_entries(db, tender, manufacturer, requirements, verdicts, outcome)
        _calculate_percentage(
            db,
            tender,
            manufacturer,
            requirements,
            verdicts,
            reference=reference
            if reference is not None and reference.manufacturer.id == manufacturer.id
            else None,
        )
        outcome.manufacturers_processed += 1
        # Коммит после каждого производителя (28.09.2026): матрица на 55 требований строится
        # 10-15 минут, и перезапуск сервера на двенадцатом производителе раньше выбрасывал
        # все одиннадцать готовых — задача начинала заново и снова не доходила до конца.
        db.commit()

    if _can_run_in_parallel(db) and total > 1:
        _evaluate_in_parallel(
            db, tender, pending, requirements, outcome, finish,
            use_manual_fallback=use_manual_fallback, named_text=named_text, reference=reference,
        )
    else:
        for position, manufacturer in enumerate(pending, start=1):
            label = manufacturer.brand_name or manufacturer.legal_name
            report_progress(f"Матрица соответствия: {position} из {total} производителей — {label}")
            verdicts = _evaluate_manufacturer(
                db,
                tender,
                manufacturer,
                requirements,
                outcome,
                use_manual_fallback=use_manual_fallback,
                named_text=named_text,
                reference=reference,
            )
            finish(manufacturer, verdicts)

    if outcome.manufacturers_skipped:
        outcome.messages.append(
            f"уже посчитаны ранее и пропущены: {outcome.manufacturers_skipped}"
        )
    db.commit()
    _request_missing_catalog_data(db, manufacturers, actor=actor)
    _log(db, tender, outcome, actor, level=LogLevel.WARNING if outcome.unanswered else LogLevel.INFO)
    return outcome


def count_unanswered(verdicts: dict[int, RequirementVerdict]) -> int:
    """Ячейки, по которым модель не ответила (`MODEL_NO_VERDICT`), — не «в каталоге нет данных»."""

    return sum(
        1
        for verdict in verdicts.values()
        if verdict.status == ComplianceStatus.NO_DATA.value and verdict.explanation == MODEL_NO_VERDICT
    )


# Сколько производителей считать одновременно. Матрица — 4-5 запросов к модели на
# производителя, по очереди 17 производителей шли 15-17 минут (06.10.2026); четыре потока
# укладывают их в 4-5 минут и не упираются в лимиты RouterAI (100 запросов за 10 с).
MATRIX_WORKERS = 4


def _can_run_in_parallel(db: Session) -> bool:
    """Параллельно — только когда у сессии свой пул соединений (рабочий сервер). Тестовая
    сессия живёт в одной внешней транзакции на одном соединении, и потоки со своими
    сессиями не увидели бы её незакоммиченных данных."""

    from sqlalchemy.engine import Engine

    return MATRIX_WORKERS > 1 and isinstance(db.get_bind(), Engine)


def _evaluate_in_parallel(
    db: Session,
    tender: Tender,
    manufacturers: list[Manufacturer],
    requirements: list[Requirement],
    outcome: ComplianceOutcome,
    finish,
    *,
    use_manual_fallback: bool,
    named_text: str,
    reference: TzReference | None,
) -> None:
    """Вердикты производителей считаются в потоках, каждый со своей сессией; сохраняются —
    в основном потоке по мере готовности, с тем же коммитом после каждого производителя.
    Контекст задачи (автор, отмена, выбранная модель) переносится в поток копией
    `contextvars`: без неё запросы ушли бы не той модели и не остановились бы по отмене."""

    import contextvars
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.db.session import SessionLocal

    tender_id = tender.id
    requirement_ids = [item.id for item in requirements]
    reference_plain = (
        None
        if reference is None
        else (
            reference.manufacturer.id,
            list(reference.designations),
            reference.equivalent_allowed,
            reference.clause,
        )
    )
    by_id = {item.id: item for item in manufacturers}

    def work(manufacturer_id: uuid.UUID) -> tuple[dict[int, RequirementVerdict], ComplianceOutcome]:
        session = SessionLocal()
        try:
            local_tender = session.get(Tender, tender_id)
            local_manufacturer = session.get(Manufacturer, manufacturer_id)
            local_requirements = [session.get(Requirement, item) for item in requirement_ids]
            local_reference = None
            if reference_plain is not None:
                local_reference = TzReference(
                    manufacturer=session.get(Manufacturer, reference_plain[0]),
                    designations=reference_plain[1],
                    equivalent_allowed=reference_plain[2],
                    clause=reference_plain[3],
                )
            local_outcome = ComplianceOutcome()
            verdicts = _evaluate_manufacturer(
                session,
                local_tender,
                local_manufacturer,
                local_requirements,
                local_outcome,
                use_manual_fallback=use_manual_fallback,
                named_text=named_text,
                reference=local_reference,
            )
            return verdicts, local_outcome
        finally:
            session.close()

    total = len(manufacturers)
    done = 0
    report_progress(f"Матрица соответствия: 0 из {total} производителей")
    executor = ThreadPoolExecutor(max_workers=MATRIX_WORKERS, thread_name_prefix="oraclet-matrix")
    try:
        futures = {
            executor.submit(contextvars.copy_context().run, work, item.id): item.id
            for item in manufacturers
        }
        for future in as_completed(futures):
            verdicts, local_outcome = future.result()
            manufacturer = by_id[futures[future]]
            outcome.manual_fallback_used += local_outcome.manual_fallback_used
            outcome.prejudged += local_outcome.prejudged
            outcome.messages.extend(local_outcome.messages)
            finish(manufacturer, verdicts)
            done += 1
            label = manufacturer.brand_name or manufacturer.legal_name
            report_progress(f"Матрица соответствия: {done} из {total} производителей — {label}")
    finally:
        # Отмена задачи или сбой — оставшиеся производители не запускаются.
        executor.shutdown(wait=True, cancel_futures=True)


def _fresh_manufacturers(
    db: Session,
    tender: Tender,
    requirements: list[Requirement],
    since: datetime | None,
) -> set[uuid.UUID]:
    """Производители с текущим итогом матрицы новее `since` и новее самих требований."""

    if since is None:
        return set()
    newest_requirement = max((item.created_at for item in requirements if item.created_at), default=None)
    threshold = max(since, newest_requirement) if newest_requirement else since
    return set(
        db.scalars(
            select(WinPercentage.manufacturer_id).where(
                WinPercentage.tender_id == tender.id,
                WinPercentage.is_current.is_(True),
                WinPercentage.calculated_at >= threshold,
                WinPercentage.requirements_total == len(requirements),
            )
        )
    )


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
    named_text: str = "",
    reference: TzReference | None = None,
) -> dict[int, RequirementVerdict]:
    """Вердикты по всем требованиям для одного производителя.

    Закупка из нескольких позиций спецификации (15.10.2026, раздел 61 ARCHITECTURE.md)
    считается по позициям: у каждой — своя карточка с моделями её типа, и требования позиции
    сверяются только с ними; требования без позиции (общие) — с карточкой по всем
    требованиям, как раньше. Итог производителя — по всем требованиям сразу
    (`_calculate_percentage`, `decide_verdict`): не проходит хоть одна позиция — не
    проходит производитель.

    Раньше смесь требований разных приборов уходила одной карточкой: на 32616469643
    «Россети Тюмень» «Номинальное напряжение: 400 В» трёхфазной позиции сравнивалось с
    однофазной моделью — «не соответствует» у всех, включая эталон ТЗ."""

    groups = lot_groups([item.position for item in requirements])
    made_kinds: set[str] | None = None
    if len(groups) == 1:
        verdicts = _evaluate_group(
            db, tender, manufacturer, requirements, outcome,
            use_manual_fallback=use_manual_fallback, named_text=named_text, reference=reference,
        )
    else:
        verdicts = {}
        for label, indices in groups:
            subset = [requirements[index - 1] for index in indices]
            local = _evaluate_group(
                db, tender, manufacturer, subset, outcome,
                use_manual_fallback=use_manual_fallback, named_text=named_text, reference=reference,
                # Общим требованиям — карточка по всей закупке: в ней модели всех позиций.
                context_requirements=subset if label else requirements,
                position=label,
            )
            for local_index, verdict in local.items():
                verdict.number = indices[local_index - 1]
                verdicts[indices[local_index - 1]] = verdict
        made_kinds = manufacturer_kind_groups(db, manufacturer)
        apply_position_kinds(requirements, verdicts, made_kinds)

    # Типы каталога считаются, только когда есть «не соответствует» по типу/фазности:
    # это запрос по всем моделям производителя, а нужен он редко.
    needs_kinds = made_kinds is None and any(
        _parameter_of(requirement) in TYPE_PARAMETERS
        and verdicts[index].status == ComplianceStatus.NOT_MEETS.value
        for index, requirement in enumerate(requirements, start=1)
    )
    if needs_kinds:
        made_kinds = manufacturer_kind_groups(db, manufacturer)
    apply_confidence_floor(requirements, verdicts)
    apply_transformer_scope(requirements, verdicts)
    apply_duration_check(requirements, verdicts)
    apply_evidence_check(requirements, verdicts)
    apply_expert_rules(manufacturer, requirements, verdicts, made_kinds=made_kinds)
    apply_lot_variants(requirements, verdicts)
    return verdicts


def _evaluate_group(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    outcome: ComplianceOutcome,
    *,
    use_manual_fallback: bool,
    named_text: str = "",
    reference: TzReference | None = None,
    context_requirements: list[Requirement] | None = None,
    position: str | None = None,
) -> dict[int, RequirementVerdict]:
    """Вердикты по группе требований (вся закупка или одна позиция) — номера с единицы
    внутри группы.

    Сначала — два первых источника (описание типа и каталог). Если после этого часть
    требований осталась без данных, а у производителя есть материалы из руководства, делается
    второй проход **только по этим требованиям** — именно так третий шаг описан в разделе
    5.5 ТЗ (fallback, а не обязательный источник).

    `context_requirements` — по каким требованиям собирать карточку (по умолчанию — по
    самой группе); `position` — подпись позиции: карточка из моделей её типа, числовая
    сверка — по её типу, а модели позиция названа в запросе."""

    context_requirements = context_requirements if context_requirements is not None else requirements
    only_wanted_kinds = position is not None

    def context_for(include_manual: bool) -> str:
        text, _ = build_manufacturer_context(
            db,
            manufacturer,
            include_manual=include_manual,
            requirements=context_requirements,
            named_text=named_text,
            only_wanted_kinds=only_wanted_kinds,
        )
        if text.strip() and reference is not None and reference.manufacturer.id == manufacturer.id:
            text = f"{reference_fact(reference)}\n{text}"
        return text

    context = context_for(False)
    if not context.strip():
        # Данных о производителе нет вовсе — честный no_data без обращения к модели.
        return {
            index: _no_data_verdict(index, "В каталоге нет данных по этому производителю")
            for index in range(1, len(requirements) + 1)
        }

    # Однозначные числовые требования решает код — модели уходят только остальные
    # (14.10.2026, раздел 59 ARCHITECTURE.md, `app/services/compliance_numeric.py`).
    # У позиции — по её типу: «400 В» трёхфазной позиции не подтверждается однофазной моделью.
    verdicts = prejudge_numeric(
        db, tender, manufacturer, requirements,
        groups=_position_groups(requirements) if position is not None else None,
    )
    outcome.prejudged += len(verdicts)
    asked = [index for index in range(1, len(requirements) + 1) if index not in verdicts]
    if asked:
        answered = _ask_in_batches(
            db, manufacturer, [requirements[index - 1] for index in asked], context, outcome,
            position=position,
        )
        for number, index in enumerate(asked, start=1):
            if number in answered:
                answered[number].number = index
                verdicts[index] = answered[number]

    unresolved = [
        index
        for index in range(1, len(requirements) + 1)
        if verdicts.get(index, _no_data_verdict(index)).status == ComplianceStatus.NO_DATA.value
    ]
    if use_manual_fallback and unresolved:
        manual_context = context_for(True)
        if manual_context.strip() and manual_context != context:
            subset = [requirements[index - 1] for index in unresolved]
            fallback = _ask_in_batches(
                db, manufacturer, subset, manual_context, outcome, position=position
            )
            for number, index in enumerate(unresolved, start=1):
                verdict = fallback.get(number)
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
            index, _no_data_verdict(index, MODEL_NO_VERDICT, 0.0)
        )
    return verdicts


def _requirement_text(requirement: Requirement) -> str:
    return f"{requirement.text or ''} {requirement.normalized_text or ''}"


def _position_groups(requirements: list[Requirement]) -> set[str] | None:
    """Группы типов (фазность × включение), которые следуют из требований позиции; `None` —
    тип из них не следует."""

    text = " ".join(_requirement_text(item) for item in requirements)
    return {kind_group(code) for code in kinds_from_text(text)} or None


# Уверенность «не соответствует» по типу позиции, которого нет в каталоге производителя:
# ниже порога проверки — справочник бывает неполным, и человек должен это подтвердить.
POSITION_KIND_CONFIDENCE = 0.55


# Начало пояснения отказа `apply_position_kinds` — по нему его узнаёт `apply_reference_rules`.
POSITION_KIND_PREFIX = "Позиция «"


def apply_position_kinds(
    requirements: list[Requirement],
    verdicts: dict[int, RequirementVerdict],
    made_kinds: set[str],
) -> None:
    """Позиция спецификации, прибора типа которой у производителя в каталоге нет, —
    «не соответствует» по её требованиям к типу прибора (параметры 1, 2 или формулировка,
    из которой следует тип), с пометкой «проверить».

    Отдельной карточкой позиции модель видит только модели другого типа и по правилу 10
    промпта отвечает «нет данных» — и производитель без трёхфазных полукосвенного включения
    «проходил» бы закупку, в которой такая позиция есть. Решает код, а не модель: это
    свойство каталога производителя, а не прочтение карточки. Правило молчит, когда тип
    моделей каталога не определён ни у одной (`made_kinds` пуст) — тогда судить не по чему;
    и не трогает «соответствует»: модель видела факт, которого нет в типах каталога."""

    if not made_kinds:
        return
    by_position: dict[str, list[int]] = {}
    for index, requirement in enumerate(requirements, start=1):
        key = position_key(requirement.position)
        if key:
            by_position.setdefault(key, []).append(index)
    for indices in by_position.values():
        subset = [requirements[index - 1] for index in indices]
        wanted = _position_groups(subset)
        if not wanted or wanted & made_kinds:
            continue
        targets = [
            index
            for index in indices
            if _parameter_of(requirements[index - 1]) in TYPE_PARAMETERS
            or kinds_from_text(_requirement_text(requirements[index - 1]))
        ] or [
            index for index in indices if requirements[index - 1].criticality == Criticality.CRITICAL.value
        ]
        label = subset[0].position or ""
        explanation = (
            f"{POSITION_KIND_PREFIX}{label[:80]}»: в каталоге производителя нет приборов типа "
            f"«{'; '.join(kind_labels(sorted(wanted)))}» (есть: "
            f"{'; '.join(kind_labels(sorted(made_kinds)))}) — проверить, не неполон ли справочник"
        )[:300]
        for index in targets:
            current = verdicts.get(index)
            if current is not None and current.status == ComplianceStatus.MEETS.value:
                continue
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.NOT_MEETS.value,
                source="rule",
                explanation=explanation,
                confidence=POSITION_KIND_CONFIDENCE,
            )


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


def reference_fact(reference: TzReference) -> str:
    """Строка карточки о том, что ТЗ составлено по модели этого производителя: модель
    сравнивает требования прежде всего с ней, а не с соседними моделями карточки."""

    equivalent = {
        True: "допускается эквивалент",
        False: "эквивалент не допускается",
        None: "про эквивалент документация молчит",
    }[reference.equivalent_allowed]
    return (
        f"[tz] ТЗ закупки составлено по модели этого производителя: {reference.label} "
        f"({equivalent}). Требования ТЗ описывают этот прибор — сверяй их прежде всего с ним."
    )


# Уверенность для вердиктов по эталону ТЗ. Требование, по которому в каталоге нет данных,
# у эталонного прибора выполняется почти всегда — ТЗ с него и переписано, — но не всегда:
# заказчик мог дописать своё (настройка модема под оператора). Поэтому ниже порога «на
# проверку» не опускаем, но и до уверенности фактов из каталога не поднимаем.
REFERENCE_FILL_CONFIDENCE = 0.65
# Ниже порога LOW_CONFIDENCE_THRESHOLD — ячейка уходит на проверку человеком.
REFERENCE_CONFLICT_CONFIDENCE = 0.5
# Вердикты, полученные сравнением требования с моделями из карточки производителя (в
# отличие от правил кода и реестров), — у эталона без модели в каталоге они про чужой прибор.
_MODEL_COMPARISON_SOURCES = frozenset({"si_type", "catalog", "manual", ""})


def apply_reference_rules(
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    verdicts: dict[int, RequirementVerdict],
    reference: TzReference | None,
    manufacturers: list[Manufacturer],
) -> None:
    """Вердикты, которые следуют из эталона ТЗ и фирменного ПО, — кодом, поверх модели.

    * «Модель прибора: X» — производителю X «соответствует»; остальным — «частично» как
      эквивалент, если он допущен или документация молчит (итог не выше «с оговорками»,
      см. `decide_verdict`), и «не соответствует», если эквивалент запрещён.
    * Фирменное ПО или сервис производителя (`vendor_exclusive`: «AdminTools», «CE
      Net-Connection» у Энергомеры) — другим производителям «не соответствует», что бы ни
      ответила модель: заявку с чужим прибором по этому пункту отклонят. Такие требования
      критичные (`tender_analysis`), поэтому итог «не проходит», а не «с оговорками».
    * Эталонному производителю «нет данных» заменяется на «соответствует по эталону ТЗ».
      Противоречие из каталога («не соответствует») остаётся — его должен увидеть человек.
      Если модели эталона в каталоге нет, такое противоречие — сравнение с другой моделью
      производителя: «частично» на проверку, а не «не проходит» (15.10.2026).
    """

    is_reference = reference is not None and reference.manufacturer.id == manufacturer.id
    who_reference = (
        (reference.manufacturer.brand_name or reference.manufacturer.legal_name)
        if reference is not None
        else ""
    )
    for index, requirement in enumerate(requirements, start=1):
        current = verdicts.get(index)
        status = current.status if current else ComplianceStatus.NO_DATA.value

        if is_model_requirement(requirement) or names_reference_model(requirement, reference):
            owner = match_manufacturer(requirement.vendor, manufacturers) or (
                reference.manufacturer if reference is not None else None
            )
            designation = (requirement.normalized_text or "").split(":", 1)[-1].strip()
            if owner is None:
                # Чья модель — неизвестно (нет ни производителя у требования, ни эталона), но
                # «модель не та» — это не отказ: чужой прибор поставляют как эквивалент
                # (сверка 15.10.2026: «Наименование товара: … МИР С-04…» без эталона давало
                # конкурентам «не соответствует» с уверенностью 0 и «не проходит»).
                if status == ComplianceStatus.NOT_MEETS.value and (
                    current is None or current.source not in ("rule", "tz")
                ):
                    verdicts[index] = RequirementVerdict(
                        number=index,
                        status=ComplianceStatus.PARTIAL.value,
                        source="rule",
                        explanation=(
                            f"ТЗ называет конкретную модель «{designation}»; прибор этого "
                            "производителя — эквивалент, проверить по паспорту и допустимость "
                            "эквивалента"
                        )[:300],
                        confidence=0.5,
                    )
                continue
            owner_name = owner.brand_name or owner.legal_name
            if owner.id == manufacturer.id and is_registry_requirement(requirement):
                # Номер типа СИ из ТЗ — тип эталона; в каталоге может стоять номер после
                # перерегистрации (32616407181: РиМ 384.02/2, «55522-13» — «не проходит»).
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.MEETS.value,
                    source="tz",
                    explanation=(
                        f"ТЗ называет тип СИ этого производителя: {designation} — проверить "
                        "действующий номер во ФГИС «Аршин»"
                    )[:300],
                    confidence=REFERENCE_FILL_CONFIDENCE,
                )
            elif owner.id == manufacturer.id:
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.MEETS.value,
                    source="tz",
                    explanation=f"ТЗ называет модель этого производителя: {designation}"[:300],
                    confidence=0.95,
                )
            elif reference is not None and reference.equivalent_allowed is False:
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.NOT_MEETS.value,
                    source="rule",
                    explanation=(
                        f"Эквивалент не допускается: закупается модель {owner_name} "
                        f"«{designation}»"
                    )[:300],
                    confidence=0.95,
                )
            else:
                # Чужой прибор — только эквивалент, и это «частично», а не «соответствует»
                # (замечание 13.10.2026, закупка 32616451908: ПКУ-10 кВ под МИРТЕК-135-РУ,
                # в матрице два требования — и все восемнадцать производителей «проходят» со
                # 100%). Эквивалентность доказывают по паспорту, и равняться с эталоном, под
                # который переписано ТЗ, по одному этому пункту конкурент не может.
                allowed = reference is None or reference.equivalent_allowed is True
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.PARTIAL.value,
                    source="rule",
                    explanation=(
                        f"Эталон ТЗ — {owner_name} «{designation}»; "
                        + (
                            "эквивалент допускается — соответствие эталону доказать по "
                            "паспорту прибора"
                            if allowed
                            else "допустим ли эквивалент, документация не говорит — проверить"
                        )
                    )[:300],
                    confidence=0.7 if allowed else 0.4,
                )
            continue

        if is_vendor_exclusive(requirement):
            owner = match_manufacturer(requirement.vendor, manufacturers)
            if owner is None:
                continue  # производителя нет в справочнике — решает модель по фактам
            what = (requirement.normalized_text or requirement.text or "")[:120]
            if owner.id != manufacturer.id:
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.NOT_MEETS.value,
                    source="rule",
                    explanation=(
                        f"Требуется фирменное ПО/сервис {owner.brand_name or owner.legal_name} "
                        f"({what}) — прибор другого производителя его не поддерживает"
                    )[:300],
                    confidence=0.9,
                )
            elif status == ComplianceStatus.NO_DATA.value:
                verdicts[index] = RequirementVerdict(
                    number=index,
                    status=ComplianceStatus.MEETS.value,
                    source="tz",
                    explanation=f"Фирменное ПО/сервис самого производителя: {what}"[:300],
                    confidence=0.8,
                )
            continue

        if (
            is_reference
            and status == ComplianceStatus.NOT_MEETS.value
            and current is not None
            and current.source == "rule"
            and current.explanation.startswith(POSITION_KIND_PREFIX)
        ):
            # Нет в каталоге прибора типа позиции — у эталона это неполнота справочника, а не
            # отказ: ТЗ написано под его прибор (32616409293, 15.10.2026: эталон Метроника «не
            # проходил» по «Высоковольтный прибор учёта» — таких моделей в её карточке нет).
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.PARTIAL.value,
                source="tz",
                explanation=(
                    f"ТЗ составлено по прибору {who_reference}. {current.explanation}"
                )[:300],
                confidence=REFERENCE_CONFLICT_CONFIDENCE,
            )
            continue

        if (
            is_reference
            and _parameter_of(requirement) in TYPE_PARAMETERS
            and status != ComplianceStatus.MEETS.value
            and reference.products
        ):
            wanted = {
                kind_group(code)
                for code in kinds_from_text(f"{requirement.text or ''} {requirement.normalized_text or ''}")
            }
            confirmed = bool(wanted) and wanted <= reference.kind_groups
            # Тип требования — тип прибора, который ТЗ называет; «не соответствует» тут —
            # сравнение с другим исполнением из карточки (см. `TzReference.kind_groups`). Если
            # каталог тип не подтверждает (32616394121: сплит-исполнения МИР С-05 в каталоге
            # нет, а ТЗ его называет) — «соответствует» на проверку человеком, а не «не
            # проходит» по собственному прибору.
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.MEETS.value,
                source="tz",
                explanation=(
                    f"ТЗ называет модель {who_reference} {reference.label}"
                    + (
                        f" этого типа ({'; '.join(kind_labels(sorted(wanted)))})"
                        if confirmed
                        else "; в каталоге это исполнение не подтверждено — проверить по паспорту"
                    )
                )[:300],
                confidence=0.8 if confirmed else 0.55,
            )
            continue

        if (
            is_reference
            and status == ComplianceStatus.NOT_MEETS.value
            and not reference.products
            and current is not None
            and current.source in _MODEL_COMPARISON_SOURCES
        ):
            # Модели эталона в каталоге нет — «не соответствует» получено сравнением с другой
            # моделью производителя, а не с прибором, по которому писано ТЗ (32616469643,
            # 15.10.2026: эталон — ВШУ в сборе с ПУ полукосвенного включения, в каталоге
            # Нартиса только счётчики, и «Максимальный ток 10 А» сравнили со счётчиком прямого
            # включения на 100 А). Противоречие остаётся видным — «частично» с пояснением
            # каталога и на проверку, — но «не проходит» эталону оно не ставит. Если модель
            # эталона в каталоге есть, противоречие — про неё саму и остаётся «не соответствует».
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.PARTIAL.value,
                source=current.source,
                explanation=(
                    f"ТЗ составлено по модели {who_reference} {reference.label}, её нет в "
                    f"каталоге; по другой модели: {current.explanation} — проверить по паспорту"
                )[:300],
                confidence=REFERENCE_CONFLICT_CONFIDENCE,
            )
            continue

        if (
            is_reference
            and status == ComplianceStatus.NO_DATA.value
            # Модель не ответила — сбой, а не пустота каталога: «соответствует по эталону»
            # спрятало бы его (06.10.2026: при нулевом балансе RouterAI Энергомера получила
            # бы «проходит» без единого ответа модели).
            and (current is None or current.explanation != MODEL_NO_VERDICT)
        ):
            verdicts[index] = RequirementVerdict(
                number=index,
                status=ComplianceStatus.MEETS.value,
                source="tz",
                explanation=(
                    f"ТЗ составлено по модели {who_reference} {reference.label} — требование "
                    "описывает эталонный прибор; в каталоге подтверждения нет, проверить по "
                    "паспорту"
                )[:300],
                confidence=REFERENCE_FILL_CONFIDENCE,
            )


# Сроки, где больше — всегда лучше: межповерочный интервал, гарантия, срок службы. Счётчики
# трансформаторов (ТТ/ТН) — не свойство прибора учёта, их не трогаем.
_DURATION_PARAMETERS = (
    ("межповер", re.compile(r"межповерочн", re.IGNORECASE)),
    ("гарант", re.compile(r"гаранти", re.IGNORECASE)),
    ("служб", re.compile(r"срок\w*\s+службы", re.IGNORECASE)),
)
_TRANSFORMER_RE = re.compile(r"\bт[тн]\b|трансформатор", re.IGNORECASE)
_DURATION_VALUE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(лет|года|год|мес)", re.IGNORECASE)
# Пояснение про часть моделей производителя — выбор между ними за моделью, не за кодом.
_PARTIAL_COVERAGE_RE = re.compile(r"не\s+(?:для|у)\s+всех|у\s+других|у\s+остальных|но\s+не\b", re.IGNORECASE)


def _durations(text: str) -> list[tuple[float, str]]:
    """Сроки в тексте: (месяцы, запись как в тексте) — «192 месяца» и «16 лет» равны по
    месяцам, но записаны по-разному, и это различие нужно ниже."""

    values = []
    for number, unit in _DURATION_VALUE_RE.findall(text or ""):
        in_months = unit.lower().startswith("мес")
        months = float(number.replace(",", ".")) * (1 if in_months else 12)
        # «2026 года» — дата, а не срок.
        if 0 < months <= 1200:
            values.append((months, f"{number.replace(',', '.')} {'мес' if in_months else 'лет'}"))
    return values


def apply_duration_check(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> None:
    """Арифметика сроков — кодом, по значению, которое модель сама процитировала в пояснении.

    Проверка 13.10.2026 по 8,8 тыс. ячеек матрицы: «Межповерочный интервал: 16 лет» при
    «192 месяца» в карточке модель оценивала то «соответствует», то «частично», то «не
    соответствует — 192 месяца меньше требуемых 16 лет». Переводить месяцы в годы модель
    ошибается, а на «не проходит» по критичному требованию это влияет напрямую.

    Вердикт меняется, только когда он противоречит арифметике и значение однозначно:
    пояснение говорит о том же сроке, называет значение прибора иначе, чем записано
    требование (повтор «16 лет» из требования — не значение прибора), и все такие значения —
    по одну сторону от требуемого. «У одних моделей 192 мес., но не у всех» не трогаем."""

    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts.get(index)
        if verdict is None or verdict.status == ComplianceStatus.NO_DATA.value:
            continue
        text = requirement.normalized_text or requirement.text or ""
        explanation = verdict.explanation or ""
        parameter = next((stem for stem, pattern in _DURATION_PARAMETERS if pattern.search(text)), None)
        if parameter is None or _TRANSFORMER_RE.search(text):
            continue
        if parameter not in explanation.lower() or _PARTIAL_COVERAGE_RE.search(explanation):
            continue
        required = _durations(text.split(":", 1)[-1])
        if len(required) != 1:
            continue
        wanted, wanted_written = required[0]
        at_most = bool(re.search(r"не\s+более|не\s+выше", text, re.IGNORECASE))
        stated = [months for months, written in _durations(explanation) if written != wanted_written]
        if not stated:
            continue
        fits = [months <= wanted if at_most else months >= wanted for months in stated]
        if all(fits):
            status = ComplianceStatus.MEETS.value
        elif not any(fits) and not re.search(r"некоторых|части\s+моделей", explanation, re.IGNORECASE):
            # «У некоторых моделей 96 мес.» — про остальные модель молчит; одной подходящей
            # модели производителю достаточно, поэтому «не соответствует» тут не выводим.
            status = ComplianceStatus.NOT_MEETS.value
        else:
            continue
        if status == verdict.status:
            continue
        if status == ComplianceStatus.MEETS.value:
            sign = "≤" if at_most else "≥"
        else:
            sign = ">" if at_most else "<"
        verdicts[index] = RequirementVerdict(
            number=index,
            status=status,
            source=verdict.source,
            explanation=(
                f"{explanation} [проверено расчётом: {stated[0]:g} мес. {sign} {wanted:g} мес.]"
            )[:300],
            confidence=max(verdict.confidence, 0.85),
        )


# Параметры, которые сравниваются числом: «не соответствует» по ним без названного значения
# из карточки — не вывод, а догадка модели.
_NUMERIC_PARAMETER_RE = re.compile(
    r"класс\w*\s+точност|габарит|масс[аы]|\bвес\b|межповерочн|гаранти|срок\w*\s+службы|"
    r"температур|\bток\b|напряжени|наработк",
    re.IGNORECASE,
)
UNEVIDENCED_EXPLANATION = "значение из карточки не названо — проверить по паспорту"


def apply_evidence_check(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> None:
    """«Не соответствует» по числовому параметру без единой цифры в пояснении — «нет данных».

    Проверка 13.10.2026: 96 из 246 таких вердиктов модели не называли значения вовсе —
    «Класс точности ТН в карточке не соответствует требуемому», «Габариты больше требуемых».
    У Энергомеры на закупке под её же ПКУ СЕ401 (32616399389) так набралось четыре «не
    соответствует» по ТТ и ТН, и эталон «не проходил». Вердикт, решающий «не проходит», без
    факта ставить нельзя: человек его не проверит, а оспорить нечем. Вызывается только для
    ответов модели — до правил кода."""

    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts.get(index)
        if verdict is None or verdict.status != ComplianceStatus.NOT_MEETS.value:
            continue
        text = requirement.normalized_text or requirement.text or ""
        if not _NUMERIC_PARAMETER_RE.search(text) or not re.search(r"\d", text.split(":", 1)[-1]):
            continue
        if re.search(r"\d", verdict.explanation or ""):
            continue
        verdicts[index] = RequirementVerdict(
            number=index,
            status=ComplianceStatus.NO_DATA.value,
            source=verdict.source,
            explanation=f"{verdict.explanation} [{UNEVIDENCED_EXPLANATION}]".strip()[:300],
            confidence=0.4,
        )


# Ниже этой уверенности «не соответствует» модели — не вывод, а отказ отвечать.
NOT_MEETS_CONFIDENCE_FLOOR = 0.3
UNSURE_EXPLANATION = "модель не уверена в ответе — проверить по паспорту"


def apply_confidence_floor(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> None:
    """«Не соответствует» с уверенностью ниже 0,3 — «нет данных».

    Сверка матрицы на сервере 15.10.2026: модель ставила «не соответствует» с уверенностью
    0,0 («Модель не соответствует заданному наименованию»), и такой ответ по критичному
    требованию решал «не проходит» наравне с фактом из каталога. Сама модель вывода не
    сделала — решать за неё итог нельзя. Правила кода (`rule`, `tz`, `astra`) не трогаем."""

    for index in range(1, len(requirements) + 1):
        verdict = verdicts.get(index)
        if (
            verdict is None
            or verdict.status != ComplianceStatus.NOT_MEETS.value
            or verdict.source in ("rule", "tz", "astra")
            or verdict.confidence >= NOT_MEETS_CONFIDENCE_FLOOR
        ):
            continue
        verdicts[index] = RequirementVerdict(
            number=index,
            status=ComplianceStatus.NO_DATA.value,
            source=verdict.source,
            explanation=f"{verdict.explanation} [{UNSURE_EXPLANATION}]".strip()[:300],
            confidence=verdict.confidence,
        )


# Требование к трансформатору тока/напряжения — по названию параметра (до двоеточия):
# «Класс точности ТН», «Межповерочный интервал ТТ». «Счётчик трансформаторного включения»
# и «подключение через ТТ» — свойства самого счётчика, их не трогаем.
_TRANSFORMER_SUBJECT_RE = re.compile(r"\bт[тн]\b|трансформатор\w*\s+(?:тока|напряжени)", re.IGNORECASE)
_METER_CONNECTION_RE = re.compile(r"включени|подключ|через", re.IGNORECASE)
TRANSFORMER_EXPLANATION = (
    "требование к трансформатору тока/напряжения, а пояснение — о самом приборе учёта; "
    "проверить по паспорту трансформатора"
)


def is_transformer_requirement(requirement: Requirement) -> bool:
    text = requirement.normalized_text or requirement.text or ""
    subject = text.split(":", 1)[0]
    return bool(_TRANSFORMER_SUBJECT_RE.search(subject)) and not _METER_CONNECTION_RE.search(subject)


def apply_transformer_scope(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> None:
    """Вердикт по требованию к ТТ/ТН, который говорит о счётчике, а не о трансформаторе, —
    «нет данных».

    Закупка 32616469643 (15.10.2026, ВШУ в сборе с ПУ полукосвенного включения): «Класс
    точности ТН: не ниже 0,5» — «не соответствует: класс точности A1R1», «Межповерочный
    интервал ТТ» — «соответствует: у НАРТИС-И100 192 месяца». Модель сравнила требование к
    трансформатору с классом и интервалом счётчика, и эталон ТЗ «не проходил». Пояснение,
    которое говорит о самом трансформаторе (ПКУ с ТТ и ТН в каталоге), остаётся."""

    for index, requirement in enumerate(requirements, start=1):
        verdict = verdicts.get(index)
        if verdict is None or verdict.status == ComplianceStatus.NO_DATA.value:
            continue
        if not is_transformer_requirement(requirement):
            continue
        if _TRANSFORMER_RE.search(verdict.explanation or ""):
            continue
        verdicts[index] = RequirementVerdict(
            number=index,
            status=ComplianceStatus.NO_DATA.value,
            source=verdict.source,
            explanation=f"{verdict.explanation} [{TRANSFORMER_EXPLANATION}]".strip()[:300],
            confidence=0.4,
        )


_YES_VALUES = frozenset({"да", "есть", "имеется"})
_NO_VALUES = frozenset({"нет", "отсутствует", "не допускается"})
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _subject_and_value(requirement: Requirement) -> tuple[str, str] | None:
    text = requirement.normalized_text or ""
    if ":" not in text:
        return None
    subject, value = text.split(":", 1)
    return " ".join(subject.lower().split()), value.strip().lower().rstrip(".")


def _values_conflict(first: str, second: str) -> bool:
    """Два значения одного параметра, которые одному прибору не выполнить сразу: «да» и
    «нет», «230 В» и «400 В», «не хуже 1,0» и «не хуже 2,0». Перечни («Ethernet» и
    «USB») — не противоречие: интерфейсов у прибора бывает несколько."""

    if {first, second} <= _YES_VALUES | _NO_VALUES:
        return (first in _YES_VALUES) != (second in _YES_VALUES)
    numbers = [
        {float(item.replace(",", ".")) for item in _NUMBER_RE.findall(value)} for value in (first, second)
    ]
    return bool(numbers[0]) and bool(numbers[1]) and numbers[0] != numbers[1]


LOT_VARIANT_EXPLANATION = (
    "в закупке несколько позиций: требование «{other}» выполнено — это значение, вероятно, "
    "для другой позиции; проверить прибор для этой"
)


def apply_lot_variants(
    requirements: list[Requirement], verdicts: dict[int, RequirementVerdict]
) -> None:
    """Смешанный лот: «не соответствует» по значению, которое противоречит другому требованию
    того же параметра, выполненному производителем, — «нет данных».

    Закупка 32616469643 (15.10.2026): ВШУ с однофазным ПУ и ВШУ с трёхфазным ПУ
    полукосвенного включения — в одном списке «Номинальное напряжение: 230 В» и «400 В»,
    «сменный GSM-модуль: да» и «нет», реактивная энергия «не хуже 1,0» и «не хуже 2,0».
    Одному прибору все не выполнить, и каждый производитель «не проходил» по одной из пар:
    модель сравнивала требование трёхфазной позиции с однофазной моделью из карточки
    («230 В не соответствует 400 В»). Раз другое значение того же параметра выполнено,
    отказ — про чужую позицию, а не про производителя; проверка остаётся за человеком.
    Правила кода (`rule`, `tz`) не трогаем — они решают по производителю, а не по модели.

    С 15.10.2026 это страховка: требования с позицией спецификации сверяются только с
    моделями своей позиции (`_evaluate_manufacturer`, раздел 61 ARCHITECTURE.md), и
    эвристика их не касается — работает для требований без позиции (закупки, разобранные
    до появления позиций, и пропуски модели извлечения)."""

    parsed = {
        index: _subject_and_value(requirement)
        for index, requirement in enumerate(requirements, start=1)
        if not position_key(requirement.position)
    }
    met: dict[str, list[tuple[int, str]]] = {}
    for index, item in parsed.items():
        verdict = verdicts.get(index)
        if item is not None and verdict is not None and verdict.status == ComplianceStatus.MEETS.value:
            met.setdefault(item[0], []).append((index, item[1]))

    for index, item in parsed.items():
        verdict = verdicts.get(index)
        if item is None or verdict is None or verdict.status != ComplianceStatus.NOT_MEETS.value:
            continue
        if verdict.source in ("rule", "tz"):
            continue
        subject, value = item
        other = next(
            (other_index for other_index, other_value in met.get(subject, []) if _values_conflict(value, other_value)),
            None,
        )
        if other is None:
            continue
        other_text = requirements[other - 1].normalized_text or ""
        verdicts[index] = RequirementVerdict(
            number=index,
            status=ComplianceStatus.NO_DATA.value,
            source=verdict.source,
            explanation=(
                f"{verdict.explanation} [{LOT_VARIANT_EXPLANATION.format(other=other_text[:80])}]"
            ).strip()[:300],
            confidence=0.4,
        )


def _wording_matches(item, requirement: Requirement) -> bool:
    """Номер параметра с правилом кода ставит модель извлечения, и она ошибается: на закупке
    32616469643 (15.10.2026) «Дискретные входы: 6 шт.» получили параметр 26, и Нартис —
    эталон ТЗ — схлопотал «не соответствует: трёхпозиционное реле только у Энергомеры, Тайпит
    и Пульсара». Правило, решающее «не проходит» за модель, срабатывает только когда и сама
    формулировка требования узнаётся как этот параметр."""

    if not item.patterns:
        return True
    text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
    return any(pattern.search(text) for pattern in item.patterns)


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
            # Отказ по типу позиции решил код (`apply_position_kinds`) — снимать нечего.
            and current.source != "rule"
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
        if item is not None and not _wording_matches(item, requirement):
            continue
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


def catalog_models(db: Session, manufacturer: Manufacturer) -> list[CatalogModel]:
    """Модели производителя со значениями характеристик для предрешения кодом. Без
    руководства: оно — третий шаг сопоставления, только для оставшихся без ответа."""

    query = select(Product).where(Product.manufacturer_id == manufacturer.id)
    if manufacturer.is_mirtek:
        # Как и в карточке: снятой с производства моделью МИРТЕК требование не закрыть.
        query = query.where(Product.status == ProductStatus.ACTIVE.value)
    products = list(db.scalars(query.order_by(Product.model_name)))
    by_product: dict[uuid.UUID, list[ProductCharacteristic]] = {}
    if products:
        for characteristic in db.scalars(
            select(ProductCharacteristic).where(
                ProductCharacteristic.product_id.in_([product.id for product in products])
            )
        ):
            by_product.setdefault(characteristic.product_id, []).append(characteristic)
    models = []
    for product in products:
        characteristics = by_product.get(product.id, [])
        values = {
            item.field_name: item.value
            for item in characteristics
            if item.value and item.source != "user_manual"
        }
        if values:
            groups = {kind_group(code) for code in product_kinds(product, characteristics)}
            models.append(CatalogModel(product.model_name, values, groups))
    return models


def prejudge_numeric(
    db: Session,
    tender: Tender,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    *,
    groups: set[str] | None = None,
) -> dict[int, RequirementVerdict]:
    """Вердикты кодом по однозначным числовым требованиям (номер → вердикт): «соответствует»
    по факту из каталога, у МИРТЕК по току и тарифам — и «не соответствует» (22.10.2026);
    остальное — модели (см. `compliance_numeric`). Сверяется формулировка ТЗ, если
    сокращённая потеряла часть условий (`wording_for_code`).

    `groups` — типы позиции спецификации; без них — типы всей закупки."""

    texts = [
        (index, wording_for_code(item.text, item.normalized_text))
        for index, item in enumerate(requirements, start=1)
    ]
    if not any(requirement_checks(text) for _, text in texts):
        return {}
    models = catalog_models(db, manufacturer)
    if groups is None:
        groups = {kind_group(code) for code in (tender.meter_kinds or [])} or None
    verdicts: dict[int, RequirementVerdict] = {}
    for index, text in texts:
        found = prejudge(text, models, groups, NUMERIC_ENABLED, decisive=manufacturer.is_mirtek)
        if found is not None:
            verdicts[index] = RequirementVerdict(
                number=index,
                status=found.status,
                source="catalog",
                explanation=found.explanation,
                confidence=PREJUDGED_CONFIDENCE,
            )
    return verdicts


def _ask_in_batches(
    db: Session,
    manufacturer: Manufacturer,
    requirements: list[Requirement],
    context: str,
    outcome: ComplianceOutcome,
    *,
    position: str | None = None,
) -> dict[int, RequirementVerdict]:
    """Спрашивает модель пачками по `REQUIREMENTS_PER_REQUEST` и склеивает вердикты,
    пересчитывая локальные номера пачки в сквозные.

    Неудача одной пачки не отменяет остальные: требования из неё останутся без вердикта
    (и получат `no_data` выше по стеку), но соседние пачки сохранятся — это лучше, чем
    терять всего производителя из-за одного оборванного ответа."""

    verdicts: dict[int, RequirementVerdict] = {}
    for offset in range(0, len(requirements), REQUIREMENTS_PER_REQUEST):
        batch = requirements[offset : offset + REQUIREMENTS_PER_REQUEST]
        # Каждой пачке — своя выборка из карточки (14.10.2026, `focus_context`).
        batch_context = focus_context(context, batch, _focus_limit(db, batch))
        batch_verdicts = _ask_model(db, manufacturer, batch, batch_context, outcome, position=position)
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
    *,
    position: str | None = None,
) -> dict[int, RequirementVerdict]:
    # Карточка — первой, требования — после (06.10.2026): одинаковое начало запроса провайдер
    # берёт из кэша (у DeepSeek — автоматически, в разы дешевле). С 14.10.2026 карточка у
    # пачек своя (`focus_context`), и из кэша идёт системный промпт, а карточка — только
    # когда она короче предела и уходит целиком; зато она втрое короче.
    user_text = (
        f"КАРТОЧКА ПРОИЗВОДИТЕЛЯ «{manufacturer.brand_name or manufacturer.legal_name}»:\n{context}\n\n"
        + (
            f"ПОЗИЦИЯ СПЕЦИФИКАЦИИ: {position} — все требования ниже относятся к этому изделию.\n"
            if position
            else ""
        )
        + f"ТРЕБОВАНИЯ ЗАКУПКИ:\n{_requirements_block(requirements)}"
    )
    try:
        result = run_structured(
            db,
            system_prompt=_SYSTEM_PROMPT,
            user_text=user_text,
            response_model=ComplianceResult,
        )
    except (AiQuotaExceededError, AiNotConfiguredError):
        # Деньги кончились или модель выключена — остальные запросы упрутся в то же самое.
        # Расчёт останавливается с ошибкой: посчитанные производители уже сохранены, а
        # продолжение досчитает остальных. Раньше задача шла дальше, на каждый запрос
        # получала отказ и завершалась «успешно» с пустой матрицей (06.10.2026).
        raise
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


def _labelled(requirement: Requirement, limit: int) -> str:
    """Формулировка для итога производителя — с позицией спецификации, если она есть: в
    закупке из двух приборов «Номинальное напряжение: 400 В» без позиции не объясняет,
    какой из них не подошёл."""

    text = (requirement.normalized_text or requirement.text or "")[:limit]
    position = position_key(requirement.position)
    if not position:
        return text
    label = f"поз. {position}" if position.isdigit() else (requirement.position or "")[:40]
    return f"{label}: {text}"


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
    *,
    reference: TzReference | None = None,
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
            _labelled(requirement, 120) for requirement in failed_critical[:3]
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
    if reference is not None:
        reason = f"Эталон ТЗ — {reference.label}. {reason}"

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
            tz_reference=reference is not None,
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
        return _labelled(requirement, 90)

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
        elif status == ComplianceStatus.PARTIAL.value and is_model_requirement(requirement):
            # Эквивалент эталонной модели ТЗ — оговорка при любой критичности: модель
            # извлечения ставит «Модель прибора» и «важным», а «проходит» наравне с эталоном
            # по нему нельзя (`apply_reference_rules`, замечание 13.10.2026).
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
        f"решено кодом без модели: {outcome.prejudged}; "
        f"требуют проверки человеком: {outcome.needs_review}"
    )
    if outcome.unanswered_text():
        details = f"Внимание, {outcome.unanswered_text()}; {details}"
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
