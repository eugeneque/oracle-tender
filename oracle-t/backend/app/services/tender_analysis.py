"""ИИ-анализ тендера: требования, критичность, тип конкурса, ОКПД2, регион
(раздел 5.4 ТЗ — Этап 5).

Разделение труда между кодом и моделью здесь сделано осознанно, а не «всё отдадим ИИ»:

- **Код** делает то, что проверяемо и должно быть воспроизводимо: ищет коды ОКПД2 по
  регулярному выражению, сверяет регион со справочником Приложения H, выводит федеральный
  округ по региону, взвешивает критичность в проценте победителя.
- **Модель** делает то, что кодом не берётся: вычленяет требования из сплошного текста
  документации, приводит формулировку к нормальному виду, оценивает критичность и относит
  требование к группе характеристик Приложения C.

Там, где сходятся оба, приоритет у кода: если ОКПД2 найден регулярным выражением в тексте
документа — берём его, а не тот, который «вспомнила» модель. Модель ошибается в цифрах
(галлюцинирует правдоподобные коды вида 26.51.63.120 вместо 26.51.63.130), а ОКПД2 —
основной фильтр релевантности (раздел 5.4 ТЗ, п.5), и тихая ошибка здесь дорого стоит.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

import pydantic
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Criticality, Requirement, RequirementKind
from app.models.log import LogLevel
from app.models.region import Region
from app.models.tender import Tender, TenderType
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.user import User
from app.seed.characteristics_data import CHARACTERISTIC_GROUPS
from app.seed.meter_parameters import PARAMETERS_BY_NO, detect_parameter, parameters_prompt_list
from app.services.audit import log_action
from app.services.document_service import classify_document, reextract_stale_documents
from app.services.ai_client import AiQuotaExceededError, chunk_text, run_structured
from app.services.meter_kind import METER_KINDS, fill_tender_kinds
from app.services import okpd2_service

# Кусок текста на один запрос. Тендерная документация длиннее «Описания типа», а системный
# промпт здесь короче (список групп, а не всех ~120 полей), поэтому кусок крупнее, чем в
# `characteristic_extraction`.
MAX_CHUNK_CHARS = 14_000

# Сколько кусков документа разбирать максимум. Тендерная документация бывает на сотни страниц
# (сметы, ведомости объёмов), и без предела один тендер мог бы съесть сотню запросов к
# модели. Шестнадцать, а не двенадцать, как было: на проекте договора ИСУ из заявки
# (см. `_DEVICE_TERMS_RE`) техническое задание занимает пять кусков из восемнадцати, и при
# двенадцати его начало — с требованием поддержки СПОДЭС — в отбор не проходило.
MAX_CHUNKS_PER_TENDER = 16

# Когда кусков больше предела, в модель уходят не первые попавшиеся, а те, где о приборе
# говорится больше всего (см. `_select_chunks`). Слова — признаки требования к прибору учёта,
# а не к договору вообще: проект договора ИСУ на сотню страниц (заявка «Новосибирскэнергосбыт»,
# 15.09.2026) — это 238 тысяч символов, из которых техническое задание с характеристиками
# приборов лежит в приложении на восьмидесятой странице, за пределом первых двенадцати кусков.
_DEVICE_TERMS_RE = re.compile(
    r"прибор[а-я]* учет|прибор[а-я]* учёт|сч[её]тчик|\bпу\b|\bипу\b|\bси\b|"
    r"класс[а-я]* точност|номинальн|напряжен|\bток[а-я]*\b|интерфейс|протокол|спод[эе]с|"
    r"\brs-?485\b|\bplc\b|\bgsm\b|\bnb-?iot\b|\blora|реле|госреестр|реестр[а-я]* си\b|"
    r"поверк|межповерочн|тариф|трансформатор|характеристик|требовани[яйе] к|"
    r"техническ[а-я]* задани|спецификац|фаз[а-я]*\b|исполнени[ея]\b|корпус|дисплей|"
    r"пломб|температур|гост\b|срок[а-я]* службы|гаранти|"
    # Закупки на работы и услуги (18.09.2026): техническое обслуживание, монтаж, АИИС КУЭ.
    # Без этих слов куски ТЗ на обслуживание проигрывали в ранжировании проекту договора.
    r"обслуживани|ремонт|монтаж|аскуэ|аиис|успд|допуск|лиценз|\bсро\b|квалификац|"
    r"периодичност|регламентн",
    re.IGNORECASE,
)

# Код ОКПД2: XX.XX[.XX[.XXX]]. Ищутся только коды **от трёх групп** — и это не придирка к
# форме, а вывод из живого прогона по всем закупкам с документами (05.09.2026). Двухгрупповых
# кодов там не нашлось ни одного настоящего, зато нашлось десять ложных: «19.28», «55.14»,
# «10.22», «34.01» — номера пунктов, приложений и строк смет, которые формой от кода не
# отличаются. Настоящие же коды все были полными: 26.51.63.130, 43.21.10.220, 26.51.45.190.
# Заказчик, указывая ОКПД2, пишет его целиком.
_OKPD2_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{1,2}(?:\.\d{3})?\b")

# Двухгрупповой код принимается только рядом с явным упоминанием классификатора — «ОКПД2
# 26.51». Так его иногда указывают в извещении, и терять этот случай не хочется, а в отрыве
# от маркера две группы цифр значат что угодно.
_OKPD2_LABELLED_RE = re.compile(
    r"ОКПД\s*-?\s*2?\s*[:№—–-]?\s*(\d{2}\.\d{2}(?:\.\d{1,2})?(?:\.\d{3})?)",
    re.IGNORECASE,
)

# Даты в документах выглядят как коды: «Дата подведения итогов: 23.12.2026» давала ОКПД2
# «23.12» (стекло обработанное) для тендера на метрологические услуги. Даты маскируются ДО
# поиска кодов — иначе их не отличить: у «23.12» и «23.12.2026» общее начало, и проверка
# самого совпадения не помогает.
# Хвост — `(?!\d)`, а не `\b`: в документах дата почти всегда слипается с буквой
# («от 16.08.2024г.»), а между цифрой и кириллической буквой границы слова нет — такая дата
# не маскировалась и давала «ОКПД2 16.08» на закупке счётчиков.
_DATE_CANDIDATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})(?!\d)")

# Приоритетный код (раздел 5.4 ТЗ, п.5) — счётчики электроэнергии. Если в документе встретился
# он, берём его, даже когда рядом перечислены другие: тендер на счётчики может упоминать
# сопутствующие коды (монтажные работы, кабель), но релевантность определяет этот.
PRIORITY_OKPD2 = "26.51.63.130"

# Коды, по которым закупается продукция производителей из раздела 4.3 ТЗ. Порядок = приоритет:
# три категории электросчётчиков (.130, .131, .139), вода, газ, затем ветка приборов учёта.
RELEVANT_OKPD2_PREFIXES = okpd2_service.RELEVANT_PREFIXES

_TENDER_TYPE_VALUES = {t.value for t in TenderType}
_CRITICALITY_VALUES = {c.value for c in Criticality}
_KIND_VALUES = {k.value for k in RequirementKind}

_REQUIREMENTS_SYSTEM_PROMPT = """Ты анализируешь документацию закупки в области учёта \
электроэнергии — поставка приборов учёта (счётчиков электроэнергии, воды, газа, тепла), \
их монтаж, замена, поверка, обслуживание, ремонт, создание и обслуживание систем \
АСКУЭ/АИИС КУЭ — и извлекаешь из неё требования.

