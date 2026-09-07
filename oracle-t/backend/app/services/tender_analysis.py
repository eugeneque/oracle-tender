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

from app.models.analysis import Criticality, Requirement
from app.models.log import LogLevel
from app.models.region import Region
from app.models.tender import Tender, TenderType
from app.models.tender_document import DocumentClass, TenderDocument
from app.models.user import User
from app.seed.characteristics_data import CHARACTERISTIC_GROUPS
from app.services.audit import log_action
from app.services.document_service import classify_document, reextract_stale_documents
from app.services.yandex_ai_client import chunk_text, run_structured

# Кусок текста на один запрос. Тендерная документация длиннее «Описания типа», а системный
# промпт здесь короче (список групп, а не всех ~120 полей), поэтому кусок крупнее, чем в
# `characteristic_extraction`.
MAX_CHUNK_CHARS = 14_000

# Сколько кусков документа разбирать максимум. Тендерная документация бывает на сотни страниц
# (сметы, ведомости объёмов), но требования к прибору лежат в начале — в техническом задании;
# без предела один тендер мог бы съесть сотню запросов к модели.
MAX_CHUNKS_PER_TENDER = 12

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

# Коды, по которым закупается продукция производителей из раздела 4.3 ТЗ. Порядок = приоритет.
RELEVANT_OKPD2_PREFIXES = (
    "26.51.63.130",  # счётчики электроэнергии — основной
    "26.51.63.120",  # счётчики жидкости (воды)
    "26.51.63.110",  # счётчики газа
    "26.51.63",
    "26.51.6",
    "26.51",
)

_TENDER_TYPE_VALUES = {t.value for t in TenderType}
_CRITICALITY_VALUES = {c.value for c in Criticality}

_REQUIREMENTS_SYSTEM_PROMPT = """Ты анализируешь документацию закупки приборов учёта \
(счётчиков электроэнергии, воды, газа, тепла) и извлекаешь из неё требования к товару.

Что считать требованием: любое проверяемое условие к прибору, его характеристикам, \
комплектации, документам или порядку поставки. Например: класс точности, номинальное \
напряжение, наличие реле, тип интерфейса, поддержка протокола, наличие поверки, срок \
гарантии, наличие в Госреестре СИ.

Строка спецификации, ведомости или перечня закупаемого товара — ТОЖЕ требование, даже если \
она состоит из одного наименования. «Счетчик СЕ208, 30 шт.» означает требование к типу \
прибора, и его нужно извлечь: в закупке у единственного поставщика такая строка часто \
единственное, что вообще сказано о товаре.

Что НЕ является требованием и должно быть пропущено: реквизиты сторон, суммы и порядок \
оплаты, сроки подачи заявок, требования к участнику закупки (опыт, лицензии, отсутствие \
в РНП), общие фразы без проверяемого условия.

Правила заполнения полей:
1. `text` — формулировка ИЗ ДОКУМЕНТА, дословно, без сокращений и пересказа.
2. `normalized_text` — то же требование коротко и однозначно, в виде «параметр: значение». \
Например: «Класс точности активной энергии: не хуже 1,0».
3. `criticality` — строго одно из: critical, important, minor.
   - critical: без выполнения заявку отклонят (класс точности, номинальные ток/напряжение, \
наличие в Госреестре СИ, тип прибора, количество фаз);
   - important: существенно влияет на выбор (интерфейсы, протоколы, реле, температурный \
диапазон, межповерочный интервал);
   - minor: второстепенное (цвет корпуса, тип упаковки, пожелания без «должен»).
4. `group_name` — группа характеристик из справочника ниже, к которой относится требование. \
Если ни одна не подходит — пустая строка.
5. Не придумывай требований, которых нет в тексте. Если требований нет — верни пустой список.

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
    criticality: str
    group_name: str


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


@dataclass
class AnalysisOutcome:
    """Итог анализа для отчёта пользователю. Отдельно считаются пропущенные требования:
    молчаливая потеря части списка выглядела бы как «в тендере мало требований»."""

    requirements_saved: int = 0
    requirements_skipped: int = 0
    chunks_processed: int = 0
    chunks_failed: int = 0
    tender_type: str | None = None
    okpd2_code: str | None = None
    region_code: str | None = None
    messages: list[str] = field(default_factory=list)


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


def _extract_requirements(
    db: Session,
    tender: Tender,
    text: str,
    primary_document_id: uuid.UUID | None,
    outcome: AnalysisOutcome,
) -> None:
    chunks = chunk_text(_prioritised_text(text), max_chars=MAX_CHUNK_CHARS)[
        :MAX_CHUNKS_PER_TENDER
    ]
    if not chunks:
        return

    system_prompt = _REQUIREMENTS_SYSTEM_PROMPT.format(groups_list=_groups_list())
    extracted: list[ExtractedRequirement] = []
    for chunk in chunks:
        try:
            result = run_structured(
                db,
                system_prompt=system_prompt,
                user_text=chunk,
                response_model=RequirementsResult,
            )
        except Exception as exc:  # noqa: BLE001 - один неудачный кусок не должен терять остальные
            logger.warning(f"Извлечение требований из куска документации не удалось: {exc}")
            outcome.chunks_failed += 1
            continue
        outcome.chunks_processed += 1
        extracted.extend(result.requirements)

    if outcome.chunks_processed == 0:
        outcome.messages.append("Ни один фрагмент документации не удалось разобрать")
        return

    _replace_requirements(db, tender, extracted, primary_document_id, outcome)


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

        group = item.group_name.strip() if item.group_name else ""
        db.add(
            Requirement(
                tender_id=tender.id,
                source_document_id=primary_document_id,
                text=text_value,
                normalized_text=(item.normalized_text or "").strip() or None,
                criticality=criticality,
                category=group if group in known_groups else None,
            )
        )
        outcome.requirements_saved += 1


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
        f"Требований сохранено: {outcome.requirements_saved}; фрагментов разобрано: "
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
