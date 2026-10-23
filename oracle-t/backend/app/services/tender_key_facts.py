"""Ключевые сведения закупки для менеджера: то, что тендерный отдел смотрит первым (23.10.2026).

Замечания тендерного отдела «Что нужно видеть менеджеру в Битрикс»: вкладка сделки получала
все требования ТЗ подряд, в порядке извлечения — сотня строк, где паспорт и методика поверки
стоят вперемешку с интерфейсом связи, а причину «не подходит» приходилось искать глазами.
Менеджер же сначала спрашивает одно и то же: под чей прибор написано ТЗ, какая связь и
какие параметры критичны, габариты, срок поставки, сколько уровней у системы, нужна ли
криптозащита и интеграция с ПО — и по какому параметру не проходит наш прибор.

С версии портала 1.4.1 (23.10.2026) сведения уходят одной строкой `technical_summary` — семь
полей, которые портал показывает блоком «Краткая выжимка по ТЗ»; критичные характеристики и
габариты остаются строками требований (критичные идут первыми).

Сведения собираются кодом из того, что уже есть: требования, матрица соответствия МИРТЕК,
эталон ТЗ (`detect_brand_lock`, `WinPercentage.tz_reference`), ПО верхнего уровня
(`upper_software.PLATFORMS`), разделы «Дополнительно» и текст документации. Новых обращений к
модели нет. То, что выведено по косвенным признакам (уровень системы), так и подписано —
«по признакам», с цитатой, чтобы менеджер мог проверить.

`is_generic_norm` — фильтр общих норм (паспорт, методика поверки, «новый, не бывший в
употреблении», типовые требования к участнику по закону): во вкладку сделки они не идут,
если только наш прибор по ним не проходит.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.upper_software import PLATFORMS
from app.models.analysis import (
    ComplianceMatrixEntry,
    ComplianceStatus,
    Criticality,
    Requirement,
    RequirementKind,
    WinPercentage,
)
from app.models.manufacturer import Manufacturer
from app.models.tender import Tender
from app.models.tender_card import TenderCard
from app.models.tender_document import TenderDocument
from app.services.tz_reference import is_registry_requirement

if TYPE_CHECKING:
    from app.services.ai_conclusion_service import BrandLock

# Сколько текста документации просматривать: столько же читает детектор эталона ТЗ.
DOC_SCAN_LIMIT = 400_000
MAX_LISTED = 8


@dataclass
class KeyFact:
    """Одно сведение: что спрашивает менеджер, ответ и откуда он взят.

    `key` — имя поля строки `technical_summary` портала (`docs/AI_API.md` 1.4.1, «Краткая
    выжимка по ТЗ»): портал требует передавать эти имена без изменений."""

    key: str
    title: str
    value: str
    # Вывод или оговорка: «эквивалент допускается», «по признакам, проверить» и т. п.
    result: str | None = None
    details: str | None = None
    quote: str | None = None
    document: str | None = None
    # Вывод по косвенным признакам или не найден — выжимка уходит со статусом «нужно проверить».
    review: bool = False

    @property
    def text(self) -> str:
        """Значение одной строкой для выжимки: «Энергомера: CE207 — эквивалент допускается»."""

        if not self.result:
            return self.value
        return f"{self.value} — {_lower_first(self.result)}"


def _lower_first(text: str) -> str:
    # «Эквивалент допускается» → «эквивалент…», но «ПО «Пирамида»» и «МИРТЕК» — как есть.
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


# --- общие нормы ------------------------------------------------------------------------------

# Нормы, одинаковые для любого прибора и любой закупки: документы на товар, поверка, качество
# и упаковка. Они не отличают закупку от соседней и прячут то, что отличает.
_GENERIC_PRODUCT_RE = re.compile(
    r"паспорт|формуляр|руководств\w*\s+по\s+эксплуатац|методик\w*\s+поверк"
    r"|свидетельств\w*\s+о\s+поверк|знак\w*\s+поверк|сертификат\w*\s+(?:соответстви|об\s+утвержд)"
    r"|декларац\w*\s+о\s+соответстви|утвержд\w+\s+тип\w*\s+средств"
    r"|не\s+бывш\w*\s+в\s+употреблени|не\s+восстановлен|качеств\w*\s+и\s+комплектност"
    r"|упаков|маркировк|тар[аеуы]\b|страны?\s+происхождени",
    re.IGNORECASE,
)
# Типовые требования к участнику по 44-ФЗ / 223-ФЗ: их выполняет любая действующая компания.
_GENERIC_PARTICIPANT_RE = re.compile(
    r"ликвидац|банкрот|недоимк|задолженност|недобросовестн|\bрнп\b|судимост|дисквалифик"
    r"|конфликт\w*\s+интерес|офшор|приостановлен|любое\s+юридическое\s+лицо"
    r"|соответствовать\s+требованиям\s+законодательств|отвечать\s+требованиям\s+документаци"
    r"|исключительн\w+\s+прав|сведения\s+об\s+участник\w*\s+закупки.{0,40}реестр"
    r"|административн\w+\s+правонарушени|не\s+является\s+иностранн",
    re.IGNORECASE,
)


def requirement_text(requirement: Requirement) -> str:
    return (requirement.normalized_text or requirement.text or "").strip()


def is_generic_norm(requirement: Requirement) -> bool:
    """Общая норма, которая не говорит ничего об этой закупке и этом приборе."""

    text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
    if requirement.kind == RequirementKind.SUPPLY.value:
        return True
    if requirement.kind == RequirementKind.PARTICIPANT.value:
        return bool(_GENERIC_PARTICIPANT_RE.search(text))
    if is_registry_requirement(requirement):
        # Реестр промпродукции (ПП 1875) отсекает приборы — это не общая норма.
        return False
    if requirement.kind == RequirementKind.SERVICE.value:
        return requirement.criticality == Criticality.MINOR.value or bool(_GENERIC_PRODUCT_RE.search(text))
    return requirement.category == "Документация" or bool(_GENERIC_PRODUCT_RE.search(text))


_STATUS_RANK = {
    ComplianceStatus.NOT_MEETS.value: 0,
    ComplianceStatus.PARTIAL.value: 1,
}
_CRITICALITY_RANK = {
    Criticality.CRITICAL.value: 0,
    Criticality.IMPORTANT.value: 1,
    Criticality.MINOR.value: 2,
}
_KIND_RANK = {RequirementKind.PRODUCT.value: 0, RequirementKind.SERVICE.value: 1}


def priority(requirement: Requirement, status: str | None) -> tuple[int, int, int]:
    """Порядок строк ТЗ: сначала то, по чему наш прибор не проходит, затем критичные,
    требования к прибору раньше требований к работам."""

    return (
        _STATUS_RANK.get(status or "", 2),
        _CRITICALITY_RANK.get(requirement.criticality, 3),
        _KIND_RANK.get(requirement.kind, 2),
    )


# --- источники текста -------------------------------------------------------------------------


@dataclass
class _Sources:
    product: list[Requirement]
    requirements: list[Requirement]
    # (имя файла, текст) — приоритетные файлы и ТЗ первыми.
    documents: list[tuple[str, str]]
    extra: dict[str, list[str]]


def _load_sources(db: Session, tender: Tender, requirements: list[Requirement]) -> _Sources:
    ids = [tender.id] + ([tender.analysis_tender_id] if tender.analysis_tender_id else [])
    rows = db.scalars(
        select(TenderDocument)
        .where(TenderDocument.tender_id.in_(ids), TenderDocument.extracted_text.is_not(None))
        .order_by(TenderDocument.is_priority_source.desc(), TenderDocument.created_at)
    ).all()
    documents: list[tuple[str, str]] = []
    left = DOC_SCAN_LIMIT
    for document in rows:
        if left <= 0:
            break
        text = document.extracted_text[:left]
        documents.append((document.file_name, text))
        left -= len(text)
    card = db.get(TenderCard, tender.id)
    extra = (((card.payload or {}).get("extra_sections") or {}).get("sections") or {}) if card else {}
    product = [item for item in requirements if item.kind == RequirementKind.PRODUCT.value]
    return _Sources(product=product, requirements=requirements, documents=documents, extra=extra)


def _sentence(text: str, start: int, end: int, limit: int = 300, *, trim: bool = True) -> str:
    """Предложение вокруг совпадения — для цитаты. Длинное перечисление обрезается слева,
    чтобы совпадение не уехало за предел цитаты."""

    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start))
    if trim:
        left = max(left, start - 120)
    right_candidates = [pos for pos in (text.find(".", end), text.find("\n", end), text.find(";", end)) if pos != -1]
    right = min(right_candidates) if right_candidates else len(text)
    sentence = re.sub(r"\s+", " ", text[left + 1 : right]).strip(" .;:-")
    return sentence[:limit] if trim else sentence


def _search(sources: _Sources, pattern: re.Pattern[str], *, documents: bool = True):
    """Первое совпадение: требования, затем «Дополнительно», затем документация.
    Возвращает (цитата, имя файла или None, требование или None)."""

    for requirement in sources.requirements:
        text = requirement.text or ""
        match = pattern.search(text)
        if match:
            return _sentence(text, match.start(), match.end()), None, requirement
    for items in sources.extra.values():
        for text in items or []:
            match = pattern.search(text or "")
            if match:
                return _sentence(text, match.start(), match.end()), None, None
    if documents:
        for name, text in sources.documents:
            match = pattern.search(text)
            if match:
                return _sentence(text, match.start(), match.end()), name, None
    return None


def _short(requirement: Requirement, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", requirement_text(requirement))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _subject(requirement: Requirement) -> str:
    """Название параметра: «Базовый (максимальный) ток» из «Базовый (максимальный) ток: 5 (80) А»."""

    if requirement.field_name:
        return requirement.field_name
    text = requirement_text(requirement)
    subject = text.split(":", 1)[0].strip()
    return subject[:80] if subject and len(subject) < len(text) else _short(requirement, 80)


def _join(items: list[str], separator: str = "; ") -> str:
    unique = list(dict.fromkeys(item for item in items if item))
    extra = len(unique) - MAX_LISTED
    text = separator.join(unique[:MAX_LISTED])
    return f"{text}; и ещё {extra}" if extra > 0 else text


# --- сведения ---------------------------------------------------------------------------------


def _tz_target(db: Session, tender: Tender, lock: "BrandLock | None") -> KeyFact:
    if lock is not None:
        name = lock.manufacturer.brand_name or lock.manufacturer.legal_name
        # «ФОБОС-3» и «ФОБОС 3», «Фобос 3Т» и «ФОБОС 3Т» — одна модель.
        named = {re.sub(r"[\s\u00a0-]+", " ", model).strip().upper(): model for model in lock.models}
        listed = list(named.values())
        # Выжимка — одна строка в карточке: восемь полных обозначений Энергомеры её затопили бы.
        models = ", ".join(listed[:4]) + (f" и ещё {len(listed) - 4}" if len(listed) > 4 else "")
        models = models or "модели названы без обозначения"
        if lock.equivalent_allowed is False:
            result = "Эквивалент не допускается"
        elif lock.exclusive:
            result = "Требуется фирменное ПО производителя: " + "; ".join(lock.exclusive[:3])
        elif lock.equivalent_allowed:
            result = "Эквивалент допускается"
        else:
            result = "Об эквиваленте документация молчит — уточнить"
        if lock.is_ours:
            result = f"Наш прибор, {_lower_first(result)}"
        return KeyFact(
            "specification_target", "ТЗ составлено под", f"{name}: {models}", result,
            quote=lock.clause, review=lock.equivalent_allowed is None and not lock.exclusive,
        )
    reference = db.execute(
        select(Manufacturer)
        .join(WinPercentage, WinPercentage.manufacturer_id == Manufacturer.id)
        .where(
            WinPercentage.tender_id == tender.id,
            WinPercentage.is_current.is_(True),
            WinPercentage.tz_reference.is_(True),
        )
    ).scalars().first()
    if reference is not None:
        name = reference.brand_name or reference.legal_name
        return KeyFact(
            "specification_target",
            "ТЗ составлено под",
            name,
            "Наш прибор" if reference.is_mirtek else "Эталон матрицы соответствия: характеристики ТЗ совпадают с его прибором",
            review=True,
        )
    return KeyFact(
        "specification_target", "ТЗ составлено под", "Не определено", "Модели конкретного производителя в ТЗ не названы"
    )


_INTERFACES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("RS-485", r"rs[\s-]?485"),
        ("RS-232", r"rs[\s-]?232"),
        ("CAN", r"\bcan\b"),
        ("Ethernet", r"ethernet"),
        ("GSM/GPRS", r"\bgsm\b|\bgprs\b"),
        ("LTE/4G", r"\blte\b|\b4g\b|\b3g\b"),
        ("NB-IoT", r"nb[\s-]?iot"),
        ("NB-Fi", r"nb[\s-]?fi\b"),
        ("LoRaWAN", r"lora"),
        ("PLC", r"\bplc\b|power\s*line|по\s+силов\w+\s+(?:сет|лини)"),
        ("ZigBee", r"zigbee"),
        ("Wi-Fi", r"wi[\s-]?fi"),
        ("Bluetooth", r"bluetooth"),
        ("Радиоканал", r"\brf\b|радио(?:канал|модем|модул|интерфейс)|\b(?:433|868|2[,.]4)\s*(?:мгц|ггц)"),
        ("Оптопорт", r"оптопорт|оптическ\w*\s+(?:порт|интерфейс)"),
        ("M-Bus", r"\bm[\s-]?bus\b"),
        ("USB", r"\busb\b"),
    )
)
_PROTOCOLS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in (
        ("СПОДЭС", r"сподэс"),
        ("DLMS/COSEM", r"dlms|cosem"),
        ("Modbus", r"modbus"),
        ("МЭК 61107 / 62056", r"61107|62056"),
        ("МЭК 60870-5-104", r"60870"),
    )
)
# Радиомодем NB-Fi, LoRaWAN и ZigBee — уже радиоканал; отдельной строкой он только мешает.
_RADIO_FAMILIES = {"NB-Fi", "LoRaWAN", "ZigBee", "Wi-Fi", "Bluetooth"}
_OPTIONAL_RE = re.compile(r"опционал|по\s+заказу|дополнительн\w*\s+модул|сменн\w*\s+модул", re.IGNORECASE)


def _found_labels(requirements: list[Requirement], table) -> tuple[list[str], dict[str, bool]]:
    """Найденные названия по порядку и признак «только опционально»."""

    found: list[str] = []
    optional: dict[str, bool] = {}
    for requirement in requirements:
        text = f"{requirement.text or ''} {requirement.normalized_text or ''}"
        is_optional = bool(_OPTIONAL_RE.search(text))
        for label, pattern in table:
            if pattern.search(text):
                if label not in found:
                    found.append(label)
                optional[label] = optional.get(label, True) and is_optional
    return found, optional


def _interfaces(sources: _Sources) -> KeyFact:
    interfaces, optional = _found_labels(sources.product, _INTERFACES)
    if _RADIO_FAMILIES.intersection(interfaces) and "Радиоканал" in interfaces:
        interfaces.remove("Радиоканал")
    protocols, _ = _found_labels(sources.product, _PROTOCOLS)
    if not interfaces and not protocols:
        return KeyFact("communication_interfaces", "Интерфейсы связи", "В требованиях к прибору не указаны")
    named = [f"{label} (опционально)" if optional.get(label) else label for label in interfaces]
    value = ", ".join(named) or "не указаны"
    # Формулировка ТЗ, а не сокращённая: сокращённая («Тип прибора: трёхфазный») порой теряет
    # именно название интерфейса.
    rows = [
        re.sub(r"\s+", " ", item.text or "")[:110]
        for item in sources.product
        if any(pattern.search(item.text or "") for _, pattern in _INTERFACES + _PROTOCOLS)
    ]
    return KeyFact(
        "communication_interfaces",
        "Интерфейсы связи",
        value,
        ("Протоколы: " + ", ".join(protocols)) if protocols else None,
        details=_join(rows),
    )


# Фраза, которая устанавливает срок: «Срок поставки не должен превышать 30 дней…»,
# «Поставка осуществляется в течение 10 рабочих дней…», «Срок поставки: до 01.12.2026».
# Без числа или даты («сроки поставки согласуются») фраза ничего не говорит.
_DELIVERY_RE = re.compile(
    r"(?:срок\w*\s+(?:поставки|поставка|отгрузки)(?:\s+товара)?|поставк\w+\s+(?:товар\w*\s+)?(?:осуществля|производ)\w*)"
    r"[^.;\n]{0,60}?(?:не\s+(?:должен\s+)?превыша|составля|:|—|в\s+течени|не\s+позднее|до\s+\d|с\s+\d{1,2}\.\d{2}|по\s+\d{1,2}\.\d{2})"
    r"[^.;\n]{0,80}?\d",
    re.IGNORECASE,
)
# В проекте договора срок поставки чаще всего упомянут в пунктах о нарушениях и неустойке —
# 32616298746: «срок поставки товара исчисляется в течение 2 дней с даты направления…» в
# пункте о неподписанной заявке. Такие фразы срок не задают.
_PENALTY_RE = re.compile(r"наруш|неустойк|штраф|пен[иья]\b|отказат|просроч|если\s+поставщик", re.IGNORECASE)
_SCHEDULE_RE = re.compile(r"график\w*\s+поставк", re.IGNORECASE)


def _delivery(sources: _Sources) -> KeyFact:
    texts = (
        [(None, item.text or "") for item in sources.requirements]
        + [(None, text or "") for items in sources.extra.values() for text in items or []]
        + [(name, text) for name, text in sources.documents]
    )
    for name, text in texts:
        for match in _DELIVERY_RE.finditer(text):
            quote = _sentence(text, match.start(), match.end())
            if not _PENALTY_RE.search(_sentence(text, match.start(), match.end(), trim=False)):
                return KeyFact("delivery_terms", "Срок поставки", quote, quote=quote, document=name)
    found = _search(sources, _SCHEDULE_RE)
    if found is not None:
        return KeyFact(
            "delivery_terms", "Срок поставки", "По графику поставки", "приложение к договору, смотреть документацию",
            quote=found[0], document=found[1], review=True,
        )
    return KeyFact("delivery_terms", "Срок поставки", "В документации не найден", review=True)


_LEVELS_RE = re.compile(r"(одно|двух|тр[её]х|[123]-?х)[\s-]*уровнев\w*", re.IGNORECASE)
_LEVEL_WORDS = {"одно": 1, "1х": 1, "1-х": 1, "двух": 2, "2х": 2, "2-х": 2, "трех": 3, "3х": 3, "3-х": 3}
_LEVEL_LABELS = {1: "Одноуровневая", 2: "Двухуровневая", 3: "Трёхуровневая"}
_USPD_RE = re.compile(
    r"\bуспд\b|\bивкэ\b|концентратор\w*|устройств\w*\s+сбора\s+и\s+передачи\s+данных|маршрутизатор\w*\s+данных",
    re.IGNORECASE,
)
_TOP_RE = re.compile(
    r"\bивк\b|верхн\w+\s+уровн|сервер\w*\s+(?:сбора|опроса|аскуэ|аиис|учета|учёта)|центр\w*\s+сбора\s+(?:и\s+обработки\s+)?данных"
    r"|программн\w+\s+(?:обеспечени\w+|комплекс\w*)\s+(?:верхн|сбора|аскуэ|аиис)",
    re.IGNORECASE,
)


def _system_level(sources: _Sources) -> KeyFact:
    counts: dict[int, int] = {}
    quote = document = None
    texts = [(None, item.text or "") for item in sources.requirements] + [
        (name, text) for name, text in sources.documents
    ]
    for name, text in texts:
        for match in _LEVELS_RE.finditer(text):
            level = _LEVEL_WORDS.get(match.group(1).lower().replace("ё", "е"))
            if level is None:
                continue
            counts[level] = counts.get(level, 0) + 1
            if quote is None:
                quote, document = _sentence(text, match.start(), match.end()), name
    if counts:
        level = max(counts, key=lambda item: (counts[item], item))
        return KeyFact(
            "system_level", "Уровни системы", _LEVEL_LABELS[level], "Прямо указано в документации",
            quote=quote, document=document,
        )
    uspd = _search(sources, _USPD_RE)
    # Названное ПО верхнего уровня («Пирамида», «АльфаЦЕНТР») — тоже верхний уровень системы.
    top = _search(sources, _TOP_RE) or next(
        (found for _, patterns in _PLATFORM_RES for pattern in patterns if (found := _search(sources, pattern))),
        None,
    )
    signs = [label for label, found in (("УСПД / концентратор", uspd), ("верхний уровень (ИВК, сервер, ПО)", top)) if found]
    first = uspd or top
    if uspd and top:
        value = _LEVEL_LABELS[3]
    elif top:
        value = _LEVEL_LABELS[2]
    elif uspd:
        value = "Не менее двух уровней"
    else:
        return KeyFact(
            "system_level", "Уровни системы", "Только приборы учёта",
            "В документации нет УСПД и верхнего уровня — проверить", review=True,
        )
    return KeyFact(
        "system_level", "Уровни системы", value,
        "По признакам: " + ", ".join(signs) + " — проверить по ТЗ",
        quote=first[0], document=first[1], review=True,
    )


_CRYPTO_RE = re.compile(
    r"\bскзи\b|криптограф\w*|(?<!рас)шифров\w*|гост\s*р?\s*34\.1[0-3]|гост\s*28147|сертификат\w*\s+фсб",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(r":\s*(?:нет|не\s+требуется|отсутствует|не\s+предусмотрен\w*)\s*\.?$", re.IGNORECASE)


def _crypto(sources: _Sources) -> KeyFact:
    for requirement in sources.product:
        text = requirement.text or ""
        match = _CRYPTO_RE.search(text)
        if not match:
            continue
        if _NEGATIVE_RE.search(text.strip()):
            return KeyFact("crypto_protection", "Криптозащита", "Не требуется", quote=_short(requirement, 200))
        return KeyFact("crypto_protection", "Криптозащита", "Требуется", _short(requirement, 200), quote=_short(requirement, 300))
    found = _search(sources, _CRYPTO_RE)
    if found is None:
        return KeyFact("crypto_protection", "Криптозащита", "В документации не упоминается")
    quote, document, _ = found
    return KeyFact(
        "crypto_protection", "Криптозащита", "Упоминается в документации", quote,
        quote=quote, document=document, review=True,
    )


_PLATFORM_RES = [
    (profile.name, [re.compile(alias, re.IGNORECASE) for alias in profile.aliases]) for profile in PLATFORMS.values()
]
_INTEGRATION_RE = re.compile(
    r"интеграц\w*|совместим\w*\s+с\s+(?:по\b|программн|ивк|аскуэ|аиис|систем)|передач\w+\s+данных\s+в\s+(?:по\b|систем|ивк)",
    re.IGNORECASE,
)


def _software(sources: _Sources, lock: "BrandLock | None") -> KeyFact:
    texts = [f"{item.text or ''} {item.normalized_text or ''}" for item in sources.requirements]
    platforms = [name for name, patterns in _PLATFORM_RES if any(p.search(t) for p in patterns for t in texts)]
    if not platforms:
        documents = [text for _, text in sources.documents]
        platforms = [name for name, patterns in _PLATFORM_RES if any(p.search(t) for p in patterns for t in documents)]
    exclusive = list(lock.exclusive) if lock is not None and not lock.is_ours else []
    found = _search(sources, _INTEGRATION_RE, documents=False)
    if not platforms and not exclusive and found is None:
        return KeyFact("software_integration", "Интеграция с ПО", "В ТЗ не упоминается")
    parts = platforms + [f"фирменное ПО: {item}" for item in exclusive[:2]]
    value = ", ".join(parts) if parts else (found[0] if found else "")
    result = "Фирменное ПО другого производителя — прибор МИРТЕК по нему не пройдёт" if exclusive else None
    return KeyFact("software_integration", "Интеграция с ПО", value, result, quote=found[0] if found else None)


def split_requirement(requirement: Requirement) -> tuple[str, str | None]:
    """Параметр и требуемое значение: «Класс точности: 0,5S» → («Класс точности», «0,5S»).
    Формулировка без «параметр: значение» — целиком, без значения."""

    text = re.sub(r"\s+", " ", requirement_text(requirement)).strip()
    subject, colon, value = text.partition(":")
    value = value.strip()
    if colon and value and subject.strip() and len(subject) <= 80:
        return subject.strip(), value
    return text, None


def _mismatch(
    sources: _Sources, cells: dict[uuid.UUID, tuple[ComplianceMatrixEntry, str | None]]
) -> KeyFact | None:
    """Почему наш прибор не подходит: параметры, по которым не проходит ни одна модель МИРТЕК.

    Формат портала: каждая причина с новой строки, критичные первыми —
    «Критично: Класс точности — требуется 0,5S → МИРТЕК-32: 1,0»."""

    if not cells:
        return None
    rows = []
    for requirement in sources.requirements:
        cell = cells.get(requirement.id)
        if cell is None or cell[0].status not in _STATUS_RANK:
            continue
        rank = (
            _CRITICALITY_RANK.get(requirement.criticality, 3),
            _STATUS_RANK[cell[0].status],
            _KIND_RANK.get(requirement.kind, 2),
        )
        rows.append((rank, requirement, cell))
    rows.sort(key=lambda item: item[0])
    no_data = sum(1 for entry, _ in cells.values() if entry.status == ComplianceStatus.NO_DATA.value)
    if not rows:
        value = "Не выявлено: МИРТЕК проходит по всем проверенным параметрам"
        result = f"без данных — проверить вручную: {no_data}" if no_data else None
        return KeyFact("mismatch_reasons", "Причины несоответствия", value, result, review=bool(no_data))

    lines = []
    for _, requirement, (entry, model) in rows:
        marks = []
        if requirement.criticality == Criticality.CRITICAL.value:
            marks.append("Критично")
        if entry.status == ComplianceStatus.PARTIAL.value:
            marks.append("частично" if marks else "Частично")
        prefix = ", ".join(marks) + ": " if marks else ""
        explanation = re.sub(r"\s+", " ", entry.explanation or "").strip()[:160] or "не соответствует"
        _, value = split_requirement(requirement)
        # «Программируемое реле управления нагрузкой» без значения — требуется само наличие.
        required = f" — требуется {value[:120]}" if value else ""
        lines.append(f"{prefix}{_subject(requirement)}{required} → {model or 'наша модель'}: {explanation}")
    lines = list(dict.fromkeys(lines))
    if len(lines) > MAX_LISTED + 2:
        lines = lines[: MAX_LISTED + 2] + [f"…и ещё {len(lines) - MAX_LISTED - 2}"]
    return KeyFact("mismatch_reasons", "Причины несоответствия", "\n".join(lines), review=True)


def key_facts(
    db: Session,
    tender: Tender,
    requirements: list[Requirement],
    cells: dict[uuid.UUID, tuple[ComplianceMatrixEntry, str | None]],
    lock: "BrandLock | None",
) -> list[KeyFact]:
    """Ключевые сведения — поля выжимки `technical_summary` в порядке примера портала.

    `cells` — лучшая ячейка матрицы МИРТЕК по требованию (как в пакете Битрикса), `lock` —
    результат `detect_brand_lock` по требованиям к товару: оба уже посчитаны вызывающим.
    """

    sources = _load_sources(db, tender, requirements)
    # Критичные характеристики и габариты в выжимку портала не входят: это строки требований,
    # и критичные из них идут первыми (`priority`).
    facts = [
        _tz_target(db, tender, lock),
        _delivery(sources),
        _mismatch(sources, cells),
        _system_level(sources),
        _crypto(sources),
        _interfaces(sources),
        _software(sources, lock),
    ]
    return [fact for fact in facts if fact is not None]