Требование — любое проверяемое условие. Их три вида, и у каждого требования вид указывается \
в поле `kind`:
- `product` — к товару: характеристики прибора, комплектация, документы на товар, порядок \
поставки. Например: класс точности, номинальное напряжение, наличие реле, тип интерфейса, \
поддержка протокола, наличие поверки, срок гарантии на прибор, наличие в Госреестре СИ. \
Строка спецификации, ведомости или перечня закупаемого товара — ТОЖЕ требование к товару, \
даже если она состоит из одного наименования: «Счетчик СЕ208, 30 шт.» означает требование \
к типу прибора, и его нужно извлечь — в закупке у единственного поставщика такая строка \
часто единственное, что вообще сказано о товаре.
- `service` — к работам и услугам: состав и объём работ, периодичность обслуживания, сроки \
выполнения, время реакции на заявку, гарантия на выполненные работы и установленные части, \
порядок приёмки и отчётности, требования к безопасности выполнения работ, к используемым \
запасным частям и материалам.
- `participant` — к участнику закупки: лицензии, допуски СРО, членство в реестрах, опыт \
аналогичных договоров, квалификация и группа допуска персонала, наличие аттестованной \
лаборатории или средств поверки, отсутствие в РНП.
- `supply` — условия поставки, которые выполняет поставщик независимо от того, чей прибор \
он везёт: товар новый, не восстановленный, год выпуска, дата поверки, первичная поверка \
в стоимости, упаковка, маркировка и опломбировка тары, документы при отгрузке (паспорт, \
сертификаты, УПД), серийные номера, сроки и порядок доставки, приёмка, претензии, замена \
брака, возврат стоимости, информация о сервисных центрах. Отличие от `product`: \
требование `product` выполняется или нет В ЗАВИСИМОСТИ ОТ МОДЕЛИ прибора (характеристика, \
функция, комплектация самого прибора, гарантийный срок, наличие в реестрах, программное \
обеспечение), требование `supply` — нет.

Если в закупке нет товара (только услуги), требований `product` не будет — это нормально; \
извлекай `service` и `participant`. Если есть только товар — наоборот.

Что НЕ является требованием и должно быть пропущено: реквизиты сторон, суммы и порядок \
оплаты, сроки подачи заявок и порядок проведения закупки, определения терминов, \
обязательства заказчика, общие фразы без проверяемого условия («услуги должны быть \
качественными»).

Правила заполнения полей:
1. `text` — формулировка ИЗ ДОКУМЕНТА, дословно, без сокращений и пересказа.
2. `normalized_text` — то же требование коротко и однозначно, в виде «параметр: значение». \
Например: «Класс точности активной энергии: не хуже 1,0», «Гарантия на работы: не менее \
12 месяцев», «Группа допуска по электробезопасности: не ниже IV до и выше 1000 В».
3. `kind` — строго одно из: product, service, participant, supply.
4. `criticality` — строго одно из: critical, important, minor.
   - critical: без выполнения заявку отклонят (класс точности, номинальные ток/напряжение, \
наличие в Госреестре СИ, тип прибора, количество фаз, допуски-реестры: реестр российской \
промышленной продукции по ПП 719, заключение аттестационной комиссии ПАО «Россети», \
реестр российского ПО; для услуг — обязательные лицензии и допуски, состав работ; для \
участника — лицензии, СРО, обязательный опыт);
   - important: существенно влияет на выбор (интерфейсы, протоколы, реле, температурный \
диапазон, межповерочный интервал; сроки и периодичность работ, гарантия на работы, \
квалификация персонала);
   - minor: второстепенное (цвет корпуса, тип упаковки, форма отчётности, пожелания без \
«должен»).
5. `group_name` — для требований `product` группа характеристик из справочника ниже, к \
которой относится требование. Для `service` и `participant`, и если ни одна группа не \
подходит, — пустая строка.
6. Не придумывай требований, которых нет в тексте. Если требований нет — верни пустой список.
7. `parameter_no` — для требований `product` номер параметра из перечня «Параметры для \
приборов учёта» ниже (перечень тендерного отдела, по нему проверяется каждое ТЗ на прибор \
учёта). Если требование не относится ни к одному параметру, а также для `service` и \
`participant` — 0. Руководствуйся комментариями к параметрам.
8. Одно требование — один параметр. Если в ТЗ параметры записаны вместе («Номинальный \
(максимальный) ток 5(60) А», «Габариты и масса»), раздели их на отдельные требования: \
«Номинальный ток: 5 А» (параметр 6) и «Максимальный ток: 60 А» (параметр 7).
9. Для параметра 1 (тип прибора) в `normalized_text` назови тип одним из видов: \
{meter_kinds}. Вид определяй по комментариям к параметрам 5, 6, 7, 14 и 32 \
(напряжение, токи, способ крепления, выносной индикатор). Если вид из текста не \
следует — оставь формулировку ТЗ, не угадывай.
10. Для параметра 3 (габариты) записывай размеры в порядке ширина (b) × длина (a) × \
высота (c), как в перечне, и указывай, «не более» это или точное значение.
11. Обозначение прибора. Если документ называет конкретную модель или исполнение прибора \
(«CE207 R7.849.2.OG.QUVLF GS01 SPDs (или эквивалент)», «МИР С-05.10-230-5(80)», «Меркурий \
234 ARTM»), извлеки это ОТДЕЛЬНЫМ требованием `product`: `normalized_text` — «Модель \
прибора: <обозначение точно как в документе>», и допиши « (или эквивалент)», только если \
в документе так и сказано; `parameter_no` — 0, `criticality` — critical. Обозначение \
переписывай посимвольно: латиница и кириллица в нём не взаимозаменяемы. В остальных \
требованиях обозначение тоже не выбрасывай, если оно там есть.
12. `vendor` — производитель, которому принадлежит названное в требовании фирменное \
программное обеспечение, сервис, протокол, оборудование или модель прибора. Примеры: \
«работа через ПО AdminTools», «поддержка M2M-сервера CE Net-Connection» — Энергомера; \
«Модель прибора: CE207 …» — Энергомера; «конфигуратор MeterTools» — МИРТЕК (если так \
следует из твоих знаний). Называй производителя, как он назван в списке производителей \
ниже, если он там есть. Если требование нейтрально (класс точности, СПОДЭС, RS-485, \
GSM, ПО верхнего уровня «Пирамида», «Энергосфера» и другие системы, которые \
поддерживают приборы многих производителей) — пустая строка. Не угадывай: если не \
уверен, кому принадлежит программа, — пустая строка.
13. `vendor_exclusive` — true, если требование может выполнить ТОЛЬКО продукция `vendor`: \
фирменная программа или сервис производителя, без оговорки «или эквивалент» \
(«Работа со счётчиком через интерфейсы связи производится с применением ПО \
«AdminTools»»). Обозначение модели с «или эквивалент» — false: эквивалент другого \
производителя допустим. Если `vendor` пустой — false.
14. Один и тот же параметр в одном фрагменте извлекай один раз, даже если документ \
повторяет его в разных разделах (технические характеристики, функциональные требования, \
спецификация): возьми самую полную формулировку.

Производители приборов учёта в справочнике:
{manufacturers_list}

Параметры для приборов учёта:
{parameters_list}

Справочник групп характеристик:
{groups_list}"""

_CLASSIFY_SYSTEM_PROMPT = """Ты классифицируешь закупку приборов учёта по типу и извлекаешь \
из её текста служебные поля.

`tender_type` — строго одно из значений:
- supply_only — закупается ТОЛЬКО поставка приборов учёта, без монтажа;
- complex — поставка приборов И работы по их установке/монтажу;
- works_only — ТОЛЬКО работы по установке/монтажу/замене приборов, без поставки;
- reverification — повторная (периодическая) поверка уже установленных приборов;
- other — ничего из перечисленного (наладка без монтажа, аренда приборов, разработка ПО учёта).

`okpd2_code` — код ОКПД2 закупаемого товара, если он прямо указан в тексте, иначе пустая \
строка. Не угадывай и не додумывай код: пустая строка лучше неверного кода.

`region_name` — субъект РФ заказчика (например, «Ростовская область», «Республика Татарстан»), \
если он следует из адреса заказчика или места поставки, иначе пустая строка.

`delivery_region_name` — субъект РФ места поставки, если он указан ОТДЕЛЬНО и отличается от \
региона заказчика, иначе пустая строка.

`summary` — 1-2 предложения: что закупают и на что обратить внимание поставщику приборов учёта.
"""


class ExtractedRequirement(pydantic.BaseModel):
    """Поля объявлены **обязательными**, без значений по умолчанию, и это не формальность.

    Со значениями по умолчанию модель их просто не заполняла: в JSON Schema они становятся
    необязательными, и на реальном прогоне все 86 извлечённых требований вернулись с
    критичностью «important» и пустой группой — то есть с дефолтами, а не с оценкой модели.
    Критичность при этом входит в формулу процента победителя (раздел 5.5 ТЗ), так что
    молчаливая подстановка одного значения на все требования обесценивала расчёт.
    """

    text: str
    normalized_text: str
    kind: str
    criticality: str
    group_name: str
    parameter_no: int
    vendor: str
    vendor_exclusive: bool


class RequirementsResult(pydantic.BaseModel):
    requirements: list[ExtractedRequirement]


class ClassificationResult(pydantic.BaseModel):
    """Все поля обязательные — по той же причине, что и в `ExtractedRequirement`: поле со
    значением по умолчанию модель вправе не заполнять. Там, где ответа нет, промпт требует
    пустую строку — это осознанный «не знаю», а не молчаливый дефолт."""

    tender_type: str
    okpd2_code: str
    region_name: str
    delivery_region_name: str
    summary: str


_CONSOLIDATE_SYSTEM_PROMPT = """Ты получаешь пронумерованный список требований, извлечённых \
из документации ОДНОЙ закупки по фрагментам. Одно и то же требование в нём встречается \
несколько раз: техническое задание повторяется в проекте договора и в извещении, а внутри \
ТЗ один параметр описан и в технических характеристиках, и в функциональных требованиях, \
и в спецификации.

Найди группы дубликатов — требования, которые проверяют ОДНО И ТО ЖЕ условие с одним и \
тем же значением, сказанное разными словами: «Гарантийный срок: не менее 7 лет» и \
«Гарантийный срок эксплуатации — не менее 7 лет с даты подписания УПД»; «Протокол обмена: \
СПОДЭС» и «Протокол передачи: СПОДЭС»; «Встроенный GSM/GPRS-модем» и «Интерфейсы связи: \
встроенный GSM/GPRS модем».

Дубликат и тогда, когда одно требование ПОКРЫВАЕТ другое: та же проверка, но с \
подробностями. «Наличие реле управления нагрузкой: 80 А», «Встроенный расцепитель (реле \
отключения нагрузки) 80 А» и «Встроенный расцепитель с управлением отключением по \
программируемым критериям» — одна группа, оставь самое подробное. «Интерфейсы связи: \
GSM/GPRS, оптопорт» и «Интерфейсы связи: встроенный GSM/GPRS модем, оптопорт» — одна \
группа. «Ёмкость журнала событий: не менее 100» и «Состав журнала событий …; ёмкость не \
менее 100» — одна группа. Объединять по покрытию можно, только если оставленное \
требование содержит ВСЁ, что проверяет поглощённое.

НЕ дубликаты, их не объединяй:
- разные параметры одного узла: базовый и максимальный ток; глубина хранения месячных и \
суточных энергий; класс точности по активной и по реактивной энергии;
- одно условие с разными значениями: «гарантия не менее 5 лет» и «не менее 7 лет» — \
противоречие в документации должен увидеть человек;
- общее и частное: «Интерфейсы связи: GSM/GPRS, оптопорт» и «Наличие слота для \
SIM-карты» — разные проверки;
- требования разного вида (к товару, к работам, к участнику, условия поставки).

Для каждой группы укажи `keep` — номер самой полной и точной формулировки, и \
`duplicates` — номера остальных требований группы. Требования без дубликатов не \
перечисляй. Если дубликатов нет — пустой список."""


class DuplicateGroup(pydantic.BaseModel):
    keep: int
    duplicates: list[int]


class ConsolidationResult(pydantic.BaseModel):
    groups: list[DuplicateGroup]


@dataclass
class AnalysisOutcome:
    """Итог анализа для отчёта пользователю. Отдельно считаются пропущенные требования:
    молчаливая потеря части списка выглядела бы как «в тендере мало требований»."""

    requirements_saved: int = 0
    # Сколько требований какого вида (`RequirementKind`) — в сообщение задачи: «требований:
    # 0» на закупке услуг раньше читалось как сбой, «к товару: 0, к услугам: 14» — как итог.
    requirements_by_kind: dict[str, int] = field(default_factory=dict)
    requirements_skipped: int = 0
    # Сколько повторов убрано: дословных (по нормализованной формулировке) и смысловых
    # (проход модели `_consolidate`).
    duplicates_removed: int = 0
    chunks_processed: int = 0
    chunks_failed: int = 0
    tender_type: str | None = None
    okpd2_code: str | None = None
    region_code: str | None = None
    messages: list[str] = field(default_factory=list)


_KIND_SHORT_LABELS = {
    RequirementKind.PRODUCT.value: "к товару",
    RequirementKind.SERVICE.value: "к услугам",
    RequirementKind.PARTICIPANT.value: "к участнику",
    RequirementKind.SUPPLY.value: "условия поставки",
}


def kinds_summary(outcome: AnalysisOutcome) -> str:
    """« (к товару: 3, к услугам: 14)» — или пустая строка, когда требований нет."""

    if not outcome.requirements_by_kind:
        return ""
    parts = [
        f"{_KIND_SHORT_LABELS[kind]}: {outcome.requirements_by_kind[kind]}"
        for kind in _KIND_SHORT_LABELS
        if outcome.requirements_by_kind.get(kind)
    ]
    return f" ({', '.join(parts)})"


def _groups_list() -> str:
    return "\n".join(f"  - {group}" for group in CHARACTERISTIC_GROUPS)


def _mask_date(match: re.Match) -> str:
    """Заменяет дату пробелом, а похожий на дату код ОКПД2 оставляет как есть.

    Отличаются они по смыслу чисел, а не по форме: у даты первое число — день (1–31), второе
    — месяц (1–12). «23.12.2026» под это подходит, а «71.12.40» (услуги в области метрологии)
    и «26.51.63» (приборы учёта) — нет: 71 не бывает днём, 51 не бывает месяцем. Слепая
    маска по форме съедала как раз настоящие коды.
    """

    day, month, _year = (int(part) for part in match.groups())
    is_date = 1 <= day <= 31 and 1 <= month <= 12
    return " " if is_date else match.group(0)


def _is_plausible_okpd2(code: str) -> bool:
    """Отсеивает то, что похоже на код формой, но им не является.

    Проверяется раздел классификатора: в ОКПД2 их 01–99, причём коды вида `00.x` не
    существуют. Этого хватает, чтобы не пропустить остатки нумерации пунктов («п. 4.2.1»
    сюда и так не попадает — там одна цифра в начале), и при этом не завести справочник всех
    разделов, который пришлось бы поддерживать.
    """

    head, *tail = code.split(".")
    if not (head.isdigit() and 1 <= int(head) <= 99):
        return False
    # Нулевых группировок в ОКПД2 нет — нумерация внутри раздела начинается с единицы.
    # Без этой проверки временем «до 16.00» подменялся код товара.
    return all(int(part) != 0 for part in tail if part.isdigit())


def is_relevant_okpd2(code: str | None) -> bool:
    """Относится ли код к продукции производителей раздела 4.3 ТЗ — основной фильтр
    релевантности тендера (раздел 5.4 ТЗ, п.5)."""

    if not code:
        return False
    return any(code.startswith(prefix) for prefix in RELEVANT_OKPD2_PREFIXES)


def extract_okpd2_from_text(text: str) -> str | None:
    """Код ОКПД2 из текста документа — регулярным выражением, а не моделью (см. докстринг
    модуля). Приоритетный код счётчиков электроэнергии выигрывает у остальных, найденных
    в том же документе; иначе берётся первый код, попадающий в релевантную иерархию
    (Приложение F), а при полном их отсутствии — первый найденный код вообще."""

    # Сначала выкидываем даты, потом ищем коды: см. `_DATE_CANDIDATE_RE` и `_mask_date`.
    cleaned = _DATE_CANDIDATE_RE.sub(_mask_date, text or "")

    # Помеченные словом «ОКПД» — прямое указание заказчика, им доверяем без оговорок.
    labelled = [code for code in _OKPD2_LABELLED_RE.findall(cleaned) if _is_plausible_okpd2(code)]

    # Непомеченные берём, только если код попадает в нашу нишу (Приложение F). Причина в
    # живом прогоне 05.09.2026: по форме код ОКПД2 неотличим от сметной расценки и номера
    # пункта, и «91.05.01» (шифр машин и механизмов в смете) уходил в поле кода тендера на
    # замену приборов учёта, а «10.15.1» — тендера на антикоррозионную защиту. Ложный код
    # тише отсутствующего: он молча искажает основной фильтр релевантности (раздел 5.4 ТЗ,
    # п.5). Последовательности вида «26.51.63.130» такой опасности не несут — диапазон узкий,
    # и случайный шифр в него не попадает. Всё остальное оставляем модели: она видит
    # содержание закупки, а не одну лишь форму числа.
    unlabelled = [
        code
        for code in _OKPD2_RE.findall(cleaned)
        if _is_plausible_okpd2(code) and is_relevant_okpd2(code)
    ]

    codes = labelled + [code for code in unlabelled if code not in labelled]
    if not codes:
        return None

    if PRIORITY_OKPD2 in codes:
        return PRIORITY_OKPD2

    for prefix in RELEVANT_OKPD2_PREFIXES:
        for code in codes:
            if code.startswith(prefix):
                return code
    return codes[0]


def match_region(db: Session, name: str | None) -> Region | None:
    """Регион по названию из текста — сверкой со справочником Приложения H, а не доверием
    к формулировке модели. Названия в документах пишут по-разному («Респ. Татарстан»,
    «Республика Татарстан», «г. Москва»), поэтому сравнение идёт по значащей части названия
    без типа субъекта."""

    if not name or not name.strip():
        return None

    needle = _region_key(name)
    if not needle:
        return None

    regions = list(db.scalars(select(Region)))
    for region in regions:
        if _region_key(region.name) == needle:
            return region
    # Частичное совпадение — на случай «Ханты-Мансийский автономный округ» против
    # «Ханты-Мансийский автономный округ - Югра» в справочнике.
    for region in regions:
        key = _region_key(region.name)
        if key and (key.startswith(needle) or needle.startswith(key)):
            return region
    return None


_REGION_NOISE_RE = re.compile(
    r"\b(республика|область|край|автономный|автономная|округ|город|обл|респ|г|ао)\b\.?",
    re.IGNORECASE,
)


def _region_key(name: str) -> str:
    cleaned = name.lower().replace("ё", "е")
    cleaned = _REGION_NOISE_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[^0-9a-zа-я\- ]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _tender_text(db: Session, tender: Tender) -> tuple[str, uuid.UUID | None]:
    """Текст для анализа: наименование тендера плюс извлечённый текст его документов.

    Наименование добавляется всегда — у части тендеров документы недоступны без регистрации
    на площадке (раздел 4.1 ТЗ), и тогда заголовок остаётся единственным источником, из
    которого вообще можно определить тип закупки."""

    documents = list(
        db.scalars(
            select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id)
            .order_by(TenderDocument.downloaded_at)
        )
    )
    parts = [tender.title or ""]
    primary_document_id: uuid.UUID | None = None
    for document in documents:
        if document.extracted_text:
            if primary_document_id is None:
                primary_document_id = document.id
            parts.append(f"\n\n=== Документ: {document.file_name} ===\n{document.extracted_text}")
    return "\n".join(part for part in parts if part.strip()), primary_document_id


def analyze_tender(db: Session, tender: Tender, *, actor: User | None) -> AnalysisOutcome:
    """Полный анализ тендера (раздел 5.4 ТЗ). Повторный запуск заменяет ранее извлечённые
    требования: документация тендера может обновиться, и смешивать старые требования с
    новыми нельзя. Подтверждённые человеком требования сохраняются — правка человека имеет
    приоритет над автоматикой (тот же принцип, что и в справочнике продукции)."""

    outcome = AnalysisOutcome()
    # Документы, разобранные устаревшими правилами, читаются заново из хранилища: иначе
    # улучшения разбора действовали бы только на вновь собранные тендеры.
    reextracted = reextract_stale_documents(db, tender)
    if reextracted:
        outcome.messages.append(f"Документов переразобрано по новым правилам: {reextracted}")

    text, primary_document_id = _tender_text(db, tender)
    if not text.strip():
        outcome.messages.append("У тендера нет ни наименования, ни распознанных документов")
        _log(db, tender, outcome, actor, level=LogLevel.WARNING)
        return outcome

    _classify(db, tender, text, outcome)
    _extract_requirements(db, tender, text, primary_document_id, outcome)
    # Тип прибора уточняется по извлечённым требованиям к товару: в наименовании закупки
    # его обычно нет, а в ТЗ есть напряжение, токи и крепление.
    db.flush()
    fill_tender_kinds(db, tender)

    db.commit()
    db.refresh(tender)
    _log(db, tender, outcome, actor)
    return outcome


def _classify(db: Session, tender: Tender, text: str, outcome: AnalysisOutcome) -> None:
    """Тип конкурса, ОКПД2 и регион. Модель отвечает по началу документации: тип закупки и
    реквизиты заказчика всегда в её первой части, а гонять по всему тексту дорого и незачем."""

    head = text[:MAX_CHUNK_CHARS]
    try:
        result = run_structured(
            db,
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_text=head,
            response_model=ClassificationResult,
        )
    except Exception as exc:  # noqa: BLE001 - недоступность модели не должна ронять анализ целиком
        logger.warning(f"Классификация тендера {tender.external_id} не удалась: {exc}")
        outcome.messages.append(f"Классификация не выполнена: {exc}")
        result = None

    if result is not None:
        if result.tender_type in _TENDER_TYPE_VALUES:
            tender.tender_type = result.tender_type
            outcome.tender_type = result.tender_type
        elif result.tender_type:
            outcome.messages.append(
                f"Модель вернула неизвестный тип конкурса «{result.tender_type}» — поле оставлено пустым"
            )
        if result.summary:
            tender.ai_comment = result.summary

    # ОКПД2: сначала код из самого текста (детерминированно), и только если его там нет —
    # то, что предложила модель. См. докстринг модуля.
    code = extract_okpd2_from_text(text)
    if code is None and result is not None and result.okpd2_code:
        candidate = result.okpd2_code.strip()
        code = candidate if _OKPD2_RE.fullmatch(candidate) else None
        if code:
            outcome.messages.append("Код ОКПД2 взят из ответа модели — в тексте он не найден")
    if code:
        tender.okpd2_code = code
        outcome.okpd2_code = code

    if result is not None:
        organizer_region = match_region(db, result.region_name)
        if organizer_region is not None and tender.region_organizer_code is None:
            tender.region_organizer_code = organizer_region.code
            # ФО выводится по региону справочником, а не спрашивается у модели (раздел 5.4
            # ТЗ, п.6: «вычисляется по региону организатора»).
            tender.federal_district_code = organizer_region.federal_district_code
            outcome.region_code = organizer_region.code
        elif organizer_region is not None and organizer_region.code != tender.region_organizer_code:
            # Регион организатора уже определён по адресу заказчика из карточки закупки —
            # это более надёжный источник, чем регион, упомянутый в тексте документации: там
            # чаще всего названо место поставки. Перезапись давала расхождение вида
            # «заказчик — Россети Юг (Ростов), регион организатора — Краснодарский край»,
            # а от этого поля зависят ФО и ответственный по региону в Excel-выгрузке
            # (Приложение D ТЗ).
            outcome.messages.append(
                f"Регион организатора оставлен прежним ({tender.region_organizer_code}): "
                f"в документации упомянут {organizer_region.name}, но адрес заказчика надёжнее"
            )
            outcome.region_code = tender.region_organizer_code

        delivery_region = match_region(db, result.delivery_region_name)
        # Если регион поставки отдельно не указан — дублируется регион организатора
        # (раздел 5.4 ТЗ, п.6).
        tender.region_delivery_code = (
            delivery_region.code if delivery_region is not None
            else (organizer_region.code if organizer_region is not None else None)
        )


# Заголовок раздела внутри склеенного текста: `_tender_text` помечает так каждый документ
# тендера, а `document_extraction` — каждый файл внутри архива.
_SECTION_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)

# Порядок разбора документов комплекта. Требования к прибору лежат в техническом задании и
# спецификации; смета, проект договора и протоколы занимают больше всего страниц, а
# проверяемых условий к товару не содержат почти никогда. Читается ограниченное число кусков
# (`MAX_CHUNKS_PER_TENDER`), поэтому важно, что попадёт в них первым.
_CLASS_ORDER: dict[str, int] = {
    DocumentClass.TZ_DESCRIPTION.value: 0,
    DocumentClass.NOTICE.value: 1,
    DocumentClass.OTHER.value: 2,
    DocumentClass.CONTRACT.value: 3,
    DocumentClass.SSR.value: 4,
    DocumentClass.PROTOCOL.value: 5,
}

_DOCUMENT_PREFIX_RE = re.compile(r"^Документ:\s*", re.IGNORECASE)


def _prioritised_text(text: str) -> str:
    """Тот же текст, но документы переставлены: техническое задание первым, смета и договор
    последними, повторы выброшены.

    Зачем: комплект документации бывает на сотни страниц, а модель читает ограниченное число
    кусков с начала. На закупке «Россети Центр» (32616337073) в эти куски попадали извещение
    и проект договора, техническое задание оставалось за пределом — и анализ возвращал ноль
    требований на закупке, где их полсотни.

    Повторы отбрасываются по имени файла: заказчик, меняя условия, выкладывает не изменённый
    файл, а весь комплект заново — «Уведомление об изменении №1» у той же закупки содержало те
    же семь документов, что и основной архив. Из одноимённых остаётся последний: он и есть
    действующая редакция, а порядок разбора берётся по первому вхождению.
    """

    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return text

    head = text[: matches[0].start()].strip()
    order_of: dict[str, int] = {}
    body_of: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for order, match in enumerate(matches):
        end = matches[order + 1].start() if order + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue  # заголовок вложенного архива: его содержимое идёт следующими разделами
        name = _DOCUMENT_PREFIX_RE.sub("", match.group(1)).strip()
        key = name.rpartition("/")[2].casefold()
        order_of.setdefault(key, order)
        body_of[key] = body
        name_of[key] = name

    if not body_of:
        return text

    sections = sorted(
        body_of,
        key=lambda key: (
            _CLASS_ORDER.get(
                classify_document(name_of[key]), _CLASS_ORDER[DocumentClass.OTHER.value]
            ),
            order_of[key],
        ),
    )
    parts = [head] if head else []
    parts.extend(f"=== {name_of[key]} ===\n{body_of[key]}" for key in sections)
    return "\n\n".join(parts)


def _select_chunks(chunks: list[str]) -> list[str]:
    """Какие куски документации отдать модели, если их больше `MAX_CHUNKS_PER_TENDER`.

    Пока документация помещается в предел, читается вся — порядок и состав не меняются.
    Когда не помещается, раньше брались первые двенадцать кусков, и на длинном проекте
    договора требования к прибору не читались вовсе: в первых кусках — предмет, оплата,
    ответственность, споры. Теперь куски ранжируются по числу упоминаний прибора и его
    характеристик, берутся самые насыщенные, а порядок среди отобранных остаётся
    документным — извлечённые требования тогда идут в том же порядке, что и в ТЗ.

    Первый кусок остаётся всегда: в нём наименование тендера и начало основного
    документа — по ним модель понимает, о какой закупке речь.
    """

    if len(chunks) <= MAX_CHUNKS_PER_TENDER:
        return chunks

    scored = sorted(
        range(1, len(chunks)),
        key=lambda index: len(_DEVICE_TERMS_RE.findall(chunks[index])),
        reverse=True,
    )
    keep = {0, *scored[: MAX_CHUNKS_PER_TENDER - 1]}
    return [chunk for index, chunk in enumerate(chunks) if index in keep]


def _extract_requirements(
    db: Session,
    tender: Tender,
    text: str,
    primary_document_id: uuid.UUID | None,
    outcome: AnalysisOutcome,
) -> None:
    chunks = _select_chunks(chunk_text(_prioritised_text(text), max_chars=MAX_CHUNK_CHARS))
    if not chunks:
        return

    system_prompt = _REQUIREMENTS_SYSTEM_PROMPT.format(
        groups_list=_groups_list(),
        parameters_list=parameters_prompt_list(),
        meter_kinds="; ".join(f"«{label}»" for label in METER_KINDS.values()),
        manufacturers_list=_manufacturers_list(db),
    )
    extracted: list[ExtractedRequirement] = []
    for index, chunk in enumerate(chunks):
        try:
            result = run_structured(
                db,
                system_prompt=system_prompt,
                user_text=chunk,
                response_model=RequirementsResult,
            )
        except AiQuotaExceededError as exc:
            # Остальные куски упрутся в тот же лимит — не тратим на них время.
            outcome.chunks_failed += len(chunks) - index
            outcome.messages.append(str(exc))
            break
        except Exception as exc:  # noqa: BLE001 - один неудачный кусок не должен терять остальные
            logger.warning(f"Извлечение требований из куска документации не удалось: {exc}")
            outcome.chunks_failed += 1
            continue
        outcome.chunks_processed += 1
        extracted.extend(result.requirements)

    if outcome.chunks_processed == 0:
        outcome.messages.append("Ни один фрагмент документации не удалось разобрать")
        return

    extracted = _drop_exact_duplicates(extracted, outcome)
    extracted = _consolidate(db, extracted, outcome)
    _replace_requirements(db, tender, extracted, primary_document_id, outcome)


def _manufacturers_list(db: Session) -> str:
    from app.models.manufacturer import Manufacturer

    rows = db.scalars(select(Manufacturer).order_by(Manufacturer.legal_name))
    return "\n".join(
        f"- {item.brand_name or item.legal_name} ({item.legal_name})" for item in rows
    ) or "- (справочник пуст)"


_CRITICALITY_RANK = {
    Criticality.CRITICAL.value: 0,
    Criticality.IMPORTANT.value: 1,
    Criticality.MINOR.value: 2,
}


def _merge_into(target: ExtractedRequirement, other: ExtractedRequirement) -> None:
    """Повтор требования отдаёт оставшемуся то, что в нём сильнее: критичность, номер
    параметра, привязку к производителю. Иначе «гарантия — критичное» из спецификации
    потерялось бы, если оставлена формулировка из договора с «важным»."""

    if _CRITICALITY_RANK.get(other.criticality, 1) < _CRITICALITY_RANK.get(target.criticality, 1):
        target.criticality = other.criticality
    if not target.parameter_no and other.parameter_no:
        target.parameter_no = other.parameter_no
    if not target.group_name and other.group_name:
        target.group_name = other.group_name
    if not target.vendor and other.vendor:
        target.vendor = other.vendor
    target.vendor_exclusive = target.vendor_exclusive or other.vendor_exclusive


def _is_protected(item: ExtractedRequirement) -> bool:
    return bool((item.vendor or "").strip()) or (item.normalized_text or "").lower().startswith(
        "модель прибора"
    )


def _same_vendor(item: ExtractedRequirement, other: ExtractedRequirement) -> bool:
    return (
        _is_protected(other)
        and (item.vendor or "").strip().casefold() == (other.vendor or "").strip().casefold()
        and (item.normalized_text or "").lower().startswith("модель прибора")
        == (other.normalized_text or "").lower().startswith("модель прибора")
    )


def _drop_exact_duplicates(
    items: list[ExtractedRequirement], outcome: AnalysisOutcome
) -> list[ExtractedRequirement]:
    """Повторы с одинаковой нормализованной формулировкой. Сравнивать дословный `text`
    мало: ТЗ закупки 32616436166 целиком вложено в проект договора, и «Гарантийный срок: не
    менее 7 лет» приходило четырежды с разными исходными фразами."""

    kept: dict[tuple[str, str], ExtractedRequirement] = {}
    result: list[ExtractedRequirement] = []
    for item in items:
        key = (item.kind, _dedup_key(item.normalized_text or item.text or ""))
        if not key[1]:
            result.append(item)
            continue
        if key in kept:
            _merge_into(kept[key], item)
            outcome.duplicates_removed += 1
            continue
        kept[key] = item
        result.append(item)
    return result


# Больше требований за раз модель сводит неаккуратно, а столько в одной закупке не бывает
# почти никогда; хвост сверх предела остаётся как есть.
MAX_REQUIREMENTS_TO_CONSOLIDATE = 250


def _consolidate(
    db: Session, items: list[ExtractedRequirement], outcome: AnalysisOutcome
) -> list[ExtractedRequirement]:
    """Смысловые повторы — отдельным проходом модели по всему списку. Извлечение идёт по
    фрагментам, и каждый фрагмент не знает, что уже извлечено из других: «Протокол обмена:
    СПОДЭС» и «Протокол передачи: СПОДЭС», «Встроенный GSM/GPRS-модем» и «Интерфейсы связи:
    встроенный GSM/GPRS модем». Дубли раздувают матрицу и искажают счёт критичных без ответа.

    Сбой прохода не теряет требований — остаётся список после дословной очистки."""

    if len(items) < 2:
        return items
    head = items[:MAX_REQUIREMENTS_TO_CONSOLIDATE]
    listing = "\n".join(
        f"{number}. [{_KIND_SHORT_LABELS.get(item.kind, item.kind)}] "
        f"{item.normalized_text or item.text} — «{(item.text or '')[:200]}»"
        for number, item in enumerate(head, start=1)
    )
    try:
        result = run_structured(
            db,
            system_prompt=_CONSOLIDATE_SYSTEM_PROMPT,
            user_text=listing,
            response_model=ConsolidationResult,
        )
    except Exception as exc:  # noqa: BLE001 - без сведения повторов анализ всё равно полезен
        logger.warning(f"Сведение повторов требований не удалось: {exc}")
        outcome.messages.append(f"Повторы требований сведены только дословно: {exc}")
        return items

    dropped: set[int] = set()
    for group in result.groups:
        keep = group.keep
        if not 1 <= keep <= len(head) or keep in dropped:
            continue
        for number in group.duplicates:
            if number == keep or not 1 <= number <= len(head) or number in dropped:
                continue
            if head[number - 1].kind != head[keep - 1].kind:
                continue  # промпт запрещает, но цена ошибки — потерянное требование
            if _is_protected(head[number - 1]) and not _same_vendor(head[number - 1], head[keep - 1]):
                # Обозначение эталонной модели и фирменное ПО производителя не поглощаются:
                # на живом прогоне «Модель прибора: CE207 … (или эквивалент)» ушло в «Тип
                # прибора», и закупка перестала быть «под Энергомеру».
                continue
            _merge_into(head[keep - 1], head[number - 1])
            dropped.add(number)
    outcome.duplicates_removed += len(dropped)
    return [
        item for number, item in enumerate(head, start=1) if number not in dropped
    ] + items[MAX_REQUIREMENTS_TO_CONSOLIDATE:]


def _replace_requirements(
    db: Session,
    tender: Tender,
    extracted: list[ExtractedRequirement],
    primary_document_id: uuid.UUID | None,
    outcome: AnalysisOutcome,
) -> None:
    existing = list(
        db.scalars(select(Requirement).where(Requirement.tender_id == tender.id))
    )
    kept_texts = set()
    for requirement in existing:
        if requirement.verified_by_user:
            kept_texts.add(_dedup_key(requirement.text))
        else:
            db.delete(requirement)

    seen: set[str] = set(kept_texts)
    known_groups = set(CHARACTERISTIC_GROUPS)
    for item in extracted:
        text_value = (item.text or "").strip()
        if not text_value:
            outcome.requirements_skipped += 1
            continue

        # Куски документа идут с перекрытием (см. `chunk_text`), поэтому одно и то же
        # требование приходит из соседних кусков дважды.
        key = _dedup_key(text_value)
        if key in seen:
            continue
        seen.add(key)

        criticality = item.criticality if item.criticality in _CRITICALITY_VALUES else None
        if criticality is None:
            criticality = Criticality.IMPORTANT.value
            outcome.messages.append(
                f"Неизвестная критичность «{item.criticality}» — записано как «важное»"
            )

        # Неизвестный вид — «к товару»: так требование хотя бы попадёт в матрицу, а не
        # исчезнет; для закупок на поставку это к тому же почти всегда верно.
        kind = item.kind if item.kind in _KIND_VALUES else RequirementKind.PRODUCT.value
        group = item.group_name.strip() if item.group_name else ""
        parameter_no = None
        if kind == RequirementKind.PRODUCT.value:
            parameter_no = item.parameter_no if item.parameter_no in PARAMETERS_BY_NO else None
            # Правила кода (реле, ПП 719, Astra) не должны зависеть от того, проставила ли
            # модель номер: узнаём такие параметры и по формулировке.
            if parameter_no is None:
                parameter_no = detect_parameter(f"{text_value} {item.normalized_text or ''}")
        vendor = (item.vendor or "").strip()[:100] or None
        vendor_exclusive = bool(vendor) and bool(item.vendor_exclusive)
        if vendor_exclusive and kind == RequirementKind.PRODUCT.value:
            # Фирменное ПО или сервис чужого производителя: заявку с другим прибором по нему
            # отклонят, как бы модель ни оценила «важность» формулировки.
            criticality = Criticality.CRITICAL.value
        db.add(
            Requirement(
                tender_id=tender.id,
                source_document_id=primary_document_id,
                vendor=vendor,
                vendor_exclusive=vendor_exclusive,
                text=text_value,
                normalized_text=(item.normalized_text or "").strip() or None,
                criticality=criticality,
                kind=kind,
                # Группа характеристик — свойство товара; у требований к работам и участнику
                # её нет, даже если модель что-то подставила.
                category=group
                if kind == RequirementKind.PRODUCT.value and group in known_groups
                else None,
                parameter_no=parameter_no,
            )
        )
        outcome.requirements_saved += 1
        outcome.requirements_by_kind[kind] = outcome.requirements_by_kind.get(kind, 0) + 1


def _dedup_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _log(
    db: Session,
    tender: Tender,
    outcome: AnalysisOutcome,
    actor: User | None,
    *,
    level: LogLevel = LogLevel.INFO,
) -> None:
    details = (
        f"Требований сохранено: {outcome.requirements_saved}"
        f"{kinds_summary(outcome)}; повторов убрано: {outcome.duplicates_removed}; "
        f"фрагментов разобрано: "
        f"{outcome.chunks_processed}, не удалось: {outcome.chunks_failed}; "
        f"тип конкурса: {outcome.tender_type or '—'}; ОКПД2: {outcome.okpd2_code or '—'}"
    )
    if outcome.messages:
        details += "; " + "; ".join(outcome.messages)

    log_action(
        db,
        component="tender_analysis",
        action=f"analyze_tender:{tender.external_id}",
        result="success" if outcome.requirements_saved or outcome.tender_type else "empty",
        level=level,
        details=details,
        user_id=actor.id if actor else None,
    )
    db.commit()
